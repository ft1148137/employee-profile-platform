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


@pytest.fixture
def pi_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[PiHarness]:
    harness = PiHarness(
        config=PiModelConfig(
            provider="synthetic",
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
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer synthetic-api-key"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            harness.requests.append(request)
            if harness.rate_limited:
                self.send_response(429)
                self.send_header("Retry-After", "60")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"sensitive upstream error"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            tool_result = next(
                (m for m in request["messages"] if m["role"] == "tool"), None
            )
            delta = (
                {"role": "assistant", "content": "已完成"}
                if tool_result
                else {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "synthetic-call",
                            "type": "function",
                            "function": {
                                "name": "submit_draft",
                                "arguments": json.dumps({"statement": "合成建议"}),
                            },
                        }
                    ],
                }
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
                delta={}, finish_reason="stop" if tool_result else "tool_calls"
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
