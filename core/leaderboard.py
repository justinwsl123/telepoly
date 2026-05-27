"""大佬榜 / Hall of Fame · 昨日赢家排行。

逻辑：
  按 user 维度对昨天 settled 的事件聚合 sum(payout - amount)，取 Top N。
  返回每个赢家：display_name（首字母 mask）、净赢金额、关联 tx hash（如有）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Bet, Event, User


def yesterday_window() -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    return today_start - timedelta(days=1), today_start


def top_winners(session: Session, *, top_n: int = 3,
                start: datetime | None = None,
                end: datetime | None = None) -> list[dict]:
    if start is None or end is None:
        start, end = yesterday_window()

    # 只算昨日结算的、status=won 的下注
    stmt = (
        select(
            Bet.user_id,
            func.sum(Bet.payout_micro - Bet.amount_micro).label("pnl_micro"),
            func.count(Bet.id).label("n_wins"),
        )
        .join(Event, Event.id == Bet.event_id)
        .where(
            Bet.status == "won",
            Event.settled_at >= start,
            Event.settled_at < end,
        )
        .group_by(Bet.user_id)
        .order_by(func.sum(Bet.payout_micro - Bet.amount_micro).desc())
        .limit(top_n)
    )
    rows = list(session.execute(stmt).all())

    out = []
    for user_id, pnl_micro, n_wins in rows:
        user = session.get(User, user_id)
        if not user or pnl_micro <= 0:
            continue
        out.append({
            "user_id": user_id,
            "display": _mask_name(user),
            "pnl_micro": int(pnl_micro),
            "n_wins": int(n_wins),
        })
    return out


def _mask_name(user: User) -> str:
    """脱敏：@joh*** 或 J·*** ，保护用户隐私。"""
    if user.username:
        u = user.username
        if len(u) <= 3:
            return f"@{u}"
        return f"@{u[:2]}{'•' * 3}"
    if user.first_name:
        return f"{user.first_name[0]}•"
    return f"User #{user.id}"


def render_hall_of_fame(winners: Sequence[dict], date_label: str) -> str:
    """渲染发到 Channel 的 Markdown 文本。"""
    if not winners:
        return None  # 无赢家就不发

    from core.money import micro_to_usdt
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"🏆 *Hall of Fame · {date_label}*",
        "_The biggest winners from yesterday's market:_",
        "",
    ]
    for i, w in enumerate(winners):
        medal = medals[i] if i < len(medals) else "·"
        usdt = micro_to_usdt(w["pnl_micro"])
        wins = w["n_wins"]
        suffix = f" · {wins} winning bets" if wins > 1 else ""
        lines.append(f"{medal}  *{w['display']}* — `+{usdt:.2f} USDT`{suffix}")
    lines += [
        "",
        "_Today's market is open — could you be on tomorrow's leaderboard?_ 👀",
    ]
    return "\n".join(lines)
