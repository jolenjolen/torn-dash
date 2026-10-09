"""Session cookie helpers shared by every router."""
from fastapi import HTTPException, Request, Response

from . import config, users

COOKIE = "td_session"


def https(request: Request) -> bool:
    if config.COOKIE_SECURE in ("true", "1", "yes"):
        return True
    if config.COOKIE_SECURE in ("false", "0", "no"):
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def current_player(request: Request) -> int:
    pid = users.session_player(request.cookies.get(COOKIE))
    if not pid or not users.get(pid):
        raise HTTPException(401, "Sign in with your Torn API key.")
    return pid


def set_cookie(resp: Response, request: Request, token: str):
    resp.set_cookie(COOKIE, token, max_age=config.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=https(request), path="/")
