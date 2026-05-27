"""跨 Bot 共享钱包 REST API · 让 KickAI / 未来 Bot 共用一套余额账本。

设计原则：
  - TelePoly 是钱包"权威源"；其他 bot 通过 HTTPS API 借用余额；
  - 鉴权：HTTP Header `X-Wallet-Api-Key`，与 .env 的 WALLET_API_KEY 比对；
  - 余额变动统一过 core.ledger（双式记账），保证审计；
  - 防重：每个 charge / credit 必须带 idempotency_key（外部 bot 自己生成 UUID）。

API：
  GET  /api/wallet/balance/{tg_user_id}      → {balance_micro}
  POST /api/wallet/charge                     扣款（用户在外 bot 消费）
       body: {tg_user_id, amount_micro, idempotency_key, reason, note?}
  POST /api/wallet/credit                     入账（外 bot 给用户发奖励）
       body: {tg_user_id, amount_micro, idempotency_key, reason, note?}
  POST /api/wallet/ensure_user                确保用户在 TelePoly 侧存在
       body: {tg_user_id, username?, first_name?, lang?}

外部 bot 集成示例（Python）见 docs/UNIFIED_WALLET.md。
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from db.models import Ledger, User
from db.session import get_session
from core.ledger import record_ledger
from core.users import get_or_create_user


router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _api_key() -> str:
    return os.getenv("WALLET_API_KEY", "")


def _check_auth(x_wallet_api_key: str | None) -> None:
    expected = _api_key()
    if not expected:
        raise HTTPException(503, "WALLET_API_KEY not configured on server")
    if not x_wallet_api_key or not secrets.compare_digest(x_wallet_api_key, expected):
        raise HTTPException(401, "invalid api key")


# ----------------------------- Schemas -----------------------------
class EnsureUserReq(BaseModel):
    tg_user_id: int
    username: str | None = None
    first_name: str | None = None
    lang: str | None = None


class TxReq(BaseModel):
    tg_user_id: int
    amount_micro: int
    idempotency_key: str
    reason: str          # 调用方语义，例如 "kickai_paywall" / "kickai_refund"
    note: str | None = None


class TxResp(BaseModel):
    ok: bool
    balance_micro: int
    ledger_id: int | None = None
    duplicate: bool = False


# ----------------------------- Endpoints -----------------------------
@router.get("/balance/{tg_user_id}")
def get_balance(tg_user_id: int, x_wallet_api_key: str | None = Header(default=None)):
    _check_auth(x_wallet_api_key)
    with get_session() as s:
        user = s.scalars(select(User).where(User.tg_user_id == tg_user_id)).first()
        return {"tg_user_id": tg_user_id, "exists": bool(user),
                "balance_micro": user.balance_micro if user else 0}


@router.post("/ensure_user")
def ensure_user(req: EnsureUserReq, x_wallet_api_key: str | None = Header(default=None)):
    _check_auth(x_wallet_api_key)
    with get_session() as s:
        user, created = get_or_create_user(
            s, tg_user_id=req.tg_user_id,
            username=req.username, first_name=req.first_name,
            lang=(req.lang or "en")[:2],
        )
        return {"id": user.id, "tg_user_id": user.tg_user_id,
                "balance_micro": user.balance_micro, "created": created}


def _idempotent_lookup(session, idempotency_key: str) -> Ledger | None:
    return session.scalars(
        select(Ledger).where(Ledger.note == f"idem:{idempotency_key}")
    ).first()


@router.post("/charge", response_model=TxResp)
def charge(req: TxReq, x_wallet_api_key: str | None = Header(default=None)) -> TxResp:
    _check_auth(x_wallet_api_key)
    if req.amount_micro <= 0:
        raise HTTPException(400, "amount_micro must be positive")

    with get_session() as s:
        existing = _idempotent_lookup(s, req.idempotency_key)
        if existing:
            user = s.get(User, existing.user_id)
            return TxResp(ok=True, balance_micro=user.balance_micro,
                          ledger_id=existing.id, duplicate=True)

        user = s.scalars(select(User).where(User.tg_user_id == req.tg_user_id)).first()
        if not user:
            raise HTTPException(404, "user not found; call /ensure_user first")
        if user.balance_micro < req.amount_micro:
            raise HTTPException(402, "insufficient balance")

        note_full = f"idem:{req.idempotency_key}"
        if req.note:
            note_full += f" · {req.note[:200]}"

        entry = record_ledger(
            s, user=user, delta_micro=-req.amount_micro,
            reason=req.reason or "external_charge",
            note=note_full,
        )
        return TxResp(ok=True, balance_micro=user.balance_micro, ledger_id=entry.id)


@router.post("/credit", response_model=TxResp)
def credit(req: TxReq, x_wallet_api_key: str | None = Header(default=None)) -> TxResp:
    _check_auth(x_wallet_api_key)
    if req.amount_micro <= 0:
        raise HTTPException(400, "amount_micro must be positive")

    with get_session() as s:
        existing = _idempotent_lookup(s, req.idempotency_key)
        if existing:
            user = s.get(User, existing.user_id)
            return TxResp(ok=True, balance_micro=user.balance_micro,
                          ledger_id=existing.id, duplicate=True)

        user = s.scalars(select(User).where(User.tg_user_id == req.tg_user_id)).first()
        if not user:
            raise HTTPException(404, "user not found; call /ensure_user first")

        note_full = f"idem:{req.idempotency_key}"
        if req.note:
            note_full += f" · {req.note[:200]}"

        entry = record_ledger(
            s, user=user, delta_micro=req.amount_micro,
            reason=req.reason or "external_credit",
            note=note_full,
        )
        return TxResp(ok=True, balance_micro=user.balance_micro, ledger_id=entry.id)
