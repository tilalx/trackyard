from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config
from .db import Database
from .localizer import WindowZone, localize


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    label: str
    last_seen: float | None
    rssi: int | None
    zone: str | None
    distance_m: float | None
    sparkline: list[int]
    stale: bool


def device_statuses(
    db: Database, cfg: Config, now: float | None = None, last_known: dict[str, WindowZone] | None = None
) -> list[DeviceStatus]:
    """``last_known`` (optional, kept by the caller across calls) remembers each badge's last
    localized window, so a badge that dropped out of the lookback keeps its last zone instead
    of turning unlocated. Without it the last zone is recomputed from the badge's own history."""
    now = time.time() if now is None else now
    last_known = {} if last_known is None else last_known
    labels = {str(k): str(v) or str(k) for k, v in cfg.targets.items()}

    last_rows = db.bulk_last_seen()
    last_by_id = {r["target_id"]: r for r in last_rows}

    if last_rows:
        most_recent = max(r["ts"] for r in last_rows)
        lookback = most_recent - max(cfg.localizer.window_seconds * 50, 600)
    else:
        lookback = now - 600
    recent_by_id = db.bulk_recent_rssi(lookback)

    out: list[DeviceStatus] = []
    for device_id in sorted(last_by_id):
        last = last_by_id[device_id]
        recent = recent_by_id.get(device_id, [])
        if recent:
            zones = localize(recent, cfg)
            cur = zones[-1] if zones else None
            if cur is not None:
                last_known[device_id] = cur
        else:
            cur = last_known.get(device_id)
            if cur is None:
                zones = localize(db.recent_rssi(device_id, limit=200), cfg)
                cur = zones[-1] if zones else None
                if cur is not None:
                    last_known[device_id] = cur
        out.append(
            DeviceStatus(
                device_id=device_id,
                label=labels.get(device_id, device_id),
                last_seen=last["ts"] if last else None,
                rssi=last["rssi"] if last else None,
                zone=(cur.zone or None) if cur else None,
                distance_m=cur.distance_m if cur else None,
                sparkline=[r[1] for r in recent[-40:]],
                stale=bool(last) and (now - last["ts"] > cfg.localizer.gap_seconds),
            )
        )
    return out


def statuses_at(db: Database, cfg: Config, at: float) -> list[DeviceStatus]:
    """Badge states as they were at ``at``: only badges heard in the lookback before it."""
    labels = {str(k): str(v) or str(k) for k, v in cfg.targets.items()}
    lookback = max(cfg.localizer.window_seconds * 50, 600)
    out: list[DeviceStatus] = []
    for device_id, series in sorted(db.sightings_between(at - lookback, at).items()):
        zones = localize(series, cfg)
        cur = zones[-1] if zones else None
        last_ts, last_rssi = series[-1][0], series[-1][1]
        out.append(
            DeviceStatus(
                device_id=device_id,
                label=labels.get(device_id, device_id),
                last_seen=last_ts,
                rssi=last_rssi,
                zone=(cur.zone or None) if cur else None,
                distance_m=cur.distance_m if cur else None,
                sparkline=[r[1] for r in series[-40:]],
                stale=at - last_ts > cfg.localizer.gap_seconds,
            )
        )
    return out
