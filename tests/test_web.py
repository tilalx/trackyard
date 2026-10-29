from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_ibeacon
from trackyard.db import Database
from trackyard.scanner import AdvInput, IngestEngine
from trackyard.sinks import SqliteSink
from trackyard.web import LIVE_COLS, LiveState, Refreshing, create_app


@pytest.fixture
def seeded(config):
    db = Database(":memory:")
    engine = IngestEngine(config, "pi-a", "hci0", SqliteSink(db, batch=False))
    for i in range(5):
        engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0 + i))
        engine.handle(AdvInput("BB", -60, make_ibeacon(9, 9999), ts=100.5 + i))
    return engine, db, TestClient(create_app(config, db, stream_interval=0.01, push_interval=0.01, field_interval=0))


def test_static_assets_are_gzipped_and_revalidated(seeded):
    _, _, client = seeded
    r = client.get("/static/app.js", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    again = client.get("/static/app.js", headers={"if-none-match": r.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/static/nope.js").status_code == 404
    font = client.get("/static/IBMPlexSans-Regular-Latin1.woff2", headers={"accept-encoding": "gzip"})
    assert font.headers["content-type"] == "font/woff2"
    assert "content-encoding" not in font.headers


def test_analytics_has_tag_stats_and_etag(seeded):
    _, _, client = seeded
    r = client.get("/api/analytics")
    body = r.json()
    by_id = {s["target_id"]: s for s in body["tag_stats"]}
    assert by_id["TN_00412345"]["sightings"] == 5
    assert by_id["TN_00412345"]["label"] == "My badge (self)"
    assert client.get("/api/analytics", headers={"if-none-match": r.headers["etag"]}).status_code == 304


def test_tag_detail(seeded):
    _, _, client = seeded
    body = client.get("/api/tag/TN_00412345").json()
    assert body["label"] == "My badge (self)"
    assert body["sightings_in_range"] == 5
    assert body["receivers"][0]["receiver"] == "pi-a"
    assert body["segments"][0]["zone"] == "pi-a"
    assert [p["target_id"] for p in body["peers"]] == ["TN_00909999"]
    assert client.get("/api/tag/TN_missing").status_code == 404


def test_route_snaps_hours_and_is_compact(seeded):
    engine, _, client = seeded
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=time.time()))
    body = client.get("/api/route?hours=20").json()
    assert body["hours"] == 24
    assert body["receivers"] == ["pi-a"]
    assert all(len(r) == 5 for r in body["rows"])


def _events(lines):
    event = None
    for line in lines:
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            yield event, json.loads(line[6:])
            event = None


def _rows(data):
    return [dict(zip(LIVE_COLS, r, strict=True)) for r in data]


def test_stream_sends_only_changed_badges(seeded):
    engine, _, client = seeded
    with client.stream("GET", "/api/stream") as r:
        events = _events(r.iter_lines())
        kind, full = next(events)
        assert kind == "status"
        assert full["cols"] == list(LIVE_COLS)
        assert {t["target_id"] for t in _rows(full["targets"])} == {"TN_00412345", "TN_00909999"}
        engine.handle(AdvInput("AA", -45, make_ibeacon(4, 12345), ts=200.0))
        seq = full["seq"]
        for kind, data in events:
            if kind != "delta":
                continue
            assert data["base"] == seq
            seq = data["seq"]
            if data["upsert"]:
                rows = _rows(data["upsert"])
                assert [t["target_id"] for t in rows] == ["TN_00412345"]
                assert rows[0]["rssi"] == -45
                assert data["remove"] == []
                break


def test_stream_holds_back_signal_only_changes(config):
    db = Database(":memory:")
    engine = IngestEngine(config, "pi-a", "hci0", SqliteSink(db, batch=False))
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
    client = TestClient(create_app(config, db, stream_interval=0.01, push_interval=0.01, field_interval=3600))
    with client.stream("GET", "/api/stream") as r:
        events = _events(r.iter_lines())
        assert next(events)[0] == "status"
        engine.handle(AdvInput("AA", -41, make_ibeacon(4, 12345), ts=101.0))
        pi_b = IngestEngine(config, "pi-b", "hci0", SqliteSink(db, batch=False))
        for ts in (150.0, 160.0, 170.0):  # hysteresis needs consecutive windows to switch zone
            pi_b.handle(AdvInput("AA", -20, make_ibeacon(4, 12345), ts=ts))
        for _, (kind, data) in zip(range(50), events, strict=False):
            if kind == "delta" and data["upsert"]:
                rows = _rows(data["upsert"])
                assert rows[0]["zone"] == "pi-b"  # the -41 dBm blip alone was never pushed
                break
        else:
            raise AssertionError("zone change was never pushed")


def test_live_state_skips_rebuild_when_unchanged():
    builds = []
    token = [1]
    live = LiveState(lambda: builds.append(1) or {"n": len(builds)}, lambda: token[0], min_interval=0.0)
    assert live.current() == (1, {"n": 1})
    assert live.current() == (1, {"n": 1})
    token[0] = 2
    assert live.current() == (2, {"n": 2})
    assert len(builds) == 2


def test_refreshing_serves_stale_value_while_recomputing():
    calls = []
    r = Refreshing(lambda: calls.append(1) or len(calls), min_ttl=3600)
    assert r.get() == 1
    assert r.get() == 1
    assert len(calls) == 1


def test_refreshing_backs_off_after_background_failure():
    calls = []

    def compute():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("db gone")
        return 1

    r = Refreshing(compute, min_ttl=3600)
    assert r.get() == 1
    r._expires = 0.0
    r.kick()
    for _ in range(100):
        if not r._running:
            break
        time.sleep(0.01)
    for _ in range(5):
        r.kick()
    time.sleep(0.05)
    assert len(calls) == 2
    assert r.value == 1
