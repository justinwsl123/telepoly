"""池子时序快照 · 走势图数据源。

设计：
  - 每分钟跑一次 capture_open_events()，给所有 open 事件写一行 PoolTimepoint。
  - 当事件刚创建时立即写一条"零点"，避免曲线起点为空。
  - 查询走势用 fetch_timeline()，返回降采样后的点（最多 ~120 点保证渲染流畅）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import asc, func, select
from sqlalchemy.orm import Session

from db.models import Bet, Event, PoolTimepoint


def capture_event(session: Session, event: Event) -> PoolTimepoint:
    n_bets = session.scalar(
        select(func.count()).select_from(Bet).where(Bet.event_id == event.id)
    ) or 0
    pt = PoolTimepoint(
        event_id=event.id,
        pool_yes_micro=event.pool_yes_micro,
        pool_no_micro=event.pool_no_micro,
        n_bets=n_bets,
    )
    session.add(pt)
    return pt


def capture_open_events(session: Session) -> int:
    """给所有 open / locked（未结算）事件各写一行。"""
    evs = list(session.scalars(
        select(Event).where(Event.state.in_(("open", "locked")))
    ))
    for ev in evs:
        capture_event(session, ev)
    return len(evs)


def fetch_timeline(session: Session, event_id: int, max_points: int = 120) -> list[dict]:
    """
    返回时间序列：[{t, yes, no, total, yes_share}]
    超过 max_points 时按等距降采样保留代表性点。
    """
    rows = list(session.scalars(
        select(PoolTimepoint).where(PoolTimepoint.event_id == event_id)
        .order_by(asc(PoolTimepoint.captured_at))
    ))
    if not rows:
        return []

    if len(rows) > max_points:
        step = len(rows) / max_points
        sampled = [rows[int(i * step)] for i in range(max_points)]
        if rows[-1] not in sampled:
            sampled.append(rows[-1])
        rows = sampled

    out = []
    for r in rows:
        total = r.pool_yes_micro + r.pool_no_micro
        yes_share = (r.pool_yes_micro / total) if total > 0 else 0.5
        out.append({
            "t": r.captured_at.isoformat(),
            "yes": r.pool_yes_micro,
            "no": r.pool_no_micro,
            "total": total,
            "yes_share": yes_share,
            "n_bets": r.n_bets,
        })
    return out


def cleanup_old_timepoints(session: Session, older_than_days: int = 30) -> int:
    """清理 N 天前的快照（节省空间）。"""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    result = session.execute(
        PoolTimepoint.__table__.delete().where(PoolTimepoint.captured_at < cutoff)
    )
    return result.rowcount or 0
