from __future__ import annotations

import argparse
import getpass

from rahband.config import get_settings
from rahband.services.auth import AuthStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Rahband web users")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="create a user or reset its password")
    add.add_argument("username")
    disable = subparsers.add_parser("disable", help="disable a user and revoke sessions")
    disable.add_argument("username")
    enable = subparsers.add_parser("enable", help="enable a user")
    enable.add_argument("username")
    subparsers.add_parser("list", help="list users without password data")
    args = parser.parse_args()
    store = AuthStore(get_settings().auth_database_path, get_settings().auth_session_hours)
    if args.command == "add":
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
        store.upsert_user(args.username, password)
        print(f"User '{args.username}' is ready.")
    elif args.command in {"enable", "disable"}:
        if not store.set_enabled(args.username, args.command == "enable"):
            raise SystemExit("User not found")
        print(f"User '{args.username}' {args.command}d.")
    else:
        for user in store.list_users():
            print(f"{user['username']}\t{'enabled' if user['enabled'] else 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
