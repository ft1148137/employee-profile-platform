import itertools
import json
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class CIContractTests(unittest.TestCase):
    def test_gate_fails_closed(self) -> None:
        for filter_result, changed, results in itertools.product(
            ("success", "failure", "cancelled", "skipped"),
            ("true", "false", ""),
            itertools.product(("success", "failure", "cancelled", "skipped"), repeat=2),
        ):
            expected = filter_result == "success" and (
                (changed == "true" and results == ("success", "success"))
                or (changed == "false" and results == ("skipped", "skipped"))
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / ".github/scripts/require-tests.sh"),
                    filter_result,
                    changed,
                    *results,
                ],
                check=False,
                capture_output=True,
            )
            self.assertEqual(result.returncode == 0, expected)

    def test_actual_path_patterns(self) -> None:
        filters = [
            yaml.safe_load((ROOT / path).read_text())
            for path in (".github/ci-paths.yml", ".github/ci-deploy-paths.yml")
        ]
        cases = [
            (["docs/setup.md", "CHANGELOG.md"], [False, False, False]),
            (["docs/diagram.svg"], [False, False, False]),
            (["backend/README.md"], [False, False, False]),
            (["templates/profile-import/SKILL.md"], [True, True, False]),
            ([".github/ISSUE_TEMPLATE/bug.yml"], [False, False, False]),
            (["pi/src/server.ts"], [True, False, False]),
            (["pi/Dockerfile"], [True, False, True]),
            (["backend/app/main.py"], [True, True, False]),
            (["frontend/src/main.tsx"], [True, True, False]),
            (["docs/a.md", "backend/app/main.py"], [True, True, False]),
            (["pi/src/server.ts", "frontend/src/main.tsx"], [True, True, False]),
        ]
        for path in (
            "compose.yml",
            "compose.dev.yml",
            "backend/Dockerfile",
            "frontend/Dockerfile.playwright",
            "install.sh",
            "upgrade.sh",
            "backup.sh",
            "restore.sh",
            "deploy/common.sh",
            ".env.example",
            ".github/workflows/test-docker-compose.yml",
            ".github/ci-paths.yml",
            "unknown/new.config",
        ):
            cases.append(([path], [True, True, True]))
        # Use the same matcher and quantifiers as dorny/paths-filter.
        script = """
import picomatch from 'picomatch';
const {filters, cases} = await Bun.stdin.json();
const matches = (paths, rules, every) => paths.some(path => {
  const values = rules.map(rule => picomatch(rule, {dot: true})(path));
  return every ? values.every(Boolean) : values.some(Boolean);
});
for (const [paths, expected] of cases) {
  const [filter, deploy] = filters;
  const actual = [matches(paths, filter.runtime, true),
    matches(paths, filter.playwright, true),
    matches(paths, filter.unknown, true) || matches(paths, deploy.changed, false)];
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw Error(JSON.stringify({paths, actual, expected}));
  }
}
"""
        subprocess.run(
            ["bun", "-e", script],
            cwd=ROOT,
            check=True,
            input=json.dumps({"filters": filters, "cases": cases}),
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
