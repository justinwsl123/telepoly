"""多选项（multi-kind）下注引擎。

彩池规则（Parimutuel）：
  odds_k = total_pool / pool_k × (1 - fee)
  Bet.side 存 opt_key（例如 "gpt"）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Bet, Event, EventOption, User
from core.ledger import record_ledger
from core.betting import event_bets_of_user
from telepoly_bot.config import settings


class MultiBetError(Exception):
    """业务错误（直接显示给用户）。"""


# ----------------------------- 实时赔率 -----------------------------

def implied_odds_multi(
    option_pools: dict[str, int],
    fee_bps: int,
) -> dict[str, float]:
    """
    返回每个选项的当前赔率（押 1U 赢时能拿回多少，含本金）。
    选项池为 0 时，赔率为 0.0（不可下注）。
    """
    total = sum(option_pools.values())
    fee_factor = 1 - fee_bps / 10_000
    result: dict[str, float] = {}
    for key, pool in option_pools.items():
        if pool > 0 and total > 0:
            result[key] = (total / pool) * fee_factor
        else:
            result[key] = 0.0
    return result


def predict_payout_multi(
    option_pools: dict[str, int],
    fee_bps: int,
    opt_key: str,
    bet_micro: int,
) -> int:
    """
    估算：如果现在下 bet_micro 到 opt_key，该选项赢时能拿回多少 micro。
    """
    new_pools = dict(option_pools)
    new_pools[opt_key] = new_pools.get(opt_key, 0) + bet_micro
    total = sum(new_pools.values())
    fee_factor = 10_000 - fee_bps
    payout_pool = total * fee_factor // 10_000
    own_pool = new_pools[opt_key]
    if own_pool <= 0:
        return 0
    return payout_pool * bet_micro // own_pool


# ----------------------------- 下注 -----------------------------

def place_bet_multi(
    session: Session,
    *,
    user: User,
    event: Event,
    opt_key: str,
    amount_micro: int,
) -> Bet:
    """
    向 multi 事件的某选项下注。

    验证顺序：
      1. 事件类型 & 状态
      2. opt_key 存在
      3. 最低下注额
      4. 余额
      5. 鲸鱼防护（基于总池）
    """
    if event.kind != "multi":
        raise MultiBetError("此事件不是多选项竞猜")
    if event.state != "open":
        raise MultiBetError("当前事件不接受下注（未开盘 / 已封盘）")

    # 加载选项
    options: list[EventOption] = list(session.scalars(
        select(EventOption).where(EventOption.event_id == event.id)
    ))
    opt_map = {o.opt_key: o for o in options}
    if opt_key not in opt_map:
        raise MultiBetError(f"无效的选项: {opt_key}")

    if amount_micro < settings.min_bet_micro:
        raise MultiBetError(f"最低下注 {settings.min_bet_usdt} USDT")
    if user.balance_micro < amount_micro:
        raise MultiBetError("余额不足，请先 /deposit 充值")

    # 鲸鱼防护：基于总池（所有选项之和）
    total_pool = sum(o.pool_micro for o in options)
    user_existing = sum(
        b.amount_micro for b in event_bets_of_user(session, event.id, user.id)
    )
    new_user_total = user_existing + amount_micro
    new_pool_total = total_pool + amount_micro
    BASELINE_MICRO = 200 * 1_000_000
    cap_ratio = int(new_pool_total * settings.max_bet_ratio)
    cap = max(cap_ratio, BASELINE_MICRO)
    if new_user_total > cap:
        raise MultiBetError(
            f"为防止单人控盘，本次最大可下注金额受限"
            f"（单人 ≤ 总池 {int(settings.max_bet_ratio * 100)}%）。"
        )

    # 记录此刻赔率（展示用）
    option_pools = {o.opt_key: o.pool_micro for o in options}
    odds_map = implied_odds_multi(option_pools, event.fee_bps)
    odds_at_bet = odds_map.get(opt_key) or None

    bet = Bet(
        event_id=event.id,
        user_id=user.id,
        side=opt_key,              # 复用 Bet.side 存储选项键
        amount_micro=amount_micro,
        odds_at_bet=odds_at_bet,
        status="placed",
    )
    session.add(bet)
    session.flush()

    # 资金扣减 + 进选项池子
    record_ledger(
        session,
        user=user,
        delta_micro=-amount_micro,
        reason="bet_place",
        ref_id=bet.id,
        ref_type="bet",
        note=f"event={event.id} opt={opt_key}",
    )
    opt_map[opt_key].pool_micro += amount_micro

    return bet
