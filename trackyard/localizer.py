from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .config import Config, DistanceConfig


@dataclass(frozen=True)
class WindowZone:
    window_start: int
    zone: str
    median_rssi: float | None
    distance_m: float | None


def _select_path_loss_n(rssi_diff: float, dcfg: DistanceConfig) -> float:
    if rssi_diff <= dcfg.near_threshold_dbm:
        return dcfg.path_loss_n_near
    if rssi_diff >= dcfg.far_threshold_dbm:
        return dcfg.path_loss_n_far
    ratio = (rssi_diff - dcfg.near_threshold_dbm) / (dcfg.far_threshold_dbm - dcfg.near_threshold_dbm)
    return dcfg.path_loss_n_near + ratio * (dcfg.path_loss_n_far - dcfg.path_loss_n_near)


def approx_distance_m(rssi: float, tx_power_at_1m: int, n: float) -> float:
    return round(10 ** ((tx_power_at_1m - rssi) / (10 * n)), 2)


def refined_distance_m(rssi: float, tx_power_at_1m: int, dcfg: DistanceConfig) -> float:
    rssi_diff = tx_power_at_1m - rssi
    if rssi_diff < 0:
        return dcfg.min_distance_m
    if rssi_diff == 0:
        return 1.0
    n = _select_path_loss_n(rssi_diff, dcfg)
    d = 10 ** (rssi_diff / (10 * n))
    return round(max(dcfg.min_distance_m, min(d, dcfg.max_distance_m)), 2)


def _trimmed_median(values: list[int | float], trim_frac: float = 0.2) -> float:
    if len(values) <= 2:
        return statistics.median(values)
    s = sorted(values)
    cut = max(1, int(len(s) * trim_frac))
    trimmed = s[cut:-cut] if cut < len(s) // 2 else s
    return statistics.median(trimmed)


def _receiver_estimate(
    pairs: list[tuple[int, int | None]], dcfg: DistanceConfig, fallback_tx: int
) -> tuple[float, float, int]:
    rssis = [p[0] for p in pairs]
    med_rssi = _trimmed_median(rssis)
    tx_vals = [p[1] for p in pairs if p[1] is not None]
    tx = int(statistics.median(tx_vals)) if tx_vals else fallback_tx
    dist = refined_distance_m(med_rssi, tx, dcfg)
    return med_rssi, dist, len(rssis)


def _window_winner(
    samples_by_receiver: dict[str, list[tuple[int, int | None]]], cfg: Config
) -> tuple[str, float | None, float | None]:
    dcfg = cfg.localizer.distance

    if not dcfg.enabled:
        best_zone, best_med = "", None
        for receiver, pairs in samples_by_receiver.items():
            rssis = [p[0] for p in pairs]
            med = _trimmed_median(rssis)
            if best_med is None or med > best_med:
                best_zone, best_med = receiver, med
        return best_zone, best_med, None

    estimates: list[tuple[str, float, float, int]] = []
    for receiver, pairs in samples_by_receiver.items():
        med_rssi, dist, count = _receiver_estimate(pairs, dcfg, cfg.localizer.distance.tx_power_at_1m)
        estimates.append((receiver, med_rssi, dist, count))

    if not estimates:
        return "", None, None

    if not dcfg.use_weighted_fusion or len(estimates) == 1:
        best = max(estimates, key=lambda e: e[1])
        return best[0], best[1], best[2]

    # Weighted fusion: combine distance estimates, weight by 1/variance proxy and sample count
    total_weight = 0.0
    weighted_dist = 0.0
    best_zone = max(estimates, key=lambda e: e[1])[0]
    best_rssi = max(estimates, key=lambda e: e[1])[1]

    for _recv, med_rssi, dist, count in estimates:
        rssi_quality = max(1.0, 127.0 + med_rssi)
        weight = rssi_quality * math.sqrt(count)
        weighted_dist += dist * weight
        total_weight += weight

    fused_dist = round(weighted_dist / total_weight, 2) if total_weight > 0 else None
    return best_zone, best_rssi, fused_dist


def raw_windows(sightings: list[tuple[float, int, str, int | None]], cfg: Config) -> list[WindowZone]:
    w = cfg.localizer.window_seconds
    buckets: dict[int, dict[str, list[tuple[int, int | None]]]] = {}
    for ts, rssi, receiver, *rest in sightings:
        tx_power = rest[0] if rest else None
        start = int(ts // w) * w
        buckets.setdefault(start, {}).setdefault(receiver, []).append((rssi, tx_power))

    out: list[WindowZone] = []
    for start in sorted(buckets):
        zone, med, dist = _window_winner(buckets[start], cfg)
        out.append(WindowZone(window_start=start, zone=zone, median_rssi=med, distance_m=dist))
    return out


def apply_hysteresis(windows: list[WindowZone], cfg: Config) -> list[WindowZone]:
    need = max(1, cfg.localizer.hysteresis_windows)
    stable: list[WindowZone] = []
    current: str | None = None
    candidate: str | None = None
    streak = 0

    for wz in windows:
        if current is None:
            current = wz.zone
        if wz.zone == current:
            candidate, streak = None, 0
        else:
            if wz.zone == candidate:
                streak += 1
            else:
                candidate, streak = wz.zone, 1
            if streak >= need:
                current = wz.zone
                candidate, streak = None, 0
        stable.append(WindowZone(wz.window_start, current, wz.median_rssi, wz.distance_m))
    return stable


def localize(sightings: list[tuple[float, int, str, int | None]], cfg: Config) -> list[WindowZone]:
    return apply_hysteresis(raw_windows(sightings, cfg), cfg)
