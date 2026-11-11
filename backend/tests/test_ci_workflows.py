import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@pytest.mark.parametrize(
    "changes,changed,prepare,shards,success",
    [
        ("success", "true", "success", "success", True),
        ("success", "false", "skipped", "skipped", True),
        ("success", "true", "success", "failure", False),
        ("success", "true", "success", "cancelled", False),
        ("success", "true", "success", "skipped", False),
        ("failure", "", "skipped", "skipped", False),
        ("cancelled", "", "skipped", "skipped", False),
        ("success", "false", "skipped", "failure", False),
        ("success", "", "skipped", "skipped", False),
        ("success", "true", "failure", "skipped", False),
        ("success", "true", "cancelled", "skipped", False),
    ],
)
def test_playwright_gate(
    changes: str, changed: str, prepare: str, shards: str, success: bool
) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "playwright.yml").read_text())
    gate = workflow["jobs"]["test-playwright"]
    assert gate["if"] == "${{ always() }}"
    assert set(gate["needs"]) == {"changes", "playwright-prepare", "playwright-shard"}
    result = subprocess.run(
        ["bash", "-e", "-c", gate["steps"][-1]["run"]],
        cwd=WORKFLOWS.parents[1],
        env={
            **os.environ,
            "FILTER": changes,
            "CHANGED": changed,
            "PREPARE": prepare,
            "TESTS": shards,
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
