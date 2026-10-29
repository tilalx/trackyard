from __future__ import annotations

import time
from dataclasses import dataclass

from .badge import BadgeFrame, parse_badge
from .config import Config
from .db import Sighting


@dataclass
class AdvInput:
    address: str
    rssi: int
    manufacturer_data: dict[int, bytes]
    raw_adv: bytes | None = None
    tx_power: int | None = None
    ts: float | None = None


class Sink:

    def add_sighting(self, s: Sighting) -> None:
        raise NotImplementedError


class IngestEngine:
    def __init__(self, config: Config, receiver: str, adapter: str, sink: Sink):
        self.config = config
        self.receiver = receiver
        self.adapter = adapter
        self.sink = sink

    def handle(self, adv: AdvInput) -> BadgeFrame | None:
        ts = adv.ts if adv.ts is not None else time.time()
        frame = parse_badge(adv.manufacturer_data, self.config.badge_ibeacon_uuid)
        if frame is None:
            return None

        self.sink.add_sighting(
            Sighting(
                ts=ts,
                receiver=self.receiver,
                adapter_id=self.adapter,
                target_id=frame.badge_id,
                address=adv.address,
                rssi=adv.rssi,
                tx_power=frame.tx_power_at_1m,
                raw_adv=adv.raw_adv,
            )
        )
        return frame


async def run_scan(config: Config, receiver: str, adapter: str, sink: Sink, duration: float | None = None) -> None:
    import asyncio

    from bleak import BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    engine = IngestEngine(config, receiver, adapter, sink)

    def cb(device: BLEDevice, adv: AdvertisementData) -> None:
        engine.handle(
            AdvInput(
                address=device.address,
                rssi=adv.rssi if adv.rssi is not None else -127,
                manufacturer_data=dict(adv.manufacturer_data),
                raw_adv=bytes(adv.manufacturer_data.get(0x004C, b"")),
                tx_power=adv.tx_power,
            )
        )

    kwargs = {"adapter": adapter} if adapter else {}
    scanner = BleakScanner(detection_callback=cb, **kwargs)
    await scanner.start()
    try:
        if duration is None:
            while True:
                await asyncio.sleep(60)
        else:
            await asyncio.sleep(duration)
    finally:
        await scanner.stop()
