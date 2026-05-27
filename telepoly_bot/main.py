"""TelePoly 主 Bot 入口。

启动：
    uv run python -m telepoly_bot.main

环境变量：见 .env.example
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from telegram.ext import Application, ApplicationBuilder

from db.init import main as init_db
from scheduler.jobs import register_jobs
from telepoly_bot.config import settings
from telepoly_bot.handlers import admin, event, me, referral, start, wallet


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

    app = ApplicationBuilder().token(settings.telepoly_bot_token).build()

    start.register(app)
    event.register(app)
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
