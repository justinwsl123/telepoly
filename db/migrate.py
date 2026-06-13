"""幂等 Schema 迁移助手（非 Alembic）。

用途：项目使用 create_all 而非 Alembic。对于新部署，create_all 直接建表；
对于已存在的 DB（SQLite 或 Postgres），本模块补丁缺失列。

调用时机：db/init.py 在 Base.metadata.create_all 之后调用 run_migrations。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _col_exists(conn, table: str, col: str) -> bool:
    """跨方言检查列是否存在。"""
    try:
        # PRAGMA 只在 SQLite 可用
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if rows:
            return any(r[1] == col for r in rows)
    except Exception:
        pass
    # Postgres / 通用方案：information_schema
    try:
        r = conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=:t AND column_name=:c"
        ), {"t": table, "c": col}).first()
        return r is not None
    except Exception:
        return False


def run_migrations(engine: Engine) -> None:
    """执行所有幂等迁移步骤。"""
    with engine.begin() as conn:
        # 1. events.kind 列（添加于 v0.2）
        if not _col_exists(conn, "events", "kind"):
            try:
                conn.execute(text(
                    "ALTER TABLE events ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'binary'"
                ))
                logger.info("migration: added events.kind column")
            except Exception as e:
                # 其他引擎可能报 duplicate column，忽略即可
                logger.debug(f"migration events.kind skipped: {e}")

    logger.success("db migrations: up to date")
