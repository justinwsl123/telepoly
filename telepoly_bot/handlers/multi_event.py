"""多选项竞猜事件的 Bot 展示与下注流程。

注册后接管以 "mbet:" / "mamt:" / "mconfirm:" 开头的 callback 数据。
当 get_active_event 返回 kind="multi" 事件时，由 handlers/event.py 路由到此。
"""
from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from db.models import Event, EventOption
from db.session import get_session
from core.multi_events import get_event_options
from core.multi_betting import MultiBetError, implied_odds_multi, place_bet_multi, predict_payout_multi
from core.money import micro_to_usdt, usdt_to_micro, fmt_usdt
from core.users import get_or_create_user
from telepoly_bot.config import settings
from telepoly_bot.keyboards import menu_keyboard


# 模型 emoji（根据 opt_key）
OPTION_EMOJI = {
    "gpt":      "🟢",
    "claude":   "🟠",
    "gemini":   "🔵",
    "qwen":     "🟡",
    "deepseek": "🟣",
    "kimi":     "⚪",
}

# 每用户下注状态：{tg_user_id: (event_id, opt_key)}
PENDING_MULTI: dict[int, tuple[int, str]] = {}


# ----------------------------- 渲染卡片 -----------------------------

def render_multi_event_card(
    event: Event,
    options: list[EventOption],
    standings: list[dict] | None = None,
) -> str:
    """生成多选项事件的 HTML 卡片文字。"""
    total_pool = sum(o.pool_micro for o in options)
    opt_pools = {o.opt_key: o.pool_micro for o in options}
    odds_map = implied_odds_multi(opt_pools, event.fee_bps)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    close = event.close_at.replace(tzinfo=timezone.utc) if event.close_at.tzinfo is None else event.close_at
    secs = int((close - now).total_seconds())
    if secs <= 0:
        countdown = "CLOSED"
    elif secs >= 86_400:
        countdown = f"{secs // 86_400}d left"
    elif secs >= 3600:
        countdown = f"{secs // 3600}h {secs % 3600 // 60}m left"
    else:
        countdown = f"{secs // 60}m left"

    live_or_closed = "🔴 LIVE" if countdown != "CLOSED" else "⚫ CLOSED"
    pool_str = f"{total_pool / 1_000_000:,.2f}"
    close_str = close.strftime("%Y-%m-%d %H:%M UTC")

    # 信息卡头
    card_lines = [
        f"💰 {pool_str} USDT  ·  TOTAL POOL",
        f"{live_or_closed}  ·  ⏳ {countdown}",
        "━━━━━━━━━━━━",
    ]
    info_card = "\n".join(f"<b>{line}</b>" for line in card_lines)

    # 构建选项行（按当前赔率显示）
    standings_map: dict[str, float] = {}
    if standings:
        for s in standings:
            standings_map[s["opt_key"]] = s.get("current_usd", 0)

    body = [
        "",
        f"🏆 <b>{html.escape(event.title)}</b>",
    ]
    if event.description:
        body.append(f"<i>{html.escape(event.description[:120])}{'…' if len(event.description) > 120 else ''}</i>")
    body.append("")

    BAR = "▰"; EMPTY = "▱"
    for opt in options:
        emoji = OPTION_EMOJI.get(opt.opt_key, "⚫")
        odds = odds_map.get(opt.opt_key, 0.0)
        pool_usdt = opt.pool_micro / 1_000_000
        share = opt.pool_micro / total_pool if total_pool > 0 else 0
        bar_filled = max(0, min(10, round(share * 10)))
        bar_str = BAR * bar_filled + EMPTY * (10 - bar_filled)
        odds_str = f"{odds:.2f}x" if odds > 0 else "N/A"
        pool_str_opt = f"{pool_usdt:,.2f}U"

        # Oracle 当前净值（如果有）
        oracle_str = ""
        if opt.opt_key in standings_map:
            oracle_str = f"  💹${standings_map[opt.opt_key]:,.0f}"

        label = html.escape(opt.label)
        body.append(
            f"{emoji} <b>{label}</b>  →  <b>{odds_str}</b>  ({pool_str_opt}){oracle_str}"
        )
        body.append(f"   {bar_str}  {round(share * 100)}%")
    body.append("")
    body.append(f"⏰ Closes: {close_str}")
    body.append("<i>Parimutuel · 赢家按比例分总池（5% 手续费）</i>")
    body.append("")

    return "\u200b\n" + info_card + "\n" + "\n".join(body)


