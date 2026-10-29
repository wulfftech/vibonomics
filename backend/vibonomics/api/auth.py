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
