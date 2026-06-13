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


# ----------------------------- WC Contest Oracle 结算 -----------------------------

async def auto_settle_wc_contest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    世界杯 AI 模型竞猜自动结算。

    条件（全部满足才执行）：
      1. 存在 kind="multi" 且标题包含"世界杯"的事件
      2. 事件状态为 "locked"（已封盘，close_at 已过）
      3. Oracle 能返回胜者
      4. 仅在 main bot 实例运行（避免矩阵重复）
    """
    if settings.bot_id != "main":
        return

    from datetime import datetime, timezone
    from sqlalchemy import select as sa_select

    from db.models import Event
    from core.multi_settlement import settle_multi_event
    from integrations.oracle import resolve_winning_model

    now = datetime.now(timezone.utc)

    with get_session() as s:
        evs = list(s.scalars(
            sa_select(Event).where(
                Event.kind == "multi",
                Event.state == "locked",
            )
        ))
        # 只处理标题含"世界杯"且截止时间已过的竞猜
        target = None
        for ev in evs:
            if "世界杯" in (ev.title or "") and ev.close_at <= now.replace(tzinfo=None):
                target = ev
                break

        if not target:
            return

        logger.info(f"[wc_contest] event#{target.id} is locked, querying Oracle…")
        winning_opt = resolve_winning_model()
        if not winning_opt:
            logger.warning("[wc_contest] Oracle returned None, skipping settlement")
            return

        try:
            summary = settle_multi_event(
                s, target, winning_opt,
                evidence_url="https://telegrowth.ai/vb_accounts",
            )
            logger.success(
                f"[wc_contest] settled event#{target.id} → winner={winning_opt} "
                f"winners={summary['winners']} fee={summary['fee_micro']}"
            )
        except Exception as e:
            logger.error(f"[wc_contest] settlement failed: {e}")
            return

    # 发公告
    if settings.announce_channel_id:
        try:
            await ctx.bot.send_message(
                chat_id=settings.announce_channel_id,
                text=(
                    f"🏆 *世界杯 AI 擂台竞猜 — 已结算！*\n\n"
                    f"获胜模型：*{winning_opt.upper()}*\n"
                    f"赢家数量：{summary.get('winners', 0)}\n"
                    f"总池手续费：{summary.get('fee_micro', 0) / 1_000_000:.2f} USDT\n\n"
                    f"🔗 证据：https://telegrowth.ai/vb_accounts"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"[wc_contest] announce failed: {e}")


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
    # 世界杯 AI 竞猜自动结算（每小时检查一次，截止后用 Oracle 结算）
    jq.run_repeating(auto_settle_wc_contest, interval=3600, first=120, name="auto_settle_wc_contest")
