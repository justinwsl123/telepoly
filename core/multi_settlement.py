"""多选项（multi-kind）结算引擎。

规则（彩池对赌 / Parimutuel）：
  total_pool   = Σ option.pool_micro
  fee          = total_pool × fee_bps / 10000
  payout_pool  = total_pool − fee
  winning_pool = pool_micro of winning option

  每个下注赢家 i：
    payout_i = payout_pool × user_bet_i / winning_pool
  最后一笔吃余数，保证 Σ payout_i == payout_pool。

边界：
  - winning_pool == 0（赢家无人下注）→ void + 全员退款
  - 仅一个选项有下注（无对手盘）→ void + 全员退款
  - event.outcome 存 opt_key（与 binary 的 "yes"/"no" 并列使用）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Bet, Event, EventOption, EventSnapshot, User
from core.ledger import record_ledger


class MultiSettlementError(Exception):
    pass


def settle_multi_event(
    session: Session,
    event: Event,
    winning_opt_key: str,
    evidence_url: str | None = None,
) -> dict:
    """
    结算 multi 事件。
    winning_opt_key: 赢得竞猜的选项键，如 "gpt"。
    返回汇总 dict。
    """
    if event.kind != "multi":
        raise MultiSettlementError("此事件不是 multi 类型")
    if event.state not in ("open", "locked"):
        raise MultiSettlementError(f"事件状态 {event.state} 不可结算")

    options: list[EventOption] = list(session.scalars(
        select(EventOption).where(EventOption.event_id == event.id)
    ))
    opt_map = {o.opt_key: o for o in options}
    if winning_opt_key not in opt_map:
        raise MultiSettlementError(f"无效的获胜选项: {winning_opt_key}")

    bets: list[Bet] = list(session.scalars(
        select(Bet).where(Bet.event_id == event.id)
    ))

    total_pool = sum(o.pool_micro for o in options)
    winning_pool = opt_map[winning_opt_key].pool_micro

    # 判断是否需要 void：赢方无人下注，或只有一方有下注
    sides_with_bets = sum(1 for o in options if o.pool_micro > 0)
    do_void = winning_pool == 0 or sides_with_bets <= 1

    summary = {"winners": 0, "losers": 0, "fee_micro": 0, "payout_total_micro": 0, "refunded": 0}

    if do_void:
        for bet in bets:
            user = session.get(User, bet.user_id)
            record_ledger(
                session, user=user,
                delta_micro=bet.amount_micro,
                reason="bet_refund",
                ref_id=bet.id, ref_type="bet",
                note=f"event={event.id} multi void",
            )
            bet.status = "refunded"
            bet.payout_micro = bet.amount_micro
            summary["refunded"] += 1
        event.outcome = "void"
        event.state = "void"
    else:
        fee = total_pool * event.fee_bps // 10_000
        payout_pool = total_pool - fee
        summary["fee_micro"] = fee

        winning_bets = [b for b in bets if b.side == winning_opt_key]
        winning_bets.sort(key=lambda b: b.id)  # 保证余数分配确定

        distributed = 0
        for idx, bet in enumerate(winning_bets):
            user = session.get(User, bet.user_id)
            if idx == len(winning_bets) - 1:
                payout = payout_pool - distributed  # 最后一笔吃余数
            else:
                payout = payout_pool * bet.amount_micro // winning_pool
            distributed += payout

            record_ledger(
                session, user=user,
                delta_micro=payout,
                reason="bet_payout",
                ref_id=bet.id, ref_type="bet",
                note=f"event={event.id} won opt={winning_opt_key}",
            )
            bet.status = "won"
            bet.payout_micro = payout
            summary["winners"] += 1

        for bet in bets:
            if bet.side != winning_opt_key:
                bet.status = "lost"
                bet.payout_micro = 0
                summary["losers"] += 1

        if fee > 0:
            record_ledger(
                session, user=None,
                delta_micro=fee,
                reason="fee",
                ref_id=event.id, ref_type="event",
                note=f"event={event.id} multi fee {event.fee_bps}bps",
            )

        # 标记获胜选项
        opt_map[winning_opt_key].is_winner = True

        summary["payout_total_micro"] = distributed
        event.outcome = winning_opt_key
        event.state = "settled"

    event.evidence_url = evidence_url
    event.settled_at = datetime.utcnow()

    # 联动推广员佣金
    if event.state == "settled" and summary.get("fee_micro", 0) > 0:
        try:
            _record_affiliate_commissions(session, event, bets, summary["fee_micro"])
        except Exception as e:
            from loguru import logger
            logger.warning(f"affiliate commission hook failed for multi event {event.id}: {e}")

    # 结算快照（pool_yes/no 对 multi 无意义，统一写 total / 0）
    snap = session.get(EventSnapshot, event.id) or EventSnapshot(event_id=event.id)
    snap.total_bets = len(bets)
    snap.total_users = len({b.user_id for b in bets})
    snap.pool_yes_micro = total_pool  # 存总池，便于汇总报表
    snap.pool_no_micro = 0
    snap.fee_micro = summary["fee_micro"]
    snap.snapshot_at = datetime.utcnow()
    session.merge(snap)

    return summary


def _record_affiliate_commissions(
    session: Session, event: Event, bets: list[Bet], fee_micro: int
) -> None:
    """复用 settlement.py 同名逻辑，按下注占比分佣金给推广员。"""
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
            source_payment_ref=f"telepoly:multi_event:{event.id}:user:{user_id}",
        )
