"""Bound origin work before it can exhaust the transaction pool."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestAdmission:
    def __init__(self, app: ASGIApp, limit: int = 8, wait_seconds: float = 0.1):
        self.app = app
        self.slots = asyncio.Semaphore(limit)
        self.wait_seconds = wait_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        try:
            async with asyncio.timeout(self.wait_seconds):
                await self.slots.acquire()
        except TimeoutError:
            await JSONResponse(
                {"detail": "Archive is busy; retry shortly"}, status_code=503,
                headers={"Retry-After": "2", "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.slots.release()
