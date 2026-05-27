"""Aiberm LLM 网关封装 · 出题助手。

设计：
  - 一次调用让模型生成 5 个候选题，输出严格 JSON；
  - 题目偏好：24-72h 内可结算、二元化、有公开可验证的事实判定来源、
    对非洲博彩用户 / 加密用户具有娱乐性的"好下注"题；
  - 输出 schema：
      [{
        "title": str,
        "description": str,
        "yes_label": str,
        "no_label": str,
        "close_in_hours": float,
        "category": "crypto"|"sports"|"politics"|"entertainment"|"other",
        "evidence_source": str,
        "spice_score": int (1-10),
      }, ...]
"""
from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from telepoly_bot.config import settings


SYSTEM_PROMPT = """You are TelePoly's daily-event curator.

TelePoly is a Telegram-native daily prediction market settled in USDT.
Each day we run ONE binary (YES/NO) market that closes within 24-72 hours.

Your job: given a brief from the operator, propose **5 distinct candidate questions**
that we could publish today. Optimize for:

1. RESOLVABILITY — outcome can be objectively verified by a public source
   within 72h (price feeds, official accounts, sports scoreboards).
2. SPICE — emotional pull. Crypto price levels, sports upsets, political
   surprises, viral entertainment moments. Avoid boring or overly technical.
3. BALANCE — answer is genuinely uncertain at publish time (around 30-70%
   implied probability). Skip "almost certainly yes" questions.
4. SHORT TITLE — under 90 chars, ends with a question mark.

Output STRICT JSON array, no commentary. Each item:
{
  "title": "...",
  "description": "concise 1-2 sentence context, includes the resolution source",
  "yes_label": "YES",
  "no_label": "NO",
  "close_in_hours": 24,
  "category": "crypto|sports|politics|entertainment|other",
  "evidence_source": "URL or named source (e.g. CoinMarketCap snapshot, FIFA official)",
  "spice_score": 1-10
}
"""


def is_enabled() -> bool:
    return bool(settings.llm_api_key)


def suggest_events(brief: str = "", n: int = 5) -> list[dict[str, Any]]:
    """
    调用 LLM 生成候选题。失败时返回空列表（admin_web 显示降级提示）。
    """
    if not is_enabled():
        logger.warning("aiberm: LLM_API_KEY not configured")
        return []

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base,
    )

    user_msg = brief.strip() or (
        "Operator brief: it's a normal day, propose 5 strong candidates spanning crypto, "
        "football, and one wildcard entertainment/politics topic."
    )
    user_msg += f"\n\nReturn exactly {n} candidates. JSON array only."

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.85,
            max_tokens=1500,
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"aiberm call failed: {e}")
        return []

    candidates = _extract_json_array(content)
    valid = [c for c in candidates if _looks_valid(c)]
    return valid[:n]


def _extract_json_array(text: str) -> list[dict]:
    """容错：模型可能裹 ```json ... ``` 或前后多空文本。"""
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence:
        text = fence.group(1)
    text = text.strip()
    # 尝试直接解析
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        pass
    # 兜底：拉出第一个 [ ... ]
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return []


def _looks_valid(c: dict) -> bool:
    if not isinstance(c, dict):
        return False
    if not c.get("title") or not isinstance(c["title"], str):
        return False
    try:
        h = float(c.get("close_in_hours", 24))
        if h <= 0 or h > 168:
            return False
    except (TypeError, ValueError):
        return False
    return True
