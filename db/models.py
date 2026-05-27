"""
SQLAlchemy ORM 模型 · TelePoly 全部数据表。

设计要点：
1. 所有金额一律用 BIGINT micro 存（USDT × 10^6），禁止 float。
2. 资金流动必须经 Ledger（双式记账），便于审计 + 对账。
3. Event 状态机：draft → open → locked → settled / void。
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.utcnow()


# ----------------------------- 用户 -----------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    bot_id: Mapped[str] = mapped_column(String(32), default="main", index=True)
    source_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affiliate_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    referrer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    age_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    balance_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / banned

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------- 事件 -----------------------------
class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(32), default="main", index=True)

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    yes_label: Mapped[str] = mapped_column(String(32), default="YES")
    no_label: Mapped[str] = mapped_column(String(32), default="NO")
    cover_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    open_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # draft / open / locked / settled / void

    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)  # yes / no / void
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    fee_bps: Mapped[int] = mapped_column(Integer, default=500)
    pool_yes_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    pool_no_micro: Mapped[int] = mapped_column(BigInteger, default=0)

    # 推送到 channel 后的 message_id（用于事后编辑封盘/结算结果）
    announce_channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    announce_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------- 下注 -----------------------------
class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    side: Mapped[str] = mapped_column(String(8), nullable=False)  # yes / no
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    odds_at_bet: Mapped[float | None] = mapped_column(nullable=True)

    payout_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="placed")
    # placed / won / lost / refunded

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ----------------------------- 账本 -----------------------------
class Ledger(Base):
    """
    所有资金变动唯一来源。
    user_id NULL 表示平台账户（手续费收入、退款发起方等）。
    """
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    delta_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 正进负出
    reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # deposit / bet_place / bet_payout / bet_refund / withdraw / fee / adjust

    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    balance_after_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ----------------------------- 充值 -----------------------------
class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending / confirmed / credited / rejected

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------- 提现 -----------------------------
class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_micro: Mapped[int] = mapped_column(BigInteger, default=1_000_000)  # 默认收 1 USDT 网络费

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending / approved / rejected / sent / confirmed / failed

    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------- HD 钱包派生地址 -----------------------------
class WalletAddress(Base):
    __tablename__ = "wallet_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True)

    derive_index: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    address: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ----------------------------- 事件结算快照 -----------------------------
class EventSnapshot(Base):
    __tablename__ = "event_snapshots"

    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), primary_key=True)
    total_bets: Mapped[int] = mapped_column(Integer, default=0)
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    pool_yes_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    pool_no_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    fee_micro: Mapped[int] = mapped_column(BigInteger, default=0)

    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
