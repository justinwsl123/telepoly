"""Event card rendering (shared between DM, channel and admin web)."""
from __future__ import annotations

from datetime import timezone

from db.models import Event
from core.betting import implied_odds
from core.money import fmt_usdt


def render_event_card(event: Event, *_unused) -> str:
    """Render the headline event card shown in DMs and channels.

    `*_unused` keeps backward compat with old `render_event_card(ev, lang)` calls.
    """
    yes_odds, no_odds = implied_odds(event.pool_yes_micro, event.pool_no_micro, event.fee_bps)
    total_pool = event.pool_yes_micro + event.pool_no_micro
    close_str = event.close_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    desc = (event.description or "").strip()
    desc_block = f"\n_{desc}_\n" if desc else ""

    return (
        f"💰 *TOTAL POOL · {fmt_usdt(total_pool)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *{event.title}*"
        f"{desc_block}\n"
        f"🟢 {event.yes_label}  →  *{yes_odds:.2f}x*   ({fmt_usdt(event.pool_yes_micro, '')} USDT)\n"
        f"🔴 {event.no_label}   →  *{no_odds:.2f}x*   ({fmt_usdt(event.pool_no_micro, '')} USDT)\n\n"
        f"⏰ Closes: {close_str}\n"
        f"_Parimutuel · winners split the losers' pool. The earlier you bet, the better the odds._"
    )


def render_settlement_announcement(event: Event, summary: dict, *_unused) -> str:
    """Channel announcement after an event is settled / voided."""
    if event.outcome == "void":
        body = "↩️ *Voided*. All stakes refunded.\n"
    else:
        body = (
            f"🏆 Result: *{event.outcome.upper()}*\n"
            f"💰 Pool: {fmt_usdt(event.pool_yes_micro + event.pool_no_micro)}\n"
            f"🏛 Fee (5%): {fmt_usdt(summary.get('fee_micro', 0))}\n"
            f"👥 Winners: {summary.get('winners', 0)} · Losers: {summary.get('losers', 0)}\n"
        )
    head = f"📣 *Event settled*\n_{event.title}_\n\n"
    evi = f"\n🔗 Evidence: {event.evidence_url}" if event.evidence_url else ""
    return head + body + evi
