from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from app.domains.im.schemas import Platform


@dataclass(frozen=True)
class Event:
    org_id: UUID
    platform: Platform
    event_id: str
    external_user_id: str = ""
    external_chat_id: str = ""
    private: bool = False
    text: str | None = None
    callback_id: str | None = None
    token: str | None = None
    message_id: str | None = None


class ImError(Exception):
    def __init__(self, code: str, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(code)


class Adapter(Protocol):
    platform: ClassVar[Platform]

    def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None: ...
    def poll(self) -> AsyncIterator[Event]: ...
    async def send_text(self, external_chat_id: str, text: str) -> None: ...
    async def render(
        self, external_chat_id: str, text: str, buttons: list[tuple[str, str]]
    ) -> str: ...
    async def answer_callback(self, callback_id: str) -> None: ...
    async def clear_buttons(self, external_chat_id: str, message_id: str) -> None: ...
    async def test_connection(self) -> str: ...
    async def close(self) -> None: ...
