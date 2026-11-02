import argparse
import json
import sys

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.seed.loader import load_demo
from app.seed.schema import SeedError


def main() -> None:
    parser = argparse.ArgumentParser(description="Load synthetic demo data")
    parser.add_argument("command", choices=["load"])
    parser.add_argument(
        "--path", type=type(settings.DEMO_DATA_DIR), default=settings.DEMO_DATA_DIR
    )
    parser.add_argument("--allow-non-dev", action="store_true")
    args = parser.parse_args()
    try:
        with Session(engine) as session:
            result = load_demo(session, args.path, allow_non_dev=args.allow_non_dev)
    except SeedError as exc:
        parser.exit(1, f"{exc}\n")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
