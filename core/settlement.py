"""结算引擎 · 全 Bot 最关键的逻辑，必须有单测覆盖。

规则（彩池对赌 / Parimutuel）：
  total_pool = pool_yes + pool_no
  fee        = total_pool × fee_bps / 10000
  payout_pool = total_pool − fee

  对每个赢方用户 i：
    payout_i = payout_pool × user_bet_i / winning_pool

边界处理：
  - 若 winning_pool == 0（赢方无人下注）→ 全员退款，事件视为 void。
  - 若 losing_pool  == 0（输方无人下注）→ 全员退款（彩池没有对手盘，视为 void）。
  - outcome=void → 全员退款 100%，平台不收手续费。
余数处理：
  整数除法会有余数（比如 1000 micro 分成 3 份 = 333+333+333+1）。
  最后一笔 payout 把余数加上，保证 sum(payouts) == payout_pool。
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Bet, Event, EventSnapshot, User
from core.ledger import record_ledger


class SettlementError(Exception):
    pass


def settle_event(
    session: Session,
    event: Event,
    outcome: str,
    evidence_url: str | None = None,
) -> dict:
    """
    执行结算。
    outcome: "yes" | "no" | "void"
    返回汇总 dict：{"winners": n, "losers": n, "fee_micro": x, "payout_total_micro": y}
    """
    outcome = outcome.lower()
    if outcome not in ("yes", "no", "void"):
        raise SettlementError("outcome 必须是 yes / no / void")
    if event.state not in ("open", "locked"):
        raise SettlementError(f"事件状态 {event.state} 不可结算")

    bets: list[Bet] = list(session.scalars(select(Bet).where(Bet.event_id == event.id)))

    pool_yes = event.pool_yes_micro
    pool_no = event.pool_no_micro
    total_pool = pool_yes + pool_no

    # 自动 void：单边无人下注 → 全员退款
    if outcome != "void" and (pool_yes == 0 or pool_no == 0):
        outcome = "void"

    summary = {"winners": 0, "losers": 0, "fee_micro": 0, "payout_total_micro": 0, "refunded": 0}

    if outcome == "void":
        for bet in bets:
            user = session.get(User, bet.user_id)
            record_ledger(
                session,
                user=user,
                delta_micro=bet.amount_micro,
                reason="bet_refund",
                ref_id=bet.id,
                ref_type="bet",
                note=f"event={event.id} void",
            )
            bet.status = "refunded"
            bet.payout_micro = bet.amount_micro
            summary["refunded"] += 1
        event.outcome = "void"
        event.state = "void"
    else:
        winning_side = outcome  # yes / no
        winning_pool = pool_yes if winning_side == "yes" else pool_no
        fee = total_pool * event.fee_bps // 10_000
        payout_pool = total_pool - fee
        summary["fee_micro"] = fee

        winning_bets = [b for b in bets if b.side == winning_side]
        # 排序保证余数分配确定（id 升序），最后一笔吃余数
        winning_bets.sort(key=lambda b: b.id)

        distributed = 0
        for idx, bet in enumerate(winning_bets):
            user = session.get(User, bet.user_id)
            if idx == len(winning_bets) - 1:
                payout = payout_pool - distributed  # 最后一笔吃余数，保证总和守恒
            else:
                payout = payout_pool * bet.amount_micro // winning_pool
            distributed += payout

            record_ledger(
                session,
                user=user,
                delta_micro=payout,
                reason="bet_payout",
                ref_id=bet.id,
                ref_type="bet",
                note=f"event={event.id} won side={winning_side}",
            )
            bet.status = "won"
            bet.payout_micro = payout
            summary["winners"] += 1

        for bet in bets:
            if bet.side != winning_side:
                bet.status = "lost"
                bet.payout_micro = 0
                summary["losers"] += 1

        # 平台手续费入账（user=None 表示平台）
        if fee > 0:
            record_ledger(
                session,
                user=None,
                delta_micro=fee,
                reason="fee",
                ref_id=event.id,
                ref_type="event",
                note=f"event={event.id} fee {event.fee_bps}bps",
            )

        summary["payout_total_micro"] = distributed
        event.outcome = winning_side
        event.state = "settled"

    event.evidence_url = evidence_url
    event.settled_at = datetime.utcnow()

    # 联动 TeleGrowth：手续费按下注用户的 referrer 比例分到 L1/L2 推广员。
    # 失败不影响主结算（已 commit 不回滚）。
    if outcome != "void" and summary.get("fee_micro", 0) > 0:
        try:
            _record_affiliate_commissions(session, event, bets, summary["fee_micro"])
        except Exception as e:
            from loguru import logger
            logger.warning(f"affiliate commission hook failed for event {event.id}: {e}")

    # 快照
    snap = session.get(EventSnapshot, event.id) or EventSnapshot(event_id=event.id)
    snap.total_bets = len(bets)
    snap.total_users = len({b.user_id for b in bets})
    snap.pool_yes_micro = pool_yes
    snap.pool_no_micro = pool_no
    snap.fee_micro = summary["fee_micro"]
    snap.snapshot_at = datetime.utcnow()
    session.merge(snap)

    return summary


def _record_affiliate_commissions(session: Session, event: Event, bets: list[Bet], fee_micro: int) -> None:
    """
    手续费按"每个下注用户在总池中的占比"分配 referrer 佣金。
    比如 user A 押了总池 10%，他贡献了 fee 的 10%，他的 referrer 拿其中 40% (L1)。
    """
    from integrations.telegrowth import record_commission_for_fee
    from core.money import micro_to_usdt

    total_pool = sum(b.amount_micro for b in bets)
    if total_pool <= 0:
        return
    by_user: dict[int, int] = {}
    for b in bets:
        by_user[b.user_id] = by_user.get(b.user_id, 0) + b.amount_micro

    for user_id, user_total in by_user.items():
        user = session.get(User, user_id)
        if not user or not user.referrer_code:
            continue
        user_fee_micro = fee_micro * user_total // total_pool
        if user_fee_micro <= 0:
            continue
        record_commission_for_fee(
            payer_tg_user_id=user.tg_user_id,
            payer_referrer_code=user.referrer_code,
            fee_usdt=float(micro_to_usdt(user_fee_micro)),
            source_payment_ref=f"telepoly:event:{event.id}:user:{user_id}",
        )
