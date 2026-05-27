"""Day-3 加餐功能的烟测：走势图 / 大佬榜 / Mini App 鉴权 / 钱包 API 幂等。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base
from core.events import create_event, lock_event, open_event
from core.betting import place_bet
from core.settlement import settle_event
from core.snapshots import capture_event, fetch_timeline
from core.leaderboard import top_winners, render_hall_of_fame
from core.users import get_or_create_user
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
    return create_event(s, title="X?", description="", close_at=datetime.utcnow() + timedelta(hours=1))


# ------------------------ 走势图 ------------------------
def test_snapshot_pipeline(session):
    ev = _ev(session); open_event(session, ev)
    a, _ = get_or_create_user(session, tg_user_id=1)
    a.balance_micro = usdt_to_micro(50)
    place_bet(session, user=a, event=ev, side="yes", amount_micro=usdt_to_micro(10))
    capture_event(session, ev)

    timeline = fetch_timeline(session, ev.id)
    assert len(timeline) >= 2  # open_event 写一条 + 我们手动一条
    latest = timeline[-1]
    assert latest["yes"] == usdt_to_micro(10)
    assert 0 <= latest["yes_share"] <= 1


def test_chart_renders_png():
    from telepoly_bot.charts import render_pool_timeline
    points = [
        {"t": "2026-01-01T10:00:00", "yes": 100_000_000, "no": 50_000_000,
         "total": 150_000_000, "yes_share": 100/150, "n_bets": 3},
        {"t": "2026-01-01T11:00:00", "yes": 200_000_000, "no": 200_000_000,
         "total": 400_000_000, "yes_share": 0.5, "n_bets": 8},
    ]
    png = render_pool_timeline(points, title="Test event")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 魔术头


def test_chart_placeholder_for_empty():
    from telepoly_bot.charts import render_pool_timeline
    png = render_pool_timeline([])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ------------------------ 大佬榜 ------------------------
def test_top_winners_yesterday(session):
    """伪造一个昨天结算的事件，确认 Top 排序正确。"""
    ev = _ev(session); open_event(session, ev)
    big, _ = get_or_create_user(session, tg_user_id=10, username="bigfish")
    small, _ = get_or_create_user(session, tg_user_id=11, username="smallfish")
    loser, _ = get_or_create_user(session, tg_user_id=12, username="lostit")
    big.balance_micro = usdt_to_micro(200)
    small.balance_micro = usdt_to_micro(200)
    loser.balance_micro = usdt_to_micro(200)

    place_bet(session, user=big, event=ev, side="yes", amount_micro=usdt_to_micro(50))
    place_bet(session, user=small, event=ev, side="yes", amount_micro=usdt_to_micro(20))
    place_bet(session, user=loser, event=ev, side="no", amount_micro=usdt_to_micro(80))

    lock_event(session, ev)
    settle_event(session, ev, "yes")
    # 把 settled_at 改到昨天
    ev.settled_at = datetime.utcnow() - timedelta(hours=12)
    session.flush()

    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(seconds=1)
    winners = top_winners(session, top_n=3, start=start, end=end)
    assert len(winners) == 2
    assert winners[0]["user_id"] == big.id  # 押的多赢的多
    assert winners[0]["pnl_micro"] > winners[1]["pnl_micro"]

    text = render_hall_of_fame(winners, "Yesterday")
    assert "🥇" in text
    assert "bigfish" not in text  # 必须脱敏
    assert "@bi" in text


# ------------------------ Mini App 鉴权 ------------------------
def test_init_data_signature_validates():
    """构造一个合法 initData，验证签名通过。"""
    import hashlib, hmac, json
    from urllib.parse import urlencode

    from admin_web.miniapp import verify_init_data
    bot_token = "123:test_token"
    user_obj = {"id": 42, "username": "alice", "first_name": "Alice", "language_code": "en"}
    fields = {
        "auth_date": str(int(datetime.utcnow().timestamp())),
        "user": json.dumps(user_obj, separators=(",", ":")),
    }
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields.keys()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode({**fields, "hash": sig})

    parsed = verify_init_data(init_data, bot_token)
    assert parsed and parsed["id"] == 42

    # 篡改后应失败
    bad = init_data.replace(sig, "0" * len(sig))
    assert verify_init_data(bad, bot_token) is None


# ------------------------ 钱包 API 幂等 ------------------------
def test_wallet_api_idempotent_charge(session, monkeypatch):
    """直接调底层 charge handler 验证幂等。需要 mock get_session 指向我们的 session。"""
    from admin_web import wallet_api
    from db import session as session_mod

    # 让 wallet_api 用我们这个 in-memory session
    monkeypatch.setenv("WALLET_API_KEY", "test-key")
    from contextlib import contextmanager

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(wallet_api, "get_session", fake_session)

    user, _ = get_or_create_user(session, tg_user_id=999, username="payer")
    user.balance_micro = usdt_to_micro(100)
    session.flush()

    req = wallet_api.TxReq(
        tg_user_id=999, amount_micro=usdt_to_micro(30),
        idempotency_key="test-charge-1", reason="kickai_paywall",
    )
    r1 = wallet_api.charge(req, x_wallet_api_key="test-key")
    assert r1.ok and not r1.duplicate
    assert r1.balance_micro == usdt_to_micro(70)

    # 同 key 再调一次 → 应该返回 duplicate=True，余额不变
    r2 = wallet_api.charge(req, x_wallet_api_key="test-key")
    assert r2.duplicate
    assert r2.balance_micro == usdt_to_micro(70)