def multi_event_keyboard(event_id: int, options: list[EventOption]) -> InlineKeyboardMarkup:
    """每个选项一个按钮，最多 3 列。"""
    rows = []
    row = []
    for opt in options:
        emoji = OPTION_EMOJI.get(opt.opt_key, "⚫")
        label = opt.label.split(" (")[0][:20]  # 截短
        row.append(InlineKeyboardButton(
            f"{emoji} {label}",
            callback_data=f"mbet:{event_id}:{opt.opt_key}",
        ))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)

    from os import getenv
    base = getenv("MINIAPP_BASE_URL", "").rstrip("/")
    if base and base.startswith("https://"):
        from telegram import WebAppInfo
        rows.append([InlineKeyboardButton(
            "📈 Live chart & bet",
            web_app=WebAppInfo(url=f"{base}/miniapp/event/{event_id}"),
        )])

    rows += [
        [InlineKeyboardButton("📅 All open events", callback_data="events")],
        [
            InlineKeyboardButton("👤 My bets", callback_data="me"),
            InlineKeyboardButton("💵 Deposit", callback_data="deposit"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _amount_keyboard_multi(event_id: int, opt_key: str) -> InlineKeyboardMarkup:
    quick = [1, 5, 10, 50, 100]
    rows = [
        [InlineKeyboardButton(f"{x} USDT", callback_data=f"mamt:{event_id}:{opt_key}:{x}") for x in quick[:3]],
        [InlineKeyboardButton(f"{x} USDT", callback_data=f"mamt:{event_id}:{opt_key}:{x}") for x in quick[3:]],
        [InlineKeyboardButton("✏️ Custom", callback_data=f"mamt:{event_id}:{opt_key}:custom")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"detail:{event_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard_multi(event_id: int, opt_key: str, amount_usdt: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Confirm {amount_usdt} USDT → {opt_key.upper()}",
            callback_data=f"mconfirm:{event_id}:{opt_key}:{amount_usdt}",
        ),
        InlineKeyboardButton("❌ Cancel", callback_data=f"detail:{event_id}"),
    ]])


# ----------------------------- 发送卡片 -----------------------------

async def send_multi_event_card(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    event_id: int,
) -> None:
    """发送多选项事件卡片（纯文本，无封面图）。"""
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            await ctx.bot.send_message(chat_id=chat_id, text="❌ Event not found.",
                                       reply_markup=menu_keyboard())
            return
        options = get_event_options(s, event_id)

    # 尝试拉 Oracle 实时排名（失败不影响展示）
    standings: list[dict] = []
    try:
        from integrations.oracle import model_standings
        standings = model_standings()
    except Exception:
        pass

    text = render_multi_event_card(ev, options, standings)
    kb = multi_event_keyboard(event_id, options)

    if ev.cover_url:
        try:
            from telepoly_bot.views import cover_photo_input
            photo = cover_photo_input(ev)
            if photo:
                await ctx.bot.send_photo(chat_id=chat_id, photo=photo, caption=text,
                                         parse_mode="HTML", reply_markup=kb)
                return
        except Exception:
            pass

    await ctx.bot.send_message(chat_id=chat_id, text=text,
                               parse_mode="HTML", reply_markup=kb,
                               disable_web_page_preview=True)


# ----------------------------- Callback Handlers -----------------------------

async def cb_mbet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """mbet:{event_id}:{opt_key} — 选中选项，弹出金额选择器。"""
    q = update.callback_query
    await q.answer()
    _, event_id_str, opt_key = q.data.split(":", 2)
    event_id = int(event_id_str)

    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await q.edit_message_text("❌ This market is closed.")
            return
        opts = get_event_options(s, event_id)
        opt = next((o for o in opts if o.opt_key == opt_key), None)
        if not opt:
            await q.edit_message_text("❌ Invalid option.")
            return
        label = opt.label

    text = f"💸 <b>Choose your bet amount</b>\nOption: <b>{html.escape(label)}</b>"
    try:
        await q.edit_message_caption(caption=text, parse_mode="HTML",
                                     reply_markup=_amount_keyboard_multi(event_id, opt_key))
    except Exception:
        try:
            await q.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=_amount_keyboard_multi(event_id, opt_key))
        except Exception:
            await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                       parse_mode="HTML",
                                       reply_markup=_amount_keyboard_multi(event_id, opt_key))


