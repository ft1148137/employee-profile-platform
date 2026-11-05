"""Keep audit filters readable when backend events are added."""

import ast
import re
from pathlib import Path


def test_audit_labels_cover_backend_events() -> None:
    root = Path(__file__).resolve().parents[3]
    labels = (root / "frontend/src/components/Admin/auditLabels.ts").read_text()
    expansions = {
        "appeal.{}": {"open", "upheld", "reopened"},
        "setup.step_{}": {"completed", "skipped"},
        "sync_run.{}": {"succeeded", "failed"},
        "external_identity.{}": {"linked", "unlinked"},
        "account.{}": {"password_reset", "disabled", "enabled"},
    }
    actions: set[str] = {"seed.loaded"}
    targets: set[str] = set()
    for path in (root / "backend/app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {"record_audit", "audit"}:
                continue
            values = {k.arg: k.value for k in node.keywords}
            if node.func.id == "audit" and len(node.args) > 2:
                values["action"] = node.args[2]
            for key, destination in (("action", actions), ("target_type", targets)):
                value = values.get(key)
                if isinstance(value, ast.JoinedStr):
                    pattern = "".join(
                        p.value if isinstance(p, ast.Constant) else "{}"
                        for p in value.values
                    )
                    assert pattern in expansions, f"Expand audit pattern {pattern}"
                    destination.update(pattern.format(v) for v in expansions[pattern])
                elif isinstance(value, ast.IfExp):
                    destination.update(
                        n.value
                        for n in (value.body, value.orelse)
                        if isinstance(n, ast.Constant)
                    )
                elif isinstance(value, ast.Constant):
                    destination.update(
                        n.value
                        for n in ast.walk(value)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    )
    for name, values in (("auditLabels", actions), ("targetLabels", targets)):
        block = labels.split(f"export const {name}")[1].split("}", 1)[0]
        for value in values:
            assert re.search(
                rf'["\s]{re.escape(value)}"?:\s*"[^"\n]*[\u4e00-\u9fff]', block
            ), value
