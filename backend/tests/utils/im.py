import json
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from app.domains.im.adapters import telegram


@pytest.fixture
def telegram_server(monkeypatch: pytest.MonkeyPatch) -> Generator[dict]:
    state = {
        "status": 200,
        "body": None,
        "paths": [],
        "requests": [],
        "updates": [],
        "webhook": "",
        "bot_id": 12345,
        "responses": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.handle_request({})

        def do_POST(self) -> None:
            self.handle_request(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )

        def handle_request(self, body: dict) -> None:
            method = self.path.rsplit("/", 1)[-1]
            state["paths"].append(self.path)
            state["requests"].append((method, body))
            result = {
                "getMe": {"id": state["bot_id"], "is_bot": True},
                "getWebhookInfo": {"url": state["webhook"]},
                "getUpdates": list(state["updates"]),
                "sendMessage": {"message_id": 1},
            }.get(method)
            status, response = state["responses"].get(
                method, (state["status"], state["body"])
            )
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(
                    response
                    if response is not None
                    else json.dumps({"ok": True, "result": result}).encode()
                )
            except BrokenPipeError, ConnectionResetError:
                pass

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    original = httpx.AsyncClient.__init__

    def connect(client: httpx.AsyncClient, **kwargs: object) -> None:
        base = str(kwargs.pop("base_url"))
        assert base.startswith("https://api.telegram.org/bot")
        original(
            client,
            base_url=base.replace(
                "https://api.telegram.org", f"http://127.0.0.1:{server.server_port}"
            ),
            **kwargs,
        )

    monkeypatch.setattr(telegram.httpx.AsyncClient, "__init__", connect)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
