"""Run M8 coordination cases sequentially and summarize the comparison."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
CASES = sorted((BACKEND / "tests/eval/planning/cases").glob("*.yaml"))


def model_calls(row: dict[str, Any]) -> int | None:
    count = row.get("model_calls")
    # Older runs initialized failed calls to zero without receiving TurnResult.
    if count is None or (count == 0 and row["status"] != "passed"):
        return None
    return int(count)


def report(rows: list[dict[str, Any]], model: str, thinking: str, fake: bool) -> str:
    # shortcut: Pi omits token usage; add token cost when its protocol exposes it.
    baseline = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/master"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    project = os.getenv("COMPOSE_PROJECT_NAME", "nere741")
    db_port = os.getenv("POSTGRES_PORT", "15741")
    api_port = os.getenv("API_PORT", "18741")
    pi = urlsplit(os.getenv("PI_URL", "http://127.0.0.1:20741"))
    pi_address = f"{pi.hostname}:{pi.port}" if pi.port else str(pi.hostname)
    pi_url = f"{pi.scheme}://{pi_address}"
    command = (
        "uv run python " + shlex.join(sys.argv)
        if Path(sys.argv[0]).name == "eval_coordination.py"
        else f"uv run python scripts/eval_coordination.py --mode both{' --fake' if fake else ''} --model {model} --thinking {thinking} --report ../docs/m8-coordination-eval.md"
    )
    lines = [
        "# M8 协调 Agent 评测",
        "",
        f"- 日期：{date.today().isoformat()}",
        f"- master 基线提交：`{baseline}`",
        f"- 模型：{'脚本假模型' if fake else model}；思考档位：{thinking}",
        f"- 环境：独立 Docker Compose 项目 `{project}`；数据库 `localhost:{db_port}`；后端端口预留 `{api_port}`。"
        + (
            ""
            if fake
            else f" Pi 开发服务通过 `compose.chatgpt-local.yml` 只读挂载本机授权，监听 `{pi_address}`。"
        ),
        f"- 环境变量：`COMPOSE_PROJECT_NAME={project}`、`POSTGRES_PORT={db_port}`、`API_PORT={api_port}`、本任务独立数据库的 `DATABASE_URL`。"
        + (
            ""
            if fake
            else f" 真实模型另设 `FASTAPI_ENV=development`、`CHATGPT_LOCAL_AUTH=true`、`PI_URL={pi_url}`。"
        ),
        f"- 命令：`{command}`",
        "- Pi 当前不向后端返回 token 用量；本次只计模型调用次数、工具调用次数和用时。",
        "- 模型调用次数未取得成功 TurnResult 时记为不可取得（旧结果中失败状态的 0 同样按缺失处理）；仅对已知计数取均值，括号显示已知/已评估数量。全组未知显示 —，不能据此比较成本高低。工具次数和用时仍按实际记录统计。",
        "- 旧失败记录的 0 不是实际零调用；工具次数只覆盖回调已观察到的调用。模型调用均值不能直接作成本优劣比较，failed:model_unavailable 也可能由参数校验、权限或服务异常触发，不能直接归因为供应商故障。",
        "",
        "## 汇总",
        "",
        "| 做法 | 已评估/总数 | 违反约束 | 解析失败 | 模型失败 | 快照外人员 | 经理修改均值 | 修改次数分布 | 模型调用均值 | 工具调用均值 | 用时均值(秒) | 新人方案案例数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for mode, label in (("agent", "纯 Agent"), ("agent_validator", "Agent + 校验器")):
        group = [r for r in rows if r["mode"] == mode]
        done = [r for r in group if not r["status"].startswith("deferred:")]
        known_calls = [r for r in done if model_calls(r) is not None]

        def average(key: str, items: list[dict[str, Any]] = done) -> str:
            return (
                f"{sum(r.get(key, 0) for r in items) / len(items):.2f}"
                if items
                else "—"
            )

        changes = Counter(r.get("manager_changes", "—") for r in done)
        lower_bound = [
            5 if r["manager_changes"] == "5+" else int(r["manager_changes"])
            for r in done
            if r.get("manager_changes") not in (None, "—")
        ]
        distribution = ", ".join(
            f"{n}: {changes[str(n)]}" for n in range(6) if changes[str(n)]
        )
        if changes["5+"]:
            distribution += f"{', ' if distribution else ''}5+: {changes['5+']}"
        if changes["—"]:
            distribution += f"{', ' if distribution else ''}不可计算: {changes['—']}"
        violations = sum(bool(r.get("violation")) for r in done)
        ratio = (
            f"{violations}/{len(done)} ({violations / len(done):.1%})" if done else "—"
        )
        prefix = (
            f"| {label} | {len(done)}/{len(group)} | {ratio} | "
            f"{sum(bool(r.get('parse_failure')) for r in done)} | {sum(r['status'].startswith('failed:') for r in done)} | "
            f"{sum(bool(r.get('outside_candidates')) for r in done)} | "
        )
        lines.append(
            prefix
            + (
                f"≥{sum(lower_bound) / len(lower_bound):.2f}"
                if lower_bound and changes["5+"]
                else f"{sum(lower_bound) / len(lower_bound):.2f}"
                if lower_bound
                else "—"
            )
            + f" | {distribution or '—'} | {average('model_calls', known_calls)} ({len(known_calls)}/{len(done)}) | {average('tool_calls')} | {average('seconds')} | "
            f"{sum(bool(r.get('newcomer')) for r in done)} |"
        )
    lines.extend(
        [
            "",
            "经理修改规则：从最终方案出发，在回滚的保存点内调用 S3 `revise_option`。先把等级不足、离职或超额人员换成快照中已确认周余量总和最大的合格候选人（余量 = 容量 - 其他工作 - 平台分配）；再按 `outside_window` 改回需求时间；最后每轮降低 10 个百分点投入。最多五次，仍达不到规则引擎最好结论记 `5+`；均值以 5 次代入 `5+`，因此是下界。没有可解析最终方案时，修改次数无法计算。",
            "",
            "## 逐案例",
            "",
            "表中 `passed` 表示案例运行完成，不能代表模型达到业务指标；解析失败或模型失败计为违反约束。deferred 不计入违反比例。",
            "",
            "| 案例 | 做法 | 状态 | 基线 | 最终结论 | 违反 | 修改 | 模型/工具调用 | 秒 | 兜底后卡上最好的结论不比规则引擎差 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for r in rows:
        count = model_calls(r)
        fallback = r.get("fallback_not_worse")
        fallback_label = "—" if fallback is None else "是" if fallback else "否"
        lines.append(
            f"| {r['id']} | {r['mode']} | {r['status']} | {r.get('baseline', '—')} | {r.get('verdict', '—')} | "
            f"{'是' if r.get('violation') else '否'} | {r.get('manager_changes', '—')} | "
            f"{count if count is not None else '不可取得'}/{r.get('tool_calls', 0)} | {r.get('seconds', 0):.2f} | "
            f"{fallback_label} |"
        )
    deferred = [
        f"{r['id']} ({r['mode']}: {r['status']})"
        for r in rows
        if r["status"].startswith("deferred")
    ]
    lines.extend(
        [
            "",
            "## 与 M7 基线对比",
            "",
            "M7 的 34 个案例全部通过，出现新人方案的案例数为 2。此表中的违反约束按逐案例规则引擎最好结论比较；出现新人方案数不得少于 2。",
            *(
                f"{label}：结论不更差要求{'满足' if not any(r.get('violation') for r in group) else '未满足'}；"
                f"新人方案 {sum(bool(r.get('newcomer')) for r in group)} 个，"
                f"数量要求{'满足' if sum(bool(r.get('newcomer')) for r in group) >= 2 else '未满足'}。"
                for mode, label in (
                    ("agent", "纯 Agent"),
                    ("agent_validator", "Agent + 校验器"),
                )
                if (
                    group := [
                        r
                        for r in rows
                        if r["mode"] == mode and not r["status"].startswith("deferred:")
                    ]
                )
            ),
            f"待重跑：{', '.join(deferred) if deferred else '无'}。",
            "",
            "硬指标：每个案例的 `capacity_*`、审计、收件箱及 IM 通知行在评测前后相同；工具结果没有其他部门个人字段。",
            "",
            "shortcut: Pi 不回传 token 用量；需要货币成本时升级 Pi 协议。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("agent", "agent_validator", "both"), default="both"
    )
    parser.add_argument("--case")
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--model", default="chatgpt/gpt-6.1-sol")
    parser.add_argument("--thinking", default="high")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.case and args.case not in {p.stem for p in CASES}:
        parser.error(f"未知案例：{args.case}")
    with TemporaryDirectory() as directory:
        result_path = Path(directory) / "results.jsonl"
        env = {
            **os.environ,
            "COORDINATION_EVAL_ALL": "1",
            "COORDINATION_EVAL_CASE": args.case or "",
            "COORDINATION_EVAL_MODE": args.mode,
            "COORDINATION_EVAL_FAKE": "1" if args.fake else "0",
            "COORDINATION_EVAL_MODEL": args.model,
            "COORDINATION_EVAL_THINKING": args.thinking,
            "COORDINATION_EVAL_RESULT": str(result_path),
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/eval/coordination/test_coordination_eval.py",
                "-q",
            ],
            cwd=BACKEND,
            env=env,
            check=False,
        )
        rows = (
            [json.loads(line) for line in result_path.read_text().splitlines()]
            if result_path.exists()
            else []
        )
    if args.report and rows:
        args.report.write_text(report(rows, args.model, args.thinking, args.fake))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)  # noqa: T201
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
