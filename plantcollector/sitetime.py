"""
Plant time. Everything a person reads - session log, report times, file and folder dates, the daily report hour -
is in the plant's own time zone (site.timezone in the config, Asia/Baghdad = Erbil, UTC+3, no daylight saving).
The CSV rows keep their UTC timestamp with a trailing Z so a row is never ambiguous.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:                                          # pragma: no cover
    ZoneInfo = None

_TZ: tzinfo = timezone.utc
_NAME = "UTC"


def set_zone(name: str | None) -> None:
    """Called when a config is loaded. An unknown name keeps the fixed Erbil offset (UTC+3)."""
    global _TZ, _NAME
    name = name or "Asia/Baghdad"
    try:
        _TZ = ZoneInfo(name)
    except Exception:
        from datetime import timedelta
        _TZ = timezone(timedelta(hours=3), "UTC+3")
    _NAME = name


def zone() -> tzinfo:
    return _TZ


def zone_name() -> str:
    return _NAME if ZoneInfo is not None and _NAME != "UTC+3" else "Asia/Baghdad"


def label() -> str:
    return "Erbil time" if _NAME in ("Asia/Baghdad", "UTC+3") else _NAME


def local(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_TZ)


def hms(ts: datetime) -> str:
    return local(ts).strftime("%H:%M:%S")


def day(ts: datetime) -> str:
    return local(ts).strftime("%Y-%m-%d")


def now() -> datetime:
    return datetime.now(_TZ)


set_zone("Asia/Baghdad")
