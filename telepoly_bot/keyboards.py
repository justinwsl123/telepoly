"""Centralized InlineKeyboard builders (English only)."""
from __future__ import annotations

import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def _miniapp_url(event_id: int) -> str | None:
    """Mini App link (HTTPS). MINIAPP_BASE_URL=Railway public URL; unset → hide button."""
    base = os.getenv("MINIAPP_BASE_URL", "").rstrip("/")
    if not base or not base.startswith("https://"):
        return None
    return f"{base}/miniapp/event/{event_id}"


def event_keyboard(event_id: int, yes_label: str = "YES", no_label: str = "NO",
                   yes_odds: float = 0.0, no_odds: float = 0.0) -> InlineKeyboardMarkup:
    """Main event card keyboard: YES / NO bet buttons + navigation.

    Odds args are accepted for backward compatibility but no longer rendered
    on the buttons — tapping a side leads straight to the amount picker, so
    showing a "live" multiplier on the button itself only confuses (the real
    odds keep moving as the pool grows).
    """
    yes_text = f"🟢 Bet {yes_label}"
    no_text  = f"🔴 Bet {no_label}"

    rows = [
        [
            InlineKeyboardButton(yes_text, callback_data=f"bet:{event_id}:yes"),
            InlineKeyboardButton(no_text,  callback_data=f"bet:{event_id}:no"),
        ],
    ]
    mini = _miniapp_url(event_id)
    if mini:
        rows.append([InlineKeyboardButton("📈 Live chart & bet", web_app=WebAppInfo(url=mini))])
    rows += [
        [InlineKeyboardButton("📅 All open events", callback_data="events")],
        [
            InlineKeyboardButton("👤 My bets", callback_data="me"),
            InlineKeyboardButton("💵 Deposit", callback_data="deposit"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def amount_keyboard(event_id: int, side: str) -> InlineKeyboardMarkup:
    """Quick-pick bet amount in USDT."""
    quick = [1, 5, 10, 50, 100]
    rows = [
        [InlineKeyboardButton(f"{x} USDT", callback_data=f"amt:{event_id}:{side}:{x}") for x in quick[:3]],
        [InlineKeyboardButton(f"{x} USDT", callback_data=f"amt:{event_id}:{side}:{x}") for x in quick[3:]],
        [InlineKeyboardButton("✏️ Custom amount", callback_data=f"amt:{event_id}:{side}:custom")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"detail:{event_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(event_id: int, side: str, amount_usdt: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Confirm {amount_usdt} USDT on {side.upper()}",
                             callback_data=f"confirm:{event_id}:{side}:{amount_usdt}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"detail:{event_id}"),
    ]])


def menu_keyboard() -> InlineKeyboardMarkup:
    """Compact main menu (shown when there is no active event)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 All open events", callback_data="events")],
        [InlineKeyboardButton("👤 My bets", callback_data="me"),
         InlineKeyboardButton("💵 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("👥 Invite & earn", callback_data="invite")],
    ])


def events_list_keyboard(events) -> InlineKeyboardMarkup:
    """One row per open event → tapping opens that event card.

    `events` is an iterable of Event ORM rows (must have .id, .title, optional pool fields).
    """
    rows = []
    for ev in events:
        title = (ev.title or "").strip()
        if len(title) > 48:
            title = title[:47] + "…"
        rows.append([InlineKeyboardButton(f"🎯 {title}", callback_data=f"detail:{ev.id}")])
    rows.append([InlineKeyboardButton("⬅️ Back to today", callback_data="today")])
    return InlineKeyboardMarkup(rows)


def age_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ I'm 18+", callback_data="age_ok")]])
