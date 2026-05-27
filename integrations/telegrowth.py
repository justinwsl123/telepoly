"""TelePoly ⟷ TeleGrowth 联动 · affiliate / 漏斗回流。

设计：
  - TeleGrowth 维护 `agents` (推广员)  + `commissions` (佣金) 两张表，跨产品共享。
  - TelePoly 通过 TELEGROWTH_DB_URL 直连 TeleGrowth 的 SQLite/Postgres。
  - 每当用户结算赢/输（事件结算时），按平台手续费的 40% / 8% 分给 L1 / L2 推广员。
  - 佣金不直接打钱，只写入 `commissions` 表（status=hold），
    TeleGrowth 自有定时任务每周转 payable → 批量出款。
  - 不配 TELEGROWTH_DB_URL 时本模块所有方法 no-op，让 MVP 也能裸跑。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import create_engine, text

from telepoly_bot.config import settings


L1_RATE = 0.40
L2_RATE = 0.08
COMMISSION_HOLD_DAYS = 7


def _engine():
    """惰性构造 TeleGrowth DB 引擎。"""
    if not settings.telegrowth_db_url:
        return None
    return create_engine(settings.telegrowth_db_url, future=True)


def is_enabled() -> bool:
    return bool(settings.telegrowth_db_url)


# ----------------------------- 推荐码解析 -----------------------------
def resolve_referrer(code: str) -> Optional[dict]:
    """
    根据 ?start=ref_<code> 中的 code 查 TeleGrowth.agents。
    返回 {agent_id, parent_agent_id, agent_code} 或 None。
    """
    eng = _engine()
    if not eng or not code:
        return None
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT id, parent_agent_id, agent_code FROM agents WHERE agent_code = :c AND status='active'"),
                {"c": code},
            ).first()
            if not row:
                return None
            return {"agent_id": row[0], "parent_agent_id": row[1], "agent_code": row[2]}
    except Exception as e:
        logger.warning(f"telegrowth resolve_referrer failed: {e}")
        return None


# ----------------------------- 佣金写入 -----------------------------
def record_commission_for_fee(
    *,
    payer_tg_user_id: int,
    payer_referrer_code: str | None,
    fee_usdt: float,
    source_payment_ref: str,
) -> int:
    """
    用户产生 fee（每次下注或结算手续费）时写两笔佣金（L1 + L2，如果有）。
    返回写入的佣金条数。

    fee_usdt 是平台抽到的手续费金额（USDT）；commission 从中分。
    """
    if not is_enabled() or not payer_referrer_code or fee_usdt <= 0:
        return 0

    ref = resolve_referrer(payer_referrer_code)
    if not ref:
        return 0

    written = 0
    available_at = (datetime.utcnow() + timedelta(days=COMMISSION_HOLD_DAYS)).isoformat(sep=" ", timespec="seconds")
    eng = _engine()
    try:
        with eng.begin() as conn:
            # L1 直推
            conn.execute(text("""
                INSERT INTO commissions
                    (agent_id, source_user_id, source_payment, level, payment_usd, commission_usd, status, available_at)
                VALUES
                    (:aid, :uid, :pay_ref, 1, :payment, :comm, 'hold', :avail)
            """), {
                "aid": ref["agent_id"],
                "uid": str(payer_tg_user_id),
                "pay_ref": source_payment_ref,
                "payment": fee_usdt,
                "comm": round(fee_usdt * L1_RATE, 4),
                "avail": available_at,
            })
            written += 1

            # L2 间推
            if ref["parent_agent_id"]:
                conn.execute(text("""
                    INSERT INTO commissions
                        (agent_id, source_user_id, source_payment, level, payment_usd, commission_usd, status, available_at)
                    VALUES
                        (:aid, :uid, :pay_ref, 2, :payment, :comm, 'hold', :avail)
                """), {
                    "aid": ref["parent_agent_id"],
                    "uid": str(payer_tg_user_id),
                    "pay_ref": source_payment_ref,
                    "payment": fee_usdt,
                    "comm": round(fee_usdt * L2_RATE, 4),
                    "avail": available_at,
                })
                written += 1
    except Exception as e:
        logger.warning(f"telegrowth record_commission failed: {e}")
        return 0

    logger.info(f"commission recorded: l1+{ref['agent_id']} fee={fee_usdt}U rows={written} ref={source_payment_ref}")
    return written


# ----------------------------- 漏斗事件回流 -----------------------------
def report_funnel_event(*, event_type: str, tg_user_id: int, source_channel: str | None,
                        amount_usdt: float = 0.0, meta: dict | None = None) -> None:
    """
    把 TelePoly 的关键漏斗事件写到 TeleGrowth.funnel_events，统一北极星指标。
    event_type 例：bot_start / placed_first_bet / first_deposit / first_payout
    """
    if not is_enabled():
        return
    eng = _engine()
    try:
        import json
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO funnel_events
                    (tg_user_id, event_type, source_channel, amount_usd, meta_json, created_at)
                VALUES
                    (:uid, :et, :sc, :amt, :meta, datetime('now'))
            """), {
                "uid": str(tg_user_id),
                "et": event_type,
                "sc": source_channel,
                "amt": amount_usdt,
                "meta": json.dumps(meta or {}),
            })
    except Exception as e:
        # funnel_events 列名可能不一致 → 忽略而不抛
        logger.debug(f"funnel report skipped ({event_type}): {e}")
