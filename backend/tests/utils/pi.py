import json
import os
import select
import subprocess
from collections.abc import Generator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.domains.model.pi_client import PiModelConfig

SERVICE_TOKEN = "synthetic-service-token"


@dataclass
class PiLogs:
    path: Path
    start: int
    end: int | None = None

    def read_text(self) -> str:
        with self.path.open("rb") as stream:
            stream.seek(self.start)
            return stream.read(
                -1 if self.end is None else self.end - self.start
            ).decode()


@dataclass
class PiHarness:
    config: PiModelConfig
    logs: PiLogs
    requests: list[dict[str, Any]] = field(default_factory=list)
    rate_limited: bool = False
    replies: list[Any] = field(default_factory=list)
    auto_draft: bool = False


@dataclass
class _PiProcess:
    process: subprocess.Popen[bytes]
    ready_fd: int
    model_url: str
    logs: Path
    harness: PiHarness | None = None

    def command(self, command: bytes) -> bytes:
        if self.process.poll() is not None:
            pytest.fail(
                f"Pi exited with code {self.process.returncode}; see {self.logs}"
            )
        assert self.process.stdin is not None
        self.process.stdin.write(command + b"\n")
        self.process.stdin.flush()
        if not select.select([self.ready_fd], [], [], 15)[0]:
            pytest.fail(f"Pi did not respond to {command!r}; see {self.logs}")
        reply = os.read(self.ready_fd, 32).strip()
        if not reply:
            pytest.fail(f"Pi closed its control pipe; see {self.logs}")
        return reply


@pytest.fixture(scope="session")
def _pi_process(tmp_path_factory: pytest.TempPathFactory) -> Generator[_PiProcess]:
    service: _PiProcess

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:
            harness = service.harness
            assert harness is not None
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
                if request["messages"] and request["messages"][-1]["role"] == "tool"
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
                            "level": body["capabilities"][0]["levels"][0]["key"],
                            "evidence": [r["ref"] for r in body["records"]],
                            "explanation": "合成工作记录",
                            "uncertainty": "需要本人确认",
                        }
                    ]
                    if body["records"]
                    and body["capabilities"]
                    and body["capabilities"][0]["levels"]
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
    thread = Thread(target=model_server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    model_url = f"http://127.0.0.1:{model_server.server_port}/v1"
    root = Path(__file__).resolve().parents[3]
    log_path = tmp_path_factory.mktemp("pi") / "pi.log"
    ready_fd, reply_fd = os.pipe()
    try:
        with log_path.open("wb") as logs:
            with subprocess.Popen(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    """
import { once } from 'node:events';
import { writeSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { createPiServer } from './pi/server.mjs';
import { streamSimple } from './pi/runtime.mjs';
let server, loginScope;
const reply = value => writeSync(Number(process.env.TEST_REPLY_FD), String(value) + '\\n');
const login = async ({ onAuth, onManualCodeInput }) => {
  onAuth({ url: 'https://auth.openai.com/oauth/authorize?state=synthetic-state' });
  const input = await Promise.race([onManualCodeInput(),
    once(loginScope.signal, 'abort').then(() => { throw new Error('cancelled'); })]);
  if (input.includes('failure')) throw new Error('synthetic-refresh sensitive upstream');
  return { access: 'synthetic-access', refresh: 'synthetic-refresh', expires: Date.now() + 7200000, accountId: 'synthetic-account' };
};
const refresh = async value => {
  if (value === 'synthetic-rejected-refresh') throw new Error('synthetic-access synthetic-rejected-refresh upstream');
  return { access: 'rotated-access', refresh: 'rotated-refresh', expires: Date.now() + 7200000, accountId: 'synthetic-account' };
};
// Reuse the loaded SDK while giving every case fresh turn and login state.
for await (const command of createInterface({ input: process.stdin })) {
  if (command === 'start' && !server) {
    loginScope = new AbortController();
    server = createPiServer({ token: process.env.PI_SERVICE_TOKEN, login, refresh,
      streamFn: (model, context, options) => streamSimple(model.provider === 'openai-codex'
        ? { ...model, provider: 'openai-compatible', api: 'openai-completions', baseUrl: process.env.TEST_MODEL_URL } : model, context, options)
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    console.log(JSON.stringify({ type: 'listening', port: server.address().port }));
    reply(server.address().port);
  } else if (command === 'stop' && server) {
    loginScope.abort();
    const closed = once(server, 'close');
    server.close();
    server.closeAllConnections();
    await closed;
    server = undefined;
    reply('stopped');
  } else {
    throw new Error('Unexpected Pi test command');
  }
}
""",
                ],
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=logs,
                stderr=logs,
                pass_fds=(reply_fd,),
                env={
                    **os.environ,
                    "PI_SERVICE_TOKEN": SERVICE_TOKEN,
                    "TEST_MODEL_URL": model_url,
                    "TEST_REPLY_FD": str(reply_fd),
                    "PI_EGRESS_ALLOW_LOOPBACK": "1",
                },
            ) as process:
                os.close(reply_fd)
                reply_fd = -1
                service = _PiProcess(process, ready_fd, model_url, log_path)
                try:
                    yield service
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
    finally:
        os.close(ready_fd)
        if reply_fd != -1:
            os.close(reply_fd)
        model_server.shutdown()
        model_server.server_close()
        thread.join()


@pytest.fixture
def pi_service(
    _pi_process: _PiProcess, monkeypatch: pytest.MonkeyPatch
) -> Generator[PiHarness]:
    log = _pi_process.logs
    harness = PiHarness(
        config=PiModelConfig(
            provider="openai-compatible",
            api="openai-completions",
            id="synthetic",
            baseUrl=_pi_process.model_url,
            api_key=SecretStr("synthetic-api-key"),
        ),
        logs=PiLogs(log, log.stat().st_size),
    )
    port = int(_pi_process.command(b"start"))
    _pi_process.harness = harness
    try:
        monkeypatch.setattr(settings, "PI_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(settings, "PI_SERVICE_TOKEN", SecretStr(SERVICE_TOKEN))
        yield harness
    finally:
        try:
            assert _pi_process.command(b"stop") == b"stopped"
        finally:
            _pi_process.harness = None
            harness.logs.end = log.stat().st_size
