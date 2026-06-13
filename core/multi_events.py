"""多选项（multi-kind）事件 CRUD。

完全复用 Event 状态机（draft→open→locked→settled/void）和 core/events.py
中的 transition / open_event / lock_event / void_event。
本模块只负责：创建 multi 事件 + 写入 EventOption 行。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select

from db.models import Event, EventOption
from core.events import create_event, open_event


def create_multi_event(
    session: Session,
    *,
    title: str,
    description: str,
    close_at: datetime,
    options: list[dict],          # [{opt_key, label, color?, sort_order?}]
    fee_bps: int = 500,
    bot_id: str = "main",
    created_by: int | None = None,
    cover_url: str | None = None,
) -> Event:
    """
    创建 kind="multi" 的事件，并写入 EventOption 行。

    options 格式：
      [{"opt_key": "gpt", "label": "GPT-5.5", "color": "#10a37f", "sort_order": 0}, ...]
    opt_key 必须 ≤ 8 字符（复用 Bet.side String(8)）。
    """
    if not options:
        raise ValueError("multi event must have at least 2 options")
    for o in options:
        key = o.get("opt_key", "")
        if not key or len(key) > 8:
            raise ValueError(f"opt_key must be 1–8 chars, got: '{key}'")

    ev = create_event(
        session,
        title=title,
        description=description,
        close_at=close_at,
        fee_bps=fee_bps,
        bot_id=bot_id,
        created_by=created_by,
        cover_url=cover_url,
    )
    ev.kind = "multi"
    session.flush()

    for idx, opt in enumerate(options):
        row = EventOption(
            event_id=ev.id,
            opt_key=opt["opt_key"],
            label=opt.get("label", opt["opt_key"]),
            color=opt.get("color"),
            sort_order=opt.get("sort_order", idx),
            pool_micro=0,
            is_winner=False,
        )
        session.add(row)
    session.flush()
    return ev


def get_event_options(session: Session, event_id: int) -> list[EventOption]:
    """按 sort_order 返回某事件的所有选项。"""
    return list(session.scalars(
        select(EventOption)
        .where(EventOption.event_id == event_id)
        .order_by(EventOption.sort_order)
    ))
