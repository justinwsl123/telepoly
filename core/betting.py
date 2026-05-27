"""下注引擎：实时赔率 + 边界检查 + 资金结算到 Ledger。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from db.models import Bet, Event, User
from core.ledger import record_ledger
from telepoly_bot.config import settings


class BetError(Exception):
    """业务错误（直接显示给用户）。"""


# ----------------------------- 实时赔率 -----------------------------
def implied_odds(pool_yes_micro: int, pool_no_micro: int, fee_bps: int) -> tuple[float, float]:
    """
    返回 (yes_odds, no_odds)：押 1U 在结算后能拿回多少（含本金）。
    赔率 = 总池 / 自方池 × (1 - fee)。
    某一方为空时，返回该方 0.0（视为不可下注）。
    """
    total = pool_yes_micro + pool_no_micro
    fee_factor = 1 - fee_bps / 10_000
    yes_odds = (total / pool_yes_micro) * fee_factor if pool_yes_micro > 0 else 0.0
    no_odds = (total / pool_no_micro) * fee_factor if pool_no_micro > 0 else 0.0
    return yes_odds, no_odds


def predict_payout(
    pool_yes_micro: int,
    pool_no_micro: int,
    fee_bps: int,
    side: str,
    bet_micro: int,
) -> int:
    """
    "如果我现在下注 bet_micro，事件结算后我赢的话能拿回多少 micro"。
    UI 用：用户输入金额时即时显示预估回报。
    """
    if side == "yes":
        new_yes = pool_yes_micro + bet_micro
        new_total = new_yes + pool_no_micro
    else:
        new_no = pool_no_micro + bet_micro
        new_total = pool_yes_micro + new_no
    fee_factor = 10_000 - fee_bps
    payout_pool = new_total * fee_factor // 10_000
    self_pool = new_yes if side == "yes" else new_no
    if self_pool <= 0:
        return 0
    return payout_pool * bet_micro // self_pool


# ----------------------------- 下注 -----------------------------
def place_bet(
    session: Session,
    *,
    user: User,
    event: Event,
    side: str,
    amount_micro: int,
) -> Bet:
    side = side.lower()
    if side not in ("yes", "no"):
        raise BetError("side 必须是 yes 或 no")
    if event.state != "open":
        raise BetError("当前事件不接受下注（未开盘 / 已封盘）")
    if amount_micro < settings.min_bet_micro:
        raise BetError(f"最低下注 {settings.min_bet_usdt} USDT")
    if user.balance_micro < amount_micro:
        raise BetError("余额不足，请先 /deposit 充值")

    # 鲸鱼防护：单人单事件押注 ≤ 总池 30%（含本次）。
    # 池子较小时（< 100 USDT）放开早鸟，避免冷启动期没人能下注。
    user_existing = sum(
        b.amount_micro for b in event_bets_of_user(session, event.id, user.id)
    )
    new_user_total = user_existing + amount_micro
    new_pool_total = event.pool_yes_micro + event.pool_no_micro + amount_micro
    # 鲸鱼防护：单人总下注 ≤ max(池子 × ratio, 200 USDT)。
    # 200 USDT 的绝对下限保证早鸟用户随时能下中等金额，避免冷启动卡死。
    BASELINE_MICRO = 200 * 1_000_000
    cap_ratio = int(new_pool_total * settings.max_bet_ratio)
    cap = max(cap_ratio, BASELINE_MICRO)
    if new_user_total > cap:
        raise BetError(
            f"为防止单人控盘，本次最大可下注金额受限（单人 ≤ 池子 {int(settings.max_bet_ratio*100)}%）。"
            f"建议先等池子变大或减少金额。"
        )

    # 记录下注瞬间赔率（仅展示用，不参与实际结算）
    yes_odds, no_odds = implied_odds(event.pool_yes_micro, event.pool_no_micro, event.fee_bps)
    odds_at_bet = yes_odds if side == "yes" else no_odds

    bet = Bet(
        event_id=event.id,
        user_id=user.id,
        side=side,
        amount_micro=amount_micro,
        odds_at_bet=odds_at_bet or None,
        status="placed",
    )
    session.add(bet)
    session.flush()

    # 资金扣减 + 进池子
    record_ledger(
        session,
        user=user,
        delta_micro=-amount_micro,
        reason="bet_place",
        ref_id=bet.id,
        ref_type="bet",
        note=f"event={event.id} side={side}",
    )
    if side == "yes":
        event.pool_yes_micro += amount_micro
    else:
        event.pool_no_micro += amount_micro

    return bet


def event_bets_of_user(session: Session, event_id: int, user_id: int) -> list[Bet]:
    from sqlalchemy import select
    stmt = select(Bet).where(Bet.event_id == event_id, Bet.user_id == user_id)
    return list(session.scalars(stmt))
