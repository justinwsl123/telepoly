"""Bot 文案模板（中英双语）。

设计：每条文案给一个 key + 多语言字典。runtime 取 user.lang。
"""
from __future__ import annotations


T = {
    "welcome": {
        "en": (
            "👋 *Welcome to TelePoly* — the daily prediction market on Telegram.\n\n"
            "🎯 *How it works*\n"
            "• One question per day, settled in USDT.\n"
            "• Bet YES or NO before the deadline.\n"
            "• Winners split the losers' pool by share (5% house fee).\n\n"
            "💵 All payments are in *USDT-TRC20*. No fiat, no KYC paperwork.\n\n"
            "Tap below to begin."
        ),
        "zh": (
            "👋 *欢迎来到 TelePoly* — Telegram 上的每日预测市场。\n\n"
            "🎯 *玩法*\n"
            "• 每天一道题，全 USDT 结算。\n"
            "• 截止前自由押 YES / NO。\n"
            "• 赢家按本金比例瓜分输家的钱（5% 平台手续费）。\n\n"
            "💵 仅支持 *USDT-TRC20*，不走法币、不需要复杂 KYC。\n\n"
            "点下方按钮开始。"
        ),
    },
    "age_gate": {
        "en": "⚠️ TelePoly is for users *18 or older* only. By tapping confirm, you agree.",
        "zh": "⚠️ TelePoly 仅向 *18 岁以上* 用户开放。点击下方按钮确认。",
    },
    "no_active_event": {
        "en": "🌙 No event is open right now. The next one drops at 09:00 UTC tomorrow — stay tuned!",
        "zh": "🌙 当前没有进行中的事件。明天 09:00 UTC 将发布新一轮，敬请期待！",
    },
    "balance": {
        "en": "💰 *Balance*: `{bal}` USDT",
        "zh": "💰 *余额*：`{bal}` USDT",
    },
    "insufficient": {
        "en": "❌ Not enough balance. Tap /deposit to top up.",
        "zh": "❌ 余额不足，先 /deposit 充值。",
    },
    "bet_placed": {
        "en": (
            "✅ *Bet placed*\n"
            "Side: *{side}*\n"
            "Amount: `{amt}` USDT\n"
            "Estimated payout if you win: `{payout}` USDT\n"
            "Current odds: *{odds}x*\n\n"
            "Balance left: `{bal}` USDT"
        ),
        "zh": (
            "✅ *下注成功*\n"
            "方向: *{side}*\n"
            "金额: `{amt}` USDT\n"
            "若中奖预估回报: `{payout}` USDT\n"
            "当前赔率: *{odds}x*\n\n"
            "余额剩: `{bal}` USDT"
        ),
    },
    "deposit_info": {
        "en": (
            "💵 *Deposit USDT (TRC20)*\n\n"
            "Send any amount of *USDT on TRON (TRC20)* to:\n"
            "`{address}`\n\n"
            "⚠️ TRC20 only. *Other chains will be lost.*\n"
            "⏱  Funds will credit after 19 confirmations (~1 min).\n\n"
            "_If you have no wallet, tap /wallet for a 1-tap @Wallet option._"
        ),
        "zh": (
            "💵 *USDT 充值（TRC20）*\n\n"
            "请向以下地址转任意金额 *TRON 链 USDT (TRC20)*：\n"
            "`{address}`\n\n"
            "⚠️ 仅支持 TRC20，*其他链转入将丢失*。\n"
            "⏱  19 个确认后自动入账（约 1 分钟）。\n\n"
            "_没有钱包？/wallet 一键调用 @Wallet 充值。_"
        ),
    },
    "deposit_pending": {
        "en": "⏳ Auto-deposit watcher will go live tomorrow. For now, contact ops with your tx hash.",
        "zh": "⏳ 自动扫块明天上线。在那之前，请把转账 tx hash 发给客服人工入账。",
    },
    "settled_won": {
        "en": "🏆 *Event settled — you won!*\n{title}\n+`{payout}` USDT credited.",
        "zh": "🏆 *事件已结算 — 你赢了！*\n{title}\n+`{payout}` USDT 已到账。",
    },
    "settled_lost": {
        "en": "🪦 *Event settled* — outcome was *{outcome}*.\n{title}\nBetter luck tomorrow.",
        "zh": "🪦 *事件已结算* — 结果是 *{outcome}*。\n{title}\n明天再来。",
    },
    "settled_void": {
        "en": "↩️ *Event voided* — your stake was refunded in full.\n{title}",
        "zh": "↩️ *事件作废* — 已全额退款。\n{title}",
    },
}


def t(key: str, lang: str = "en", **fmt) -> str:
    bundle = T.get(key, {})
    text = bundle.get(lang) or bundle.get("en") or key
    return text.format(**fmt) if fmt else text
