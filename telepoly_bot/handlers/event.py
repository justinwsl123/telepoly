"""今日事件展示 + 下注流程。"""
from __future__ import annotations

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from db.models import Event
from db.session import get_session
from core.events import get_active_event
from core.betting import BetError, implied_odds, place_bet, predict_payout
from core.money import fmt_usdt, micro_to_usdt, usdt_to_micro
from core.users import get_or_create_user
from telepoly_bot.keyboards import amount_keyboard, confirm_keyboard, event_keyboard
from telepoly_bot.texts import t
from telepoly_bot.views import render_event_card

# 自定义金额输入态：user_id → (event_id, side)
PENDING_AMOUNT: dict[int, tuple[int, str]] = {}


def _user_lang(s, tg_id: int) -> str:
    user, _ = get_or_create_user(s, tg_user_id=tg_id)
    return user.lang if user.lang in ("en", "zh") else "en"


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_today(update, ctx)


async def _send_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user_id = update.effective_user.id

    with get_session() as s:
        lang = _user_lang(s, user_id)
        ev = get_active_event(s)
        if not ev:
            await msg.reply_markdown(t("no_active_event", lang))
            return
        yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
        text = render_event_card(ev, lang)
        kb = event_keyboard(ev.id, ev.yes_label, ev.no_label, yes_odds, no_odds)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await msg.reply_markdown(text, reply_markup=kb)


async def cb_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_today(update, ctx)


async def cb_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_today(update, ctx)


# ---------------- 下注流程 ----------------

async def cb_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """点 [YES] / [NO] → 选金额"""
    q = update.callback_query
    await q.answer()
    _, event_id_str, side = q.data.split(":")
    event_id = int(event_id_str)

    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await q.edit_message_text("❌ 事件已封盘 / Event closed.")
            return
        lang = _user_lang(s, q.from_user.id)
        label = ev.yes_label if side == "yes" else ev.no_label

    text = (f"💸 选择金额 / Choose amount\n方向 *{side.upper()}* ({label})"
            if lang == "zh"
            else f"💸 Choose amount\nSide *{side.upper()}* ({label})")
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=amount_keyboard(event_id, side))


async def cb_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """选金额 → 弹确认"""
    q = update.callback_query
    await q.answer()
    _, event_id_str, side, amount_str = q.data.split(":")
    event_id = int(event_id_str)

    if amount_str == "custom":
        PENDING_AMOUNT[q.from_user.id] = (event_id, side)
        await q.edit_message_text(
            "✏️ 直接发送金额（USDT），例如 `25` 或 `12.5`\n"
            "输入完成后会显示确认。"
        )
        return

    amount_usdt = float(amount_str)
    await _show_confirm(q, event_id, side, amount_usdt)


async def _show_confirm(q, event_id: int, side: str, amount_usdt: float) -> None:
    amt_micro = usdt_to_micro(amount_usdt)
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await q.edit_message_text("❌ 事件已封盘。")
            return
        payout = predict_payout(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps, side, amt_micro)
        label = ev.yes_label if side == "yes" else ev.no_label

    text = (
        f"⚠️ 请确认下注\n\n"
        f"事件: _{ev.title}_\n"
        f"方向: *{side.upper()}* ({label})\n"
        f"金额: *{amount_usdt} USDT*\n"
        f"若中奖预估回报: ≈ *{micro_to_usdt(payout):.2f} USDT*\n"
        f"（含本金；最终以结算时池子比例为准）"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=confirm_keyboard(event_id, side, amount_usdt))


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """确认下注 → 落库"""
    q = update.callback_query
    _, event_id_str, side, amount_str = q.data.split(":")
    event_id = int(event_id_str)
    amount_usdt = float(amount_str)
    amt_micro = usdt_to_micro(amount_usdt)

    with get_session() as s:
        ev = s.get(Event, event_id)
        user, _ = get_or_create_user(s, tg_user_id=q.from_user.id, username=q.from_user.username)
        lang = user.lang if user.lang in ("en", "zh") else "en"
        try:
            bet = place_bet(s, user=user, event=ev, side=side, amount_micro=amt_micro)
        except BetError as e:
            await q.answer(str(e), show_alert=True)
            return

        yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
        odds = yes_odds if side == "yes" else no_odds
        payout = predict_payout(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps, side,
                                amt_micro * 0)  # 本人这一笔已计入池子，预估"再追加 0"=零，所以重算用 bet 数据
        # 改回用 bet 自身预估
        from core.settlement import SettlementError  # noqa: F401  (不用，但保留以备后用)
        # 给用户看的"假设现在结算" payout：按当前池子比例
        winning_pool = ev.pool_yes_micro if side == "yes" else ev.pool_no_micro
        total = ev.pool_yes_micro + ev.pool_no_micro
        payout_pool = total * (10_000 - ev.fee_bps) // 10_000
        est_payout = payout_pool * bet.amount_micro // winning_pool if winning_pool else 0

        msg = t("bet_placed", lang,
                side=side.upper(),
                amt=f"{amount_usdt:.2f}",
                payout=f"{micro_to_usdt(est_payout):.2f}",
                odds=f"{odds:.2f}",
                bal=f"{micro_to_usdt(user.balance_micro):.2f}")

    await q.answer("✅", show_alert=False)
    await q.edit_message_text(msg, parse_mode="Markdown")


async def on_text_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户在 custom 金额输入态发的文本。"""
    user_id = update.effective_user.id
    pending = PENDING_AMOUNT.pop(user_id, None)
    if not pending:
        return  # 不在输入态就忽略
    event_id, side = pending

    text = (update.message.text or "").strip().replace(",", ".")
    try:
        amount_usdt = float(text)
        if amount_usdt <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("⚠️ 请发送有效数字，例如 25 或 12.5")
        PENDING_AMOUNT[user_id] = pending
        return

    # 把"消息"伪装成 callback 风格调用 _show_confirm
    fake_q = update.message
    # 直接调 confirm_keyboard 渲染
    amt_micro = usdt_to_micro(amount_usdt)
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await update.message.reply_text("❌ 事件已封盘。")
            return
        payout = predict_payout(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps, side, amt_micro)
        label = ev.yes_label if side == "yes" else ev.no_label

    await update.message.reply_markdown(
        f"⚠️ 请确认下注\n\n"
        f"事件: _{ev.title}_\n"
        f"方向: *{side.upper()}* ({label})\n"
        f"金额: *{amount_usdt} USDT*\n"
        f"若中奖预估回报: ≈ *{micro_to_usdt(payout):.2f} USDT*",
        reply_markup=confirm_keyboard(event_id, side, amount_usdt),
    )


def register(app) -> None:
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("event", cmd_today))
    app.add_handler(CallbackQueryHandler(cb_today, pattern=r"^today$"))
    app.add_handler(CallbackQueryHandler(cb_detail, pattern=r"^detail:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet, pattern=r"^bet:\d+:(yes|no)$"))
    app.add_handler(CallbackQueryHandler(cb_amount, pattern=r"^amt:\d+:(yes|no):.+$"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=r"^confirm:\d+:(yes|no):.+$"))
    # 仅在用户处于 custom 金额输入态时才生效（内部判断）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_amount), group=1)
