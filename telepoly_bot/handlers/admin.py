"""运营命令（仅 OWNER_TG_IDS 可用）。

/admin                       — 帮助
/admin_new <close_in_h> <title> | <description>
                              — 创建 draft，标题/描述用 | 分隔
/admin_publish <event_id>    — draft → open，并推送到 ANNOUNCE_CHANNEL_ID
/admin_lock <event_id>       — open → locked（手动封盘，通常自动）
/admin_settle <event_id> <yes|no|void> [evidence_url]
                              — 结算，自动按比例分钱 + 频道公告 + 私信通知赢家
/admin_topup <user_id> <usdt>
                              — 临时人工对账入账（Day 2 接自动后停用）
/admin_events                — 列出近 10 个事件
"""
from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import desc, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from db.models import Bet, Event, User
from db.session import get_session
from core.events import (
    create_event,
    lock_event as do_lock,
    open_event as do_open,
)
from core.ledger import record_ledger
from core.money import micro_to_usdt, usdt_to_micro
from core.settlement import settle_event
from telepoly_bot.config import settings
from telepoly_bot.views import render_event_card, render_settlement_announcement


def _is_owner(uid: int) -> bool:
    return uid in settings.owner_ids


def _deny():
    return "🚫 仅运营可用 / Owners only."


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    await update.message.reply_markdown(
        "*Admin commands*\n"
        "`/admin_new <hours> <title> | <description>`\n"
        "`/admin_publish <id>`\n"
        "`/admin_lock <id>`\n"
        "`/admin_settle <id> <yes|no|void> [evidence_url]`\n"
        "`/admin_topup <user_id> <usdt>`\n"
        "`/admin_events`"
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    raw = update.message.text or ""
    # 期望: /admin_new 24 BTC > $120k? | 截止UTC midnight, evidence: coinmarketcap
    try:
        body = raw.split(maxsplit=1)[1]
        hours_str, rest = body.split(maxsplit=1)
        hours = float(hours_str)
        if "|" in rest:
            title, desc = rest.split("|", 1)
        else:
            title, desc = rest, ""
    except Exception:
        await update.message.reply_text("用法 / Usage: /admin_new 24 标题 | 描述")
        return

    close_at = datetime.utcnow() + timedelta(hours=hours)
    with get_session() as s:
        ev = create_event(
            s, title=title.strip(), description=desc.strip(),
            close_at=close_at,
            fee_bps=settings.event_fee_bps,
            created_by=update.effective_user.id,
        )
        ev_id = ev.id
        text = render_event_card(ev, "en")

    await update.message.reply_markdown(
        f"📝 Draft #{ev_id} created (closes in {hours}h)\n\n{text}\n\n"
        f"用 `/admin_publish {ev_id}` 发布到频道。"
    )


async def cmd_publish(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    try:
        ev_id = int((update.message.text or "").split()[1])
    except Exception:
        await update.message.reply_text("用法: /admin_publish <id>"); return

    with get_session() as s:
        ev = s.get(Event, ev_id)
        if not ev:
            await update.message.reply_text("找不到事件"); return
        if ev.state == "draft":
            do_open(s, ev)
        elif ev.state != "open":
            await update.message.reply_text(f"事件状态 {ev.state} 不能发布"); return
        text = render_event_card(ev, "en")

        # 推到频道
        if settings.announce_channel_id:
            from telepoly_bot.keyboards import event_keyboard
            from core.betting import implied_odds
            yes_odds, no_odds = implied_odds(ev.pool_yes_micro, ev.pool_no_micro, ev.fee_bps)
            kb = event_keyboard(ev.id, ev.yes_label, ev.no_label, yes_odds, no_odds)
            try:
                m = await ctx.bot.send_message(
                    chat_id=settings.announce_channel_id,
                    text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
                )
                ev.announce_channel_id = settings.announce_channel_id
                ev.announce_message_id = m.message_id
            except Exception as e:
                logger.error(f"channel announce failed: {e}")
                await update.message.reply_text(f"⚠️ 推频道失败：{e}")

    await update.message.reply_text(f"✅ Event #{ev_id} published.")


async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    try:
        ev_id = int((update.message.text or "").split()[1])
    except Exception:
        await update.message.reply_text("用法: /admin_lock <id>"); return
    with get_session() as s:
        ev = s.get(Event, ev_id)
        if not ev:
            await update.message.reply_text("找不到事件"); return
        do_lock(s, ev)
    await update.message.reply_text(f"🔒 Event #{ev_id} locked.")


async def cmd_settle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    parts = (update.message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await update.message.reply_text("用法: /admin_settle <id> <yes|no|void> [evidence_url]")
        return
    try:
        ev_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("id 无效"); return
    outcome = parts[2].lower()
    evidence = parts[3] if len(parts) >= 4 else None

    with get_session() as s:
        ev = s.get(Event, ev_id)
        if not ev:
            await update.message.reply_text("找不到事件"); return
        try:
            summary = settle_event(s, ev, outcome, evidence_url=evidence)
        except Exception as e:
            await update.message.reply_text(f"❌ 结算失败：{e}"); return

        announce_text = render_settlement_announcement(ev, summary, "en")
        winners = [
            (s.get(User, b.user_id), b)
            for b in s.scalars(select(Bet).where(Bet.event_id == ev.id, Bet.status.in_(("won", "refunded"))))
        ]
        ev_title = ev.title
        ev_outcome = ev.outcome
        announce_chan = ev.announce_channel_id
        announce_msg = ev.announce_message_id

    # 频道公告
    if announce_chan:
        try:
            await ctx.bot.send_message(announce_chan, announce_text, parse_mode=ParseMode.MARKDOWN,
                                       reply_to_message_id=announce_msg)
        except Exception as e:
            logger.error(f"channel settle announce failed: {e}")

    # 私信通知每个赢家/退款用户
    from telepoly_bot.texts import t
    for user, bet in winners:
        if not user:
            continue
        lang = user.lang if user.lang in ("en", "zh") else "en"
        if bet.status == "won":
            txt = t("settled_won", lang, title=ev_title,
                    payout=f"{micro_to_usdt(bet.payout_micro):.2f}")
        else:
            txt = t("settled_void", lang, title=ev_title)
        try:
            await ctx.bot.send_message(user.tg_user_id, txt, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"DM to {user.tg_user_id} failed: {e}")

    await update.message.reply_text(
        f"✅ Settled #{ev_id} as {ev_outcome}. winners={summary['winners']} fee={micro_to_usdt(summary.get('fee_micro',0)):.2f}U"
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """人工对账入账（Day 2 接自动扫块后停用）。"""
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    try:
        _, uid_str, usdt_str = (update.message.text or "").split()
        target_uid = int(uid_str)
        amt = usdt_to_micro(usdt_str)
    except Exception:
        await update.message.reply_text("用法: /admin_topup <tg_user_id> <usdt>"); return

    with get_session() as s:
        user = s.scalars(select(User).where(User.tg_user_id == target_uid)).first()
        if not user:
            await update.message.reply_text("用户不存在（要先 /start）"); return
        record_ledger(s, user=user, delta_micro=amt, reason="deposit",
                      note=f"manual topup by admin {update.effective_user.id}")
        bal = user.balance_micro

    await update.message.reply_text(
        f"✅ 已入账 {usdt_str} USDT 给 user_id={target_uid}，新余额 {micro_to_usdt(bal):.2f}U"
    )
    try:
        await ctx.bot.send_message(target_uid,
            f"💵 已为你入账 *{usdt_str} USDT*。/me 查看余额。", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text(_deny()); return
    with get_session() as s:
        rows = list(s.scalars(select(Event).order_by(desc(Event.id)).limit(10)))
        lines = []
        for ev in rows:
            tot = ev.pool_yes_micro + ev.pool_no_micro
            lines.append(
                f"#{ev.id} [{ev.state}] {ev.title[:40]}  pool={micro_to_usdt(tot):.2f}U "
                f"yes={micro_to_usdt(ev.pool_yes_micro):.2f} no={micro_to_usdt(ev.pool_no_micro):.2f} "
                f"close={ev.close_at:%Y-%m-%d %H:%M}"
            )
    await update.message.reply_text("\n".join(lines) or "无事件")


def register(app) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("admin_new", cmd_new))
    app.add_handler(CommandHandler("admin_publish", cmd_publish))
    app.add_handler(CommandHandler("admin_lock", cmd_lock))
    app.add_handler(CommandHandler("admin_settle", cmd_settle))
    app.add_handler(CommandHandler("admin_topup", cmd_topup))
    app.add_handler(CommandHandler("admin_events", cmd_events))
