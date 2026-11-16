"""Run the deterministic Planning cases and optionally write a Markdown baseline."""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
CASES = sorted((BACKEND / "tests/eval/planning/cases").glob("*.yaml"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run one YAML case ID")
    parser.add_argument("--report", type=Path, help="Write the Markdown baseline")
    args = parser.parse_args()
    if args.case and args.case not in {path.stem for path in CASES}:
        parser.error(f"Unknown case: {args.case}")
    with TemporaryDirectory() as temporary:
        result_path = Path(temporary) / "results.jsonl"
        environment = {**os.environ, "PLANNING_EVAL_RESULT": str(result_path)}
        if args.case:
            environment["PLANNING_EVAL_CASE"] = args.case
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/eval/planning/test_planning_eval.py",
            "-q",
        ]
        completed = subprocess.run(command, cwd=BACKEND, env=environment, check=False)
        if completed.returncode:
            return completed.returncode
        rows = [json.loads(line) for line in result_path.read_text().splitlines()]
    if args.report:
        categories: dict[str, list[dict]] = defaultdict(list)
        verdicts: Counter[str] = Counter()
        for row in rows:
            categories[row["category"]].append(row)
            verdicts.update(row["verdicts"])
        master = subprocess.run(
            ["git", "rev-parse", "origin/master"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        lines = [
            "# M7 Planning 规则引擎评测基线",
            "",
            f"- 生成日期：{date.today().isoformat()}",
            f"- master 提交：`{master}`",
            f"- 案例：{len(rows)}，通过：{len(rows)}",
            f"- 每案例方案数均值：{sum(row['options'] for row in rows) / len(rows):.2f}",
            f"- 出现新人方案的案例数：{sum(row['newcomer'] for row in rows)}",
            "",
            "## 分类结果",
            "",
            "| 类别 | 案例数 | 通过数 |",
            "| --- | ---: | ---: |",
            *(
                f"| {category} | {len(items)} | {len(items)} |"
                for category, items in sorted(categories.items())
            ),
            "",
            "## 方案结论分布",
            "",
            "| 结论 | 方案数 |",
            "| --- | ---: |",
            *(
                f"| {verdict} | {verdicts[verdict]} |"
                for verdict in (
                    "feasible",
                    "conditional",
                    "infeasible",
                    "insufficient_data",
                )
            ),
            "",
            "这是规则引擎的基线。M8 Agent 在同一套案例上提出的方案经 Validator 后，结论不能更差，新人方案不能少。",
            "",
        ]
        args.report.write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
