"""Guard the approved publication boundary documented in ADR-0035."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
PUBLISH_CALLERS = {
    "domains/review/service.py",
    "domains/review/policy.py",
    "domains/drafting/incremental.py",
}
MESSAGE = "正式画像只能由批准的请求修改，新增写入路径需要先改 ADR"


def profile_writes(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = aliases.get(node.func.id, node.func.id)
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name in {"ProfileClaim", "ProfileVersion", "publish"}:
            calls.append((name, node.lineno))
    return calls


def test_official_profile_write_paths() -> None:
    violations = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        for name, line in profile_writes(path.read_text(encoding="utf-8")):
            allowed = (
                relative in PUBLISH_CALLERS
                if name == "publish"
                else relative == "domains/review/publish.py"
                or relative.startswith("seed/")
            )
            if not allowed:
                violations.append(f"{relative}:{line}: {name}()")
    assert not violations, MESSAGE + "\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ('# ProfileClaim()\ntext = "ProfileVersion(); publish()"', []),
        ("class ProfileClaim: pass\ndef publish(): pass", []),
        (
            "ProfileClaim(); ProfileVersion(); publish()",
            ["ProfileClaim", "ProfileVersion", "publish"],
        ),
        (
            "models.ProfileClaim(); models.ProfileVersion(); publisher.publish()",
            ["ProfileClaim", "ProfileVersion", "publish"],
        ),
        (
            "from app.domains.profile.models import ProfileClaim as Claim\nClaim()",
            ["ProfileClaim"],
        ),
        ("from app.domains.review.publish import publish as emit\nemit()", ["publish"]),
    ],
)
def test_profile_writes_scan_calls(source: str, names: list[str]) -> None:
    assert [name for name, _ in profile_writes(source)] == names
