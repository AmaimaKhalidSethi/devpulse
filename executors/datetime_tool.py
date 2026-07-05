"""
executors/datetime_tool.py
Date/time operations: current time in any IANA timezone, date parsing,
format conversion, and relative time calculation.

config keys:
  operation  Required  One of: now, format, diff, add_days
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from executors.base import AbstractExecutor

_FORMATS = {
    "iso": "%Y-%m-%dT%H:%M:%S",
    "iso_date": "%Y-%m-%d",
    "us": "%m/%d/%Y",
    "long": "%B %-d, %Y",
    "unix": None,  # special case
}

_PARSE_FORMATS = [
    "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y",
    "%m/%d/%Y", "%B %d, %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%SZ",
]


def _parse_date(s: str) -> datetime:
    for fmt in _PARSE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date string '{s}'. Supported formats: {_PARSE_FORMATS}")


def _get_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone '{tz_name}'. Use IANA names like 'Asia/Karachi' or 'UTC'")


class DatetimeExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        operation: str = str(args.get("operation", self.spec.config.get("operation", "now"))).lower()

        if operation == "now":
            tz_name = str(args.get("timezone", "UTC"))
            tz = _get_tz(tz_name)
            now = datetime.now(tz)
            return {
                "timezone": tz_name,
                "datetime": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "day_of_week": now.strftime("%A"),
                "unix_timestamp": int(now.timestamp()),
            }

        if operation == "format":
            date_str = str(args.get("date", ""))
            fmt_key = str(args.get("format", "iso"))
            dt = _parse_date(date_str)
            if fmt_key == "unix":
                return {"result": int(dt.replace(tzinfo=timezone.utc).timestamp())}
            fmt = _FORMATS.get(fmt_key)
            if fmt is None:
                raise ValueError(f"Unknown format '{fmt_key}'. Choose from: {list(_FORMATS)}")
            try:
                return {"result": dt.strftime(fmt)}
            except ValueError:
                # %-d is Linux-specific; fall back on Windows
                return {"result": dt.strftime(fmt.replace("%-d", "%d"))}

        if operation == "diff":
            d1 = _parse_date(str(args.get("date1", "")))
            d2 = _parse_date(str(args.get("date2", "")))
            delta = abs(d2 - d1)
            return {
                "days": delta.days,
                "hours": delta.days * 24 + delta.seconds // 3600,
                "total_seconds": int(delta.total_seconds()),
            }

        if operation == "add_days":
            d = _parse_date(str(args.get("date", "")))
            days = int(args.get("days", 0))
            result = d + timedelta(days=days)
            return {"result": result.strftime("%Y-%m-%d"), "original": d.strftime("%Y-%m-%d"), "days_added": days}

        raise ValueError(f"Unknown datetime operation '{operation}'. Valid: now, format, diff, add_days")
