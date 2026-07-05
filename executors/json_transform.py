"""
executors/json_transform.py
Extract or reshape JSON data using JMESPath queries.
Safe: jmespath only evaluates a query against data; no code execution.
"""
from __future__ import annotations

import json
from typing import Any

import jmespath
from jmespath.exceptions import JMESPathError

from executors.base import AbstractExecutor

_MAX_INPUT_LEN = 512_000  # 512KB


class JsonTransformExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        query: str = str(args.get("query", self.spec.config.get("query", ""))).strip()
        if not query:
            raise ValueError("arg 'query' is required (JMESPath expression)")

        raw_data = args.get("data")
        if raw_data is None:
            raise ValueError("arg 'data' is required")

        # Accept data as a dict, list, or a JSON string
        if isinstance(raw_data, str):
            if len(raw_data) > _MAX_INPUT_LEN:
                raise ValueError(f"Input data too large ({len(raw_data)} chars, max {_MAX_INPUT_LEN})")
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError as e:
                raise ValueError(f"arg 'data' is not valid JSON: {e}") from e
        else:
            data = raw_data

        try:
            result = jmespath.search(query, data)
        except JMESPathError as e:
            raise ValueError(f"Invalid JMESPath query '{query}': {e}") from e

        return {
            "query": query,
            "result": result,
            "matched": result is not None,
        }
