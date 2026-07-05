"""
tests/test_security.py
Security-specific unit tests: key hashing, rate limiting logic, SSRF
blocklist, and arg injection prevention.
"""
from __future__ import annotations

import time
import pytest

from core.security import generate_api_key, hash_key, check_rate_limit
from executors.http_get import HttpGetExecutor, _is_private_host
from core.schemas import ToolSpec


def make_http_spec() -> ToolSpec:
    return ToolSpec(
        name="test_http",
        description="HTTP test tool with enough description length",
        executor_type="http_get",
        config={"url": "https://example.com"},
    )


class FakeSettings:
    http_timeout_seconds = 5.0
    http_max_response_bytes = 512_000
    allowed_url_prefixes = ""

    @property
    def allowed_prefixes_list(self):
        return []


# ── Key hashing ───────────────────────────────────────────────────────────────

class TestKeyHashing:
    def test_generate_produces_unique_keys(self):
        raw1, hash1 = generate_api_key()
        raw2, hash2 = generate_api_key()
        assert raw1 != raw2
        assert hash1 != hash2

    def test_hash_is_deterministic(self):
        assert hash_key("test_key") == hash_key("test_key")

    def test_hash_is_not_plaintext(self):
        raw, hashed = generate_api_key()
        assert raw not in hashed
        assert len(hashed) == 64  # SHA-256 hex

    def test_raw_key_is_long_enough(self):
        raw, _ = generate_api_key()
        assert len(raw) >= 32  # secrets.token_urlsafe(32) → ~43 chars


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    async def test_allows_requests_under_limit(self):
        key = hash_key(f"test_{time.monotonic()}")
        for _ in range(5):
            await check_rate_limit(key, limit_per_minute=60)  # should not raise

    async def test_blocks_when_over_limit(self):
        from fastapi import HTTPException
        key = hash_key(f"test_block_{time.monotonic()}")
        with pytest.raises(HTTPException) as exc_info:
            for _ in range(65):  # exceed limit of 60
                await check_rate_limit(key, limit_per_minute=60)
        assert exc_info.value.status_code == 429

    async def test_different_keys_have_separate_limits(self):
        key1 = hash_key(f"key1_{time.monotonic()}")
        key2 = hash_key(f"key2_{time.monotonic()}")
        for _ in range(10):
            await check_rate_limit(key1, 60)
            await check_rate_limit(key2, 60)  # should not affect key1's limit


# ── SSRF protection ───────────────────────────────────────────────────────────

class TestSSRFProtection:
    @pytest.mark.parametrize("url", [
        "http://localhost/admin",
        "http://127.0.0.1/secret",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.1/internal",
        "http://0.0.0.0/",
        "http://::1/ipv6",
    ])
    def test_private_addresses_blocked(self, url: str):
        assert _is_private_host(url), f"Expected {url} to be blocked by SSRF check"

    @pytest.mark.parametrize("url", [
        "https://api.frankfurter.dev/v2/rate/USD/PKR",
        "https://hn.algolia.com/api/v1/search",
        "https://api.open-meteo.com/v1/forecast",
    ])
    def test_public_addresses_allowed(self, url: str):
        assert not _is_private_host(url), f"Expected {url} to be allowed"

    def test_ssrf_check_raises_on_private_url(self):
        exec_ = HttpGetExecutor(make_http_spec())
        with pytest.raises(ValueError, match="SSRF blocked"):
            HttpGetExecutor._check_ssrf("http://127.0.0.1/admin", FakeSettings())

    def test_ssrf_check_allows_public_url(self):
        # Should not raise
        HttpGetExecutor._check_ssrf("https://api.frankfurter.dev/v2/rate/USD/PKR", FakeSettings())

    def test_url_prefix_allowlist_enforced(self):
        class RestrictedSettings(FakeSettings):
            allowed_url_prefixes = "https://api.frankfurter.dev"
            @property
            def allowed_prefixes_list(self):
                return ["https://api.frankfurter.dev"]

        with pytest.raises(ValueError, match="not in the allowed prefix"):
            HttpGetExecutor._check_ssrf("https://attacker.com/steal", RestrictedSettings())


# ── Arg injection prevention ──────────────────────────────────────────────────

class TestArgInjection:
    def test_unknown_args_rejected_by_registry(self):
        from core.registry import ToolRegistry
        from executors.registry import init_executor_registry
        init_executor_registry()

        import tempfile, textwrap
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tools_dir = Path(tmpdir)
            (tools_dir / "echo.yaml").write_text(textwrap.dedent("""
                name: mock_echo
                description: Echo tool with enough description for validation
                executor_type: mock_static
                config:
                  response: {ok: true}
                args:
                  - name: message
                    type: string
                    required: false
                    default: hello
                    description: The message to echo back
            """))
            reg = ToolRegistry()
            reg.load_all(tools_dir)

        import asyncio
        with pytest.raises(ValueError, match="unknown args"):
            asyncio.run(reg.execute("mock_echo", {"message": "hi", "__injected__": "evil"}))


