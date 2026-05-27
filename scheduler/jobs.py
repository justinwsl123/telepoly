"""定时任务定义（挂在 PTB 自带的 JobQueue 上，与 bot 同进程）。

Job 列表：
  - auto_lock_events    每分钟一次，检查是否有 close_at 到期的事件 → 自动封盘
                        并在频道发"封盘"公告
  - daily_event_check   每天 09:00 UTC 提示运营出题（如果当前没 open 事件）

settle 仍走运营手动 /admin_settle，避免误判。
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from loguru import logger
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db.session import get_session
from core.events import events_to_lock, lock_event
from telepoly_bot.config import settings


async def auto_lock_events(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_session() as s:
        evs = events_to_lock(s)
        ids_locked = []
        announces = []
        for ev in evs:
            lock_event(s, ev)
            ids_locked.append(ev.id)
            if ev.announce_channel_id and ev.announce_message_id:
                announces.append((ev.announce_channel_id, ev.announce_message_id, ev.title))

    for chan_id, reply_to, title in announces:
        try:
            await ctx.bot.send_message(
                chan_id,
                f"🔒 *Closed for bets*\n_{title}_\n\nWaiting for settlement…",
                parse_mode=ParseMode.MARKDOWN,
                reply_to_message_id=reply_to,
            )
        except Exception as e:
            logger.warning(f"lock announce failed: {e}")

    if ids_locked:
        logger.info(f"auto-locked events: {ids_locked}")


async def daily_event_check(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """每天 09:00 UTC 如果没有 open 事件，提醒运营。"""
    from core.events import get_active_event
    with get_session() as s:
        active = get_active_event(s)
    if active:
        return
    for owner in settings.owner_ids:
        try:
            await ctx.bot.send_message(
                owner,
                "⏰ 09:00 UTC 提醒：今天还没发布事件。\n用 /admin_new <h> <题目> | <描述> 出题。",
            )
        except Exception:
            pass


def register_jobs(app) -> None:
    jq = app.job_queue
    jq.run_repeating(auto_lock_events, interval=60, first=10, name="auto_lock_events")
    jq.run_daily(daily_event_check, time=time(9, 0, tzinfo=timezone.utc), name="daily_event_check")
