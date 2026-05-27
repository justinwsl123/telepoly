"""事件 CRUD + 状态机。

状态：draft → open → locked → settled / void
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Event


VALID_TRANSITIONS = {
    "draft":   {"open", "void"},
    "open":    {"locked", "void"},
    "locked":  {"settled", "void"},
    "settled": set(),
    "void":    set(),
}


class EventStateError(Exception):
    pass


def create_event(
    session: Session,
    *,
    title: str,
    description: str,
    close_at: datetime,
    yes_label: str = "YES",
    no_label: str = "NO",
    cover_url: str | None = None,
    fee_bps: int = 500,
    bot_id: str = "main",
    created_by: int | None = None,
) -> Event:
    if close_at <= datetime.utcnow():
        raise ValueError("close_at must be in the future")

    ev = Event(
        bot_id=bot_id,
        title=title.strip(),
        description=description.strip() if description else None,
        yes_label=yes_label.strip() or "YES",
        no_label=no_label.strip() or "NO",
        cover_url=cover_url,
        close_at=close_at,
        fee_bps=fee_bps,
        state="draft",
        created_by=created_by,
    )
    session.add(ev)
    session.flush()
    return ev


def transition(session: Session, event: Event, target: str) -> None:
    if target not in VALID_TRANSITIONS.get(event.state, set()):
        raise EventStateError(f"cannot transition {event.state} → {target}")
    event.state = target


def open_event(session: Session, event: Event) -> None:
    """draft → open"""
    transition(session, event, "open")
    if event.open_at is None:
        event.open_at = datetime.utcnow()


def lock_event(session: Session, event: Event) -> None:
    """open → locked（封盘，截止时间到自动调用）"""
    transition(session, event, "locked")


def void_event(session: Session, event: Event) -> None:
    """任何状态 → void（仅未结算的）"""
    if event.state == "settled":
        raise EventStateError("cannot void already-settled event")
    event.state = "void"
    event.outcome = "void"
    event.settled_at = datetime.utcnow()


def list_events_by_state(session: Session, states: Iterable[str], bot_id: str = "main") -> list[Event]:
    stmt = select(Event).where(Event.state.in_(list(states)), Event.bot_id == bot_id).order_by(Event.close_at)
    return list(session.scalars(stmt))


def get_active_event(session: Session, bot_id: str = "main") -> Event | None:
    """返回最新一个 open 状态的事件（每天一道题，最多一个 active）。"""
    stmt = (
        select(Event)
        .where(Event.state == "open", Event.bot_id == bot_id)
        .order_by(Event.close_at)
        .limit(1)
    )
    return session.scalars(stmt).first()


def events_to_lock(session: Session, now: datetime | None = None) -> list[Event]:
    """所有 close_at 已过、还在 open 的事件。"""
    now = now or datetime.utcnow()
    stmt = select(Event).where(Event.state == "open", Event.close_at <= now)
    return list(session.scalars(stmt))
