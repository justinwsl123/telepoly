"""Oracle · 读取 AI 模型净值，决定世界杯竞猜赢家。

数据源（优先级）：
  1. BETScope 公开净值 API（推荐）：GET {VALUEBET_PUBLIC_API_URL}/api/public/lab/summary
     返回 accounts:[{model_id, current_usd, status, ...}]，无需暴露 TeleGrowth 本地 DB。
  2. 兜底：若配置了 TELEGROWTH_DB_URL，直接读 vb_accounts（同机部署时用）。

两个数据源都拿不到时返回 None / 空列表（no-op，不会误结算）。
"""
from __future__ import annotations

import httpx
from loguru import logger
from sqlalchemy import create_engine, text

from telepoly_bot.config import settings


# model_id → opt_key 映射（与 create_wc_contest.py 中的选项对齐）
MODEL_ID_MAP: dict[str, str] = {
    "openai/gpt-5.5":                 "gpt",
    "claude-opus-4-7":                "claude",
    "google/gemini-3.1-pro":          "gemini",
    "qwen/qwen3.7-max":               "qwen",
    "deepseek/deepseek-v4-flash":     "deepseek",
    "moonshotai/kimi-k2.6":           "kimi",
}

# 反向映射：opt_key → model_id
OPT_KEY_MAP = {v: k for k, v in MODEL_ID_MAP.items()}


# ----------------------------- 数据源 1：BETScope 公开 API -----------------------------
def _fetch_accounts_from_api() -> list[dict] | None:
    """从 BETScope 公开 summary API 拉 accounts。失败返回 None（让调用方回退 DB）。"""
    base = (settings.valuebet_public_api_url or "").strip().rstrip("/")
    if not base:
        return None
    url = f"{base}/api/public/lab/summary"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        if not data.get("ok", True):
            logger.warning(f"oracle: summary api not ok: {data.get('error')}")
            return None
        accounts = data.get("accounts") or (data.get("summary") or {}).get("accounts")
        if not isinstance(accounts, list):
            logger.warning("oracle: summary api missing 'accounts'")
            return None
        return accounts
    except Exception as e:
        logger.warning(f"oracle: public api fetch failed ({url}): {e}")
        return None


# ----------------------------- 数据源 2：TeleGrowth DB 兜底 -----------------------------
def _engine():
    if not settings.telegrowth_db_url:
        return None
    return create_engine(settings.telegrowth_db_url, future=True)


def _fetch_accounts_from_db() -> list[dict] | None:
    eng = _engine()
    if not eng:
        return None
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT model_id, current_usd, status FROM vb_accounts"
            )).fetchall()
        return [{"model_id": r[0], "current_usd": r[1], "status": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"oracle: db fetch failed: {e}")
        return None


def _load_accounts() -> list[dict]:
    """优先公开 API，失败回退本地 DB；都没有则返回空。"""
    accts = _fetch_accounts_from_api()
    if accts is None:
        accts = _fetch_accounts_from_db()
    return accts or []


def _ranked_active(accounts: list[dict]) -> list[dict]:
    """筛 active + 已知模型，按 current_usd 降序。"""
    out = []
    for a in accounts:
        mid = a.get("model_id")
        if mid not in MODEL_ID_MAP:
            continue
        if (a.get("status") or "active") != "active":
            continue
        try:
            usd = float(a.get("current_usd") or 0.0)
        except (TypeError, ValueError):
            continue
        out.append({"model_id": mid, "opt_key": MODEL_ID_MAP[mid], "current_usd": usd})
    out.sort(key=lambda x: x["current_usd"], reverse=True)
    return out


def resolve_winning_model() -> str | None:
    """返回净值最高的模型对应的 opt_key。平局取最高的第一条。拿不到数据返回 None。"""
    ranked = _ranked_active(_load_accounts())
    if not ranked:
        logger.warning("oracle: no active accounts resolved (api+db both empty)")
        return None
    win = ranked[0]
    logger.info(f"oracle: winner model_id={win['model_id']} opt_key={win['opt_key']} usd={win['current_usd']:.2f}")
    return win["opt_key"]


def model_standings() -> list[dict]:
    """返回 6 模型当前净值排名（降序）：[{opt_key, model_id, current_usd, rank}]。"""
    ranked = _ranked_active(_load_accounts())
    return [
        {"opt_key": r["opt_key"], "model_id": r["model_id"],
         "current_usd": r["current_usd"], "rank": i}
        for i, r in enumerate(ranked, start=1)
    ]
