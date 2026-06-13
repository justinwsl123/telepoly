"""TelePoly 主 Bot 入口。

启动：
    uv run python -m telepoly_bot.main

环境变量：见 .env.example
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from db.init import main as init_db
from scheduler.jobs import register_jobs
from telepoly_bot.config import settings
from telepoly_bot.handlers import admin, event, me, referral, start, wallet
from telepoly_bot.handlers import multi_event


# Shown in the Telegram blue "Menu" button next to the text input.
# Order = display order. Keep them short — Telegram clips at ~30 chars.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("start",   "Today's market"),
    ("events",  "All open events"),
    ("me",      "My balance & bets"),
    ("deposit", "Deposit USDT"),
    ("invite",  "Invite & earn"),
]


async def _post_init(application: Application) -> None:
    """Register the bot's slash-command menu so Telegram shows the blue Menu button."""
    cmds = [BotCommand(c, d) for c, d in BOT_COMMANDS]
    try:
        await application.bot.set_my_commands(cmds)
        logger.info(f"Menu commands set: {[c for c, _ in BOT_COMMANDS]}")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")


def _setup_logging() -> None:
    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - {message}")
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/telepoly.log", rotation="50 MB", retention="14 days", level="DEBUG")


def build_app() -> Application:
    if not settings.telepoly_bot_token:
        logger.error("TELEPOLY_BOT_TOKEN 未配置，无法启动")
        sys.exit(2)

    app = (
        ApplicationBuilder()
        .token(settings.telepoly_bot_token)
        .post_init(_post_init)
        .build()
    )

    start.register(app)
    event.register(app)
    multi_event.register(app)
    me.register(app)
    wallet.register(app)
    referral.register(app)
    admin.register(app)

    register_jobs(app)
    return app


def main() -> None:
    _setup_logging()
    logger.info("== TelePoly Bot starting ==")
    init_db()  # 幂等创建表
    app = build_app()

    logger.info(f"Bot username (configured): @{settings.telepoly_bot_username}")
    logger.info(f"Owners: {settings.owner_ids}")
    logger.info(f"Announce channel: {settings.announce_channel_id or '(none)'}")
    logger.info("Polling…")

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
