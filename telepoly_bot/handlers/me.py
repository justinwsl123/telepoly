"""/me · 余额 + 当前仓位 + 历史"""
from __future__ import annotations

from sqlalchemy import desc, select
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from db.models import Bet, Event
from db.session import get_session
from core.money import fmt_usdt, micro_to_usdt
from core.users import get_or_create_user


async def cmd_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_me(update, ctx)


async def cb_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _send_me(update, ctx)


async def _send_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=u.id, username=u.username)
        # 拉最近 10 笔下注
        stmt = (
            select(Bet, Event)
            .join(Event, Bet.event_id == Event.id)
            .where(Bet.user_id == user.id)
            .order_by(desc(Bet.created_at))
            .limit(10)
        )
        rows = list(s.execute(stmt).all())

        active = [(b, e) for b, e in rows if b.status == "placed"]
        history = [(b, e) for b, e in rows if b.status != "placed"]
        bal = user.balance_micro

    lines = [f"💰 *余额 / Balance*: `{micro_to_usdt(bal):.2f}` USDT"]
    if active:
        lines.append("\n🎯 *进行中 / Open positions*:")
        for b, e in active:
            lines.append(
                f"  • {e.title[:40]}…  {b.side.upper()} `{micro_to_usdt(b.amount_micro):.2f}`U"
            )
    if history:
        lines.append("\n📜 *历史 / History*:")
        for b, e in history[:5]:
            tag = {"won": "🏆", "lost": "🪦", "refunded": "↩️"}.get(b.status, "·")
            extra = (f"  +{micro_to_usdt(b.payout_micro):.2f}U"
                     if b.status == "won" else "")
            lines.append(f"  {tag} {e.title[:32]}… {b.side.upper()} `{micro_to_usdt(b.amount_micro):.2f}`U{extra}")
    if not active and not history:
        lines.append("\n_还没下过注。/today 看看今天的题目。_")

    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_markdown(text)


def register(app) -> None:
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("balance", cmd_me))
    app.add_handler(CallbackQueryHandler(cb_me, pattern=r"^me$"))
