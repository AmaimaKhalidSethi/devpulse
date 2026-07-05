"""
executors/registry.py
The executor whitelist — the ONLY mapping from executor_type strings to
real Python classes. If a type isn't here, it cannot be used in any YAML,
full stop. This is the architectural choke point that prevents
arbitrary code execution via malicious tool definitions.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from executors.base import AbstractExecutor

# Populated at bottom of this file after all executor classes are imported.
EXECUTOR_REGISTRY: dict[str, Type["AbstractExecutor"]] = {}


def _build_registry() -> dict[str, Type["AbstractExecutor"]]:
    # Importing here (not at module top) keeps circular imports clean
    from executors.http_get import HttpGetExecutor
    from executors.http_post import HttpPostExecutor
    from executors.python_math import PythonMathExecutor
    from executors.text_transform import TextTransformExecutor
    from executors.datetime_tool import DatetimeExecutor
    from executors.json_transform import JsonTransformExecutor
    from executors.mock_static import MockStaticExecutor

    return {
        "http_get": HttpGetExecutor,
        "http_post": HttpPostExecutor,
        "python_math": PythonMathExecutor,
        "text_transform": TextTransformExecutor,
        "datetime_tool": DatetimeExecutor,
        "json_transform": JsonTransformExecutor,
        "mock_static": MockStaticExecutor,
    }


# Called once at application startup — see app.py lifespan
def init_executor_registry() -> None:
    global EXECUTOR_REGISTRY
    EXECUTOR_REGISTRY.update(_build_registry())
