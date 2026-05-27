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
from core.leaderboard import render_hall_of_fame, top_winners, yesterday_window
from core.snapshots import capture_open_events, cleanup_old_timepoints
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


async def capture_pool_snapshots(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """每分钟一次池子快照，给走势图供数。"""
    try:
        with get_session() as s:
            n = capture_open_events(s)
        if n:
            logger.debug(f"[snapshots] captured {n} open events")
    except Exception as e:
        logger.warning(f"snapshot capture failed: {e}")


async def cleanup_snapshots(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        with get_session() as s:
            n = cleanup_old_timepoints(s, older_than_days=30)
        if n:
            logger.info(f"[snapshots] cleaned {n} old rows")
    except Exception as e:
        logger.warning(f"snapshot cleanup failed: {e}")


async def hall_of_fame_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """每天 23:00 UTC 推 Top 3 赢家到频道（仅主 bot 实例发，避免矩阵重复）。"""
    if settings.bot_id != "main":
        return
    if not settings.announce_channel_id:
        return
    start, end = yesterday_window()
    with get_session() as s:
        winners = top_winners(s, top_n=3, start=start, end=end)
    text = render_hall_of_fame(winners, start.strftime("%b %d"))
    if not text:
        logger.info("[hof] no winners yesterday, skip")
        return
    try:
        await ctx.bot.send_message(
            chat_id=settings.announce_channel_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        logger.success(f"[hof] posted {len(winners)} winners to channel")
    except Exception as e:
        logger.error(f"hof channel send failed: {e}")


def register_jobs(app) -> None:
    jq = app.job_queue
    jq.run_repeating(auto_lock_events, interval=60, first=10, name="auto_lock_events")
    jq.run_repeating(capture_pool_snapshots, interval=60, first=15, name="capture_pool_snapshots")
    jq.run_daily(daily_event_check, time=time(9, 0, tzinfo=timezone.utc), name="daily_event_check")
    jq.run_daily(hall_of_fame_job, time=time(23, 0, tzinfo=timezone.utc), name="hall_of_fame")
    jq.run_daily(cleanup_snapshots, time=time(3, 0, tzinfo=timezone.utc), name="cleanup_snapshots")
