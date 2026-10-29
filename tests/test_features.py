from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_ibeacon
from trackyard.db import Database
from trackyard.report import generate
from trackyard.scanner import AdvInput, IngestEngine
from trackyard.sinks import SqliteSink
from trackyard.status import device_statuses, statuses_at
from trackyard.web import LIVE_COLS, create_app


def _engines(db, config):
    return {rx: IngestEngine(config, rx, "hci0", SqliteSink(db, batch=False)) for rx in ("pi-a", "pi-b")}


@pytest.fixture
def walk(config):
    """Badge 4/12345 sits at pi-a for 100..160, then at pi-b for 200..260."""
    db = Database(":memory:")
    eng = _engines(db, config)
    for ts in range(100, 161, 2):
        eng["pi-a"].handle(AdvInput("AA", -45, make_ibeacon(4, 12345), ts=float(ts)))
        eng["pi-b"].handle(AdvInput("AA", -80, make_ibeacon(4, 12345), ts=ts + 0.5))
    for ts in range(200, 261, 2):
        eng["pi-a"].handle(AdvInput("AA", -80, make_ibeacon(4, 12345), ts=float(ts)))
        eng["pi-b"].handle(AdvInput("AA", -45, make_ibeacon(4, 12345), ts=ts + 0.5))
    return db


def test_stale_badge_keeps_last_known_zone(config):
    db = Database(":memory:")
    eng = _engines(db, config)
    for ts in range(100, 130, 2):
        eng["pi-b"].handle(AdvInput("AA", -45, make_ibeacon(4, 12345), ts=float(ts)))
    # much later another badge keeps the lookback window moving past the first one
    eng["pi-a"].handle(AdvInput("BB", -50, make_ibeacon(9, 9999), ts=5000.0))
    memo: dict = {}
    st = {s.device_id: s for s in device_statuses(db, config, now=5001.0, last_known=memo)}
    assert st["TN_00412345"].stale
    assert st["TN_00412345"].zone == "pi-b"
    assert memo["TN_00412345"].zone == "pi-b"


def test_statuses_at_reflects_the_past(walk, config):
    at_150 = {s.device_id: s for s in statuses_at(walk, config, 150.0)}
    at_250 = {s.device_id: s for s in statuses_at(walk, config, 250.0)}
    assert at_150["TN_00412345"].zone == "pi-a"
    assert at_250["TN_00412345"].zone == "pi-b"
    assert statuses_at(walk, config, 50.0) == []


def test_replay_endpoint_snaps_and_uses_live_columns(walk, config):
    client = TestClient(create_app(config, walk))
    body = client.get("/api/at", params={"ts": 154.9}).json()
    assert body["at"] == 150
    assert body["cols"] == list(LIVE_COLS)
    row = dict(zip(LIVE_COLS, body["targets"][0], strict=True))
    assert row["target_id"] == "TN_00412345" and row["zone"] == "pi-a"


def test_receiver_health(walk, config):
    client = TestClient(create_app(config, walk))
    body = client.get("/api/receivers").json()
    by_name = {r["receiver"]: r for r in body["receivers"]}
    assert set(by_name) == {"pi-a", "pi-b"}
    assert by_name["pi-b"]["last_ts"] == pytest.approx(260.5)
    assert by_name["pi-a"]["recent"] == 0  # all test data is decades old


def test_presence_spans_split_on_gaps(walk, config):
    spans = walk.presence_spans(["TN_00412345"], gap_seconds=config.localizer.gap_seconds)["TN_00412345"]
    assert spans == [[100.0, 260.5]]  # the 39.5 s gap is shorter than the 60 s stale gap
    assert walk.presence_spans(["TN_00412345"], gap_seconds=30)["TN_00412345"] == [[100.0, 160.5], [200.0, 260.5]]


def test_ground_truth_marks_and_accuracy(walk, config):
    client = TestClient(create_app(config, walk))
    assert client.post("/api/truth", content='{"target_id":"TN_00412345","zone":"pi-a"}').status_code == 415
    ok = client.post("/api/truth", json={"target_id": "TN_00412345", "zone": "pi-a", "ts": 150})
    assert ok.status_code == 200
    client.post("/api/truth", json={"target_id": "TN_00412345", "zone": "pi-a", "ts": 250})  # wrong on purpose
    acc = client.get("/api/truth/TN_00412345").json()
    assert acc["n"] == 2 and acc["correct"] == 1 and acc["accuracy"] == 0.5
    assert [m["predicted"] for m in acc["marks"]] == ["pi-a", "pi-b"]
    assert acc["confusion"] == {"pi-a": {"pi-a": 1, "pi-b": 1}}
    assert client.post("/api/truth", json={"target_id": "", "zone": "pi-a"}).status_code == 422
    report = generate(walk, config)
    assert "## Measured localization accuracy" in report and "**50%**" in report


def test_analytics_includes_presence_and_utc_offset(walk, config):
    data = TestClient(create_app(config, walk)).get("/api/analytics").json()
    assert data["presence"]["TN_00412345"] == [[100.0, 260.5]]
    assert isinstance(data["utc_offset_minutes"], int)


def test_bad_input_is_rejected_not_a_500(walk, config):
    client = TestClient(create_app(config, walk), raise_server_exceptions=False)

    def post(body):
        return client.post("/api/truth", json=body).status_code

    assert post([1]) == 422
    assert post({"target_id": "TN_00412345", "zone": "pi-a", "ts": "abc"}) == 422
    assert post({"target_id": "TN_00412345", "zone": "nowhere"}) == 422
    assert post({"target_id": "TN_99999999", "zone": "pi-a"}) == 404
    assert client.post("/api/truth", content="{", headers={"content-type": "application/json"}).status_code == 400
    assert client.get("/api/at", params={"ts": "nan"}).status_code == 422
    assert client.get("/api/at", params={"ts": "inf"}).status_code == 422
    assert walk.truths() == []


def test_sink_survives_db_errors(config):
    from trackyard.db import Sighting

    db = Database(":memory:")
    sink = SqliteSink(db, batch=True)
    real, calls = db.insert_sightings, []

    def flaky(rows):
        calls.append(len(rows))
        if len(calls) == 1:
            raise OSError("disk full")
        real(rows)

    db.insert_sightings = flaky
    sink.add_sighting(Sighting(1.0, "pi-a", "hci0", "TN_00412345", "AA", -40))
    sink.flush()  # fails, rows stay buffered
    sink.add_sighting(Sighting(2.0, "pi-a", "hci0", "TN_00412345", "AA", -41))
    sink.close()  # retries both
    assert [r["ts"] for r in db.all_sightings()] == [1.0, 2.0]