async def cb_mamt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """mamt:{event_id}:{opt_key}:{amount|custom} — 金额选择。"""
    q = update.callback_query
    await q.answer()
    _, event_id_str, opt_key, amount_str = q.data.split(":", 3)
    event_id = int(event_id_str)

    if amount_str == "custom":
        PENDING_MULTI[q.from_user.id] = (event_id, opt_key)
        msg = "✏️ Send the amount in USDT, e.g. `25` or `12.5`."
        try:
            await q.edit_message_text(msg, parse_mode="Markdown")
        except Exception:
            await ctx.bot.send_message(chat_id=update.effective_chat.id, text=msg,
                                       parse_mode="Markdown")
        return

    amount_usdt = float(amount_str)
    await _show_multi_confirm(update, ctx, event_id, opt_key, amount_usdt)


async def _show_multi_confirm(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    event_id: int,
    opt_key: str,
    amount_usdt: float,
) -> None:
    amt_micro = usdt_to_micro(amount_usdt)
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            text = "❌ This market is closed."
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text)
            return
        opts = get_event_options(s, event_id)
        opt_map = {o.opt_key: o for o in opts}
        if opt_key not in opt_map:
            await ctx.bot.send_message(chat_id=update.effective_chat.id,
                                       text="❌ Invalid option.")
            return
        opt_pools = {o.opt_key: o.pool_micro for o in opts}
        payout = predict_payout_multi(opt_pools, ev.fee_bps, opt_key, amt_micro)
        label = opt_map[opt_key].label
        title = ev.title

    text = (
        f"⚠️ *Confirm your bet*\n\n"
        f"Market: _{html.escape(title)}_\n"
        f"Option: *{opt_key.upper()}* ({label})\n"
        f"Amount: *{amount_usdt} USDT*\n"
        f"Estimated payout if you win: ≈ *{micro_to_usdt(payout):.2f} USDT*\n"
        f"_(final odds settle at market close)_"
    )
    kb = _confirm_keyboard_multi(event_id, opt_key, amount_usdt)
    q = update.callback_query
    if q is not None:
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text,
                               parse_mode="Markdown", reply_markup=kb)


async def cb_mconfirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """mconfirm:{event_id}:{opt_key}:{amount} — 确认下注。"""
    q = update.callback_query
    _, event_id_str, opt_key, amount_str = q.data.split(":", 3)
    event_id = int(event_id_str)
    amount_usdt = float(amount_str)
    amt_micro = usdt_to_micro(amount_usdt)

    with get_session() as s:
        ev = s.get(Event, event_id)
        user, _ = get_or_create_user(s, tg_user_id=q.from_user.id,
                                     username=q.from_user.username)
        try:
            bet = place_bet_multi(s, user=user, event=ev, opt_key=opt_key,
                                  amount_micro=amt_micro)
        except MultiBetError as e:
            await q.answer(str(e), show_alert=True)
            return

        # 展示成功信息
        opts = get_event_options(s, event_id)
        opt_map = {o.opt_key: o for o in opts}
        opt_pools = {o.opt_key: o.pool_micro for o in opts}
        total = sum(opt_pools.values())
        payout_pool = total * (10_000 - ev.fee_bps) // 10_000
        winning_pool = opt_map[opt_key].pool_micro
        est_payout = payout_pool * bet.amount_micro // winning_pool if winning_pool else 0

        msg = (
            f"✅ *Bet placed*\n"
            f"Option: *{opt_key.upper()}*\n"
            f"Amount: `{amount_usdt:.2f}` USDT\n"
            f"Est. payout if win: `{micro_to_usdt(est_payout):.2f}` USDT\n\n"
            f"Balance left: `{micro_to_usdt(user.balance_micro):.2f}` USDT"
        )

    await q.answer("✅", show_alert=False)
    try:
        await q.edit_message_text(msg, parse_mode="Markdown")
    except Exception:
        await ctx.bot.send_message(chat_id=update.effective_chat.id,
                                   text=msg, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(cb_mbet, pattern=r"^mbet:\d+:\w+$"))
    app.add_handler(CallbackQueryHandler(cb_mamt, pattern=r"^mamt:\d+:\w+:.+$"))
    app.add_handler(CallbackQueryHandler(cb_mconfirm, pattern=r"^mconfirm:\d+:\w+:.+$"))
