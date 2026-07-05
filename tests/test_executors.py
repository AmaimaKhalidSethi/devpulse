"""
tests/test_executors.py
Unit tests for all executors that don't need network access.
HTTP executors are tested with mocked httpx responses.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.schemas import ToolSpec
from executors.registry import init_executor_registry
from executors.python_math import PythonMathExecutor, _safe_eval
from executors.text_transform import TextTransformExecutor
from executors.datetime_tool import DatetimeExecutor
from executors.json_transform import JsonTransformExecutor
from executors.mock_static import MockStaticExecutor
import ast


@pytest.fixture(autouse=True)
def init_exec():
    init_executor_registry()


def make_spec(executor_type: str, config: dict = None, args: list = None) -> ToolSpec:
    return ToolSpec(
        name="test_tool",
        description="A test tool with enough description length",
        executor_type=executor_type,
        config=config or {},
        args=args or [],
    )


# ── PythonMathExecutor ────────────────────────────────────────────────────────

class TestPythonMath:
    @pytest.mark.asyncio
    async def test_addition(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        r = await exec_.execute({"expression": "2 + 3"})
        assert r["result"] == 5

    @pytest.mark.asyncio
    async def test_complex_expression(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        r = await exec_.execute({"expression": "(100 - 32) * 5 / 9"})
        assert abs(r["result"] - 37.777) < 0.01

    @pytest.mark.asyncio
    async def test_power(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        r = await exec_.execute({"expression": "2 ** 10"})
        assert r["result"] == 1024

    @pytest.mark.asyncio
    async def test_division_by_zero(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        with pytest.raises((ValueError, ZeroDivisionError)):
            await exec_.execute({"expression": "1 / 0"})

    @pytest.mark.asyncio
    async def test_rejects_string_literals(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        with pytest.raises(ValueError):
            await exec_.execute({"expression": "'hello'"})

    @pytest.mark.asyncio
    async def test_rejects_function_calls(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        with pytest.raises(ValueError):
            await exec_.execute({"expression": "__import__('os').system('ls')"})

    @pytest.mark.asyncio
    async def test_rejects_giant_exponentiation(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        with pytest.raises(ValueError, match="too large"):
            await exec_.execute({"expression": "9999999 ** 9999"})

    @pytest.mark.asyncio
    async def test_expression_too_long(self):
        exec_ = PythonMathExecutor(make_spec("python_math"))
        with pytest.raises(ValueError, match="too long"):
            await exec_.execute({"expression": "1 + " * 100})


# ── TextTransformExecutor ─────────────────────────────────────────────────────

class TestTextTransform:
    @pytest.mark.asyncio
    async def test_upper(self):
        exec_ = TextTransformExecutor(make_spec("text_transform", config={"operation": "upper"}))
        r = await exec_.execute({"text": "hello world"})
        assert r["result"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_slugify(self):
        exec_ = TextTransformExecutor(make_spec("text_transform"))
        r = await exec_.execute({"text": "Hello World! This is a Test.", "operation": "slugify"})
        assert r["result"] == "hello-world-this-is-a-test"

    @pytest.mark.asyncio
    async def test_word_count(self):
        exec_ = TextTransformExecutor(make_spec("text_transform"))
        r = await exec_.execute({"text": "one two three", "operation": "word_count"})
        assert r["words"] == 3

    @pytest.mark.asyncio
    async def test_hash_sha256(self):
        exec_ = TextTransformExecutor(make_spec("text_transform", config={"operation": "hash"}))
        r = await exec_.execute({"text": "hello", "algorithm": "sha256"})
        assert len(r["hash"]) == 64
        assert r["algorithm"] == "sha256"

    @pytest.mark.asyncio
    async def test_hash_rejects_unknown_algorithm(self):
        exec_ = TextTransformExecutor(make_spec("text_transform", config={"operation": "hash"}))
        with pytest.raises(ValueError, match="Unsupported"):
            await exec_.execute({"text": "hello", "algorithm": "bcrypt"})

    @pytest.mark.asyncio
    async def test_text_too_long_rejected(self):
        exec_ = TextTransformExecutor(make_spec("text_transform"))
        with pytest.raises(ValueError, match="too long"):
            await exec_.execute({"text": "x" * 60_000, "operation": "upper"})

    @pytest.mark.asyncio
    async def test_unknown_operation_rejected(self):
        exec_ = TextTransformExecutor(make_spec("text_transform"))
        with pytest.raises(ValueError, match="Unknown operation"):
            await exec_.execute({"text": "hello", "operation": "exec_code"})


# ── DatetimeExecutor ──────────────────────────────────────────────────────────

class TestDatetime:
    @pytest.mark.asyncio
    async def test_now_utc(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "now"}))
        r = await exec_.execute({"timezone": "UTC"})
        assert "datetime" in r
        assert "UTC" in r["timezone"]

    @pytest.mark.asyncio
    async def test_now_karachi(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "now"}))
        r = await exec_.execute({"timezone": "Asia/Karachi"})
        assert r["timezone"] == "Asia/Karachi"

    @pytest.mark.asyncio
    async def test_unknown_timezone_raises(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "now"}))
        with pytest.raises(ValueError, match="Unknown timezone"):
            await exec_.execute({"timezone": "Fake/Zone"})

    @pytest.mark.asyncio
    async def test_format_iso_date(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "format"}))
        r = await exec_.execute({"date": "2026-07-03", "format": "us"})
        assert r["result"] == "07/03/2026"

    @pytest.mark.asyncio
    async def test_diff_dates(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "diff"}))
        r = await exec_.execute({"date1": "2026-01-01", "date2": "2026-07-03"})
        assert r["days"] == 183

    @pytest.mark.asyncio
    async def test_add_days(self):
        exec_ = DatetimeExecutor(make_spec("datetime_tool", config={"operation": "add_days"}))
        r = await exec_.execute({"date": "2026-07-01", "days": 5})
        assert r["result"] == "2026-07-06"


# ── JsonTransformExecutor ─────────────────────────────────────────────────────

class TestJsonTransform:
    @pytest.mark.asyncio
    async def test_simple_extraction(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        r = await exec_.execute({"data": {"name": "Alice", "age": 30}, "query": "name"})
        assert r["result"] == "Alice"
        assert r["matched"] is True

    @pytest.mark.asyncio
    async def test_nested_extraction(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        r = await exec_.execute({"data": {"a": {"b": {"c": 42}}}, "query": "a.b.c"})
        assert r["result"] == 42

    @pytest.mark.asyncio
    async def test_array_filter(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        data = {"people": [{"name": "A", "age": 25}, {"name": "B", "age": 35}]}
        r = await exec_.execute({"data": data, "query": "people[?age > `30`].name"})
        assert r["result"] == ["B"]

    @pytest.mark.asyncio
    async def test_accepts_json_string_input(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        r = await exec_.execute({"data": '{"x": 1}', "query": "x"})
        assert r["result"] == 1

    @pytest.mark.asyncio
    async def test_invalid_json_string_raises(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        with pytest.raises(ValueError, match="valid JSON"):
            await exec_.execute({"data": "not json{", "query": "x"})

    @pytest.mark.asyncio
    async def test_invalid_jmespath_raises(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        with pytest.raises(ValueError, match="JMESPath"):
            await exec_.execute({"data": {"x": 1}, "query": "[invalid query!!!"})

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        exec_ = JsonTransformExecutor(make_spec("json_transform"))
        r = await exec_.execute({"data": {"x": 1}, "query": "y"})
        assert r["result"] is None
        assert r["matched"] is False


# ── MockStaticExecutor ────────────────────────────────────────────────────────

class TestMockStatic:
    @pytest.mark.asyncio
    async def test_returns_configured_response(self):
        exec_ = MockStaticExecutor(make_spec("mock_static", config={"response": {"status": "ok"}}))
        r = await exec_.execute({})
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_echo_args_when_configured(self):
        exec_ = MockStaticExecutor(make_spec("mock_static", config={"response": {}, "echo_args": True}))
        r = await exec_.execute({"msg": "hello"})
        assert r["echo"]["msg"] == "hello"
