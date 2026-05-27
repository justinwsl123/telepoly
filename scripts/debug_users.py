"""One-off: dump the users table to stdout for live debugging.

Usage: python -m scripts.debug_users
"""
from __future__ import annotations

from sqlalchemy import inspect, select

from db.models import User
from db.session import engine, get_session


def main() -> None:
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    print(f"[debug_users] table columns: {cols}")

    with get_session() as s:
        rows = list(s.scalars(select(User).order_by(User.id)))
        print(f"[debug_users] {len(rows)} user row(s):")
        for u in rows:
            print(
                f"  - id={u.id}  tg_user_id={u.tg_user_id}  "
                f"@{u.username}  age_confirmed={u.age_confirmed}  "
                f"balance={u.balance_micro}  created={u.created_at}"
            )


if __name__ == "__main__":
    main()
