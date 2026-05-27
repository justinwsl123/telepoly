"""Event card rendering (caption text + cover image loading).

Design (after user feedback 2026-05-27):
  • The COVER IMAGE is a static photo (e.g. CR7 portrait) — Telegram cannot
    edit photos after send, so we don't waste cycles trying to keep it live.
  • The CAPTION is an ASCII info-card + live odds — Telegram lets us call
    `edit_message_caption` any time, so this part can be refreshed in place
    every time the pool / probability moves.

Layout of the caption (rendered in monospace via a Markdown code-block):

  ╔════════════════════════════════════╗
  ║  1,128.00 USDT  ·  TOTAL POOL      ║
  ║  ● LIVE  ·  18d left               ║
  ╠════════════════════════════════════╣
  ║  62% YES   ▰▰▰▰▰▰▱▱▱▱              ║
  ║  54% → 57% → 59% → 61% → 62%       ║
  ║  ↑ 8.0% since open                 ║
  ╚════════════════════════════════════╝

  🎯 *<title>*
  _<description>_

  🟢 Scores    →  *1.53x*   ( 699.36 USDT )
  🔴 No goal   →  *2.50x*   ( 428.64 USDT )

  ⏰ Closes: 2026-06-15 18:00 UTC
  _Parimutuel · winners split the losers' pool._
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from db.models import Event
from core.betting import implied_odds
from core.money import fmt_usdt, micro_to_usdt


# ---------- helpers --------------------------------------------------------

_BAR_LEN = 10
_BAR_FILLED = "▰"
_BAR_EMPTY = "▱"


def _bar(share: float, length: int = _BAR_LEN) -> str:
    filled = max(0, min(length, round(share * length)))
    return _BAR_FILLED * filled + _BAR_EMPTY * (length - filled)


def _fmt_pool_number(usdt: float) -> str:
    """Compact friendly number, comma-grouped."""
    if usdt >= 10_000:
        return f"{usdt:,.0f}"
    return f"{usdt:,.2f}"


def _fmt_countdown(close_at: datetime) -> str:
    """E.g. '18d left' / '3h 42m left' / 'CLOSED'."""
    now = datetime.now(timezone.utc)
    close = close_at.replace(tzinfo=timezone.utc) if close_at.tzinfo is None else close_at
    secs = int((close - now).total_seconds())
    if secs <= 0:
        return "CLOSED"
    if secs >= 86_400:
        days = secs // 86_400
        return f"{days}d left"
    if secs >= 3600:
        return f"{secs // 3600}h {secs % 3600 // 60}m left"
    return f"{secs // 60}m left"


def _trend_line(timeline: Sequence[dict] | None, max_points: int = 5) -> str | None:
    """E.g. '54% → 57% → 59% → 61% → 62%' or None if not enough data."""
    if not timeline or len(timeline) < 2:
        return None
    if len(timeline) > max_points:
        step = (len(timeline) - 1) / (max_points - 1)
        sampled = [timeline[round(i * step)] for i in range(max_points)]
    else:
        sampled = list(timeline)
    return " → ".join(f"{round(p['yes_share'] * 100)}%" for p in sampled)


def _delta_line(timeline: Sequence[dict] | None) -> str | None:
    """E.g. '↑ 8.0% since open' / '↓ 3.4% since open' or None when flat / no data."""
    if not timeline or len(timeline) < 2:
        return None
    delta = (timeline[-1]["yes_share"] - timeline[0]["yes_share"]) * 100
    if abs(delta) < 0.5:
        return None
    arrow = "↑" if delta > 0 else "↓"
    return f"{arrow} {abs(delta):.1f}% since open"


# ---------- public API -----------------------------------------------------

_DIVIDER = "━━━━━━━━━━━━"  # short, visible without monospace


def render_event_card(event: Event, *_unused, timeline: Sequence[dict] | None = None) -> str:
    """Build the caption sent under the cover image (or as standalone message).

    Style choices (user-driven):
      • NO Markdown code block — Telegram now decorates triple-backticks
        with a left dark border + `</>` icon + small monospace font, which
        the user explicitly rejected. We instead use regular caption text
        with emoji-led structure so the font is full-size and the line
        spacing is generous.
      • Total pool is bolded and given the biggest emoji to read as the
        headline number; live odds use compact 'U' suffix instead of
        'USDT' so the side+odds+pool line stays on one row on phones.
      • `timeline` (snapshots from core.snapshots.fetch_timeline) drives
        the optional trend strip ('54% → 59% → 62%') and Δ-since-open line.
    """
    yes_odds, no_odds = implied_odds(event.pool_yes_micro, event.pool_no_micro, event.fee_bps)
    total_pool = event.pool_yes_micro + event.pool_no_micro
    yes_share = (event.pool_yes_micro / total_pool) if total_pool else 0.5
    yes_pct = round(yes_share * 100)

    countdown = _fmt_countdown(event.close_at)
    live_or_closed = "🔴 LIVE" if countdown != "CLOSED" else "⚫ CLOSED"
    pool_str = _fmt_pool_number(total_pool / 1_000_000)

    desc = (event.description or "").strip()
    close_str = event.close_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    yes_pool_str = fmt_usdt(event.pool_yes_micro, "")
    no_pool_str  = fmt_usdt(event.pool_no_micro, "")

    parts: list[str] = []

    # Header — pool number is THE headline.
    parts.append(f"💰 *{pool_str} USDT*  ·  TOTAL POOL")
    parts.append(f"{live_or_closed}  ·  ⏳ {countdown}")
    parts.append(_DIVIDER)

    # Gauge band — big YES%, then optional trend + delta.
    parts.append(f"📊 *{yes_pct}% YES*   {_bar(yes_share)}")
    trend = _trend_line(timeline)
    if trend:
        parts.append(f"📈 {trend}")
    delta = _delta_line(timeline)
    if delta:
        parts.append(f"🚀 {delta}")

    parts.append("")  # one blank line before title

    # Title + description.
    parts.append(f"🎯 *{event.title}*")
    if desc:
        parts.append(f"_{desc}_")

    parts.append("")  # one blank line before odds

    # Live odds — tight spacing, 'U' instead of 'USDT' to fit one line.
    parts.append(f"🟢 {event.yes_label} → *{yes_odds:.2f}x*  ({yes_pool_str} U)")
    parts.append(f"🔴 {event.no_label} → *{no_odds:.2f}x*  ({no_pool_str} U)")

    parts.append("")  # one blank line before footer

    # Footer.
    parts.append(f"⏰ Closes: {close_str}")
    parts.append("_Parimutuel · winners split the losers' pool_")

    return "\n".join(parts)


def load_cover_bytes(event: Event) -> bytes | None:
    """Return the cover image as bytes ready for `send_photo`, or None.

    `event.cover_url` semantics:
      • starts with 'http(s)://'   → URL is returned as-is to the caller
        (caller passes the string straight to send_photo, Telegram fetches).
      • starts with 'assets/…' or absolute path → read bytes from disk.
      • anything else / missing    → None (caller degrades to text-only).
    """
    url = (event.cover_url or "").strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        # Caller will pass the URL string directly; bytes loader is not used.
        return None
    path = Path(url)
    if not path.is_absolute():
        # Resolve relative to repo root (where the bot is launched from).
        path = Path.cwd() / path
    try:
        return path.read_bytes()
    except Exception:
        return None


def cover_photo_input(event: Event):
    """Return whatever telegram.Bot.send_photo accepts as `photo` arg.

    Either bytes (local file) or a URL string, or None if no cover.
    """
    url = (event.cover_url or "").strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return load_cover_bytes(event)


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
