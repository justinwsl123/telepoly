"""提现：用户请求 → 队列 → 自动批准 / 人工审核 → 链上发交易。

流程：
  user /withdraw <amount> <address>
       → request_withdrawal(): 校验余额 + 扣款（先冻结）→ 写 withdrawals(pending)
       → 若 amount ≤ AUTO_APPROVE_LIMIT_USDT: 自动 approve
       → 若 > 限额: 等运营在 admin_web 点 approve
  approved → process_pending_withdrawals(): 拉所有 approved → 走链上发款 → tx_hash 回填
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Iterable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import User, Withdrawal
from db.session import get_session
from core.ledger import record_ledger
from core.money import micro_to_usdt
from telepoly_bot.config import settings


class WithdrawError(Exception):
    pass


# ----------------------------- 用户请求 -----------------------------
def request_withdrawal(
    session: Session,
    *,
    user: User,
    to_address: str,
    amount_micro: int,
) -> Withdrawal:
    """
    用户发起提现：立即从余额扣款（冻结到 withdrawals 行），后续审批/发款只动 withdrawals。
    """
    if amount_micro < 5 * 1_000_000:
        raise WithdrawError("最低提现 5 USDT")
    fee_micro = 1_000_000  # 1 USDT 网络费
    total = amount_micro + fee_micro
    if user.balance_micro < total:
        raise WithdrawError(f"余额不足（含手续费 1 USDT），需要 {micro_to_usdt(total)} USDT")

    if not _looks_like_tron_address(to_address):
        raise WithdrawError("收款地址格式不正确（应为 T 开头的 TRON 地址）")

    auto_limit_micro = int(settings.wallet_auto_approve_limit_usdt * 1_000_000)
    initial_status = "approved" if amount_micro <= auto_limit_micro else "pending"

    w = Withdrawal(
        user_id=user.id,
        to_address=to_address,
        amount_micro=amount_micro,
        fee_micro=fee_micro,
        status=initial_status,
    )
    session.add(w)
    session.flush()

    # 立即从余额扣减（含手续费），落 ledger
    record_ledger(
        session, user=user, delta_micro=-total, reason="withdraw",
        ref_id=w.id, ref_type="withdrawal",
        note=f"to={to_address} amount={amount_micro} fee={fee_micro} status={initial_status}",
    )
    return w


def _looks_like_tron_address(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("T") and 30 <= len(addr) <= 40


# ----------------------------- 审批 -----------------------------
def approve(session: Session, withdrawal: Withdrawal, approver_uid: int) -> None:
    if withdrawal.status != "pending":
        raise WithdrawError(f"状态 {withdrawal.status} 不可审批")
    withdrawal.status = "approved"
    withdrawal.approved_by = approver_uid


def reject(session: Session, withdrawal: Withdrawal, approver_uid: int, reason: str) -> None:
    if withdrawal.status not in ("pending", "approved"):
        raise WithdrawError(f"状态 {withdrawal.status} 不可拒绝")
    user = session.get(User, withdrawal.user_id)
    refund = withdrawal.amount_micro + withdrawal.fee_micro
    record_ledger(
        session, user=user, delta_micro=refund, reason="bet_refund",
        ref_id=withdrawal.id, ref_type="withdrawal",
        note=f"withdraw rejected: {reason}",
    )
    withdrawal.status = "rejected"
    withdrawal.approved_by = approver_uid
    withdrawal.reject_reason = reason


# ----------------------------- 出款执行 -----------------------------
def list_approved(session: Session) -> list[Withdrawal]:
    return list(session.scalars(
        select(Withdrawal).where(Withdrawal.status == "approved").order_by(Withdrawal.id)
    ))


def execute_one(withdrawal_id: int) -> str | None:
    """链上发一笔。返回 tx_hash；失败时回滚到 status=failed 但不退款（人工介入）。"""
    from wallet.hd import hot_wallet_private_key
    from wallet.trc20 import send_trc20_usdt

    with get_session() as s:
        w = s.get(Withdrawal, withdrawal_id)
        if not w or w.status != "approved":
            return None
        to_address = w.to_address
        amount_micro = w.amount_micro

    try:
        priv = hot_wallet_private_key()
    except Exception as e:
        logger.error(f"hot wallet key load failed: {e}")
        with get_session() as s:
            w = s.get(Withdrawal, withdrawal_id)
            w.status = "failed"
            w.reject_reason = f"hot wallet unavailable: {e}"
        return None

    try:
        tx_hash = send_trc20_usdt(to_address, amount_micro, priv)
    except Exception as e:
        logger.exception(f"on-chain send failed wid={withdrawal_id}: {e}")
        with get_session() as s:
            w = s.get(Withdrawal, withdrawal_id)
            w.status = "failed"
            w.reject_reason = f"chain error: {e}"
        return None

    with get_session() as s:
        w = s.get(Withdrawal, withdrawal_id)
        w.status = "sent"
        w.tx_hash = tx_hash
        w.sent_at = datetime.utcnow()

    logger.success(f"withdrawal {withdrawal_id} sent: {tx_hash}")
    return tx_hash


async def runner_loop() -> None:
    """独立进程版（如果未来需要拆分），目前由 admin_web 触发或人工调用 execute_one。"""
    logger.info("== Withdrawal runner starting ==")
    while True:
        try:
            with get_session() as s:
                ids = [w.id for w in list_approved(s)]
            for wid in ids:
                execute_one(wid)
        except Exception as e:
            logger.exception(f"runner error: {e}")
        await asyncio.sleep(60)
