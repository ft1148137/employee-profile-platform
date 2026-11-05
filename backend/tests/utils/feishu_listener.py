import json
from multiprocessing.queues import Queue
from threading import Event
from uuid import uuid4


def message(kind: str = "p2p") -> dict:
    return {
        "header": {"event_id": uuid4().hex, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_test"}},
            "message": {
                "chat_id": "oc_test",
                "chat_type": kind,
                "message_type": "text",
                "content": json.dumps({"text": "本人合成经历"}),
            },
        },
    }


def fake_listener(_credentials: dict, events: Queue[tuple[str, str]]) -> None:
    events.put_nowait(("event", json.dumps(message())))
    Event().wait(30)
