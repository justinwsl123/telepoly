"""InlineKeyboard 集中管理。"""
from __future__ import annotations

import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def _miniapp_url(event_id: int) -> str | None:
    """Mini App 链接（HTTPS）。MINIAPP_BASE_URL=Railway public URL，未配则不显示按钮。"""
    base = os.getenv("MINIAPP_BASE_URL", "").rstrip("/")
    if not base or not base.startswith("https://"):
        return None
    return f"{base}/miniapp/event/{event_id}"


def event_keyboard(event_id: int, yes_label: str = "YES", no_label: str = "NO",
                   yes_odds: float = 0.0, no_odds: float = 0.0) -> InlineKeyboardMarkup:
    yes_text = f"🟢 {yes_label}" + (f"  {yes_odds:.2f}x" if yes_odds else "")
    no_text  = f"🔴 {no_label}"  + (f"  {no_odds:.2f}x"  if no_odds else "")

    rows = [
        [
            InlineKeyboardButton(yes_text, callback_data=f"bet:{event_id}:yes"),
            InlineKeyboardButton(no_text,  callback_data=f"bet:{event_id}:no"),
        ],
    ]
    mini = _miniapp_url(event_id)
    if mini:
        rows.append([InlineKeyboardButton("📈 Live chart & bet", web_app=WebAppInfo(url=mini))])
    rows += [
        [
            InlineKeyboardButton("📊 详情 / Details", callback_data=f"detail:{event_id}"),
            InlineKeyboardButton("👤 我的 / Me", callback_data="me"),
        ],
        [InlineKeyboardButton("💵 充值 / Deposit", callback_data="deposit")],
    ]
    return InlineKeyboardMarkup(rows)


def amount_keyboard(event_id: int, side: str) -> InlineKeyboardMarkup:
    """快速选金额（USDT）"""
    rows = []
    quick = [1, 5, 10, 50, 100]
    rows.append([InlineKeyboardButton(f"{x} U", callback_data=f"amt:{event_id}:{side}:{x}") for x in quick[:3]])
    rows.append([InlineKeyboardButton(f"{x} U", callback_data=f"amt:{event_id}:{side}:{x}") for x in quick[3:]])
    rows.append([InlineKeyboardButton("✏️ 自定义 / Custom", callback_data=f"amt:{event_id}:{side}:custom")])
    rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=f"detail:{event_id}")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(event_id: int, side: str, amount_usdt: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ 确认押 {amount_usdt} U on {side.upper()}",
                             callback_data=f"confirm:{event_id}:{side}:{amount_usdt}"),
        InlineKeyboardButton("❌ 取消", callback_data=f"detail:{event_id}"),
    ]])


def start_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    if lang == "zh":
        labels = ("📅 今日事件", "💰 我的余额", "💵 充值", "👥 邀请赚钱")
    else:
        labels = ("📅 Today's event", "💰 Balance", "💵 Deposit", "👥 Invite & earn")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="today")],
        [InlineKeyboardButton(labels[1], callback_data="me"),
         InlineKeyboardButton(labels[2], callback_data="deposit")],
        [InlineKeyboardButton(labels[3], callback_data="invite")],
    ])


def age_gate_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    label = "✅ 我已满 18 岁" if lang == "zh" else "✅ I'm 18+"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="age_ok")]])
