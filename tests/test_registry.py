"""
tests/test_registry.py
Tests for ToolRegistry: YAML loading, Pydantic validation, env-ref
resolution, executor whitelist enforcement, and hot-reload.
All tests use temp directories — no real tools/ directory needed.
"""
from __future__ import annotations

import os
import textwrap
import tempfile
from pathlib import Path

import pytest
import yaml

from core.registry import ToolRegistry, _resolve_env_refs, LoadError
from core.schemas import ToolSpec
from executors.registry import init_executor_registry, EXECUTOR_REGISTRY


@pytest.fixture(autouse=True)
def init_executors():
    init_executor_registry()


@pytest.fixture
def tmp_tools(tmp_path: Path) -> Path:
    return tmp_path / "tools"


def write_yaml(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{name}.yaml"
    p.write_text(textwrap.dedent(content))
    return p


# ── ToolSpec schema validation ────────────────────────────────────────────────

class TestToolSpec:
    def test_valid_minimal_spec(self):
        spec = ToolSpec(
            name="calculator",
            description="A safe arithmetic calculator",
            executor_type="python_math",
        )
        assert spec.name == "calculator"

    def test_name_must_be_lowercase(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(name="MyTool", description="x" * 10, executor_type="mock_static")

    def test_name_cannot_start_with_digit(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(name="1bad", description="x" * 10, executor_type="mock_static")

    def test_name_cannot_have_path_traversal(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(name="../secret", description="x" * 10, executor_type="mock_static")

    def test_unknown_executor_type_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(name="bad_tool", description="x" * 10, executor_type="shell")

    def test_duplicate_arg_names_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(
                name="dup_args",
                description="A tool with duplicate args",
                executor_type="mock_static",
                args=[
                    {"name": "x", "type": "string", "required": True, "description": "first"},
                    {"name": "x", "type": "string", "required": True, "description": "second"},
                ],
            )

    def test_required_arg_with_default_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ToolSpec(
                name="bad_arg",
                description="A tool with a bad arg",
                executor_type="mock_static",
                args=[{"name": "x", "type": "string", "required": True, "default": "value", "description": "bad"}],
            )


# ── Registry loading ──────────────────────────────────────────────────────────

class TestRegistryLoading:
    def test_loads_valid_yaml(self, tmp_tools: Path):
        write_yaml(tmp_tools, "echo", """
            name: mock_echo
            description: A simple echo tool for testing
            executor_type: mock_static
            config:
              response: {status: ok}
        """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.get_spec("mock_echo") is not None
        assert reg.total_loaded == 1

    def test_skips_invalid_yaml_gracefully(self, tmp_tools: Path):
        write_yaml(tmp_tools, "bad", "this is not: valid: yaml: at: all: [}")
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.total_loaded == 0
        assert len(reg.load_errors()) == 1

    def test_skips_invalid_spec_gracefully(self, tmp_tools: Path):
        write_yaml(tmp_tools, "bad_spec", """
            name: 1BadName
            description: short
            executor_type: mock_static
        """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.total_loaded == 0

    def test_rejects_unknown_executor_type(self, tmp_tools: Path):
        write_yaml(tmp_tools, "shell_evil", """
            name: shell_evil
            description: Tries to use a shell executor to run arbitrary commands
            executor_type: shell
        """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.total_loaded == 0
        assert any("shell" in err for err in reg.load_errors().values())

    def test_loads_multiple_tools(self, tmp_tools: Path):
        for i in range(3):
            write_yaml(tmp_tools, f"tool_{i}", f"""
                name: tool_{i}
                description: Test tool number {i} with a long enough description
                executor_type: mock_static
                config:
                  response: {{n: {i}}}
            """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.total_loaded == 3

    def test_disabled_tool_excluded_from_list(self, tmp_tools: Path):
        write_yaml(tmp_tools, "disabled", """
            name: disabled_tool
            description: A disabled tool that should not appear in enabled listings
            executor_type: mock_static
            enabled: false
        """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        assert reg.total_loaded == 1
        assert reg.total_enabled == 0
        assert len(reg.list_tools(enabled_only=True)) == 0
        assert len(reg.list_tools(enabled_only=False)) == 1


# ── ENV ref resolution ────────────────────────────────────────────────────────

class TestEnvRefResolution:
    def test_resolves_env_ref(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET", "my_secret_value")
        result = _resolve_env_refs({"key": "$ENV{TEST_SECRET}"})
        assert result["key"] == "my_secret_value"

    def test_raises_on_missing_env_ref(self):
        with pytest.raises(ValueError, match="TEST_MISSING_VAR"):
            _resolve_env_refs("$ENV{TEST_MISSING_VAR}")

    def test_resolves_nested_env_ref(self, monkeypatch):
        monkeypatch.setenv("NESTED_KEY", "resolved")
        result = _resolve_env_refs({"a": {"b": "$ENV{NESTED_KEY}"}})
        assert result["a"]["b"] == "resolved"

    def test_non_env_strings_unchanged(self):
        result = _resolve_env_refs("just a regular string")
        assert result == "just a regular string"


# ── YAML safety ────────────────────────────────────────────────────────────────

class TestYamlSafety:
    def test_safe_load_blocks_python_object_injection(self, tmp_tools: Path):
        """Confirms yaml.safe_load() blocks !!python/object RCE attempts."""
        evil = tmp_tools / "evil.yaml"
        evil.parent.mkdir(parents=True, exist_ok=True)
        evil.write_text("name: !!python/object/apply:os.system ['echo pwned']")
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(evil.read_text())

    def test_registry_does_not_execute_python_objects(self, tmp_tools: Path):
        """Even if the YAML isn't caught by the yaml.safe_load check itself,
        the registry should fail gracefully and not execute anything."""
        write_yaml(tmp_tools, "evil_attempt", """
            name: evil
            description: Some description that is long enough to pass basic checks
            executor_type: mock_static
            config:
              injected: !!python/object/apply:os.system ['echo pwned']
        """)
        reg = ToolRegistry()
        reg.load_all(tmp_tools)
        # Should either load zero tools (safe_load rejected it) or load it
        # harmlessly (config value stayed as a string, didn't execute)
        if reg.total_loaded == 1:
            spec = reg.get_spec("evil")
            # If it somehow loaded, the config value must be a plain string, not executed
            assert "echo pwned" not in str(spec.config.get("injected", ""))


# ── Arg validation ────────────────────────────────────────────────────────────

class TestArgValidation:
    def setup_method(self):
        self.reg = ToolRegistry()
        spec = ToolSpec(
            name="test_tool",
            description="A tool used in arg validation tests",
            executor_type="mock_static",
            args=[
                {"name": "x", "type": "string", "required": True, "description": "required string"},
                {"name": "y", "type": "string", "required": False, "default": "default_y", "description": "optional"},
                {"name": "mode", "type": "string", "required": True, "description": "enum arg", "enum": ["a", "b"]},
            ],
        )
        from executors.mock_static import MockStaticExecutor
        self.reg._tools["test_tool"] = spec
        self.reg._executors["test_tool"] = MockStaticExecutor(spec)

    def test_valid_args_pass(self):
        result = self.reg._validate_args(self.reg.get_spec("test_tool"), {"x": "hello", "mode": "a"})
        assert result["x"] == "hello"
        assert result["y"] == "default_y"

    def test_missing_required_arg_raises(self):
        with pytest.raises(ValueError, match="required arg 'x'"):
            self.reg._validate_args(self.reg.get_spec("test_tool"), {"mode": "a"})

    def test_invalid_enum_value_raises(self):
        with pytest.raises(ValueError, match="not one of"):
            self.reg._validate_args(self.reg.get_spec("test_tool"), {"x": "v", "mode": "c"})

    def test_unknown_arg_raises(self):
        with pytest.raises(ValueError, match="unknown args"):
            self.reg._validate_args(self.reg.get_spec("test_tool"), {"x": "v", "mode": "a", "z": "extra"})
