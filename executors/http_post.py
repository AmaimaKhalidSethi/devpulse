"""executors/http_post.py — HTTP POST with JSON body built from args."""
from __future__ import annotations

from typing import Any

import httpx
import jmespath

from core.config import get_settings
from executors.base import AbstractExecutor
from executors.http_get import HttpGetExecutor  # reuse SSRF check


class HttpPostExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        cfg = self.spec.config
        settings = get_settings()

        url: str = cfg.get("url", "")
        if not url:
            raise RuntimeError("http_post executor requires 'url' in config")

        HttpGetExecutor._check_ssrf(url, settings)
        timeout = float(cfg.get("timeout_seconds", settings.http_timeout_seconds))
        body_keys: list[str] | None = cfg.get("body_from_args")
        body = {k: v for k, v in args.items() if body_keys is None or k in body_keys}

        try:
            # follow_redirects=False prevents open-redirect SSRF bypass
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.TimeoutException:
            raise RuntimeError(f"POST to {url} timed out after {timeout}s")

        response_path: str | None = cfg.get("response_path")
        if response_path:
            return jmespath.search(response_path, data)
        return data