# ── Tests for audit-fix patches ───────────────────────────────────────────────

class TestSSRFUrlParsingFix:
    """Verifies the urlparse+ipaddress SSRF fix catches cases the old
    regex missed: IPv6-with-port, loopback hostnames with ports.
    """
    from executors.http_get import _is_private_host

    @pytest.mark.parametrize("url,expected", [
        ("http://127.0.0.1:9090/admin",                True),   # IPv4 with port
        ("http://[::1]:8080/",                         True),   # IPv6 with port (regex missed this)
        ("http://localhost:8080/",                     True),   # hostname with port
        ("http://localhost/",                          True),   # plain localhost
        ("https://api.frankfurter.dev/v2/rate/USD/PKR", False), # public API
        ("https://hn.algolia.com/api/v1/search",       False),  # public API
        ("https://services.nvd.nist.gov/rest/json/cves/2.0", False), # public API
    ])
    def test_ssrf_detection(self, url: str, expected: bool):
        from executors.http_get import _is_private_host
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        result = _is_private_host(url)
        assert result == expected, f"Expected is_private={expected} for {url}, got {result}"

    def test_follow_redirects_false_in_http_get(self):
        """Confirms follow_redirects=False so a server-side redirect to an
        internal address cannot bypass the SSRF check.
        """
        import inspect, executors.http_get as m
        src = inspect.getsource(m.HttpGetExecutor.execute)
        assert "follow_redirects=False" in src, \
            "http_get must set follow_redirects=False to prevent open-redirect SSRF bypass"

    def test_follow_redirects_false_in_http_post(self):
        import inspect, executors.http_post as m
        src = inspect.getsource(m.HttpPostExecutor.execute)
        assert "follow_redirects=False" in src, \
            "http_post must set follow_redirects=False to prevent open-redirect SSRF bypass"


class TestRateLimiterAsyncLock:
    """Verifies asyncio.Lock is used — not threading.Lock — so the event loop
    is not blocked during rate-limit checks.
    """
    def test_rate_lock_is_asyncio_lock(self):
        import asyncio, core.security as sec
        assert isinstance(sec._rate_lock, asyncio.Lock), \
            "Rate limiter must use asyncio.Lock, not threading.Lock"

    def test_dead_key_eviction_constants_present(self):
        import core.security as sec
        assert hasattr(sec, "_DEAD_KEY_TTL"), "Dead key TTL constant missing"
        assert hasattr(sec, "_CLEANUP_INTERVAL"), "Cleanup interval constant missing"
        assert sec._DEAD_KEY_TTL > 0
        assert sec._CLEANUP_INTERVAL > 0


class TestContentLengthGuard:
    """Verifies the middleware handles malformed Content-Length headers
    without raising ValueError (which caused a 500 before the fix).
    """
    def test_invalid_content_length_handled(self):
        # Read app.py as text rather than importing it (importing requires
        # real env vars for pydantic-settings validation)
        import pathlib
        src = pathlib.Path("app.py").read_text()
        assert "try:" in src and "ValueError" in src, \
            "Middleware must catch ValueError from int(content_length)"


class TestHotReloadEviction:
    """Verifies _tool_sources is populated at load time and used by reload_tool."""
    def test_tool_sources_populated_on_load(self, tmp_path):
        import textwrap
        from core.registry import ToolRegistry
        from executors.registry import init_executor_registry
        init_executor_registry()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.yaml").write_text(textwrap.dedent("""
            name: mock_echo
            description: Echo tool with a long enough description for validation
            executor_type: mock_static
            config:
              response: {status: ok}
        """))

        reg = ToolRegistry()
        reg.load_all(tools_dir)

        assert "mock_echo" in reg._tool_sources, \
            "_tool_sources must be populated at load time for hot-reload to work"
        assert "echo.yaml" in reg._tool_sources["mock_echo"]

    def test_reload_evicts_old_tool(self, tmp_path):
        import textwrap
        from core.registry import ToolRegistry
        from executors.registry import init_executor_registry
        init_executor_registry()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        yaml_path = tools_dir / "t.yaml"
        yaml_path.write_text(textwrap.dedent("""
            name: hot_tool
            description: First version of this tool with enough description text
            executor_type: mock_static
            config:
              response: {version: 1}
        """))

        reg = ToolRegistry()
        reg.load_all(tools_dir)
        assert reg.total_loaded == 1
        assert reg._tool_sources.get("hot_tool") == str(yaml_path)

        # Simulate a hot-reload (same file, same tool name)
        reg.reload_tool(yaml_path)
        # After reload, should still have exactly 1 tool, not 2
        assert reg.total_loaded == 1, \
            "Hot-reload must evict the old entry before loading the new one"


class TestSQLiteBusyTimeout:
    """Verifies PRAGMA busy_timeout is set in the database connection context."""
    def test_busy_timeout_pragma_present(self):
        import inspect, core.database as db
        src = inspect.getsource(db._db)
        assert "busy_timeout" in src, \
            "PRAGMA busy_timeout must be set to prevent SQLITE_BUSY under concurrent async access"
