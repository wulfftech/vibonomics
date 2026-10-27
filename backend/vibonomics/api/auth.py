"""Optional password protection (set ``VIBONOMICS_PASSWORD``). Off by default.

When enabled, every ``/api`` route except the auth endpoints and ``/ping`` needs a signed session
cookie. Put Vibonomics behind your reverse proxy's auth instead if you already have one.
"""

from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from ..config import get_config
from ..crypto import derive_secret

COOKIE = "vibonomics_session"
MAX_AGE = 30 * 24 * 3600

router = APIRouter(prefix="/api/auth", tags=["auth"])


@lru_cache(maxsize=1)
def _serializer() -> URLSafeTimedSerializer:
    # Derived from the encryption key so nothing extra needs configuring.
    return URLSafeTimedSerializer(derive_secret("session"), salt="vibonomics-session")


def auth_enabled() -> bool:
    return bool(get_config().password)


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=MAX_AGE)
        return True
    except BadSignature:
        return False


class LoginIn(BaseModel):
    password: str


class AuthStatus(BaseModel):
    enabled: bool
    authenticated: bool


@router.get("/status", response_model=AuthStatus)
def status(request: Request) -> AuthStatus:
    return AuthStatus(enabled=auth_enabled(), authenticated=is_authenticated(request))


@router.post("/login", response_model=AuthStatus)
def login(body: LoginIn, response: Response, request: Request) -> AuthStatus:
    expected = get_config().password
    if not expected:
        return AuthStatus(enabled=False, authenticated=True)
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Wrong password")
    response.set_cookie(
        COOKIE,
        _serializer().dumps("ok"),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return AuthStatus(enabled=True, authenticated=True)


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response) -> AuthStatus:
    response.delete_cookie(COOKIE, path="/")
    return AuthStatus(enabled=auth_enabled(), authenticated=False)
