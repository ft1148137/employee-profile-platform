from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class SecretSafeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                # FastAPI's default validation response echoes the input, including keys.
                raise HTTPException(
                    422,
                    [{k: e[k] for k in ("loc", "msg", "type")} for e in error.errors()],
                ) from None

        return handle
