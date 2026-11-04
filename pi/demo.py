"""Synthetic Python backend; starts one fixed Node process, never a model-supplied command."""

import json
import subprocess
from pathlib import Path
from typing import Any


def main() -> None:
    principals = {"A": "employee-A", "B": "employee-B"}
    results: dict[str, Any] = {}
    waiting: list[dict[str, Any]] = []
    with subprocess.Popen(
        ["node", "sample.mjs"],
        cwd=Path(__file__).parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdin is not None and process.stdout is not None

        def send(event: dict[str, Any]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps(event) + "\n")
            process.stdin.flush()

        for run_id in ("draft", "A", "B"):
            draft = run_id == "draft"
            send(
                {
                    "type": "start",
                    "id": run_id,
                    "mode": "draft" if draft else "chat",
                    "input": [
                        {"ref": "R1", "title": "Synthetic API work"},
                        {"ref": "R2", "title": "Synthetic test work"},
                    ]
                    if draft
                    else {"text": "Show my profile"},
                    "call": {
                        "type": "toolCall",
                        "id": "1",
                        "name": "submit_draft" if draft else "get_my_profile",
                        "arguments": {
                            "statement": "Implemented an API and tests",
                            "evidence": ["R1", "R2"],
                        }
                        if draft
                        else {},
                    },
                }
            )
        while len(results) < 3:
            line = process.stdout.readline()
            assert line, "Node exited without completing all runs"
            event = json.loads(line)
            run_id = event["id"]
            if event["type"] == "tool_call":
                if run_id == "draft":
                    assert event["name"] == "submit_draft"
                    value = event["args"]
                    assert set(value["evidence"]) <= {"R1", "R2"}
                    send({**event, "type": "tool_result", "result": value})
                    print("draft:", json.dumps(value))
                else:
                    assert event["name"] == "get_my_profile" and event["args"] == {}
                    waiting.append(event)
                    if len(waiting) == 2:
                        # Both sessions are suspended at a backend tool in the SAME Node process.
                        for call in reversed(waiting):
                            send(
                                {
                                    **call,
                                    "type": "tool_result",
                                    "result": {"owner": principals[call["id"]]},
                                }
                            )
            else:
                assert event["type"] == "done"
                results[run_id] = event["messages"]
        for run_id, other in (("A", "B"), ("B", "A")):
            transcript = json.dumps(results[run_id])
            assert (
                principals[run_id] in transcript and principals[other] not in transcript
            )
            print(f"chat {run_id}: Python tool -> {principals[run_id]}; isolated")
        process.stdin.close()
        assert process.wait(timeout=10) == 0


if __name__ == "__main__":
    main()
