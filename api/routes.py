"""
api/routes.py
All FastAPI routes. Every /v1/* route (except /v1/health) requires
a valid X-API-Key header, checked via the require_api_key dependency.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as FastAPIPath
from typing import Annotated

from core.database import (
    create_api_key,
    get_audit_logs,
    list_api_keys,
    log_execution,
    revoke_api_key,
)
from core.logging import get_logger
from core.registry import registry
from core.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ExecuteRequest,
    ExecuteResponse,
    HealthResponse,
    ToolSummary,
)
from core.security import generate_api_key, require_api_key, validate_admin_key

logger = get_logger(__name__)

router = APIRouter()


# ── Health (no auth) ─────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    from core.config import get_settings
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        tools_loaded=registry.total_loaded,
        tools_enabled=registry.total_enabled,
        uptime_seconds=time.time() - request.app.state.start_time,
    )


# ── Tool registry ─────────────────────────────────────────────────────────────

@router.get("/tools", tags=["tools"])
async def list_tools(
    enabled_only: bool = True,
    tag: str | None = None,
    _key: dict = Depends(require_api_key),
) -> list[ToolSummary]:
    tools = registry.list_tools(enabled_only=enabled_only)
    if tag:
        tools = [t for t in tools if tag in t.tags]
    return [
        ToolSummary(
            name=t.name,
            version=t.version,
            description=t.description,
            executor_type=t.executor_type,
            enabled=t.enabled,
            tags=t.tags,
            arg_count=len(t.args),
        )
        for t in tools
    ]


@router.get("/tools/{tool_name}", tags=["tools"])
async def get_tool(
    tool_name: Annotated[
        str,
        FastAPIPath(
            min_length=1, max_length=64,
            pattern=r"^[a-z][a-z0-9_]{0,63}$",
            description="Tool name: lowercase letters, digits, underscores only",
        ),
    ],
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    spec = registry.get_spec(tool_name)
    if spec is None:
        raise HTTPException(status_code=404, detail={"error": f"tool not found"})
    return spec.model_dump()


# ── Tool execution ────────────────────────────────────────────────────────────

@router.post("/execute", response_model=ExecuteResponse, tags=["execution"])
async def execute_tool(
    body: ExecuteRequest,
    request: Request,
    key_record: dict = Depends(require_api_key),
) -> ExecuteResponse:
    start = time.monotonic()
    client_ip = request.client.host if request.client else None

    spec = registry.get_spec(body.tool_name)
    if spec is None:
        raise HTTPException(status_code=404, detail={"error": f"tool '{body.tool_name}' not found"})

    try:
        result = await registry.execute(body.tool_name, body.args)
        latency_ms = (time.monotonic() - start) * 1000

        await log_execution(
            key_id=key_record["key_id"],
            tool_name=body.tool_name,
            success=True,
            latency_ms=latency_ms,
            request_ip=client_ip,
        )

        logger.info("tool_executed", extra={"extra": {
            "tool": body.tool_name,
            "latency_ms": round(latency_ms, 1),
            "key_id": key_record["key_id"],
        }})

        return ExecuteResponse(
            tool_name=body.tool_name,
            success=True,
            result=result,
            latency_ms=round(latency_ms, 1),
            executor_type=spec.executor_type,
        )

    except (ValueError, KeyError) as e:
        latency_ms = (time.monotonic() - start) * 1000
        await log_execution(
            key_id=key_record["key_id"],
            tool_name=body.tool_name,
            success=False,
            latency_ms=latency_ms,
            error_summary=str(e)[:300],
            request_ip=client_ip,
        )
        raise HTTPException(status_code=422, detail={"error": "validation_error", "detail": str(e)})

    except RuntimeError as e:
        latency_ms = (time.monotonic() - start) * 1000
        await log_execution(
            key_id=key_record["key_id"],
            tool_name=body.tool_name,
            success=False,
            latency_ms=latency_ms,
            error_summary=str(e)[:300],
            request_ip=client_ip,
        )
        raise HTTPException(status_code=502, detail={"error": "executor_error", "detail": str(e)})


# ── Agent chat ────────────────────────────────────────────────────────────────

@router.post("/agent/chat", response_model=AgentChatResponse, tags=["agent"])
async def agent_chat(
    body: AgentChatRequest,
    key_record: dict = Depends(require_api_key),
) -> AgentChatResponse:
    from api.agent import run_agent
    return await run_agent(body.message, key_record["key_id"])


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit/logs", tags=["audit"])
async def get_logs(
    limit: int = 100,
    tool_name: str | None = None,
    request: Request = None,
) -> list[dict]:
    from core.config import get_settings
    settings = get_settings()

    # Only admin key can view logs (compare to the admin key in settings)
    raw_key = request.headers.get("X-API-Key", "")
    import secrets
    if not secrets.compare_digest(raw_key.encode(), settings.admin_api_key.encode()):
        raise HTTPException(status_code=403, detail={"error": "audit_log_requires_admin_key"})

    return await get_audit_logs(limit=limit, tool_name=tool_name)


# ── API key management ────────────────────────────────────────────────────────

@router.post("/keys", response_model=ApiKeyResponse, tags=["keys"])
async def create_key(
    body: ApiKeyCreateRequest,
    request: Request,
) -> ApiKeyResponse:
    from core.config import get_settings
    settings = get_settings()
    validate_admin_key(request.headers.get("X-API-Key"), settings.admin_api_key)

    raw_key, key_hash = generate_api_key()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    key_id = await create_api_key(key_hash, body.label, body.rate_limit_per_minute)

    logger.info("api_key_created", extra={"extra": {"key_id": key_id, "label": body.label}})
    return ApiKeyResponse(
        key_id=key_id,
        label=body.label,
        raw_key=raw_key,
        rate_limit_per_minute=body.rate_limit_per_minute,
        created_at=now,
        is_active=True,
    )


@router.get("/keys", tags=["keys"])
async def list_keys(request: Request) -> list[dict]:
    from core.config import get_settings
    settings = get_settings()
    validate_admin_key(request.headers.get("X-API-Key"), settings.admin_api_key)
    return await list_api_keys()


@router.delete("/keys/{key_id}", tags=["keys"])
async def revoke_key(key_id: str, request: Request) -> dict:
    from core.config import get_settings
    settings = get_settings()
    validate_admin_key(request.headers.get("X-API-Key"), settings.admin_api_key)
    revoked = await revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error": f"key '{key_id}' not found"})
    logger.info("api_key_revoked", extra={"extra": {"key_id": key_id}})
    return {"revoked": True, "key_id": key_id}


# ── Registry reload (admin only) ──────────────────────────────────────────────

@router.post("/registry/reload", tags=["system"])
async def reload_registry(request: Request) -> dict:
    from core.config import get_settings
    settings = get_settings()
    validate_admin_key(request.headers.get("X-API-Key"), settings.admin_api_key)

    before = registry.total_loaded
    registry.load_all(settings.tools_dir)
    after = registry.total_loaded

    logger.info("registry_reloaded", extra={"extra": {"before": before, "after": after}})
    return {
        "reloaded": True,
        "tools_before": before,
        "tools_after": after,
        "errors": registry.load_errors(),
    }
