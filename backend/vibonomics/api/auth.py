"""Optional password protection (set ``VIBONOMICS_PASSWORD``). Off by default.

When enabled, every ``/api`` route except the auth endpoints and ``/ping`` needs a signed session
cookie. Put Vibonomics behind your reverse proxy's auth instead if you already have one.

With ``VIBONOMICS_OPEN_READ`` as well, visitors without a session can read: every GET, and the one POST
that only reads. Anything else changes something, and needs the password. A new route is therefore
protected until it is listed here.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from ..config import get_config
from ..crypto import derive_secret

COOKIE = "vibonomics_session"
MAX_AGE = 30 * 24 * 3600

# What a visitor without a session may do when ``VIBONOMICS_OPEN_READ`` is on.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READ_ONLY_POSTS = frozenset({"/api/models/value"})

router = APIRouter(prefix="/api/auth", tags=["auth"])


@lru_cache(maxsize=1)
def _serializer() -> URLSafeTimedSerializer:
    # Derived from the encryption key so nothing extra needs configuring.
    return URLSafeTimedSerializer(derive_secret("session"), salt="vibonomics-session")


def auth_enabled() -> bool:
    return bool(get_config().password)


def open_read() -> bool:
    """Whether visitors without a session may read; meaningless without a password."""
    return auth_enabled() and get_config().open_read


def anonymous_allowed(method: str, path: str) -> bool:
    """Whether a request with no session may go through when reading is open."""
    if method in READ_METHODS:
        return True
    return method == "POST" and path.rstrip("/") in READ_ONLY_POSTS


def _host_key(netloc: str) -> str:
    host = netloc.strip().lower()
    for default_port in (":80", ":443"):
        if host.endswith(default_port):
            return host[: -len(default_port)]
    return host


def _origin_is_ours(origin: str, request: Request) -> bool:
    """Whether an ``Origin`` header names this app: the Host header, a proxy's X-Forwarded-Host, or a listed host.

    A page on another site cannot set X-Forwarded-Host on a request it sends without a CORS preflight, which the API
    never answers, so trusting it here lets a proxy that rewrites Host through without opening a cross-site hole.
    """
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if not parts.netloc:  # "null": a sandboxed frame or a redirect that hid the page's origin
        return False
    ours = [request.headers.get("host", ""), *request.headers.getlist("x-forwarded-host")]
    names = {_host_key(name) for value in ours for name in value.split(",") if name.strip()}
    if _host_key(parts.netloc) in names:
        return True
    host = (parts.hostname or "").lower()
    return any(
        pattern == host or (pattern.startswith("*") and host.endswith(pattern[1:]))
        for pattern in (h.lower() for h in get_config().allowed_hosts)
    )


def is_cross_site_write(request: Request) -> bool:
    """Whether a request that changes something was sent by a page on another site.

    The body-less POSTs are "simple requests", so a page anywhere can fire them with a form or a no-cors fetch, and
    the browser adds the session cookie if its SameSite rules allow it (or there is no password at all). The browser
    says where the page came from, and a page cannot forge either header:

    - ``Sec-Fetch-Site`` decides when it is sent. Browsers send it only to HTTPS and localhost, so over plain HTTP
      to a LAN address it is missing and ``Origin`` has to decide.
    - A request with neither header is a script such as curl, which is how the status-line command posts.
    """
    if request.method in READ_METHODS:
        return False
    site = request.headers.get("sec-fetch-site", "").strip().lower()
    if site == "cross-site":
        return True
    if site in {"same-origin", "none"}:
        return False
    origin = request.headers.get("origin")
    return origin is not None and not _origin_is_ours(origin, request)


def _session_value() -> str:
    """What a session cookie carries: a digest of the password it was issued under.

    Changing ``VIBONOMICS_PASSWORD`` then ends every session, without rotating the secret key (which would
    make every stored provider key unreadable).
    """
    password = (get_config().password or "").encode()
    return hmac.new(derive_secret("session-password").encode(), password, hashlib.sha256).hexdigest()[:32]


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        value = _serializer().loads(token, max_age=MAX_AGE)
    except BadSignature:
        return False
    return isinstance(value, str) and hmac.compare_digest(value, _session_value())


class LoginIn(BaseModel):
    password: str


class AuthStatus(BaseModel):
    enabled: bool
    authenticated: bool
    open_read: bool = False


@router.get("/status", response_model=AuthStatus)
def status(request: Request) -> AuthStatus:
    return AuthStatus(enabled=auth_enabled(), authenticated=is_authenticated(request), open_read=open_read())


@router.post("/login", response_model=AuthStatus)
def login(body: LoginIn, response: Response, request: Request) -> AuthStatus:
    expected = get_config().password
    if not expected:
        return AuthStatus(enabled=False, authenticated=True)
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Wrong password")
    response.set_cookie(
        COOKIE,
        _serializer().dumps(_session_value()),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return AuthStatus(enabled=True, authenticated=True, open_read=open_read())


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response) -> AuthStatus:
    response.delete_cookie(COOKIE, path="/")
    return AuthStatus(enabled=auth_enabled(), authenticated=False, open_read=open_read())
