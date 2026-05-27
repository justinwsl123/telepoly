"""Today's market + open-markets list + bet flow (English-only UI)."""
from __future__ import annotations

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from db.models import Event
from db.session import get_session
from core.events import get_active_event, list_events_by_state
from core.betting import BetError, implied_odds, place_bet, predict_payout
from core.money import micro_to_usdt, usdt_to_micro
from core.users import get_or_create_user
from telepoly_bot.config import settings
from telepoly_bot.keyboards import (
    amount_keyboard,
    confirm_keyboard,
    event_keyboard,
    events_list_keyboard,
    menu_keyboard,
)
from telepoly_bot.texts import t
from telepoly_bot.views import render_event_card

# Per-user state for "custom amount" input: user_id → (event_id, side)
PENDING_AMOUNT: dict[int, tuple[int, str]] = {}


# ----------------------------------------------------------------------
# Today's market
# ----------------------------------------------------------------------

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await send_today(ctx, update.effective_chat.id, update.effective_user.id)


async def cb_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass
    await send_today(ctx, update.effective_chat.id, update.effective_user.id)


async def cb_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Open a specific event card (from the open-markets list)."""
    q = update.callback_query
    await q.answer()
    event_id = int(q.data.split(":", 1)[1])
    try:
        await q.message.delete()
    except Exception:
        pass
    await _send_event_card(ctx, update.effective_chat.id, event_id)


async def send_today(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    """Show the current daily market — or a fallback menu when none is open."""
    with get_session() as s:
        ev = get_active_event(s, bot_id=settings.bot_id)
        ev_id = ev.id if ev else None

    if ev_id is None:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=t("no_active_event"),
            parse_mode="Markdown",
            reply_markup=menu_keyboard(),
        )
        return

    await _send_event_card(ctx, chat_id, ev_id)


async def _send_event_card(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, event_id: int) -> None:
    """Render and send the photo+caption event card with YES/NO buttons."""
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev:
            await ctx.bot.send_message(chat_id=chat_id, text=t("no_active_event"),
                                       parse_mode="Markdown", reply_markup=menu_keyboard())
            return
        yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
        text = render_event_card(ev)
        kb = event_keyboard(ev.id, ev.yes_label, ev.no_label, yes_odds, no_odds)

    chart = await _render_event_chart(event_id)

    if chart:
        await ctx.bot.send_photo(chat_id=chat_id, photo=chart, caption=text,
                                 parse_mode="Markdown", reply_markup=kb)
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=text,
                                   parse_mode="Markdown", reply_markup=kb)


async def _render_event_chart(event_id: int) -> bytes | None:
    """Render the pool-trend chart. Return None on any failure → caller degrades to text."""
    try:
        from core.snapshots import fetch_timeline
        from telepoly_bot.charts import render_pool_timeline
        with get_session() as s:
            ev = s.get(Event, event_id)
            points = fetch_timeline(s, event_id)
            return render_pool_timeline(points, title=ev.title if ev else "",
                                         fee_bps=ev.fee_bps if ev else 500)
    except Exception:
        return None


# ----------------------------------------------------------------------
# All open markets
# ----------------------------------------------------------------------

async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_events_list(ctx, update.effective_chat.id)


async def cb_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass
    await _send_events_list(ctx, update.effective_chat.id)


async def _send_events_list(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    with get_session() as s:
        events = list_events_by_state(s, ["open"], bot_id=settings.bot_id)
        items = [(ev.id, ev.title) for ev in events]

    if not items:
        await ctx.bot.send_message(chat_id=chat_id, text=t("no_open_events"),
                                   parse_mode="Markdown", reply_markup=menu_keyboard())
        return

    # Re-fetch lightweight rows for keyboard rendering (id + title is enough).
    class _Row:
        def __init__(self, eid: int, title: str):
            self.id = eid
            self.title = title

    rows = [_Row(eid, title) for eid, title in items]
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=t("events_list_header"),
        parse_mode="Markdown",
        reply_markup=events_list_keyboard(rows),
    )


# ----------------------------------------------------------------------
# Bet flow
# ----------------------------------------------------------------------

async def cb_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """[YES] / [NO] tapped → show amount picker."""
    q = update.callback_query
    await q.answer()
    _, event_id_str, side = q.data.split(":")
    event_id = int(event_id_str)

    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await q.edit_message_text("❌ This market is closed.")
            return
        label = ev.yes_label if side == "yes" else ev.no_label

    text = f"💸 *Choose your bet amount*\nSide: *{side.upper()}* ({label})"
    # Caption-edit fails on photo messages → fall back to sending a fresh message.
    try:
        await q.edit_message_caption(caption=text, parse_mode="Markdown",
                                     reply_markup=amount_keyboard(event_id, side))
    except Exception:
        try:
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=amount_keyboard(event_id, side))
        except Exception:
            await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                       parse_mode="Markdown",
                                       reply_markup=amount_keyboard(event_id, side))


async def cb_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Amount picked → show confirmation."""
    q = update.callback_query
    await q.answer()
    _, event_id_str, side, amount_str = q.data.split(":")
    event_id = int(event_id_str)

    if amount_str == "custom":
        PENDING_AMOUNT[q.from_user.id] = (event_id, side)
        msg = "✏️ Send the amount in USDT, e.g. `25` or `12.5`."
        try:
            await q.edit_message_text(msg, parse_mode="Markdown")
        except Exception:
            await ctx.bot.send_message(chat_id=update.effective_chat.id, text=msg,
                                       parse_mode="Markdown")
        return

    amount_usdt = float(amount_str)
    await _show_confirm(update, ctx, event_id, side, amount_usdt)


