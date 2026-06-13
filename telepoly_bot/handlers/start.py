"""/start command: register user, age gate, then drop them straight on today's market."""
from __future__ import annotations

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from db.session import get_session
from core.users import get_or_create_user
from telepoly_bot.config import settings
from telepoly_bot.keyboards import age_gate_keyboard
from telepoly_bot.texts import t


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u is None:
        return

    referrer_code = None
    want_contest = False
    if ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            referrer_code = arg[4:][:64]
        # 世界杯 AI 模型冠军竞猜深链：?start=wc_champion / contest
        if arg in ("wc_champion", "contest"):
            want_contest = True

    with get_session() as s:
        user, _ = get_or_create_user(
            s,
            tg_user_id=u.id,
            username=u.username,
            first_name=u.first_name,
            lang=(u.language_code or settings.default_lang)[:2],
            referrer_code=referrer_code,
        )
        age_ok = user.age_confirmed

    # 记住意图，过完年龄门后落到竞猜卡片
    ctx.user_data["want_contest"] = want_contest

    if not age_ok:
        await update.message.reply_markdown(
            t("age_gate"),
            reply_markup=age_gate_keyboard(),
        )
        return

    if want_contest and await _try_show_contest(update, ctx):
        return
    await _greet_and_show_event(update, ctx)


async def cb_age_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=q.from_user.id, username=q.from_user.username)
        user.age_confirmed = True

    try:
        await q.message.delete()
    except Exception:
        pass
    if ctx.user_data.get("want_contest") and await _try_show_contest(update, ctx):
        return
    await _greet_and_show_event(update, ctx)


async def _try_show_contest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """找到 open 的世界杯竞猜（kind=multi）并直接渲染其卡片。找不到返回 False。"""
    from sqlalchemy import select

    from db.models import Event
    from telepoly_bot.handlers.event import _send_event_card

    ctx.user_data["want_contest"] = False
    with get_session() as s:
        ev = s.scalars(
            select(Event)
            .where(Event.kind == "multi", Event.state == "open",
                   Event.bot_id == settings.bot_id)
            .order_by(Event.close_at.desc())
            .limit(1)
        ).first()
        ev_id = ev.id if ev else None
    if ev_id is None:
        return False
    await _send_event_card(ctx, update.effective_chat.id, ev_id)
    return True


async def _greet_and_show_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a one-line welcome and immediately render today's market card."""
    from telepoly_bot.handlers.event import send_today

    chat_id = update.effective_chat.id
    await ctx.bot.send_message(chat_id=chat_id, text=t("welcome"), parse_mode="Markdown")
    await send_today(ctx, chat_id, update.effective_user.id)


def register(app) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_age_ok, pattern=r"^age_ok$"))
