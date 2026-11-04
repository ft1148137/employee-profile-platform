import json
import os
import socket
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.domains.model.pi_client import PiModelConfig


@dataclass
class PiHarness:
    config: PiModelConfig
    logs: Path
    requests: list[dict[str, Any]] = field(default_factory=list)
    rate_limited: bool = False
    replies: list[Any] = field(default_factory=list)
    auto_draft: bool = False


@pytest.fixture
def pi_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[PiHarness]:
    harness = PiHarness(
        config=PiModelConfig(
            provider="openai-compatible",
            api="openai-completions",
            id="synthetic",
            baseUrl="http://127.0.0.1",
            api_key=SecretStr("synthetic-api-key"),
        ),
        logs=tmp_path / "pi.log",
    )

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            harness.requests.append(
                {
                    "path": self.path,
                    "headers": {
                        k.lower(): v
                        for k, v in self.headers.items()
                        if k.lower() not in ("authorization", "x-api-key")
                    },
                    "body": request,
                }
            )
            if self.path != "/v1/chat/completions":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"synthetic rejection"}}')
                return
            tool_result = (
                request["messages"][-1]
                if request["messages"][-1]["role"] == "tool"
                else None
            )
            accepted = tool_result and '"accepted":true' in tool_result.get(
                "content", ""
            ).replace(" ", "")
            if harness.rate_limited:
                reply = {"status": 429}
            elif tool_result and (
                accepted or not harness.auto_draft and not harness.replies
            ):
                reply = "已完成"
            elif harness.replies:
                reply = harness.replies.pop(0)
            elif harness.auto_draft and request.get("tools"):
                body = json.loads(
                    next(
                        m["content"] for m in request["messages"] if m["role"] == "user"
                    )[0]["text"]
                )
                reply = {
                    "items": [
                        {
                            "capability_key": body["capabilities"][0]["key"],
                            "statement": "合成能力建议",
                            "level": None,
                            "evidence": [r["ref"] for r in body["records"]],
                            "explanation": "合成工作记录",
                            "uncertainty": "需要本人确认",
                        }
                    ]
                    if body["records"] and body["capabilities"]
                    else []
                }
            elif request.get("tools") and not tool_result:
                reply = {"statement": "合成建议"}
            else:
                reply = "已完成"
            if isinstance(reply, str):
                try:
                    reply = json.loads(reply)
                except ValueError:
                    pass
            if isinstance(reply, dict) and "status" in reply:
                self.send_response(reply["status"])
                self.send_header("Retry-After", str(reply.get("retry_after", 60)))
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"sensitive upstream error"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            is_tool = isinstance(reply, dict)
            calls = (
                reply.get("calls", [{"name": "submit_draft", "arguments": reply}])
                if is_tool
                else []
            )
            delta = (
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": i,
                            "id": f"call-{len(harness.requests)}-{i}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c["arguments"]),
                            },
                        }
                        for i, c in enumerate(calls)
                    ],
                }
                if is_tool
                else {"role": "assistant", "content": reply}
            )
            chunk = {
                "id": "synthetic",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "synthetic",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            chunk["choices"][0].update(
                delta={}, finish_reason="tool_calls" if is_tool else "stop"
            )
            self.wfile.write(
                ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            )

    model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = Thread(target=model_server.serve_forever)
    thread.start()
    harness.config.baseUrl = f"http://127.0.0.1:{model_server.server_port}/v1"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    monkeypatch.setattr(settings, "PI_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(
        settings, "PI_SERVICE_TOKEN", SecretStr("synthetic-service-token")
    )
    root = Path(__file__).resolve().parents[3]
    try:
        with harness.logs.open("w") as logs:
            with subprocess.Popen(
                ["node", "pi/server.mjs"],
                cwd=root,
                stdout=logs,
                stderr=logs,
                env={
                    **os.environ,
                    "PI_SERVICE_TOKEN": "synthetic-service-token",
                    "PORT": str(port),
                    "PI_HOST": "127.0.0.1",
                    "PI_EGRESS_ALLOW_LOOPBACK": "1",
                },
            ) as process:
                try:
                    with httpx.Client(timeout=1, trust_env=False) as client:
                        for _ in range(100):
                            assert process.poll() is None, harness.logs.read_text()
                            try:
                                response = client.get(
                                    settings.PI_URL + "/health",
                                    headers={
                                        "Authorization": "Bearer synthetic-service-token"
                                    },
                                )
                                if response.status_code == 200:
                                    break
                            except httpx.ConnectError:
                                pass
                            time.sleep(0.05)
                        else:
                            raise AssertionError("Pi did not become healthy")
                    yield harness
                finally:
                    process.terminate()
                    process.wait(timeout=5)
    finally:
        model_server.shutdown()
        model_server.server_close()
        thread.join()
