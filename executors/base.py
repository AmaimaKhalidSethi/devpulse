"""executors/base.py — AbstractExecutor that every executor must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.schemas import ToolSpec


class AbstractExecutor(ABC):
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> Any:
        """Execute the tool with validated args. Must be async.
        Return any JSON-serialisable value on success.
        Raise ValueError (user error) or RuntimeError (system error) on failure.
        """
        ...
