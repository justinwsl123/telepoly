"""事件卡片渲染（独立出来方便复用：私聊 + 频道 + admin web）。"""
from __future__ import annotations

from datetime import datetime, timezone

from db.models import Event
from core.betting import implied_odds
from core.money import fmt_usdt


def render_event_card(event: Event, lang: str = "en") -> str:
    yes_odds, no_odds = implied_odds(event.pool_yes_micro, event.pool_no_micro, event.fee_bps)
    total_pool = event.pool_yes_micro + event.pool_no_micro

    close_str = event.close_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if lang == "zh":
        return (
            f"🎯 *{event.title}*\n\n"
            f"{event.description or ''}\n\n"
            f"⏰ 截止：{close_str}\n"
            f"💰 总池：{fmt_usdt(total_pool)}\n"
            f"🟢 {event.yes_label}: {fmt_usdt(event.pool_yes_micro, '')}  → 赔率 *{yes_odds:.2f}x*\n"
            f"🔴 {event.no_label}: {fmt_usdt(event.pool_no_micro, '')}  → 赔率 *{no_odds:.2f}x*\n\n"
            f"_提示：早押赔率高，跟风越多赔率越被稀释。_"
        )
    return (
        f"🎯 *{event.title}*\n\n"
        f"{event.description or ''}\n\n"
        f"⏰ Closes: {close_str}\n"
        f"💰 Total pool: {fmt_usdt(total_pool)}\n"
        f"🟢 {event.yes_label}: {fmt_usdt(event.pool_yes_micro, '')}  → *{yes_odds:.2f}x*\n"
        f"🔴 {event.no_label}: {fmt_usdt(event.pool_no_micro, '')}  → *{no_odds:.2f}x*\n\n"
        f"_The earlier you bet, the better the odds._"
    )


def render_settlement_announcement(event: Event, summary: dict, lang: str = "en") -> str:
    from core.money import fmt_usdt
    if event.outcome == "void":
        body_en = f"↩️ *Voided*. All stakes refunded.\n"
        body_zh = f"↩️ *已作废*。全额退款。\n"
    else:
        body_en = (
            f"🏆 Result: *{event.outcome.upper()}*\n"
            f"💰 Pool: {fmt_usdt(event.pool_yes_micro + event.pool_no_micro)}\n"
            f"🏛 Fee (5%): {fmt_usdt(summary.get('fee_micro', 0))}\n"
            f"👥 Winners: {summary.get('winners', 0)} · Losers: {summary.get('losers', 0)}\n"
        )
        body_zh = (
            f"🏆 结果: *{event.outcome.upper()}*\n"
            f"💰 总池: {fmt_usdt(event.pool_yes_micro + event.pool_no_micro)}\n"
            f"🏛 手续费 (5%): {fmt_usdt(summary.get('fee_micro', 0))}\n"
            f"👥 赢家: {summary.get('winners', 0)} · 输家: {summary.get('losers', 0)}\n"
        )
    if lang == "zh":
        head = f"📣 *事件已结算*\n_{event.title}_\n\n"
        evi = f"\n🔗 依据: {event.evidence_url}" if event.evidence_url else ""
        return head + body_zh + evi
    head = f"📣 *Event settled*\n_{event.title}_\n\n"
    evi = f"\n🔗 Evidence: {event.evidence_url}" if event.evidence_url else ""
    return head + body_en + evi
