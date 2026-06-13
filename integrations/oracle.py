"""Oracle · 从 TeleGrowth DB 读取 AI 模型净值，决定世界杯竞猜赢家。

使用方式：
  from integrations.oracle import resolve_winning_model, model_standings

需要环境变量：
  TELEGROWTH_DB_URL — 指向 TeleGrowth 数据库的连接串（同 telegrowth.py）

若未配置 TELEGROWTH_DB_URL，函数返回 None / 空列表（no-op）。
"""
from __future__ import annotations

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


def _engine():
    if not settings.telegrowth_db_url:
        return None
    return create_engine(settings.telegrowth_db_url, future=True)


def resolve_winning_model() -> str | None:
    """
    查询 TeleGrowth vb_accounts，返回净值最高的模型对应的 opt_key。
    平局时取 current_usd 最高的第一条（ORDER BY DESC LIMIT 1）。
    未配置 TELEGROWTH_DB_URL 时返回 None。
    """
    eng = _engine()
    if not eng:
        logger.warning("oracle: TELEGROWTH_DB_URL not set, cannot resolve winner")
        return None
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT model_id, current_usd FROM vb_accounts "
                "WHERE status='active' ORDER BY current_usd DESC LIMIT 1"
            )).first()
        if not row:
            logger.warning("oracle: vb_accounts returned no rows")
            return None
        model_id = row[0]
        opt_key = MODEL_ID_MAP.get(model_id)
        if not opt_key:
            logger.warning(f"oracle: unknown model_id '{model_id}', no opt_key mapping")
            return None
        logger.info(f"oracle: winner model_id={model_id} opt_key={opt_key} usd={row[1]:.2f}")
        return opt_key
    except Exception as e:
        logger.error(f"oracle: query failed: {e}")
        return None


def model_standings() -> list[dict]:
    """
    返回 6 个模型的当前净值排名（降序）。
    每条：{"opt_key": "gpt", "model_id": "openai/gpt-5.5", "current_usd": 12345.67, "rank": 1}
    未配置 TELEGROWTH_DB_URL 时返回空列表。
    """
    eng = _engine()
    if not eng:
        return []
    model_ids = list(MODEL_ID_MAP.keys())
    placeholders = ", ".join(f"'{mid}'" for mid in model_ids)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                f"SELECT model_id, current_usd FROM vb_accounts "
                f"WHERE status='active' AND model_id IN ({placeholders}) "
                f"ORDER BY current_usd DESC"
            )).fetchall()
        result = []
        for rank, row in enumerate(rows, start=1):
            opt_key = MODEL_ID_MAP.get(row[0], row[0])
            result.append({
                "opt_key":     opt_key,
                "model_id":    row[0],
                "current_usd": float(row[1]),
                "rank":        rank,
            })
        return result
    except Exception as e:
        logger.error(f"oracle: model_standings query failed: {e}")
        return []
