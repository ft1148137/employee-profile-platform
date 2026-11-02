import argparse

from app.cli.reset_password import reset_password
from app.cli.setup_link import setup_link


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup-link")
    setup.add_argument("--base-url")
    setup.add_argument("--new-org", action="store_true")
    reset = commands.add_parser("reset-password")
    reset.add_argument("username")
    args = parser.parse_args()
    if args.command == "reset-password":
        return reset_password(args.username)
    return setup_link(base_url=args.base_url, new_org=args.new_org)


if __name__ == "__main__":
    raise SystemExit(main())
