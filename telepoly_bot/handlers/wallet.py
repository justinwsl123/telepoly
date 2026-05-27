"""充值 / 提现入口（Day 1：地址展示 + 人工对账提示，Day 2 接扫块自动入账）。"""
from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from db.models import WalletAddress
from db.session import get_session
from core.users import get_or_create_user
from telepoly_bot.config import settings
from telepoly_bot.texts import t


def _placeholder_address(user_id: int) -> str:
    """在 wallet/hd.py 接通前的占位地址 — 用平台主热钱包接收，运营人工对账。

    配 .env 的 WALLET_HOT_ADDRESS 就走这里；没配返回特殊提示串。
    """
    return settings.wallet_hot_address or "(待配置 WALLET_HOT_ADDRESS / pending setup)"


async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_deposit(update, ctx)


async def cb_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _send_deposit(update, ctx)


async def _send_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=u.id, username=u.username)
        lang = user.lang if user.lang in ("en", "zh") else "en"

        wa = s.scalars(select(WalletAddress).where(WalletAddress.user_id == user.id)).first()
        if wa:
            address = wa.address
        else:
            # Day 1：还没接 HD 派生 → 用主热钱包做共用入金（用户在 memo / 客服核对）
            address = _placeholder_address(user.id)

    msg = t("deposit_info", lang, address=address)
    note = "\n\n" + t("deposit_pending", lang)

    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(msg + note, parse_mode="Markdown")
    else:
        await target.reply_markdown(msg + note)


async def cmd_withdraw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(
        "📤 *提现 / Withdraw*\n\n"
        "Day 1 提现走人工：发 `/contact` 联系运营。\n"
        "Day 2 起切自动出款（≤ $200 自动批准，> $200 人工审核）。"
    )


def register(app) -> None:
    app.add_handler(CommandHandler("deposit", cmd_deposit))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CommandHandler("wallet", cmd_deposit))
    app.add_handler(CallbackQueryHandler(cb_deposit, pattern=r"^deposit$"))
