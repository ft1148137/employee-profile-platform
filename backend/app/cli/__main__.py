import argparse

from app.cli.setup_link import setup_link


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup-link")
    setup.add_argument("--base-url")
    setup.add_argument("--new-org", action="store_true")
    args = parser.parse_args()
    return setup_link(base_url=args.base_url, new_org=args.new_org)


if __name__ == "__main__":
    raise SystemExit(main())
