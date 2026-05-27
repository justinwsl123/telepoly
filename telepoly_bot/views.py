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

import html
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
    """E.g. '54% → 57% → 59% → 61% → 62%' or None if not enough data.

    Samples points evenly across the TIME axis rather than the row index.
    Otherwise a long flat tail (e.g. the per-minute snapshots after the
    initial seeded curve) collapses the trend strip to '62% → 62% → 62%'.
    Consecutive duplicate values are also collapsed so we never show
    'X% → X%' twice in a row.
    """
    if not timeline or len(timeline) < 2:
        return None

    if len(timeline) <= max_points:
        sampled = list(timeline)
    else:
        try:
            t0 = datetime.fromisoformat(timeline[0]["t"])
            tN = datetime.fromisoformat(timeline[-1]["t"])
        except Exception:
            t0 = tN = None

        if t0 and tN and tN > t0:
            span = (tN - t0).total_seconds()
            targets = [t0.timestamp() + span * i / (max_points - 1)
                       for i in range(max_points)]
            sampled: list[dict] = []
            j = 0
            for tgt in targets:
                # Walk through the timeline picking the first row whose
                # captured_at is >= the target timestamp.
                while j < len(timeline) - 1 and \
                        datetime.fromisoformat(timeline[j]["t"]).timestamp() < tgt:
                    j += 1
                sampled.append(timeline[j])
        else:
            # Fall back to index sampling if timestamps are unusable.
            step = (len(timeline) - 1) / (max_points - 1)
            sampled = [timeline[round(i * step)] for i in range(max_points)]

    pcts: list[int] = []
    for p in sampled:
        v = round(p["yes_share"] * 100)
        if pcts and pcts[-1] == v:
            continue
        pcts.append(v)
    if len(pcts) < 2:
        return None
    return " → ".join(f"{v}%" for v in pcts)


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

_DIVIDER = "━━━━━━━━━━━━"  # short, visible at the caption's default font


def render_event_card(event: Event, *_unused, timeline: Sequence[dict] | None = None) -> str:
    """Build the caption sent under the cover image.

    OUTPUT FORMAT: HTML (use parse_mode="HTML" when sending).

    Why HTML instead of Markdown:
      • The info-card block needs a tinted background to read as a "card"
        but WITHOUT the </> code-tag icon Telegram now adds to ```...```.
        HTML <blockquote> gives us exactly that — bg-tint + slim left bar,
        no </> button, regular caption font size (so '62% YES ▰▰▰▰▰▰▱▱▱▱'
        no longer wraps on phones).
      • Leading blank line so the caption breathes a bit off the photo.

    `timeline` (from core.snapshots.fetch_timeline) drives the trend strip
    and Δ-since-open line; both gracefully skipped when not available.
    """
    yes_odds, no_odds = implied_odds(event.pool_yes_micro, event.pool_no_micro, event.fee_bps)
    total_pool = event.pool_yes_micro + event.pool_no_micro
    yes_share = (event.pool_yes_micro / total_pool) if total_pool else 0.5
    yes_pct = round(yes_share * 100)

    countdown = _fmt_countdown(event.close_at)
    live_or_closed = "🔴 LIVE" if countdown != "CLOSED" else "⚫ CLOSED"
    pool_str = _fmt_pool_number(total_pool / 1_000_000)
    close_str = event.close_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Anything user-controlled goes through html.escape so '<' etc. don't
    # break the HTML parser. Numbers / emojis / arrows are HTML-safe.
    e_title  = html.escape(event.title or "")
    e_desc   = html.escape((event.description or "").strip())
    e_yes_l  = html.escape(event.yes_label or "YES")
    e_no_l   = html.escape(event.no_label  or "NO")
    yes_pool = fmt_usdt(event.pool_yes_micro, "")
    no_pool  = fmt_usdt(event.pool_no_micro, "")

    # --- info-card block (rendered with bg tint by Telegram) ---
    card_lines: list[str] = [
        f"💰 <b>{pool_str} USDT</b>  ·  TOTAL POOL",
        f"{live_or_closed}  ·  ⏳ {countdown}",
        _DIVIDER,
        f"📊 <b>{yes_pct}% YES</b>   {_bar(yes_share)}",
    ]
    # 4 points keeps the strip on a single line on phones (5 wrapped).
    trend = _trend_line(timeline, max_points=4)
    if trend:
        card_lines.append(f"📈 {trend}")
    delta = _delta_line(timeline)
    if delta:
        card_lines.append(f"🚀 {delta}")
    info_card = "<blockquote>" + "\n".join(card_lines) + "</blockquote>"

    # --- body ---
    body_lines: list[str] = [
        f"🎯 <b>{e_title}</b>",
    ]
    if e_desc:
        body_lines.append(f"<i>{e_desc}</i>")
    body_lines.append("")  # blank line before odds
    body_lines.append(f"🟢 {e_yes_l} → <b>{yes_odds:.2f}x</b>  ({yes_pool} U)")
    body_lines.append(f"🔴 {e_no_l} → <b>{no_odds:.2f}x</b>  ({no_pool} U)")
    body_lines.append("")
    body_lines.append(f"⏰ Closes: {close_str}")
    body_lines.append("<i>Parimutuel · winners split the losers' pool</i>")

    # Leading blank line so the card doesn't stick to the photo above.
    return "\n" + info_card + "\n\n" + "\n".join(body_lines)


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
