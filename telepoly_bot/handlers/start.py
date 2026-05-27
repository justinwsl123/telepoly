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
    if ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            referrer_code = arg[4:][:64]

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

    if not age_ok:
        await update.message.reply_markdown(
            t("age_gate"),
            reply_markup=age_gate_keyboard(),
        )
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
    await _greet_and_show_event(update, ctx)


async def _greet_and_show_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a one-line welcome and immediately render today's market card."""
    from telepoly_bot.handlers.event import send_today

    chat_id = update.effective_chat.id
    await ctx.bot.send_message(chat_id=chat_id, text=t("welcome"), parse_mode="Markdown")
    await send_today(ctx, chat_id, update.effective_user.id)


def register(app) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_age_ok, pattern=r"^age_ok$"))
