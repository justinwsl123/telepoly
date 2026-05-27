"""TelePoly 运营后台 · FastAPI + Jinja2 · Apple HIG 风格。

启动：
    uv run uvicorn admin_web.main:app --host 0.0.0.0 --port 8080

页面：
    /            概览（事件、池子、平台余额、待审提现）
    /events      事件列表 + 创建 / 发布 / 封盘 / 结算
    /users       用户检索 + 余额调整
    /treasury    平台账户 + 提现审核队列
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from db.models import Bet, Event, Ledger, User, Withdrawal
from db.session import get_session
from core.events import create_event, lock_event as do_lock, open_event as do_open
from core.ledger import record_ledger
from core.money import micro_to_usdt, usdt_to_micro
from core.settlement import settle_event
from telepoly_bot.config import settings
from admin_web import auth as auth_mod


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["usdt"] = lambda m: f"{micro_to_usdt(int(m or 0)):.2f}"
templates.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "-"

app = FastAPI(title="TelePoly Admin", docs_url=None, redoc_url=None)

# 静态资源（CSS）
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ----------------------------- 鉴权 -----------------------------
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 公开路径：登录页、静态资源、健康检查、Mini App（用 TG initData 自鉴权）
    if (
        path.startswith("/static")
        or path.startswith("/miniapp")
        or path.startswith("/api/")  # 跨 bot 钱包 API（API_KEY 鉴权）
        or path in ("/login", "/healthz")
    ):
        return await call_next(request)
    if not auth_mod.is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# Mini App 路由（Telegram WebApp）
from admin_web.miniapp import router as miniapp_router
app.include_router(miniapp_router)

# 跨 Bot 钱包 API（HTTP X-Wallet-Api-Key 鉴权）
from admin_web.wallet_api import router as wallet_api_router
app.include_router(wallet_api_router)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    token = auth_mod.login(password)
    if not token:
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth_mod.SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth_mod.SESSION_TTL)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth_mod.SESSION_COOKIE)
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ----------------------------- 概览 -----------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    with get_session() as s:
        # 关键指标
        n_users = s.scalar(select(func.count()).select_from(User)) or 0
        n_active = s.scalar(select(func.count()).select_from(Event).where(Event.state == "open")) or 0
        n_pending_withdraw = s.scalar(
            select(func.count()).select_from(Withdrawal).where(Withdrawal.status == "pending")
        ) or 0
        platform_fee_micro = s.scalar(
            select(func.coalesce(func.sum(Ledger.delta_micro), 0)).where(Ledger.reason == "fee")
        ) or 0
        total_balance_micro = s.scalar(
            select(func.coalesce(func.sum(User.balance_micro), 0))
        ) or 0
        # 24h 数据
        since = datetime.utcnow() - timedelta(hours=24)
        bet_24h = s.scalar(
            select(func.coalesce(func.sum(Bet.amount_micro), 0)).where(Bet.created_at >= since)
        ) or 0
        # 最近事件
        recent_events = list(s.scalars(select(Event).order_by(desc(Event.id)).limit(5)))

    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard",
        "n_users": n_users,
        "n_active": n_active,
        "n_pending_withdraw": n_pending_withdraw,
        "platform_fee_micro": platform_fee_micro,
        "total_balance_micro": total_balance_micro,
        "bet_24h_micro": bet_24h,
        "recent_events": recent_events,
    })


# ----------------------------- 事件 -----------------------------
@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, ai_brief: str | None = None):
    suggestions: list[dict] = []
    ai_error: str | None = None
    if ai_brief is not None:
        # 用户点了 AI Suggest（query string ai_brief 触发）
        from integrations.aiberm import is_enabled, suggest_events
        if not is_enabled():
            ai_error = "LLM_API_KEY not configured"
        else:
            suggestions = suggest_events(ai_brief)
            if not suggestions:
                ai_error = "AI returned no valid candidates, try again with a clearer brief"

    with get_session() as s:
        events = list(s.scalars(select(Event).order_by(desc(Event.id)).limit(50)))
    return templates.TemplateResponse(request, "events.html", {
        "active": "events",
        "events": events,
        "suggestions": suggestions,
        "ai_error": ai_error,
        "ai_brief": ai_brief or "",
    })


@app.post("/events/ai_suggest_pick")
async def events_ai_pick(
    title: str = Form(...),
    description: str = Form(""),
    yes_label: str = Form("YES"),
    no_label: str = Form("NO"),
    close_in_hours: float = Form(24),
):
    """从 AI 候选直接 fork 成 draft。"""
    with get_session() as s:
        create_event(
            s, title=title, description=description,
            yes_label=yes_label, no_label=no_label,
            close_at=datetime.utcnow() + timedelta(hours=close_in_hours),
            fee_bps=settings.event_fee_bps,
        )
    return RedirectResponse("/events", status_code=303)


@app.post("/events/new")
async def event_new(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    hours: float = Form(24),
    yes_label: str = Form("YES"),
    no_label: str = Form("NO"),
):
    with get_session() as s:
        create_event(
            s, title=title, description=description,
            yes_label=yes_label, no_label=no_label,
            close_at=datetime.utcnow() + timedelta(hours=hours),
            fee_bps=settings.event_fee_bps,
        )
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/publish")
async def event_publish(event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev and ev.state == "draft":
            do_open(s, ev)
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/lock")
async def event_lock(event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev and ev.state == "open":
            do_lock(s, ev)
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/settle")
async def event_settle(
    event_id: int,
    outcome: str = Form(...),
    evidence_url: str = Form(""),
):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            raise HTTPException(404)
        try:
            settle_event(s, ev, outcome, evidence_url=evidence_url or None)
        except Exception as e:
            return RedirectResponse(f"/events?err={e}", status_code=303)
    return RedirectResponse("/events", status_code=303)


# ----------------------------- 用户 -----------------------------
@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str = ""):
    with get_session() as s:
        stmt = select(User).order_by(desc(User.id)).limit(100)
        if q:
            try:
                qid = int(q)
                stmt = select(User).where(
                    (User.id == qid) | (User.tg_user_id == qid)
                )
            except ValueError:
                stmt = select(User).where(User.username.ilike(f"%{q}%"))
        users = list(s.scalars(stmt))
    return templates.TemplateResponse(request, "users.html", {
        "active": "users", "users": users, "q": q,
    })


@app.post("/users/{user_id}/topup")
async def user_topup(user_id: int, amount_usdt: float = Form(...), note: str = Form("")):
    with get_session() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404)
        record_ledger(
            s, user=user, delta_micro=usdt_to_micro(amount_usdt),
            reason="adjust" if amount_usdt < 0 else "deposit",
            note=note or "manual adjustment via admin web",
        )
    return RedirectResponse("/users", status_code=303)


# ----------------------------- 提现审核 -----------------------------
@app.get("/treasury", response_class=HTMLResponse)
async def treasury_page(request: Request):
    with get_session() as s:
        pending = list(s.scalars(
            select(Withdrawal).where(Withdrawal.status == "pending").order_by(Withdrawal.id)
        ))
        approved = list(s.scalars(
            select(Withdrawal).where(Withdrawal.status == "approved").order_by(Withdrawal.id)
        ))
        recent = list(s.scalars(
            select(Withdrawal).where(Withdrawal.status.in_(("sent", "rejected", "failed")))
            .order_by(desc(Withdrawal.id)).limit(20)
        ))
        # 平台手续费收入
        platform_fee_micro = s.scalar(
            select(func.coalesce(func.sum(Ledger.delta_micro), 0)).where(Ledger.reason == "fee")
        ) or 0
        total_balance_micro = s.scalar(
            select(func.coalesce(func.sum(User.balance_micro), 0))
        ) or 0
    return templates.TemplateResponse(request, "treasury.html", {
        "active": "treasury",
        "pending": pending, "approved": approved, "recent": recent,
        "platform_fee_micro": platform_fee_micro,
        "total_balance_micro": total_balance_micro,
    })


@app.post("/treasury/{wid}/approve")
async def treasury_approve(wid: int):
    from wallet.withdraw import approve as do_approve
    with get_session() as s:
        w = s.get(Withdrawal, wid)
        if w:
            do_approve(s, w, approver_uid=0)  # admin web 暂用 0 占位
    return RedirectResponse("/treasury", status_code=303)


@app.post("/treasury/{wid}/reject")
async def treasury_reject(wid: int, reason: str = Form("rejected by ops")):
    from wallet.withdraw import reject as do_reject
    with get_session() as s:
        w = s.get(Withdrawal, wid)
        if w:
            do_reject(s, w, approver_uid=0, reason=reason)
    return RedirectResponse("/treasury", status_code=303)


@app.post("/treasury/{wid}/execute")
async def treasury_execute(wid: int):
    """⚠️ 触发链上发款。需要 WALLET_MNEMONIC 已配置。"""
    from wallet.withdraw import execute_one
    execute_one(wid)
    return RedirectResponse("/treasury", status_code=303)
