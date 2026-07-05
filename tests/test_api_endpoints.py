"""
tests/test_api_endpoints.py
Integration tests for the FastAPI REST API using TestClient.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.config import get_settings
from core.database import _set_db_path


@pytest.fixture
def client(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Ensure settings re-read from test environment.
    get_settings.cache_clear()

    temp_dir = tmp_path_factory.mktemp("devpulse_api")
    monkeypatch.setenv("ADMIN_API_KEY", "A" * 32)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_api_key")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(temp_dir / "devpulse_test.db"))

    tools_dir = Path.cwd() / "tools"
    if tools_dir.exists():
        monkeypatch.setenv("TOOLS_DIR", str(tools_dir))

    import importlib
    import app as app_module
    importlib.reload(app_module)

    _set_db_path(temp_dir / "devpulse_test.db")

    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {
        "X-API-Key": "A" * 32,
        "Content-Type": "application/json",
    }


def test_health_endpoint_is_public(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_tools_list_requires_api_key(client: TestClient) -> None:
    response = client.get("/v1/tools?enabled_only=true")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_api_key"


def test_tools_list_returns_available_tools(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/v1/tools?enabled_only=true", headers=admin_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert all("name" in tool for tool in response.json())


def test_tool_detail_endpoint(client: TestClient, admin_headers: dict[str, str]) -> None:
    list_response = client.get("/v1/tools?enabled_only=true", headers=admin_headers)
    assert list_response.status_code == 200
    tools = list_response.json()
    assert tools
    detail_response = client.get(f"/v1/tools/{tools[0]['name']}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["name"] == tools[0]["name"]


def test_create_list_and_revoke_api_key(client: TestClient, admin_headers: dict[str, str]) -> None:
    create_response = client.post(
        "/v1/keys",
        headers=admin_headers,
        json={"label": "integration-test", "rate_limit_per_minute": 60},
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["label"] == "integration-test"
    assert created["is_active"] is True
    assert "raw_key" in created

    list_response = client.get("/v1/keys", headers=admin_headers)
    assert list_response.status_code == 200
    keys = list_response.json()
    assert any(item["key_id"] == created["key_id"] for item in keys)

    revoke_response = client.delete(f"/v1/keys/{created['key_id']}", headers=admin_headers)
    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked"] is True


def test_execute_tool_with_user_key(client: TestClient, admin_headers: dict[str, str]) -> None:
    # Create a temporary user API key for execution
    create_response = client.post(
        "/v1/keys",
        headers=admin_headers,
        json={"label": "exec-test", "rate_limit_per_minute": 60},
    )
    assert create_response.status_code == 200
    user_key = create_response.json()["raw_key"]
    user_headers = {"X-API-Key": user_key, "Content-Type": "application/json"}

    execute_response = client.post(
        "/v1/execute",
        headers=user_headers,
        json={"tool_name": "calculator", "args": {"expression": "2 + 2"}},
    )
    assert execute_response.status_code == 200
    payload = execute_response.json()
    assert payload["success"] is True
    assert payload["result"]["result"] == 4


def test_execute_tool_rejects_invalid_tool(client: TestClient, admin_headers: dict[str, str]) -> None:
    user_key = client.post(
        "/v1/keys",
        headers=admin_headers,
        json={"label": "exec-invalid", "rate_limit_per_minute": 60},
    ).json()["raw_key"]
    response = client.post(
        "/v1/execute",
        headers={"X-API-Key": user_key, "Content-Type": "application/json"},
        json={"tool_name": "does_not_exist", "args": {}},
    )
    assert response.status_code == 404


def test_execute_tool_rejects_bad_args(client: TestClient, admin_headers: dict[str, str]) -> None:
    user_key = client.post(
        "/v1/keys",
        headers=admin_headers,
        json={"label": "exec-invalid-args", "rate_limit_per_minute": 60},
    ).json()["raw_key"]
    response = client.post(
        "/v1/execute",
        headers={"X-API-Key": user_key, "Content-Type": "application/json"},
        json={"tool_name": "calculator", "args": {}},
    )
    assert response.status_code == 422


def test_agent_chat_route_uses_run_agent(client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from core.schemas import AgentChatResponse
    from api import agent as api_agent

    async def fake_run_agent(message: str, key_id: str) -> AgentChatResponse:
        return AgentChatResponse(answer="ok", tools_called=[], turns=1, had_errors=False)

    monkeypatch.setattr(api_agent, "run_agent", fake_run_agent)
    user_key = client.post(
        "/v1/keys",
        headers=admin_headers,
        json={"label": "agent-test", "rate_limit_per_minute": 60},
    ).json()["raw_key"]

    response = client.post(
        "/v1/agent/chat",
        headers={"X-API-Key": user_key, "Content-Type": "application/json"},
        json={"message": "Hello agent"},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "ok"


def test_audit_logs_require_admin_key(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/v1/audit/logs", headers=admin_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_registry_reload_requires_admin_key(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/v1/registry/reload", headers=admin_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["reloaded"] is True
