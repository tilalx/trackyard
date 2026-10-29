from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

APPLE_COMPANY_ID = 0x004C


@dataclass(frozen=True)
class BadgeFrame:
    badge_id: str
    ibeacon_uuid: str
    major: int
    minor: int
    tx_power_at_1m: int


def badge_id_from(major: int, minor: int) -> str:
    return f"TN_{major:03d}{minor:05d}"


def parse_ibeacon(manufacturer_data: dict[int, bytes]) -> BadgeFrame | None:
    data = manufacturer_data.get(APPLE_COMPANY_ID)
    if not data or len(data) < 23 or data[0] != 0x02 or data[1] != 0x15:
        return None
    beacon_uuid = str(uuid.UUID(bytes=bytes(data[2:18])))
    major, minor = struct.unpack(">HH", data[18:22])
    tx = struct.unpack("b", data[22:23])[0]
    return BadgeFrame(
        badge_id=badge_id_from(major, minor),
        ibeacon_uuid=beacon_uuid,
        major=major,
        minor=minor,
        tx_power_at_1m=tx,
    )


def parse_badge(manufacturer_data: dict[int, bytes], fleet_uuid: str) -> BadgeFrame | None:
    frame = parse_ibeacon(manufacturer_data)
    if frame is None:
        return None
    if fleet_uuid and frame.ibeacon_uuid.lower() != fleet_uuid.lower():
        return None
    return frame
