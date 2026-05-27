"""创建所有表（首次启动 / Railway 容器重建用）。

用法：
    uv run python -m db.init
"""
from pathlib import Path

from loguru import logger

from db.models import Base
from db.session import engine
from telepoly_bot.config import settings


def main() -> None:
    # 确保 data/ 目录存在（SQLite 文件路径前置目录）
    if settings.database_url.startswith("sqlite:///"):
        db_path = Path(settings.database_url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(engine)
    logger.success(f"DB initialized at {settings.database_url}")


if __name__ == "__main__":
    main()
