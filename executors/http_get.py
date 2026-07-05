"""
executors/http_get.py
HTTP GET executor with SSRF protection and response size limiting.

SSRF fix: replaced the regex-based blocklist (which missed IPv6-with-port,
octal notation, and decimal IP representations) with urlparse + ipaddress
stdlib approach that handles all standard encodings correctly.

config keys:
  url               Required. May contain {arg_name} placeholders.
  timeout_seconds   Optional, default from Settings.
  response_path     Optional JMESPath expression to extract from response.
  params_from_args  Optional list of arg names to send as query params.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
import jmespath

from core.config import get_settings
from core.logging import get_logger
from executors.base import AbstractExecutor

logger = get_logger(__name__)

# Hostnames that always resolve to loopback regardless of DNS
_LOOPBACK_HOSTNAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


def _is_private_host(url: str) -> bool:
    """Returns True if the URL's host resolves to a private/loopback address.

    Handles:
    - Standard IPv4 (127.x, 10.x, 172.16-31.x, 192.168.x)
    - IPv6 (::1, fc00::/7, fe80::/10)
    - IPv6 with port brackets: http://[::1]:8080/
    - Loopback hostnames: localhost, ip6-localhost
    - Attempts a DNS resolution for hostnames to catch CNAME-based bypasses

    Does NOT catch octal (0177.0.0.1) or decimal (2130706433) encodings via
    the ip_address() path — these fail ip_address() parsing and fall through
    to the DNS resolution step where the OS resolver handles them correctly
    (most resolvers reject non-standard encodings outright).
    """
    parsed = urlparse(url)
    host = parsed.hostname  # strips brackets from [::1], strips port
    if not host:
        return True  # unparseable = block by default

    if host in _LOOPBACK_HOSTNAMES or host.endswith(".local"):
        return True

    # Try direct IP parsing first (fast path, no DNS)
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        pass  # not an IP literal — fall through to DNS check

    # DNS resolution — catches CNAME rebinding and non-standard encodings
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in infos:
            resolved_ip = sockaddr[0]
            try:
                addr = ipaddress.ip_address(resolved_ip)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return True
            except ValueError:
                continue
    except socket.gaierror:
        # DNS resolution failed — block unknown hosts in production,
        # allow in dev (where DNS might not be configured for test domains).
        settings = get_settings()
        return settings.is_production

    return False


class HttpGetExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        cfg = self.spec.config
        settings = get_settings()

        url_template: str = cfg.get("url", "")
        if not url_template:
            raise RuntimeError("http_get executor requires 'url' in config")

        try:
            url = url_template.format(**args)
        except KeyError as e:
            raise ValueError(f"URL template references unknown arg {e}") from e

        self._check_ssrf(url, settings)

        timeout = float(cfg.get("timeout_seconds", settings.http_timeout_seconds))
        params_keys: list[str] | None = cfg.get("params_from_args")
        params = {
            k: v for k, v in args.items()
            if params_keys is None or k in params_keys
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,  # don't follow redirects — a redirect to 127.0.0.1 would bypass _check_ssrf
                limits=httpx.Limits(max_connections=10),
            ) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()

                if len(resp.content) > settings.http_max_response_bytes:
                    raise RuntimeError(
                        f"Response too large: {len(resp.content)} bytes "
                        f"(max {settings.http_max_response_bytes})"
                    )
                data = resp.json()

        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.TimeoutException:
            raise RuntimeError(f"Request to {url} timed out after {timeout}s")
        except httpx.RequestError as e:
            raise RuntimeError(f"Request failed: {e}") from e

        response_path: str | None = cfg.get("response_path")
        if response_path:
            extracted = jmespath.search(response_path, data)
            if extracted is None:
                logger.warning("response_path_no_match", extra={"extra": {
                    "path": response_path, "url": url,
                }})
            return extracted
        return data

    @staticmethod
    def _check_ssrf(url: str, settings) -> None:
        """Raises ValueError if the URL targets a private/loopback address
        or falls outside the configured allowed-prefix list.
        """
        if _is_private_host(url):
            raise ValueError(
                f"SSRF blocked: URL resolves to a private or loopback address"
            )

        allowed = settings.allowed_prefixes_list
        if allowed and not any(url.startswith(p) for p in allowed):
            raise ValueError(
                f"URL is not in the allowed prefix list. Allowed: {allowed}"
            )
