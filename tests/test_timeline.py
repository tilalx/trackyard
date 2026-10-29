from __future__ import annotations

import json
from datetime import datetime

from trackyard.db import Database
from trackyard.timeline import build_timeline, to_csv, to_json


def test_timeline_zones_and_gap(config):
    s = []
    for t in range(0, 30, 2):
        s.append((float(t), -50, "pi-a"))
    for t in range(120, 150, 2):
        s.append((float(t), -45, "pi-b"))

    segs = build_timeline(s, config)
    kinds = [seg.kind for seg in segs]
    assert "gap" in kinds
    zones = [seg.zone for seg in segs if seg.kind == "zone"]
    assert zones[0] == "pi-a"
    assert zones[-1] == "pi-b"
    gap = next(seg for seg in segs if seg.kind == "gap")
    assert gap.dwell_seconds >= config.localizer.gap_seconds


def test_exports_roundtrip(config):
    s = [(float(t), -50, "pi-a") for t in range(0, 20, 2)]
    segs = build_timeline(s, config)
    j = json.loads(to_json("TN_00412345", "My badge", segs))
    assert j["target_id"] == "TN_00412345"
    assert j["segments"]
    csv_text = to_csv(segs)
    assert csv_text.splitlines()[0] == "kind,zone,enter_ts,leave_ts,dwell_seconds,enter_local,leave_local"
    assert "enter_local" in j["segments"][0]


def test_empty_timeline(config):
    assert build_timeline([], config) == []


def test_retention_purge():
    db = Database(":memory:")
    from trackyard.db import Sighting
    db.insert_sightings([
        Sighting(ts=1000.0, receiver="r", adapter_id="h", target_id="TN_00412345", address="A", rssi=-40),
        Sighting(ts=1_000_000.0, receiver="r", adapter_id="h", target_id="TN_00412345", address="A", rssi=-40),
    ])
    db.purge_old(retention_days=7, now=1_000_000.0 + 10)
    remaining = db.all_sightings()
    assert len(remaining) == 1
    assert remaining[0]["ts"] == 1_000_000.0


def test_purge_also_drops_old_ground_truth():
    db = Database(":memory:")
    db.add_truth(1000.0, "TN_00412345", "r")
    db.add_truth(1_000_000.0, "TN_00412345", "r")
    db.purge_old(retention_days=7, now=1_000_000.0 + 10)
    assert [r["ts"] for r in db.truths()] == [1_000_000.0]


def test_local_time_formatting():
    from trackyard.timefmt import iso_local

    s = iso_local(0)
    assert s[-6] in "+-" and s[-3] == ":"  # carries the local UTC offset
    assert datetime.fromisoformat(s).timestamp() == 0
