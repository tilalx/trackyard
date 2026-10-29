from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class DistanceConfig:
    enabled: bool = True
    tx_power_at_1m: int = -59
    path_loss_n: float = 2.5
    path_loss_n_near: float = 2.0
    path_loss_n_far: float = 3.5
    near_threshold_dbm: float = 12.0
    far_threshold_dbm: float = 25.0
    min_distance_m: float = 0.3
    max_distance_m: float = 80.0
    use_weighted_fusion: bool = True


@dataclass(frozen=True)
class LocalizerConfig:
    window_seconds: int = 10
    hysteresis_windows: int = 2
    gap_seconds: int = 60
    distance: DistanceConfig = field(default_factory=DistanceConfig)


@dataclass(frozen=True)
class Receiver:
    name: str
    location: str = ""
    adapter: str = "hci0"
    x: float | None = None  # floor-plan position, 0..100 (% of map width)
    y: float | None = None  # floor-plan position, 0..100 (% of map height)


@dataclass(frozen=True)
class Config:
    badge_ibeacon_uuid: str
    localizer: LocalizerConfig
    retention_days: int
    db_path: str
    collector_host: str
    collector_port: int
    targets: dict[str, str]
    receivers: list[Receiver]
    map_image: str = ""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def project_dir(start: str | Path | None = None) -> Path:
    """Directory holding config.yaml.

    Resolution order: $TRACKYARD_DIR, then the first directory from ``start``
    (default: cwd) upwards that contains config.yaml, else cwd. This lets
    ``trackyard ...`` run from any sub-folder of the project.
    """
    env = os.environ.get("TRACKYARD_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "config.yaml").exists():
            return d
    return here


def load_config(
    config_path: str | Path = "config.yaml",
    targets_path: str | Path = "targets.yaml",
    receivers_path: str | Path = "receivers.yaml",
    base_dir: str | Path | None = None,
) -> Config:
    base = Path(base_dir) if base_dir is not None else project_dir()
    config_path, targets_path, receivers_path = (base / config_path, base / targets_path, base / receivers_path)
    cfg = _load_yaml(Path(config_path))
    loc = cfg.get("localizer", {})
    dist = loc.get("distance", {})
    targets = _load_yaml(Path(targets_path)).get("targets") or {}
    receivers_raw = _load_yaml(Path(receivers_path)).get("receivers") or []
    receivers = [r for r in receivers_raw if isinstance(r, dict) and r.get("name")]
    if len(receivers) != len(receivers_raw):
        warnings.warn(f"{receivers_path}: skipped {len(receivers_raw) - len(receivers)} receiver entries without a name", stacklevel=2)
    _web = cfg.get("web") or cfg.get("collector") or {}

    return Config(
        badge_ibeacon_uuid=str(cfg.get("badge_ibeacon_uuid", "")).lower(),
        localizer=LocalizerConfig(
            window_seconds=int(loc.get("window_seconds", 10)),
            hysteresis_windows=int(loc.get("hysteresis_windows", 2)),
            gap_seconds=int(loc.get("gap_seconds", 60)),
            distance=DistanceConfig(
                enabled=bool(dist.get("enabled", True)),
                tx_power_at_1m=int(dist.get("tx_power_at_1m", -59)),
                path_loss_n=float(dist.get("path_loss_n", 2.5)),
                path_loss_n_near=float(dist.get("path_loss_n_near", 2.0)),
                path_loss_n_far=float(dist.get("path_loss_n_far", 3.5)),
                near_threshold_dbm=float(dist.get("near_threshold_dbm", 12.0)),
                far_threshold_dbm=float(dist.get("far_threshold_dbm", 25.0)),
                min_distance_m=float(dist.get("min_distance_m", 0.3)),
                max_distance_m=float(dist.get("max_distance_m", 80.0)),
                use_weighted_fusion=bool(dist.get("use_weighted_fusion", True)),
            ),
        ),
        retention_days=int(cfg.get("retention_days", 7)),
        db_path=str(base / str(cfg.get("storage", {}).get("db_path", "trackyard.sqlite"))),
        collector_host=str(_web.get("host", "127.0.0.1")),
        collector_port=int(_web.get("port", 8000)),
        targets={str(k): str(v) for k, v in targets.items()},
        receivers=[
            Receiver(
                name=str(r["name"]),
                location=str(r.get("location", "")),
                adapter=str(r.get("adapter", "hci0")),
                x=float(r["x"]) if r.get("x") is not None else None,
                y=float(r["y"]) if r.get("y") is not None else None,
            )
            for r in receivers
        ],
        map_image=str(_web.get("map_image", "") or ""),
    )
