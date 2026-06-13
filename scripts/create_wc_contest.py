"""创建世界杯 AI 模型冠军竞猜事件。

幂等：若已存在同名 multi 事件则跳过创建（但仍可 --force-open）。

用法：
  uv run python -m scripts.create_wc_contest
  uv run python -m scripts.create_wc_contest --close-at 2026-07-19T18:00:00
  uv run python -m scripts.create_wc_contest --open   # 自动打开（draft→open）
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from loguru import logger
from sqlalchemy import select

from db.models import Event
from db.session import get_session
from core.multi_events import create_multi_event
from core.events import open_event
from telepoly_bot.config import settings


# ----------------------- 竞猜配置 -----------------------

CONTEST_TITLE = "🏆 世界杯 AI 模型擂台冠军竞猜"
CONTEST_DESC = (
    "2026 FIFA 世界杯结束时（2026-07-19），哪个 AI 模型净值最高？\n\n"
    "规则：竞猜标的是 TeleGrowth 平台上 6 个 AI 模型的账户净值（USD）。"
    "世界杯闭幕后，Oracle 自动读取各模型净值，净值最高者获胜。\n"
    "投注方押注的 USDT 全部进入彩池，赢家按比例分走扣除 5% 手续费后的总池。\n"
    "资金公开透明，可在 Tronscan 查验热钱包地址。"
)

# 默认截止时间：世界杯决赛次日 18:00 UTC（占位，可通过命令行覆盖）
DEFAULT_CLOSE_AT = datetime(2026, 7, 19, 18, 0, 0)

# 6 个选项（opt_key ≤ 8 chars）
CONTEST_OPTIONS = [
    {"opt_key": "gpt",      "label": "GPT-5.5 (OpenAI)",           "color": "#10a37f", "sort_order": 0},
    {"opt_key": "claude",   "label": "Claude Opus 4.7 (Anthropic)", "color": "#e55b33", "sort_order": 1},
    {"opt_key": "gemini",   "label": "Gemini 3.1 Pro (Google)",     "color": "#4285f4", "sort_order": 2},
    {"opt_key": "qwen",     "label": "Qwen3.7-Max (Alibaba)",       "color": "#ff6a00", "sort_order": 3},
    {"opt_key": "deepseek", "label": "DeepSeek-V4-Flash",           "color": "#1a6ef7", "sort_order": 4},
    {"opt_key": "kimi",     "label": "Kimi-K2.6 (Moonshot)",        "color": "#7c3aed", "sort_order": 5},
]


# --------------------------------------------------------

def _find_existing(session) -> Event | None:
    return session.scalars(
        select(Event)
        .where(Event.title == CONTEST_TITLE, Event.kind == "multi")
        .order_by(Event.id)
    ).first()


def create_contest(close_at: datetime, auto_open: bool = True) -> int:
    """创建（或跳过）竞猜事件，返回 event.id。"""
    with get_session() as s:
        existing = _find_existing(s)
        if existing is not None:
            logger.info(f"create_wc_contest: event#{existing.id} 已存在，跳过创建")
            ev_id = existing.id
            ev_state = existing.state
        else:
            ev = create_multi_event(
                s,
                title=CONTEST_TITLE,
                description=CONTEST_DESC,
                close_at=close_at,
                options=CONTEST_OPTIONS,
                fee_bps=settings.event_fee_bps,
                bot_id=settings.bot_id,
            )
            ev_id = ev.id
            ev_state = ev.state
            logger.success(f"create_wc_contest: 创建 event#{ev_id}")

        if auto_open and ev_state == "draft":
            ev_reload = s.get(Event, ev_id)
            open_event(s, ev_reload)
            logger.success(f"create_wc_contest: event#{ev_id} 已打开（draft→open）")

    return ev_id


def main() -> None:
    parser = argparse.ArgumentParser(description="创建世界杯 AI 模型竞猜事件")
    parser.add_argument(
        "--close-at",
        default=DEFAULT_CLOSE_AT.isoformat(),
        help=f"截止时间 ISO 格式（默认：{DEFAULT_CLOSE_AT.isoformat()}）",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="创建后保持 draft 状态，不自动打开",
    )
    args = parser.parse_args()

    try:
        close_at = datetime.fromisoformat(args.close_at)
    except ValueError:
        print(f"无效的 --close-at 格式: {args.close_at}")
        sys.exit(1)

    event_id = create_contest(close_at=close_at, auto_open=not args.no_open)
    print(f"OK: event_id={event_id}")


if __name__ == "__main__":
    main()
