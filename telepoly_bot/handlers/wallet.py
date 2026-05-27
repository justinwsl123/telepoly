"""充值 / 提现入口。

充值：
  - WALLET_MNEMONIC 已配 → 自动给用户派生独立 TRC20 地址（HD）；
  - 否则回退到主热钱包 / 占位提示。

提现：
  /withdraw <amount> <T address>
"""
from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from db.models import WalletAddress
from db.session import get_session
from core.money import micro_to_usdt
from core.users import get_or_create_user
from telepoly_bot.config import settings
from telepoly_bot.texts import t


def _get_address_for(user) -> tuple[str, bool]:
    """返回 (address, is_user_specific)。"""
    with get_session() as s:
        wa = s.scalars(select(WalletAddress).where(WalletAddress.user_id == user.id)).first()
        if wa:
            return wa.address, True

        if settings.wallet_mnemonic:
            try:
                from wallet.hd import get_or_create_user_address
                u = s.merge(user)
                wa = get_or_create_user_address(s, u)
                return wa.address, True
            except Exception:
                pass

    return settings.wallet_hot_address or "(pending setup)", False


async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_deposit(update, ctx)


async def cb_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _send_deposit(update, ctx)


async def _send_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=u.id, username=u.username)

    address, user_specific = _get_address_for(user)
    msg = t("deposit_info", address=address)
    if not user_specific:
        msg += "\n\n" + t("deposit_pending")

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_markdown(msg)


async def cmd_withdraw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /withdraw <amount> <T...address>
    """
    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.message.reply_markdown(
            "📤 *Withdraw*\n\n"
            "Usage: `/withdraw <amount> <T-address>`\n"
            "Example: `/withdraw 25 TXa...abc`\n\n"
            "⚠️ TRC20 only. Network fee 1 USDT.\n"
            "Auto-paid up to 200 USDT, larger amounts go to manual review."
        )
        return

    try:
        amount_usdt = float(parts[1])
        if amount_usdt <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Invalid amount."); return
    to_addr = parts[2].strip()

    from core.money import usdt_to_micro
    from wallet.withdraw import WithdrawError, request_withdrawal

    u = update.effective_user
    with get_session() as s:
        user, _ = get_or_create_user(s, tg_user_id=u.id, username=u.username)
        try:
            w = request_withdrawal(s, user=user, to_address=to_addr,
                                    amount_micro=usdt_to_micro(amount_usdt))
        except WithdrawError as e:
            await update.message.reply_text(f"❌ {e}")
            return
        bal = user.balance_micro
        wid = w.id
        status = w.status

    if status == "approved":
        msg = (f"✅ *Withdrawal accepted #{wid}*\n"
               f"Amount: `{amount_usdt}` USDT\n"
               f"To: `{to_addr}`\n"
               f"Network fee: 1 USDT\n"
               f"Status: auto-approved (≤ ${settings.wallet_auto_approve_limit_usdt}). "
               f"Payout job will broadcast the on-chain transaction shortly.\n"
               f"Balance left: `{micro_to_usdt(bal):.2f}` USDT")
    else:
        msg = (f"⏳ *Withdrawal queued #{wid}*\n"
               f"Amount: `{amount_usdt}` USDT exceeds the auto-approval limit — manual review.\n"
               f"Balance left: `{micro_to_usdt(bal):.2f}` USDT")

    await update.message.reply_markdown(msg)


def register(app) -> None:
    app.add_handler(CommandHandler("deposit", cmd_deposit))
    app.add_handler(CommandHandler("wallet", cmd_deposit))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CallbackQueryHandler(cb_deposit, pattern=r"^deposit$"))
