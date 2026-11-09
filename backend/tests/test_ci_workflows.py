import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


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
