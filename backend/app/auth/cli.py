"""Auth developer CLI: ``python -m app.auth.cli <command>``.

Inspect seeded users and their permissions. Never prints password hashes or plaintext
passwords.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.auth.enums import permissions_for
from app.db.session import get_sessionmaker
from app.models.user import User


def cmd_list_users(_: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            users = await session.scalars(select(User).order_by(User.email))
            for user in users:
                perms = ", ".join(sorted(p.value for p in permissions_for(user.role)))
                print(
                    f"- {user.email:34} {user.role.value:14} "
                    f"active={user.is_active!s:5} perms=[{perms}]"
                )

    asyncio.run(_go())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.auth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users").set_defaults(func=cmd_list_users)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
