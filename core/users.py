"""用户获取/创建 + 余额工具。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import User


def get_or_create_user(
    session: Session,
    *,
    tg_user_id: int,
    bot_id: str = "main",
    username: str | None = None,
    first_name: str | None = None,
    lang: str = "en",
    referrer_code: str | None = None,
    source_channel: str | None = None,
) -> tuple[User, bool]:
    """返回 (user, created)。"""
    stmt = select(User).where(User.tg_user_id == tg_user_id)
    user = session.scalars(stmt).first()
    if user:
        user.last_seen_at = datetime.utcnow()
        if username and user.username != username:
            user.username = username
        return user, False

    # 默认从 settings.bot_id 取（矩阵中每个实例不一样），允许显式参数覆盖
    if bot_id == "main":
        try:
            from telepoly_bot.config import settings
            bot_id = settings.bot_id or "main"
        except Exception:
            pass

    user = User(
        tg_user_id=tg_user_id,
        bot_id=bot_id,
        username=username,
        first_name=first_name,
        lang=lang,
        referrer_code=referrer_code,
        source_channel=source_channel,
        last_seen_at=datetime.utcnow(),
    )
    session.add(user)
    session.flush()
    return user, True
