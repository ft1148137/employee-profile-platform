"""Create a filtered worktree snapshot and account for observed VM boot intervals."""

import fcntl
import fnmatch
import json
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

METADATA = ".remote-test-snapshot"
EXCLUDED = (
    METADATA,
    ".git",
    ".claude",
    ".agents",
    ".codex",
    ".ssh",
    ".aws",
    ".gcloud",
    ".azure",
    ".gnupg",
    ".kube",
    ".docker",
    ".config",
    ".multica",
    ".auth",
    ".secrets",
    ".credentials",
    ".cache",
    "credentials",
    "secrets",
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "playwright-report",
    "test-results",
    "blob-report",
    ".env",
    ".env.*",
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials*.json",
    "service-account*.json",
    "application_default_credentials.json",
    "cloud-runner*",
    "id_rsa*",
    "id_ed25519*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
)


def allowed(path: bytes) -> bool:
    parts = Path(path.decode(errors="surrogateescape")).parts
    return all(
        part == ".env.example"
        or not any(fnmatch.fnmatchcase(part.lower(), pattern) for pattern in EXCLUDED)
        for part in parts
    )


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "--literal-pathspecs", *args])


def paths(*args: str) -> list[bytes]:
    return git(*args).rstrip(b"\0").split(b"\0")


def snapshot(destination: Path, run_id: str) -> None:
    index = destination / METADATA
    index.mkdir()
    base = git("merge-base", "origin/master", "HEAD").decode().strip()
    (index / "base").write_text(base + "\n")
    for name, revisions in (
        ("committed", (base, "HEAD")),
        ("staged", ("--cached", "HEAD")),
    ):
        diff = ("diff", "--no-renames", "--no-ext-diff", "--no-textconv", *revisions)
        changed = [
            path.decode(errors="surrogateescape")
            for path in paths(*diff, "--name-only", "-z")
            if path and allowed(path)
        ]
        (index / f"{name}.patch").write_bytes(
            git(*diff, "--binary", "--full-index", "--", *changed) if changed else b""
        )

    for raw in set(
        paths("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    ):
        if not raw or not allowed(raw):
            continue
        source = Path(raw.decode(errors="surrogateescape"))
        if source.absolute() != source.resolve():
            raise ValueError(f"Cannot sync symlink or path through symlink: {source}")
        if not source.exists():
            continue
        if not stat.S_ISREG(source.stat().st_mode):
            raise ValueError(f"Cannot sync non-regular file: {source}")
        target = destination / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    blocked = [
        path
        for path in paths("ls-tree", "-r", "--name-only", "-z", base)
        if path and not allowed(path)
    ]
    (index / "excluded").write_bytes(b"".join(path + b"\0" for path in blocked))
    (index / "id").write_text(run_id + "\n")


def estimate(
    ledger_path: Path, instance_json: str, disk: str, rate: str, cap: str, monthly: str
) -> None:
    disk_cost, hourly_rate, monthly_budget = map(Decimal, (disk, rate, monthly))
    soft_cap = Decimal(cap) if cap else monthly_budget * Decimal("0.9")
    if not all(
        value.is_finite() and value >= 0
        for value in (disk_cost, hourly_rate, soft_cap, monthly_budget)
    ):
        raise ValueError("Budget settings must be finite non-negative USD amounts")

    vm = json.loads(instance_json)
    status = vm["status"]
    now = datetime.now(UTC)
    start = vm.get("lastStartTimestamp")
    stop = (
        datetime.fromisoformat(vm["lastStopTimestamp"])
        if vm.get("lastStopTimestamp")
        else None
    )
    ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ledger_path.open("a+", encoding="utf-8") as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.seek(0)
        boots = {}
        for line in ledger:
            event = json.loads(line)
            boots[event["start"]] = (event["end"], event["closed"])

        previous = boots.copy()

        # Only associate a stop with the most recent known start before it.
        if start and stop and datetime.fromisoformat(start) <= stop:
            boots[start] = (stop.isoformat(), True)
        elif stop:
            stopped = max(
                (boot for boot in boots if datetime.fromisoformat(boot) <= stop),
                key=datetime.fromisoformat,
                default=None,
            )
            if stopped and not boots[stopped][1]:
                boots[stopped] = (stop.isoformat(), True)
        if start and status in {"RUNNING", "STOPPING", "SUSPENDING"}:
            end, closed = boots.get(start, (start, False))
            if not closed:
                boots[start] = (
                    max(now, datetime.fromisoformat(end)).isoformat(),
                    False,
                )

        for boot, (end, closed) in boots.items():
            if previous.get(boot) != (end, closed):
                ledger.write(
                    json.dumps({"start": boot, "end": end, "closed": closed}) + "\n"
                )

    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seconds = sum(
        max(
            0,
            (
                min(now, datetime.fromisoformat(end))
                - max(month, datetime.fromisoformat(boot))
            ).total_seconds(),
        )
        for boot, (end, _) in boots.items()
    )
    hours = Decimal(str(seconds)) / 3600
    cost = disk_cost + hours * hourly_rate
    print(
        f"Month (UTC), observed VM uptime: {hours:.2f} h. Estimated cost: ${cost:.2f} USD (soft cap ${soft_cap:.2f})."
    )
    print("at-cap" if cost >= soft_cap else "below-cap")


if __name__ == "__main__":
    try:
        command, *args = sys.argv[1:]
        if command == "snapshot":
            snapshot(Path(args[0]), args[1])
        elif command == "estimate":
            estimate(Path(args[0]), *args[1:])
        else:
            sys.exit("Expected snapshot or estimate")
    except (
        OSError,
        ValueError,
        ArithmeticError,
        KeyError,
        subprocess.CalledProcessError,
    ) as exc:
        sys.exit(f"Remote test metadata: {exc}")
