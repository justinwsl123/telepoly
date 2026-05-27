"""Bot copy templates (English only).

Single source of truth for user-facing strings. Keep them short,
friendly and Apple-grade clean. NO internal/operations notes here.
"""
from __future__ import annotations


T: dict[str, str] = {
    "welcome": (
        "👋 *Welcome to TelePoly* — the daily prediction market on Telegram.\n\n"
        "Bet *YES* or *NO* on today's question before the deadline.\n"
        "Winners split the losers' pool (5% house fee). All in USDT."
    ),
    "age_gate": (
        "⚠️ TelePoly is for users *18 or older*.\n"
        "By tapping below you confirm you are of legal age."
    ),
    "no_active_event": (
        "🌙 No question is open right now.\n"
        "The next one drops at 09:00 UTC tomorrow — stay tuned!"
    ),
    "no_open_events": (
        "📭 No open markets at the moment.\n"
        "Come back at 09:00 UTC for today's question."
    ),
    "events_list_header": "📅 *Open markets*\n\nTap one to view and bet:",
    "balance": "💰 *Balance*: `{bal}` USDT",
    "insufficient": "❌ Not enough balance. Tap /deposit to top up.",
    "bet_placed": (
        "✅ *Bet placed*\n"
        "Side: *{side}*\n"
        "Amount: `{amt}` USDT\n"
        "Estimated payout if you win: `{payout}` USDT\n"
        "Current odds: *{odds}x*\n\n"
        "Balance left: `{bal}` USDT"
    ),
    "deposit_info": (
        "💵 *Deposit USDT (TRC20)*\n\n"
        "Send any amount of *USDT on TRON (TRC20)* to:\n"
        "`{address}`\n\n"
        "⚠️ TRC20 only. *Other chains will be lost.*\n"
        "⏱  Funds credit after 19 confirmations (~1 min)."
    ),
    "deposit_pending": (
        "⏳ Auto-deposit watcher comes online tomorrow.\n"
        "Until then, send your tx hash to support for manual credit."
    ),
    "settled_won": "🏆 *Event settled — you won!*\n{title}\n+`{payout}` USDT credited.",
    "settled_lost": "🪦 *Event settled* — outcome was *{outcome}*.\n{title}\nBetter luck tomorrow.",
    "settled_void": "↩️ *Event voided* — your stake was refunded in full.\n{title}",
}


def t(key: str, *_unused, **fmt) -> str:
    """Lookup a copy template by key and format it.

    `*_unused` keeps backward compatibility with older `t(key, lang)` calls.
    """
    text = T.get(key, key)
    return text.format(**fmt) if fmt else text
