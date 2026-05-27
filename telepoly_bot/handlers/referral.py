"""/invite — 生成专属推荐链接（接 TeleGrowth affiliate 表 Day 2 落地）。"""
from __future__ import annotations

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from db.session import get_session
from core.users import get_or_create_user
from telepoly_bot.config import settings


def _ensure_code(user) -> str:
    """简单：affiliate_code = user_<id>，Day 2 接 TeleGrowth 切真实 code。"""
    if user.affiliate_code:
        return user.affiliate_code
    user.affiliate_code = f"u{user.id:06d}"
    return user.affiliate_code


async def cmd_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_invite(update, ctx)


async def cb_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _send_invite(update, ctx)


async def _send_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=u.id, username=u.username)
        code = _ensure_code(user)

    link = f"https://t.me/{settings.telepoly_bot_username}?start=ref_{code}"
    text = (
        "👥 *邀请赚钱 / Invite & Earn*\n\n"
        "把下面的链接发给朋友：\n"
        f"`{link}`\n\n"
        "✅ 朋友的每笔下注的手续费 *40%* 归你（一级）\n"
        "✅ 他再拉来的朋友 *8%* 归你（二级）\n"
        "_（结算后实时入账，>=$10 可申请提现，跨 Bot 共享 affiliate）_"
    )
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await target.reply_markdown(text)


def register(app) -> None:
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CallbackQueryHandler(cb_invite, pattern=r"^invite$"))
