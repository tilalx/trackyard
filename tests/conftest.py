from __future__ import annotations

import struct
import uuid

import pytest

from trackyard.config import (
    Config,
    DistanceConfig,
    LocalizerConfig,
    Receiver,
)

FLEET_UUID = "c97dea5e-3ad1-434a-b36b-55f424811cd1"


def make_ibeacon(major: int, minor: int, fleet_uuid: str = FLEET_UUID, tx: int = -59) -> dict[int, bytes]:
    body = bytes([0x02, 0x15]) + uuid.UUID(fleet_uuid).bytes + struct.pack(">HH", major, minor) + struct.pack("b", tx)
    return {0x004C: body}


@pytest.fixture
def config() -> Config:
    return Config(
        badge_ibeacon_uuid=FLEET_UUID,
        localizer=LocalizerConfig(
            window_seconds=10,
            hysteresis_windows=2,
            gap_seconds=60,
            distance=DistanceConfig(enabled=True, tx_power_at_1m=-59, path_loss_n=2.5),
        ),
        retention_days=7,
        db_path=":memory:",
        collector_host="127.0.0.1",
        collector_port=8000,
        targets={"TN_00412345": "My badge (self)"},
        receivers=[
            Receiver("pi-a", "Hall A", "hci0"),
            Receiver("pi-b", "Room B", "hci0"),
        ],
    )
