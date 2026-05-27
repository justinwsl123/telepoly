"""极简密码鉴权（cookie session）。

ADMIN_PASSWORD 来自 .env，单密码登录。
"""
from __future__ import annotations

import os
import secrets
import time

from fastapi import HTTPException, Request


_SESSIONS: dict[str, float] = {}  # token → expires_at
SESSION_COOKIE = "telepoly_admin"
SESSION_TTL = 60 * 60 * 12  # 12h


def _password() -> str:
    return os.getenv("ADMIN_PASSWORD", "")


def login(password: str) -> str | None:
    expected = _password()
    if not expected:
        return None
    if not secrets.compare_digest(password.encode(), expected.encode()):
        return None
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = time.time() + SESSION_TTL
    return token


def is_authed(request: Request) -> bool:
    if not _password():
        return True  # 未配密码视为本地开发，直接放行（Railway 必须配）
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    exp = _SESSIONS.get(token)
    if not exp or exp < time.time():
        _SESSIONS.pop(token, None)
        return False
    return True


def require_auth(request: Request) -> None:
    if not is_authed(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
