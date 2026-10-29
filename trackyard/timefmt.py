"""Timestamps are stored as UTC epoch seconds; everything shown to people is local time."""
from __future__ import annotations

from datetime import datetime


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def fmt_local(ts: float, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    return local(ts).strftime(fmt)


def iso_local(ts: float) -> str:
    """ISO 8601 with the UTC offset, e.g. 2026-10-29T07:21:03+01:00 (unambiguous and local)."""
    return local(ts).isoformat(timespec="seconds")
