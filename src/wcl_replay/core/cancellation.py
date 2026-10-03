from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar


class TaskCancelled(RuntimeError):
    pass


cancel_check: ContextVar[Callable[[], None] | None] = ContextVar("cancel_check", default=None)


def check_cancelled() -> None:
    check = cancel_check.get()
    if check is not None:
        check()
