from __future__ import annotations

from conftest import make_ibeacon
from trackyard.db import Database
from trackyard.scanner import AdvInput, IngestEngine
from trackyard.sinks import SqliteSink
from trackyard.status import device_statuses


def _engine(config):
    db = Database(":memory:")
    engine = IngestEngine(config, receiver="pi-a", adapter="hci0", sink=SqliteSink(db, batch=False))
    return engine, db


def test_badge_is_stored(config):
    engine, db = _engine(config)
    engine.handle(AdvInput("AA:BB", -40, make_ibeacon(4, 12345), ts=100.0))
    rows = db.all_sightings()
    assert len(rows) == 1
    assert rows[0]["target_id"] == "TN_00412345"


def test_all_badges_are_stored(config):
    engine, db = _engine(config)
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
    engine.handle(AdvInput("BB", -55, make_ibeacon(9, 9999), ts=101.0))
    engine.handle(AdvInput("CC", -55, make_ibeacon(7, 7777), ts=102.0))

    sightings = db.all_sightings()
    ids = [r["target_id"] for r in sightings]
    assert "TN_00412345" in ids
    assert "TN_00909999" in ids
    assert "TN_00707777" in ids


def test_non_badge_advertisement_ignored(config):
    engine, db = _engine(config)
    frame = engine.handle(AdvInput("ZZ", -30, {0x004C: b"\x10\x07abc"}, ts=100.0))
    assert frame is None
    assert db.all_sightings() == []


def test_foreign_fleet_uuid_ignored(config):
    engine, db = _engine(config)
    other = make_ibeacon(4, 12345, fleet_uuid="11111111-2222-3333-4444-555555555555")
    frame = engine.handle(AdvInput("ZZ", -30, other, ts=100.0))
    assert frame is None
    assert db.all_sightings() == []


def test_web_status_exposes_all_badges(config):
    from fastapi.testclient import TestClient

    from trackyard.web import create_app

    db = Database(":memory:")
    engine = IngestEngine(config, "mac", "", SqliteSink(db, batch=False))
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
    engine.handle(AdvInput("BB", -55, make_ibeacon(9, 9999), ts=101.0))

    body = TestClient(create_app(config, db)).get("/api/status").json()
    ids = [t["target_id"] for t in body["targets"]]
    assert "TN_00412345" in ids
    assert "TN_00909999" in ids
    assert body["total_sightings"] == 2
    assert body["unique_tags"] == 2


def test_device_statuses_cover_all_seen_devices(config):
    db = Database(":memory:")
    engine = IngestEngine(config, "pi-a", "hci0", SqliteSink(db, batch=False))
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
    engine.handle(AdvInput("BB", -55, make_ibeacon(9, 9999), ts=101.0))

    st = device_statuses(db, config, now=120.0)
    ids = [s.device_id for s in st]
    assert "TN_00412345" in ids
    assert "TN_00909999" in ids
    named = next(s for s in st if s.device_id == "TN_00412345")
    assert named.label == "My badge (self)"
    unnamed = next(s for s in st if s.device_id == "TN_00909999")
    assert unnamed.label == "TN_00909999"


def test_web_status_includes_map_receivers(config):
    from fastapi.testclient import TestClient

    from trackyard.web import create_app

    db = Database(":memory:")
    engine = IngestEngine(config, "mac", "", SqliteSink(db, batch=False))
    engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))

    client = TestClient(create_app(config, db))
    body = client.get("/api/status").json()
    names = [r["name"] for r in body["receivers"]]
    assert names == ["pi-a", "pi-b", "mac"]
    assert body["targets"][0]["zone"] == "mac"
    assert 'id="map"' in client.get("/").text


def test_load_config_finds_project_from_subdir(tmp_path, monkeypatch):
    from trackyard.config import load_config, project_dir

    (tmp_path / "config.yaml").write_text("storage:\n  db_path: data.sqlite\n")
    (tmp_path / "targets.yaml").write_text("targets:\n  TN_1: one\n")
    sub = tmp_path / "pkg" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    monkeypatch.delenv("TRACKYARD_DIR", raising=False)

    assert project_dir() == tmp_path
    cfg = load_config()
    assert cfg.targets == {"TN_1": "one"}
    assert cfg.db_path == str(tmp_path / "data.sqlite")


def test_stream_pushes_only_on_change(config):
    from fastapi.testclient import TestClient

    from trackyard.web import create_app

    db = Database(":memory:")
    engine = IngestEngine(config, "mac", "", SqliteSink(db, batch=False))
    client = TestClient(create_app(config, db, stream_interval=0.01))
    with client.stream("GET", "/api/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = r.iter_lines()
        first = [next(lines) for _ in range(4)]
        assert first[2] == "event: status"
        assert '"targets"' in first[3]
        engine.handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
        for line in lines:
            if line == "event: delta":
                data = next(lines)
                assert '"TN_00412345"' in data and '"mac"' in data
                break


def test_batched_sink_stops_flushing_after_close(config):
    import time as _time

    db = Database(":memory:")
    sink = SqliteSink(db)
    IngestEngine(config, "pi-a", "hci0", sink).handle(AdvInput("AA", -40, make_ibeacon(4, 12345), ts=100.0))
    sink.close()
    assert len(db.all_sightings()) == 1
    _time.sleep(0.6)
    assert sink._timer is None or not sink._timer.is_alive()


def test_receivers_without_name_are_skipped_with_warning(tmp_path, monkeypatch):
    import pytest as _pytest

    from trackyard.config import load_config

    (tmp_path / "config.yaml").write_text("storage:\n  db_path: data.sqlite\n")
    (tmp_path / "receivers.yaml").write_text("receivers:\n  - name: pi-a\n  - location: Hall B\n  - pi-c\n")
    monkeypatch.setenv("TRACKYARD_DIR", str(tmp_path))
    with _pytest.warns(UserWarning, match="skipped 2 receiver entries"):
        cfg = load_config()
    assert [r.name for r in cfg.receivers] == ["pi-a"]
