"""Telegram Mini App · 实时赔率页 + 一键下注。

为什么放在 admin_web：复用 FastAPI / Jinja2 基础设施，独立模块，
路由前缀 /miniapp/ 与 admin 完全分离。

鉴权：Telegram WebApp `initData` 用 bot_token 做 HMAC 签名，无需我们额外鉴权。
参考：https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from db.models import Bet, Event, User
from db.session import get_session
from core.betting import BetError, implied_odds, place_bet, predict_payout
from core.events import get_active_event
from core.money import micro_to_usdt, usdt_to_micro
from core.snapshots import fetch_timeline
from core.users import get_or_create_user
from telepoly_bot.config import settings


BASE_DIR = Path(__file__).resolve().parent
mp_templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
mp_templates.env.filters["usdt"] = lambda m: f"{micro_to_usdt(int(m or 0)):.2f}"

router = APIRouter(prefix="/miniapp")


# ----------------------------- Telegram WebApp 鉴权 -----------------------------
def verify_init_data(init_data: str, bot_token: str, max_age_sec: int = 86400) -> dict | None:
    """验证 Telegram.WebApp.initData，返回解析后的 user 字段；失败返回 None。"""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        recv_hash = pairs.pop("hash", None)
        if not recv_hash:
            return None
        # data_check_string: 排序键 + key=value 换行
        data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, recv_hash):
            return None
        # auth_date 校验防重放
        auth_date = int(pairs.get("auth_date", "0"))
        now = datetime.now(tz=timezone.utc).timestamp()
        if now - auth_date > max_age_sec:
            return None
        user_json = pairs.get("user")
        return json.loads(user_json) if user_json else None
    except Exception:
        return None


def _user_from_request(request: Request) -> User | None:
    init_data = (
        request.headers.get("x-telegram-init-data")
        or request.query_params.get("tg_init_data")
        or ""
    )
    tg_user = verify_init_data(init_data, settings.telepoly_bot_token)
    if not tg_user:
        return None
    with get_session() as s:
        u, _ = get_or_create_user(
            s, tg_user_id=tg_user.get("id"),
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            lang=(tg_user.get("language_code") or "en")[:2],
        )
        # detach 出来给调用方读字段（不会再 commit）
        s.expunge(u)
        return u


# ----------------------------- 页面 -----------------------------
@router.get("/event/{event_id}", response_class=HTMLResponse)
async def event_page(request: Request, event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            raise HTTPException(404, "event not found")
    return mp_templates.TemplateResponse(request, "miniapp_event.html", {
        "event_id": event_id,
        "title": ev.title,
        "bot_username": settings.telepoly_bot_username,
    })


@router.get("/today", response_class=HTMLResponse)
async def today_redirect(request: Request):
    with get_session() as s:
        ev = get_active_event(s)
    if not ev:
        return HTMLResponse(
            "<div style='font-family:-apple-system;padding:40px;text-align:center;'>"
            "🌙 No active event right now.<br>Check back at 09:00 UTC tomorrow."
            "</div>"
        )
    return await event_page(request, ev.id)


# ----------------------------- JSON API -----------------------------
@router.get("/api/event/{event_id}")
async def api_event(event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            raise HTTPException(404)
        yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
        timeline = fetch_timeline(s, event_id, max_points=120)
        return {
            "id": ev.id,
            "title": ev.title,
            "description": ev.description,
            "state": ev.state,
            "outcome": ev.outcome,
            "yes_label": ev.yes_label,
            "no_label": ev.no_label,
            "pool_yes_micro": ev.pool_yes_micro,
            "pool_no_micro": ev.pool_no_micro,
            "yes_odds": yes_odds,
            "no_odds": no_odds,
            "fee_bps": ev.fee_bps,
            "close_at": ev.close_at.isoformat() + "Z",
            "timeline": timeline,
        }


@router.get("/api/me")
async def api_me(request: Request):
    user = _user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "id": user.id,
        "tg_user_id": user.tg_user_id,
        "username": user.username,
        "balance_micro": user.balance_micro,
    }


@router.post("/api/bet/{event_id}")
async def api_bet(event_id: int, request: Request):
    user_view = _user_from_request(request)
    if not user_view:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    side = (body.get("side") or "").lower()
    try:
        amount_usdt = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid amount"}, status_code=400)

    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            return JSONResponse({"error": "event not found"}, status_code=404)
        # 重新拉一份当前 session 内的 User
        from sqlalchemy import select
        u = s.scalars(select(User).where(User.tg_user_id == user_view.tg_user_id)).first()
        if not u:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            bet = place_bet(s, user=u, event=ev, side=side,
                            amount_micro=usdt_to_micro(amount_usdt))
        except BetError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        winning_pool = ev.pool_yes_micro if side == "yes" else ev.pool_no_micro
        total = ev.pool_yes_micro + ev.pool_no_micro
        payout_pool = total * (10_000 - ev.fee_bps) // 10_000
        est_payout = payout_pool * bet.amount_micro // winning_pool if winning_pool else 0

        return {
            "ok": True,
            "bet_id": bet.id,
            "balance_micro": u.balance_micro,
            "estimated_payout_micro": est_payout,
        }
