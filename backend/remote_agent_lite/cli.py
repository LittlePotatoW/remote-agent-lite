from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from .config import get_settings
from .context import sync_codex_home
from .db import Database
from .security import AuthService
from .server_info import collect_server_info, render_text


async def _set_password(password: str | None) -> int:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    await db.init()
    auth = AuthService(
        db,
        ttl_days=settings.session_ttl_days,
        login_window_seconds=settings.login_window_seconds,
        login_max_failures=settings.login_max_failures,
        login_lock_seconds=settings.login_lock_seconds,
    )
    if password is None:
        password = getpass.getpass("new admin password: ")
        again = getpass.getpass("repeat password: ")
        if password != again:
            print("passwords do not match", file=sys.stderr)
            return 2
    try:
        await auth.set_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("password updated")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="remote-agent-lite")
    sub = parser.add_subparsers(dest="command", required=True)
    password = sub.add_parser("set-password", help="set the single admin password")
    password.add_argument("--password-stdin", action="store_true")
    sub.add_parser("init-context", help="write the Codex-home global AGENTS.md")
    info = sub.add_parser("server-info", help="print server resource information")
    info.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "set-password":
        password_value = None
        if args.password_stdin:
            password_value = sys.stdin.read().rstrip("\n")
        raise SystemExit(asyncio.run(_set_password(password_value)))
    if args.command == "init-context":
        settings = get_settings()
        settings.codex_home.mkdir(parents=True, exist_ok=True)
        print(sync_codex_home(settings))
        return
    if args.command == "server-info":
        data = collect_server_info()
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_text(data))
        return


if __name__ == "__main__":
    main()
