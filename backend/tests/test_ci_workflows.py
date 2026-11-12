import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
def test_backend_gate_requires_all_shards(result: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "test-backend.yml").read_text())
    shards = workflow["jobs"]["backend-shard"]
    assert shards["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": [1, 2, 3]},
    }
    gate = workflow["jobs"]["test-backend"]
    assert gate["if"] == "${{ always() }}"
    assert gate["needs"] == ["backend-shard"]
    require = gate["steps"][0]
    assert require["env"]["TEST_RESULT"] == "${{ needs.backend-shard.result }}"
    completed = subprocess.run(
        ["bash", "-e", "-c", require["run"]],
        env={**os.environ, "TEST_RESULT": result},
        check=False,
    )
    assert (completed.returncode == 0) is (result == "success")


@pytest.mark.parametrize(
    "changes,changed,shards,success",
    [
        ("success", "true", "success", True),
        ("success", "false", "skipped", True),
        ("success", "true", "failure", False),
        ("success", "true", "cancelled", False),
        ("success", "true", "skipped", False),
        ("failure", "", "skipped", False),
        ("cancelled", "", "skipped", False),
        ("success", "false", "failure", False),
        ("success", "", "skipped", False),
    ],
)
def test_playwright_gate(
    changes: str, changed: str, shards: str, success: bool
) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "playwright.yml").read_text())
    gate = workflow["jobs"]["test-playwright"]
    assert gate["if"] == "${{ always() }}"
    assert set(gate["needs"]) == {"changes", "playwright-shard"}
    result = subprocess.run(
        ["bash", "-e", "-c", gate["steps"][0]["run"]],
        env={
            **os.environ,
            "CHANGES_RESULT": changes,
            "CHANGED": changed,
            "TEST_RESULT": shards,
        },
        check=False,
    )
    assert (result.returncode == 0) is success


def test_required_workflows_have_no_top_level_path_filter() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        # BaseLoader preserves GitHub's 'on' key instead of parsing it as bool.
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert "pull_request" in workflow["on"], path
        trigger = workflow["on"]["pull_request"] or {}
        assert "paths" not in trigger and "paths-ignore" not in trigger, path
