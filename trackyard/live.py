from __future__ import annotations

import time

from rich.live import Live
from rich.table import Table

from .config import Config
from .db import Database
from .status import device_statuses

_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(_SPARK[min(len(_SPARK) - 1, int((v - lo) / span * (len(_SPARK) - 1)))] for v in values)


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def render(db: Database, cfg: Config, last_known: dict | None = None) -> Table:
    t = Table(title="trackyard — live")
    t.add_column("target")
    t.add_column("id", style="dim")
    t.add_column("zone", style="cyan")
    t.add_column("last", justify="right")
    t.add_column("rssi", justify="right")
    t.add_column("~dist", justify="right")
    t.add_column("rssi trend")

    for d in device_statuses(db, cfg, last_known=last_known):
        last_str = _ago(d.last_seen)
        t.add_row(
            d.label,
            d.device_id,
            d.zone or "—",
            f"[yellow]{last_str}[/]" if d.stale else last_str,
            f"{d.rssi}" if d.rssi is not None else "—",
            f"{d.distance_m}m" if d.distance_m is not None else "—",
            sparkline(d.sparkline),
        )
    return t


def run(db: Database, cfg: Config, refresh: float = 2.0) -> None:
    last_known: dict = {}
    try:
        with Live(render(db, cfg, last_known), refresh_per_second=4, screen=False) as live:
            while True:
                time.sleep(refresh)
                live.update(render(db, cfg, last_known))
    except KeyboardInterrupt:
        pass
