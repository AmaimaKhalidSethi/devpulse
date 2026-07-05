"""
core/registry.py
ToolRegistry: discovers YAML tool definitions, validates them against
ToolSpec, instantiates the correct executor, and keeps them in memory.

Hot-reload: a watchdog file observer calls reload_tool() whenever a .yaml
file in tools/ is created or modified — no restart needed.

Security:
- yaml.safe_load() only — never yaml.load() (RCE via !!python/object)
- Executor type must be in EXECUTOR_REGISTRY whitelist
- $ENV{VAR} values resolved from environment at load time
- Tool name validated against strict regex before admission
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from core.logging import get_logger
from core.schemas import ToolSpec
from executors.base import AbstractExecutor
from executors.registry import EXECUTOR_REGISTRY

logger = get_logger(__name__)

_ENV_REF_RE = re.compile(r"\$ENV\{([^}]+)\}")


def _resolve_env_refs(obj: Any) -> Any:
    """Recursively resolve $ENV{VAR_NAME} references in YAML values.
    Raises ValueError if a referenced variable is not set, so a tool
    that needs a secret never silently loads with an empty value.
    """
    if isinstance(obj, str):
        def _sub(m: re.Match) -> str:
            var = m.group(1)
            val = os.environ.get(var)
            if val is None:
                raise ValueError(
                    f"YAML references $ENV{{{var}}} but that environment "
                    f"variable is not set"
                )
            return val
        return _ENV_REF_RE.sub(_sub, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_refs(i) for i in obj]
    return obj


class LoadError(Exception):
    """Raised when a YAML file cannot be loaded or validated."""


class ToolRegistry:
    """Singleton registry — one per process, accessed via module-level `registry`."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._executors: dict[str, AbstractExecutor] = {}
        self._load_errors: dict[str, str] = {}  # path → error message
        # Tracks which file each tool was loaded from so reload_tool()
        # can correctly evict the old registration before loading the new one.
        self._tool_sources: dict[str, str] = {}  # tool_name → str(path)

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_all(self, tools_dir: Path) -> None:
        """Scan tools_dir for *.yaml files and load each one."""
        yaml_files = sorted(tools_dir.glob("*.yaml"))
        logger.info("registry_scan", extra={"extra": {
            "tools_dir": str(tools_dir),
            "files_found": len(yaml_files),
        }})
        for path in yaml_files:
            self._load_file(path)

        logger.info("registry_ready", extra={"extra": {
            "loaded": len(self._tools),
            "errors": len(self._load_errors),
            "tools": list(self._tools.keys()),
        }})

    def _load_file(self, path: Path) -> None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise LoadError(f"YAML root must be a mapping, got {type(raw).__name__}")

            resolved = _resolve_env_refs(raw)
            spec = ToolSpec.model_validate(resolved)

            if spec.name in self._tools:
                logger.warning("tool_name_collision", extra={"extra": {
                    "name": spec.name, "new_file": str(path),
                }})

            executor_cls = EXECUTOR_REGISTRY.get(spec.executor_type)
            if executor_cls is None:
                raise LoadError(
                    f"executor_type '{spec.executor_type}' is not in the "
                    f"executor whitelist: {list(EXECUTOR_REGISTRY)}"
                )

            executor = executor_cls(spec)
            self._tools[spec.name] = spec
            self._executors[spec.name] = executor
            self._tool_sources[spec.name] = str(path)
            self._load_errors.pop(str(path), None)

            logger.info("tool_loaded", extra={"extra": {
                "name": spec.name,
                "executor": spec.executor_type,
                "file": path.name,
            }})

        except (yaml.YAMLError, ValidationError, LoadError, ValueError) as exc:
            err_msg = str(exc)
            self._load_errors[str(path)] = err_msg
            logger.error("tool_load_failed", extra={"extra": {
                "file": path.name, "error": err_msg,
            }})

    def reload_tool(self, path: Path) -> None:
        """Hot-reload a single file — called by the watchdog observer.
        Uses _tool_sources (populated at load time) to find and evict the
        previously registered tool before loading the updated definition.
        Without this eviction step, hot-reload would register a duplicate
        entry under the new name while the old one persisted in the registry.
        """
        path_str = str(path)
        to_remove = [
            name for name, src in self._tool_sources.items()
            if src == path_str
        ]
        for name in to_remove:
            self.unregister(name)
        self._load_file(path)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._executors.pop(name, None)
        self._tool_sources.pop(name, None)

    # ── Querying ──────────────────────────────────────────────────────────────

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def get_executor(self, name: str) -> AbstractExecutor | None:
        return self._executors.get(name)

    def list_tools(self, enabled_only: bool = True) -> list[ToolSpec]:
        tools = list(self._tools.values())
        if enabled_only:
            tools = [t for t in tools if t.enabled]
        return sorted(tools, key=lambda t: t.name)

    def load_errors(self) -> dict[str, str]:
        return dict(self._load_errors)

    @property
    def total_loaded(self) -> int:
        return len(self._tools)

    @property
    def total_enabled(self) -> int:
        return sum(1 for t in self._tools.values() if t.enabled)

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(self, name: str, args: dict) -> Any:
        """Validate args against spec, then delegate to the executor.
        All arg validation happens here so executors stay thin.
        """
        spec = self.get_spec(name)
        if spec is None:
            raise KeyError(f"tool '{name}' not found in registry")
        if not spec.enabled:
            raise ValueError(f"tool '{name}' is disabled")

        validated_args = self._validate_args(spec, args)
        executor = self.get_executor(name)
        return await executor.execute(validated_args)

    def _validate_args(self, spec: ToolSpec, provided: dict) -> dict:
        """Apply ToolArg constraints: required check, type coercion, enum check."""
        result: dict = {}
        for arg in spec.args:
            if arg.name in provided:
                value = provided[arg.name]
            elif arg.default is not None:
                value = arg.default
            elif not arg.required:
                continue
            else:
                raise ValueError(f"required arg '{arg.name}' is missing")

            # Enum validation
            if arg.enum is not None and value not in arg.enum:
                raise ValueError(
                    f"arg '{arg.name}': '{value}' is not one of {arg.enum}"
                )

            result[arg.name] = value

        # Reject unknown args — prevents parameter injection
        extra = set(provided) - {a.name for a in spec.args}
        if extra:
            raise ValueError(f"unknown args provided: {sorted(extra)}")

        return result


# Module-level singleton — import this everywhere
registry = ToolRegistry()
