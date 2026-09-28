from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkerTask:
    key: str
    payload: dict[str, Any]


WorkerHandler = Callable[[WorkerTask], Awaitable[None]]


class WorkerRegistry:
    """Small in-process dispatch registry; queue infrastructure is intentionally deferred."""

    def __init__(self) -> None:
        self._handlers: dict[str, WorkerHandler] = {}

    def register(self, key: str, handler: WorkerHandler) -> None:
        if key in self._handlers:
            raise ValueError(f"Worker handler already registered: {key}")
        self._handlers[key] = handler

    async def dispatch(self, task: WorkerTask) -> None:
        try:
            handler = self._handlers[task.key]
        except KeyError as exc:
            raise LookupError(f"No worker handler registered for {task.key!r}") from exc
        await handler(task)
