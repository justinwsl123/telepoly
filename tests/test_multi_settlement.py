"""多选项竞猜单测：赔率 + 下注 + 结算守恒律 + 边界处理。

对称覆盖 tests/test_settlement.py 的 binary 路径。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Event, EventOption
from core.users import get_or_create_user
from core.multi_events import create_multi_event, get_event_options
from core.events import open_event, lock_event
from core.multi_betting import (
    MultiBetError,
    implied_odds_multi,
    predict_payout_multi,
    place_bet_multi,
)
from core.multi_settlement import settle_multi_event
from core.money import usdt_to_micro, micro_to_usdt


# ----------------------------- fixtures -----------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _ev(s):
    """标准 6-选项多竞猜事件（未打开）。"""
    return create_multi_event(
        s,
        title="WC AI Contest Test",
        description="test",
        close_at=datetime.utcnow() + timedelta(hours=1),
        options=[
            {"opt_key": "gpt",      "label": "GPT",      "sort_order": 0},
            {"opt_key": "claude",   "label": "Claude",   "sort_order": 1},
            {"opt_key": "gemini",   "label": "Gemini",   "sort_order": 2},
            {"opt_key": "qwen",     "label": "Qwen",     "sort_order": 3},
            {"opt_key": "deepseek", "label": "DeepSeek", "sort_order": 4},
            {"opt_key": "kimi",     "label": "Kimi",     "sort_order": 5},
        ],
    )


def _topup(user, amount_usdt):
    user.balance_micro += usdt_to_micro(amount_usdt)


# ----------------------------- 选项创建测试 -----------------------------

def test_event_options_created(session):
    ev = _ev(session)
    opts = get_event_options(session, ev.id)
    assert len(opts) == 6
    keys = [o.opt_key for o in opts]
    assert "gpt" in keys and "deepseek" in keys  # deepseek is exactly 8 chars
    assert ev.kind == "multi"


def test_event_state_draft_on_create(session):
    ev = _ev(session)
    assert ev.state == "draft"


# ----------------------------- 赔率测试 -----------------------------

def test_implied_odds_multi_even_pool():
    pools = {"gpt": 1_000_000, "claude": 1_000_000, "gemini": 1_000_000}
    odds = implied_odds_multi(pools, fee_bps=500)
    fee_factor = 1 - 0.05
    # total=3, own=1 → odds = 3 * 0.95 = 2.85
    for k, v in odds.items():
        assert abs(v - 3 * fee_factor) < 0.0001


def test_implied_odds_multi_zero_pool():
    pools = {"gpt": 0, "claude": 1_000_000}
    odds = implied_odds_multi(pools, fee_bps=500)
    assert odds["gpt"] == 0.0
    assert odds["claude"] > 0


def test_predict_payout_multi_simple():
    pools = {"gpt": 0, "claude": 1_000_000}
    payout = predict_payout_multi(pools, fee_bps=500, opt_key="gpt", bet_micro=1_000_000)
    # After bet: total=2M, gpt=1M → payout_pool=2M*0.95=1.9M → payout=1.9M*1M/1M=1.9M
    assert payout == 1_900_000


# ----------------------------- 下注测试 -----------------------------

def test_place_bet_multi_basic(session):
    ev = _ev(session)
    open_event(session, ev)
    u, _ = get_or_create_user(session, tg_user_id=1001)
    _topup(u, 10)

    bet = place_bet_multi(session, user=u, event=ev, opt_key="gpt",
                          amount_micro=usdt_to_micro(10))
    assert bet.side == "gpt"
    assert bet.amount_micro == usdt_to_micro(10)
    assert u.balance_micro == 0

    opts = get_event_options(session, ev.id)
    gpt_opt = next(o for o in opts if o.opt_key == "gpt")
    assert gpt_opt.pool_micro == usdt_to_micro(10)


def test_place_bet_multi_wrong_state(session):
    ev = _ev(session)  # still draft
    u, _ = get_or_create_user(session, tg_user_id=1002)
    _topup(u, 10)
    with pytest.raises(MultiBetError, match="未开盘"):
        place_bet_multi(session, user=u, event=ev, opt_key="gpt",
                        amount_micro=usdt_to_micro(5))


def test_place_bet_multi_invalid_opt_key(session):
    ev = _ev(session)
    open_event(session, ev)
    u, _ = get_or_create_user(session, tg_user_id=1003)
    _topup(u, 10)
    with pytest.raises(MultiBetError, match="无效"):
        place_bet_multi(session, user=u, event=ev, opt_key="nonexistent",
                        amount_micro=usdt_to_micro(5))


def test_place_bet_multi_insufficient_balance(session):
    ev = _ev(session)
    open_event(session, ev)
    u, _ = get_or_create_user(session, tg_user_id=1004)
    _topup(u, 1)  # only 1 USDT
    with pytest.raises(MultiBetError, match="余额不足"):
        place_bet_multi(session, user=u, event=ev, opt_key="claude",
                        amount_micro=usdt_to_micro(5))


def test_place_bet_multi_wrong_kind(session):
    """place_bet_multi 拒绝 binary 事件。"""
    from core.events import create_event
    from core.events import open_event as _open
    ev = create_event(session, title="binary", description="x",
                      close_at=datetime.utcnow() + timedelta(hours=1))
    _open(session, ev)
    u, _ = get_or_create_user(session, tg_user_id=1005)
    _topup(u, 10)
    with pytest.raises(MultiBetError, match="不是多选项"):
        place_bet_multi(session, user=u, event=ev, opt_key="yes",
                        amount_micro=usdt_to_micro(5))


# ----------------------------- 结算测试 -----------------------------

def test_basic_settlement_winner(session):
    """正常结算：gpt 赢，资金守恒，赢家得彩池，输家清零。"""
    ev = _ev(session)
    open_event(session, ev)

    # 分散下注避免鲸鱼风控
    gpt_users = []
    for i in range(3):
        u, _ = get_or_create_user(session, tg_user_id=2000 + i)
        _topup(u, 100)
        place_bet_multi(session, user=u, event=ev, opt_key="gpt",
                        amount_micro=usdt_to_micro(50))
        gpt_users.append(u)

    other_users = []
    for opt in ["claude", "gemini", "qwen"]:
        u, _ = get_or_create_user(session, tg_user_id=2100 + len(other_users))
        _topup(u, 100)
        place_bet_multi(session, user=u, event=ev, opt_key=opt,
                        amount_micro=usdt_to_micro(50))
        other_users.append(u)

    lock_event(session, ev)
    summary = settle_multi_event(session, ev, "gpt")

    total_bet = usdt_to_micro(50 * (3 + 3))  # 6 × 50 = 300 USDT
    assert summary["fee_micro"] + summary["payout_total_micro"] == total_bet
    assert summary["winners"] == 3
    assert summary["losers"] == 3

    # 输家余额为 50 USDT（充了 100，下了 50）
    for u in other_users:
        assert u.balance_micro == usdt_to_micro(50)


def test_settlement_remainder_conservation(session):
    """整数除法余数必须守恒：fee + payouts == total_bet。"""
    ev = _ev(session)
    open_event(session, ev)

    # 3 个 gpt 用户，下注金额故意产生余数
    for i, amt in enumerate([3, 7, 11], start=3000):
        u, _ = get_or_create_user(session, tg_user_id=i)
        _topup(u, 100)
        place_bet_multi(session, user=u, event=ev, opt_key="gpt",
                        amount_micro=usdt_to_micro(amt))

    # claude 用于制造对手盘
    u_c, _ = get_or_create_user(session, tg_user_id=3999)
    _topup(u_c, 100)
    place_bet_multi(session, user=u_c, event=ev, opt_key="claude",
                    amount_micro=usdt_to_micro(13))

    lock_event(session, ev)
    summary = settle_multi_event(session, ev, "gpt")
    total_bet = usdt_to_micro(3 + 7 + 11 + 13)
    assert summary["fee_micro"] + summary["payout_total_micro"] == total_bet


def test_void_winning_pool_empty(session):
    """赢方无人下注 → void + 退款。"""
    ev = _ev(session)
    open_event(session, ev)

    u, _ = get_or_create_user(session, tg_user_id=4001)
    _topup(u, 50)
    place_bet_multi(session, user=u, event=ev, opt_key="claude",
                    amount_micro=usdt_to_micro(50))

    lock_event(session, ev)
    # gpt 赢，但 gpt 池为 0
    summary = settle_multi_event(session, ev, "gpt")
    assert ev.state == "void"
    assert summary["refunded"] == 1
    assert u.balance_micro == usdt_to_micro(50)


def test_void_single_option_bets(session):
    """只有一个选项有下注（无对手盘）→ void。"""
    ev = _ev(session)
    open_event(session, ev)

    for i in range(2):
        u, _ = get_or_create_user(session, tg_user_id=5000 + i)
        _topup(u, 100)
        place_bet_multi(session, user=u, event=ev, opt_key="gemini",
                        amount_micro=usdt_to_micro(50))

    lock_event(session, ev)
    summary = settle_multi_event(session, ev, "gemini")
    assert ev.state == "void"
    assert summary["refunded"] == 2


def test_winner_flag_set(session):
    """结算后 EventOption.is_winner 正确标记。"""
    ev = _ev(session)
    open_event(session, ev)

    for opt in ["deepseek", "kimi"]:
        u, _ = get_or_create_user(session, tg_user_id=6000 + len(opt))
        _topup(u, 100)
        place_bet_multi(session, user=u, event=ev, opt_key=opt,
                        amount_micro=usdt_to_micro(30))

    lock_event(session, ev)
    settle_multi_event(session, ev, "deepseek")

    opts = get_event_options(session, ev.id)
    opt_map = {o.opt_key: o for o in opts}
    assert opt_map["deepseek"].is_winner is True
    assert opt_map["kimi"].is_winner is False


def test_invalid_opt_key_settlement_raises(session):
    """结算时指定不存在的 opt_key 应抛出异常。"""
    from core.multi_settlement import MultiSettlementError
    ev = _ev(session)
    open_event(session, ev)

    u, _ = get_or_create_user(session, tg_user_id=7001)
    _topup(u, 10)
    place_bet_multi(session, user=u, event=ev, opt_key="gpt",
                    amount_micro=usdt_to_micro(5))

    lock_event(session, ev)
    with pytest.raises(MultiSettlementError, match="无效的获胜选项"):
        settle_multi_event(session, ev, "chatgpt_wrong_key")


def test_whale_cap_multi(session):
    """单人超过总池 30% → 被拒绝（池子超过 200 USDT 基准后）。"""
    ev = _ev(session)
    open_event(session, ev)

    # 先铺池子：每个 opt 200 USDT，共 6×200=1200 USDT
    for idx, opt in enumerate(["gpt", "claude", "gemini", "qwen", "deepseek", "kimi"]):
        for j in range(10):
            u, _ = get_or_create_user(session, tg_user_id=8000 + idx * 10 + j)
            _topup(u, 100)
            place_bet_multi(session, user=u, event=ev, opt_key=opt,
                            amount_micro=usdt_to_micro(20))

    whale, _ = get_or_create_user(session, tg_user_id=9999)
    _topup(whale, 10000)
    with pytest.raises(MultiBetError, match="控盘"):
        # 总池 ~1200 USDT，cap = (1200+bet)*0.30；bet=600 → cap=540 < 600 → 被拒
        place_bet_multi(session, user=whale, event=ev, opt_key="gpt",
                        amount_micro=usdt_to_micro(600))
