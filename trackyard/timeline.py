from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass

from .config import Config
from .localizer import WindowZone, localize
from .timefmt import iso_local


@dataclass
class Segment:
    kind: str
    zone: str | None
    enter_ts: float
    leave_ts: float
    dwell_seconds: float


def build_timeline(sightings: list[tuple[float, int, str]], cfg: Config) -> list[Segment]:
    if not sightings:
        return []
    sightings = sorted(sightings)
    windows: list[WindowZone] = localize(sightings, cfg)
    w = cfg.localizer.window_seconds
    gap = cfg.localizer.gap_seconds

    segments: list[Segment] = []
    cur_zone: str | None = None
    enter = 0.0
    last_end = 0.0
    for wz in windows:
        start, end = float(wz.window_start), float(wz.window_start + w)
        if cur_zone is None:
            cur_zone, enter, last_end = wz.zone, start, end
            continue
        contiguous = start - last_end <= gap
        if wz.zone == cur_zone and contiguous:
            last_end = end
            continue
        segments.append(Segment("zone", cur_zone, enter, last_end, last_end - enter))
        if not contiguous:
            segments.append(Segment("gap", None, last_end, start, start - last_end))
        cur_zone, enter, last_end = wz.zone, start, end
    if cur_zone is not None:
        segments.append(Segment("zone", cur_zone, enter, last_end, last_end - enter))
    return segments


def to_json(target_id: str, label: str, segments: list[Segment]) -> str:
    return json.dumps(
        {"target_id": target_id, "label": label, "segments": [
            {**asdict(s), "enter_local": iso_local(s.enter_ts), "leave_local": iso_local(s.leave_ts)} for s in segments
        ]},
        indent=2,
    )


def to_csv(segments: list[Segment]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["kind", "zone", "enter_ts", "leave_ts", "dwell_seconds", "enter_local", "leave_local"])
    for s in segments:
        writer.writerow([s.kind, s.zone or "", f"{s.enter_ts:.1f}", f"{s.leave_ts:.1f}", f"{s.dwell_seconds:.1f}",
                         iso_local(s.enter_ts), iso_local(s.leave_ts)])
    return buf.getvalue()
