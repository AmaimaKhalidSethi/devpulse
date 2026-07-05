"""
executors/text_transform.py
String operations and cryptographic hashing.

config keys:
  operation  Required  One of the ops listed in _OPS below.
             (Or set in YAML so the tool is locked to one operation.)

args (defined in YAML per-tool):
  text       string  Required  The input text
  operation  string  Optional  Override the config operation
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from executors.base import AbstractExecutor

_MAX_TEXT_LEN = 50_000  # 50KB — plenty for any real text operation

_HASH_ALGORITHMS = {"sha256", "sha512", "md5", "blake2b", "sha1"}
# Note: md5 and sha1 are included because the tool is for checksums/
# deduplication, not for password storage. The YAML description should
# make this clear. If you add a password-hashing tool, use bcrypt/argon2.

_SLUG_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"[-\s]+")


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _SLUG_RE.sub("", text).strip().lower()
    return _WHITESPACE_RE.sub("-", text)


_OPS = {
    "upper": lambda t, _cfg: t.upper(),
    "lower": lambda t, _cfg: t.lower(),
    "title": lambda t, _cfg: t.title(),
    "strip": lambda t, _cfg: t.strip(),
    "reverse": lambda t, _cfg: t[::-1],
    "slugify": lambda t, _cfg: _slugify(t),
    "word_count": lambda t, _cfg: {
        "words": len(t.split()),
        "chars": len(t),
        "chars_no_spaces": len(t.replace(" ", "")),
        "lines": len(t.splitlines()),
    },
    "hash": lambda t, cfg: _do_hash(t, cfg),
}


def _do_hash(text: str, cfg: dict) -> dict:
    algo = cfg.get("algorithm", "sha256").lower()
    # Explicitly check against the allowlist BEFORE calling hashlib.new —
    # hashlib.new accepts many algorithms (including some like 'bcrypt' via
    # OpenSSL) that are not appropriate for this tool's use case.
    if algo not in _HASH_ALGORITHMS:
        raise ValueError(f"Unsupported hash algorithm '{algo}'. Choose from: {sorted(_HASH_ALGORITHMS)}")
    h = hashlib.new(algo, text.encode("utf-8")).hexdigest()
    return {"algorithm": algo, "hash": h, "input_length": len(text)}


class TextTransformExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        text: str = str(args.get("text", ""))
        if len(text) > _MAX_TEXT_LEN:
            raise ValueError(f"Text too long: {len(text)} chars (max {_MAX_TEXT_LEN})")

        # Operation: prefer arg override, fall back to config, then error.
        operation: str = str(args.get("operation", self.spec.config.get("operation", ""))).lower()
        if not operation:
            raise ValueError("No operation specified (set in YAML config or pass as arg)")
        if operation not in _OPS:
            raise ValueError(f"Unknown operation '{operation}'. Valid: {sorted(_OPS)}")

        effective_cfg = {**self.spec.config, **args}
        result = _OPS[operation](text, effective_cfg)
        if isinstance(result, str):
            return {"result": result, "operation": operation}
        return result
