"""账本：所有资金变动唯一入口。

任何对 user.balance_micro 的修改都必须经 record_ledger 写一笔流水，
否则违反"双式记账"，对账会失败。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from db.models import Ledger, User


def record_ledger(
    session: Session,
    *,
    user: User | None,
    delta_micro: int,
    reason: str,
    ref_id: int | None = None,
    ref_type: str | None = None,
    tx_hash: str | None = None,
    note: str | None = None,
) -> Ledger:
    """
    记一笔账。
    - user=None 表示平台自身（手续费收入 / 退款发起方）。
    - delta_micro 正进负出。
    - 不在此处 commit，由调用方控制事务。
    """
    if user is not None:
        new_balance = user.balance_micro + delta_micro
        if new_balance < 0:
            raise ValueError(
                f"insufficient balance: user={user.id} balance={user.balance_micro} delta={delta_micro}"
            )
        user.balance_micro = new_balance

    entry = Ledger(
        user_id=user.id if user else None,
        delta_micro=delta_micro,
        reason=reason,
        ref_id=ref_id,
        ref_type=ref_type,
        tx_hash=tx_hash,
        balance_after_micro=user.balance_micro if user else None,
        note=note,
    )
    session.add(entry)
    return entry
