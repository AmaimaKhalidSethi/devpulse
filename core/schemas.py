"""
core/schemas.py
Pydantic v2 schemas for tool definitions, requests, and responses.

ToolSpec is the contract every YAML file must satisfy before the tool is
admitted to the registry. Rejection at load time, not at call time.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Constants ────────────────────────────────────────────────────────────────

# Exhaustive whitelist — a YAML declaring any other type is rejected at load.
EXECUTOR_TYPES = Literal[
    "http_get",
    "http_post",
    "python_math",
    "text_transform",
    "datetime_tool",
    "json_transform",
    "mock_static",
]

# Tool names: lowercase, start with a letter, underscores allowed, max 64 chars.
# Enforced at load time — prevents path-traversal-style names like "../secret".
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ARG_TYPES = Literal["string", "integer", "float", "boolean", "object", "array"]


# ── Tool definition schemas (what lives in a .yaml file) ─────────────────────

class ToolArg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    type: ARG_TYPES
    required: bool = True
    default: Any = None
    description: str = Field(min_length=1, max_length=500)
    enum: list[Any] | None = None  # restricts valid values, validated per-call

    @field_validator("name")
    @classmethod
    def name_is_identifier(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", v):
            raise ValueError(f"arg name '{v}' must be a valid Python identifier")
        return v

    @model_validator(mode="after")
    def default_only_if_not_required(self) -> "ToolArg":
        if self.required and self.default is not None:
            raise ValueError(f"arg '{self.name}': required=True is incompatible with a default value")
        return self


class ToolSpec(BaseModel):
    """Schema every YAML tool definition must satisfy.
    Fields map 1:1 to YAML keys so validation errors name the exact YAML key.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(default="1.0", max_length=16)
    description: str = Field(min_length=10, max_length=1000)
    executor_type: EXECUTOR_TYPES
    enabled: bool = True
    tags: list[str] = Field(default_factory=list, max_length=10)
    args: list[ToolArg] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_matches_pattern(cls, v: str) -> str:
        if not _TOOL_NAME_RE.match(v):
            raise ValueError(
                f"tool name '{v}' must match ^[a-z][a-z0-9_]{{0,63}}$ "
                f"(lowercase, start with letter, underscores allowed)"
            )
        return v

    @field_validator("tags")
    @classmethod
    def tags_are_strings(cls, v: list[str]) -> list[str]:
        for tag in v:
            if not isinstance(tag, str) or len(tag) > 32:
                raise ValueError("each tag must be a string ≤ 32 chars")
        return v

    @model_validator(mode="after")
    def arg_names_unique(self) -> "ToolSpec":
        names = [a.name for a in self.args]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"duplicate arg names: {set(dupes)}")
        return self


# ── API request/response schemas ─────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def tool_name_safe(cls, v: str) -> str:
        if not _TOOL_NAME_RE.match(v):
            raise ValueError(f"invalid tool_name '{v}'")
        return v


class ExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None
    latency_ms: float
    executor_type: str


class ToolSummary(BaseModel):
    """Lightweight listing entry — not the full spec."""
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str
    executor_type: str
    enabled: bool
    tags: list[str]
    arg_count: int


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8192)
    session_id: str | None = None


class AgentChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    tools_called: list[str]
    turns: int
    had_errors: bool


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    version: str
    tools_loaded: int
    tools_enabled: int
    uptime_seconds: float


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str
    label: str
    # Raw key returned ONLY on creation — never stored, never returned again.
    raw_key: str | None = None
    rate_limit_per_minute: int
    created_at: str
    is_active: bool
