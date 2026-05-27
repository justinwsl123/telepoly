"""One-shot seeder: create + publish a demo World Cup market if none exists.

Run at container start when SEED_DEMO_EVENT=1 in the environment.
Idempotent: skipped if any event with the same title already exists.

Usage (locally):
    python -m scripts.seed_demo

Usage (Railway): set SEED_DEMO_EVENT=1 once. After the next deploy the
seeder runs, then you can unset / set to 0 to avoid running again.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select

from db.models import Event
from db.session import get_session
from core.events import create_event, open_event
from telepoly_bot.config import settings


DEMO_TITLE = "Will Cristiano Ronaldo score for Portugal today?"
DEMO_DESC = (
    "World Cup · Portugal match today. Bet YES if you think CR7 scores at "
    "least one goal in regulation time (90 + injury). Penalty shootout goals "
    "do not count. Settled the moment the final whistle blows, with the "
    "official FIFA box score linked as evidence."
)
DEMO_YES_LABEL = "Scores"
DEMO_NO_LABEL = "No goal"


def already_seeded(session) -> bool:
    """True if a market with the demo title is already in the DB (any state)."""
    existing = session.scalars(
        select(Event).where(Event.title == DEMO_TITLE)
    ).first()
    return existing is not None


def seed(hours_until_close: int = 6) -> int:
    """Create the demo event, open it immediately, return its id."""
    close_at = datetime.utcnow() + timedelta(hours=hours_until_close)
    with get_session() as s:
        if already_seeded(s):
            existing = s.scalars(select(Event).where(Event.title == DEMO_TITLE)).first()
            logger.info(f"seed_demo: already seeded → event#{existing.id} (state={existing.state})")
            return existing.id

        ev = create_event(
            s,
            title=DEMO_TITLE,
            description=DEMO_DESC,
            close_at=close_at,
            yes_label=DEMO_YES_LABEL,
            no_label=DEMO_NO_LABEL,
            fee_bps=settings.event_fee_bps,
            bot_id=settings.bot_id,
        )
        open_event(s, ev)
        logger.success(f"seed_demo: created + opened event#{ev.id} '{ev.title}'")
        return ev.id


def main() -> None:
    try:
        seed()
    except Exception as e:
        logger.error(f"seed_demo failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
