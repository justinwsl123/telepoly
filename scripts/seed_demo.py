"""Seed (or refresh) the headline World Cup demo market.

What this does:
  1. Find an existing demo (any event with yes_label="Scores"), or create one.
  2. Set / overwrite its title, description, close_at, fee and seed pool to
     match the spec below.
  3. Open it (state="open") so it shows up on /start immediately.
  4. Backfill ~5 synthetic snapshots so the trend chart looks organic from
     the moment a user first sees the card.

Idempotent: rerunning the script just re-syncs the values; it never creates
duplicate demos.

Tune the constants at the top of this file (close_at, seed pool, yes share)
to taste — the next deploy (with SEED_DEMO_EVENT=1) will pick them up.

Run locally:   python -m scripts.seed_demo
Run on Railway: set SEED_DEMO_EVENT=1, then deploy (start.sh runs it).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import asc, select

from db.models import Event, PoolTimepoint
from db.session import get_session
from core.events import create_event
from telepoly_bot.config import settings


# -------- Spec (the only knobs you'll usually want to tweak) ------------

DEMO_TITLE = (
    "Will Cristiano Ronaldo score in Portugal's first 2026 World Cup match?"
)
DEMO_DESC = (
    "FIFA World Cup 2026 · Portugal's group stage opener. Bet YES if you "
    "believe CR7 scores at least one goal in regulation time (90 + injury). "
    "Penalty-shootout goals do NOT count. Market closes at kick-off and "
    "settles right after the final whistle, using the FIFA official box "
    "score as evidence."
)
DEMO_YES_LABEL = "Scores"
DEMO_NO_LABEL = "No goal"
# Cover image (read from the repo at runtime; not refreshed afterwards).
# Relative to the repo root, so it works both locally and on Railway.
DEMO_COVER_URL = "assets/cr7.jpg"

# Portugal's first match kick-off (UTC). Adjust once FIFA publishes the
# definitive fixture; the script will simply update the existing demo on
# the next deploy.
DEMO_CLOSE_AT_UTC = datetime(2026, 6, 15, 18, 0, 0)

# Headline numbers shown on the card before any real user bets in.
DEMO_TOTAL_POOL_USDT = 1128.0
DEMO_YES_SHARE = 0.62


# ------------------------------------------------------------------------


def _split_pool(total_usdt: float, yes_share: float) -> tuple[int, int]:
    """Convert (total, yes_share) into integer (yes_micro, no_micro)."""
    total_micro = int(round(total_usdt * 1_000_000))
    yes_micro = int(round(total_micro * yes_share))
    no_micro = total_micro - yes_micro
    return yes_micro, no_micro


def _find_demo(session) -> Event | None:
    """Locate our demo regardless of its current title/state."""
    return session.scalars(
        select(Event).where(Event.yes_label == DEMO_YES_LABEL).order_by(asc(Event.id))
    ).first()


def _backfill_snapshots(session, event_id: int, final_yes: int, final_no: int) -> None:
    """Drop any existing snapshots for the event and write a fresh 5-point
    history that grows smoothly from a small early pool to the current
    headline numbers. This makes the trend chart look alive on launch."""
    session.execute(
        PoolTimepoint.__table__.delete().where(PoolTimepoint.event_id == event_id)
    )

    final_total = final_yes + final_no
    final_share = (final_yes / final_total) if final_total else 0.5

    # Hours-ago offset, fraction of final pool, yes-share at that moment.
    # Designed so the chart drifts upward toward `final_share` over ~24h.
    timeline = [
        (24, 0.15, max(0.50, final_share - 0.08)),
        (12, 0.35, max(0.52, final_share - 0.05)),
        ( 6, 0.60, max(0.55, final_share - 0.03)),
        ( 3, 0.85, max(0.58, final_share - 0.01)),
        ( 0, 1.00, final_share),
    ]
    now = datetime.utcnow()
    for hours_ago, frac, share in timeline:
        total = int(final_total * frac)
        yes = int(total * share)
        no = total - yes
        session.add(PoolTimepoint(
            event_id=event_id,
            pool_yes_micro=yes,
            pool_no_micro=no,
            n_bets=int(8 * frac),  # purely cosmetic
            captured_at=now - timedelta(hours=hours_ago),
        ))


def seed() -> int:
    """Create or refresh the demo and return its id."""
    yes_micro, no_micro = _split_pool(DEMO_TOTAL_POOL_USDT, DEMO_YES_SHARE)

    with get_session() as s:
        ev = _find_demo(s)

        if ev is None:
            ev = create_event(
                s,
                title=DEMO_TITLE,
                description=DEMO_DESC,
                close_at=DEMO_CLOSE_AT_UTC,
                yes_label=DEMO_YES_LABEL,
                no_label=DEMO_NO_LABEL,
                fee_bps=settings.event_fee_bps,
                bot_id=settings.bot_id,
            )
            logger.success(f"seed_demo: created event#{ev.id}")
        else:
            logger.info(f"seed_demo: refreshing existing event#{ev.id}")

        ev.title = DEMO_TITLE
        ev.description = DEMO_DESC
        ev.close_at = DEMO_CLOSE_AT_UTC
        ev.yes_label = DEMO_YES_LABEL
        ev.no_label = DEMO_NO_LABEL
        ev.cover_url = DEMO_COVER_URL
        ev.pool_yes_micro = yes_micro
        ev.pool_no_micro = no_micro
        ev.state = "open"
        if ev.open_at is None:
            ev.open_at = datetime.utcnow() - timedelta(hours=24)
        s.flush()

        _backfill_snapshots(s, ev.id, yes_micro, no_micro)

        total = (yes_micro + no_micro) / 1_000_000
        logger.success(
            f"seed_demo: event#{ev.id} ready — pool={total:.2f} USDT, "
            f"yes_share={yes_micro / (yes_micro + no_micro):.0%}, "
            f"closes {DEMO_CLOSE_AT_UTC.isoformat()}Z"
        )
        return ev.id


def main() -> None:
    try:
        seed()
    except Exception as e:
        logger.error(f"seed_demo failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
