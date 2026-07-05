"""
executors/mock_static.py
Returns a static configured response — useful for pipeline testing,
demo tools, and canary checks that the registry itself is working.

config keys:
  response  Required  The value to return (any JSON-serialisable type)
  echo_args bool      If true, merge the call's args into the response
"""
from __future__ import annotations

from typing import Any

from executors.base import AbstractExecutor


class MockStaticExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        cfg = self.spec.config
        response = cfg.get("response", {"status": "ok", "tool": self.spec.name})
        if cfg.get("echo_args", False):
            if isinstance(response, dict):
                return {**response, "echo": args}
            return {"response": response, "echo": args}
        return response
