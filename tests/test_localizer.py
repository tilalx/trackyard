from __future__ import annotations

from trackyard.localizer import (
    approx_distance_m,
    localize,
    raw_windows,
)


def test_strongest_median_wins_window(config):
    sightings = [
        (1.0, -70, "pi-a"), (2.0, -72, "pi-a"),
        (3.0, -55, "pi-b"), (4.0, -57, "pi-b"),
    ]
    w = raw_windows(sightings, config)
    assert len(w) == 1
    assert w[0].zone == "pi-b"


def test_hysteresis_suppresses_single_window_flap(config):
    s = []
    for i in range(3):
        s += [(i * 10 + 1, -50, "pi-a")]
    s += [(35, -40, "pi-b")]
    for i in range(4, 7):
        s += [(i * 10 + 1, -50, "pi-a")]
    zones = [wz.zone for wz in localize(s, config)]
    assert set(zones) == {"pi-a"}


def test_hysteresis_switches_after_two_consecutive(config):
    s = [(1, -50, "pi-a"), (11, -50, "pi-a")]
    s += [(21, -40, "pi-b"), (31, -40, "pi-b")]
    zones = [wz.zone for wz in localize(s, config)]
    assert zones[0] == "pi-a"
    assert zones[-1] == "pi-b"


def test_distance_is_monotonic():
    near = approx_distance_m(-59, -59, 2.5)
    far = approx_distance_m(-85, -59, 2.5)
    assert near < far
    assert abs(near - 1.0) < 0.01
