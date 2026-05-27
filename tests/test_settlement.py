"""结算引擎单测：守恒律 + 余数 + void 边界。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

# 测试用内存 DB（必须在 import db.session 之前设置）
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Event
from core.users import get_or_create_user
from core.events import create_event, open_event, lock_event
from core.betting import place_bet
from core.settlement import settle_event
from core.money import usdt_to_micro


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _ev(s):
    return create_event(
        s,
        title="BTC 是否突破 $120k？",
        description="测试事件",
        close_at=datetime.utcnow() + timedelta(hours=1),
    )


def _topup(user, amount_usdt):
    user.balance_micro += usdt_to_micro(amount_usdt)


def test_basic_yes_wins(session):
    ev = _ev(session)
    open_event(session, ev)

    # 用 5 个用户每人下 100 → YES 池 200 / NO 池 300，分散下注绕开单人 30% 风控
    yes_users = []
    for i in range(2):
        u, _ = get_or_create_user(session, tg_user_id=10 + i)
        _topup(u, 100); yes_users.append(u)
    no_users = []
    for i in range(3):
        u, _ = get_or_create_user(session, tg_user_id=20 + i)
        _topup(u, 100); no_users.append(u)

    for u in yes_users:
        place_bet(session, user=u, event=ev, side="yes", amount_micro=usdt_to_micro(100))
    for u in no_users:
        place_bet(session, user=u, event=ev, side="no", amount_micro=usdt_to_micro(100))

    lock_event(session, ev)
    summary = settle_event(session, ev, "yes", evidence_url="https://example.com/proof")

    total_bet = usdt_to_micro(500)
    assert summary["fee_micro"] + summary["payout_total_micro"] == total_bet
    assert summary["fee_micro"] == total_bet * 500 // 10_000
    assert summary["winners"] == 2
    assert summary["losers"] == 3

    # 每个 YES 赢家投了 100 / 200 = 50% → 拿走 payout_pool 一半（除最后一笔吃余数）
    payout_pool = total_bet * 9500 // 10_000
    assert yes_users[0].balance_micro == payout_pool // 2
    # 输家清零
    assert no_users[0].balance_micro == 0


def test_void_when_one_side_empty(session):
    ev = _ev(session)
    open_event(session, ev)
    a, _ = get_or_create_user(session, tg_user_id=11)
    _topup(a, 50)
    place_bet(session, user=a, event=ev, side="yes", amount_micro=usdt_to_micro(50))

    settle_event(session, ev, "yes")
    assert ev.state == "void"
    assert a.balance_micro == usdt_to_micro(50)  # 退款


def test_explicit_void_refunds_all(session):
    ev = _ev(session)
    open_event(session, ev)
    # 池子都低于 100 USDT，避免触发 30% 风控
    a, _ = get_or_create_user(session, tg_user_id=21)
    b, _ = get_or_create_user(session, tg_user_id=22)
    _topup(a, 100); _topup(b, 100)
    place_bet(session, user=a, event=ev, side="yes", amount_micro=usdt_to_micro(40))
    place_bet(session, user=b, event=ev, side="no",  amount_micro=usdt_to_micro(40))

    summary = settle_event(session, ev, "void")
    assert summary["fee_micro"] == 0
    assert a.balance_micro == usdt_to_micro(100)  # 全额退回
    assert b.balance_micro == usdt_to_micro(100)


def test_remainder_conservation(session):
    """整除有余数时，最后一笔吃掉余数，总和必须守恒。"""
    ev = _ev(session)
    open_event(session, ev)
    # 用故意会产生余数的金额
    users = []
    for i, amt in enumerate([3, 7, 11], start=100):
        u, _ = get_or_create_user(session, tg_user_id=i)
        _topup(u, 100)
        place_bet(session, user=u, event=ev, side="yes", amount_micro=usdt_to_micro(amt))
        users.append(u)
    loser, _ = get_or_create_user(session, tg_user_id=200)
    _topup(loser, 100)
    place_bet(session, user=loser, event=ev, side="no", amount_micro=usdt_to_micro(13))

    summary = settle_event(session, ev, "yes")
    total_bet = usdt_to_micro(3 + 7 + 11 + 13)
    assert summary["fee_micro"] + summary["payout_total_micro"] == total_bet


def test_max_bet_ratio(session):
    """池子超过 100 USDT 后，鲸鱼单注 > 30% 被拒。"""
    from core.betting import BetError, place_bet
    ev = _ev(session); open_event(session, ev)
    # 先撑起 200 USDT 的池子
    for i in range(20):
        u, _ = get_or_create_user(session, tg_user_id=300 + i)
        _topup(u, 100)
        place_bet(session, user=u, event=ev, side="yes" if i % 2 == 0 else "no",
                  amount_micro=usdt_to_micro(10))
    whale, _ = get_or_create_user(session, tg_user_id=999)
    _topup(whale, 10000)
    with pytest.raises(BetError):
        place_bet(session, user=whale, event=ev, side="yes", amount_micro=usdt_to_micro(500))