async def _show_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        event_id: int, side: str, amount_usdt: float) -> None:
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
        payout = predict_payout(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps, side, amt_micro)
        label = ev.yes_label if side == "yes" else ev.no_label
        title = ev.title

    text = (
        f"⚠️ *Confirm your bet*\n\n"
        f"Market: _{title}_\n"
        f"Side: *{side.upper()}* ({label})\n"
        f"Amount: *{amount_usdt} USDT*\n"
        f"Estimated payout if you win: ≈ *{micro_to_usdt(payout):.2f} USDT*\n"
        f"_(includes your stake; final odds settle on event close)_"
    )
    kb = confirm_keyboard(event_id, side, amount_usdt)

    q = update.callback_query
    if q is not None:
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text,
                               parse_mode="Markdown", reply_markup=kb)


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm tapped → write the bet to the DB."""
    q = update.callback_query
    _, event_id_str, side, amount_str = q.data.split(":")
    event_id = int(event_id_str)
    amount_usdt = float(amount_str)
    amt_micro = usdt_to_micro(amount_usdt)

    with get_session() as s:
        ev = s.get(Event, event_id)
        user, _ = get_or_create_user(s, tg_user_id=q.from_user.id, username=q.from_user.username)
        try:
            bet = place_bet(s, user=user, event=ev, side=side, amount_micro=amt_micro)
        except BetError as e:
            await q.answer(str(e), show_alert=True)
            return

        yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
        odds = yes_odds if side == "yes" else no_odds

        # "What if we settled right now?" — pure UX preview, not a promise.
        winning_pool = ev.pool_yes_micro if side == "yes" else ev.pool_no_micro
        total = ev.pool_yes_micro + ev.pool_no_micro
        payout_pool = total * (10_000 - ev.fee_bps) // 10_000
        est_payout = payout_pool * bet.amount_micro // winning_pool if winning_pool else 0

        msg = t("bet_placed",
                side=side.upper(),
                amt=f"{amount_usdt:.2f}",
                payout=f"{micro_to_usdt(est_payout):.2f}",
                odds=f"{odds:.2f}",
                bal=f"{micro_to_usdt(user.balance_micro):.2f}")

    await q.answer("✅", show_alert=False)
    try:
        await q.edit_message_text(msg, parse_mode="Markdown")
    except Exception:
        await ctx.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode="Markdown")


async def on_text_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch the user's free-text amount while they're in custom-amount mode."""
    user_id = update.effective_user.id
    pending = PENDING_AMOUNT.pop(user_id, None)
    if not pending:
        return
    event_id, side = pending

    text = (update.message.text or "").strip().replace(",", ".")
    try:
        amount_usdt = float(text)
        if amount_usdt <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("⚠️ Please send a valid number, e.g. 25 or 12.5")
        PENDING_AMOUNT[user_id] = pending
        return

    amt_micro = usdt_to_micro(amount_usdt)
    with get_session() as s:
        ev = s.get(Event, event_id)
        if not ev or ev.state != "open":
            await update.message.reply_text("❌ This market is closed.")
            return
        payout = predict_payout(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps, side, amt_micro)
        label = ev.yes_label if side == "yes" else ev.no_label
        title = ev.title

    await update.message.reply_markdown(
        f"⚠️ *Confirm your bet*\n\n"
        f"Market: _{title}_\n"
        f"Side: *{side.upper()}* ({label})\n"
        f"Amount: *{amount_usdt} USDT*\n"
        f"Estimated payout if you win: ≈ *{micro_to_usdt(payout):.2f} USDT*",
        reply_markup=confirm_keyboard(event_id, side, amount_usdt),
    )


def register(app) -> None:
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("event", cmd_today))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CallbackQueryHandler(cb_today, pattern=r"^today$"))
    app.add_handler(CallbackQueryHandler(cb_events, pattern=r"^events$"))
    app.add_handler(CallbackQueryHandler(cb_detail, pattern=r"^detail:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet, pattern=r"^bet:\d+:(yes|no)$"))
    app.add_handler(CallbackQueryHandler(cb_amount, pattern=r"^amt:\d+:(yes|no):.+$"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=r"^confirm:\d+:(yes|no):.+$"))
    # Only matches when the user is in custom-amount mode (handler returns early otherwise).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_amount), group=1)
