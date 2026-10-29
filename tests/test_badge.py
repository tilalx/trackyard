from __future__ import annotations

from conftest import FLEET_UUID, make_ibeacon
from trackyard.badge import badge_id_from, parse_badge


def test_badge_id_format():
    assert badge_id_from(4, 12345) == "TN_00412345"
    assert badge_id_from(0, 0) == "TN_00000000"
    assert badge_id_from(123, 45678) == "TN_12345678"


def test_parse_matches_fleet():
    frame = parse_badge(make_ibeacon(4, 12345), FLEET_UUID)
    assert frame is not None
    assert frame.badge_id == "TN_00412345"
    assert frame.major == 4 and frame.minor == 12345
    assert frame.tx_power_at_1m == -59


def test_parse_rejects_other_fleet():
    assert parse_badge(make_ibeacon(4, 12345, "11111111-2222-3333-4444-555555555555"), FLEET_UUID) is None


def test_parse_rejects_non_ibeacon():
    assert parse_badge({0x004C: b"\x10\x07xx"}, FLEET_UUID) is None
    assert parse_badge({}, FLEET_UUID) is None
