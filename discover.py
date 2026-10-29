"""Survey nearby BLE advertisers to find out how your badge identifies itself.

Lists every device heard during the scan with its manufacturer / service data,
decoded iBeacon and Eddystone frames, address type and advertising interval.
Use it once to find the iBeacon UUID for ``badge_ibeacon_uuid`` in config.yaml.

    python discover.py -t 30 --min-rssi -70
    python discover.py --gatt <ADDRESS>   # read-only GATT dump of your own badge
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import struct
import time
import uuid
from dataclasses import dataclass, field
from itertools import pairwise

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

COMPANY_IDS = {
    0x004C: "Apple",
    0x0059: "Nordic Semiconductor",
    0x0006: "Microsoft",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x0118: "Radius Networks",
}

EDDYSTONE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"
EDDYSTONE_URL_SCHEMES = ["http://www.", "https://www.", "http://", "https://"]
EDDYSTONE_URL_SUFFIXES = [
    ".com/", ".org/", ".edu/", ".net/", ".info/", ".biz/", ".gov/",
    ".com", ".org", ".edu", ".net", ".info", ".biz", ".gov",
]


@dataclass
class Seen:
    device: BLEDevice
    timestamps: list[float] = field(default_factory=list)
    rssi: list[int] = field(default_factory=list)
    adv: AdvertisementData | None = None
    connectable: bool | None = None
    raw_variants: set[str] = field(default_factory=set)


def address_type(address: str) -> str:
    if len(address) == 36:
        return "macOS-UUID (real address hidden by CoreBluetooth)"
    msb = int(address.split(":")[0], 16)
    top = msb >> 6
    return {
        0b11: "random static (or public)",
        0b01: "resolvable private (RPA, rotating)",
        0b00: "non-resolvable private (or public)",
        0b10: "reserved/public",
    }[top]


def rebuild_raw(adv: AdvertisementData) -> bytes:
    out = bytearray()

    def ad(t: int, payload: bytes) -> None:
        out.extend([len(payload) + 1, t])
        out.extend(payload)

    if adv.local_name:
        ad(0x09, adv.local_name.encode())
    if adv.tx_power is not None:
        ad(0x0A, struct.pack("b", adv.tx_power))
    for u in adv.service_uuids:
        u16 = _short_uuid(u)
        ad(0x03, struct.pack("<H", u16)) if u16 is not None else ad(0x07, uuid.UUID(u).bytes[::-1])
    for u, data in adv.service_data.items():
        u16 = _short_uuid(u)
        if u16 is not None:
            ad(0x16, struct.pack("<H", u16) + data)
        else:
            ad(0x21, uuid.UUID(u).bytes[::-1] + data)
    for cid, data in adv.manufacturer_data.items():
        ad(0xFF, struct.pack("<H", cid) + data)
    return bytes(out)


def _short_uuid(u: str) -> int | None:
    if u.lower().endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
        return int(u[4:8], 16)
    return None


def match_frames(adv: AdvertisementData) -> list[str]:
    hits: list[str] = []
    for cid, d in adv.manufacturer_data.items():
        if cid == 0x004C and len(d) >= 23 and d[0] == 0x02 and d[1] == 0x15:
            u = uuid.UUID(bytes=bytes(d[2:18]))
            major, minor = struct.unpack(">HH", d[18:22])
            tx = struct.unpack("b", d[22:23])[0]
            hits.append(f"iBeacon uuid={u} major={major} minor={minor} txAt1m={tx}dBm")
        if len(d) >= 24 and d[0] == 0xBE and d[1] == 0xAC:
            bid = d[2:22].hex()
            tx = struct.unpack("b", d[22:23])[0]
            hits.append(f"AltBeacon mfg=0x{cid:04X} id={bid} refRSSI={tx}dBm")
    ed = adv.service_data.get(EDDYSTONE_UUID)
    if ed:
        ftype = ed[0]
        if ftype == 0x00 and len(ed) >= 18:
            hits.append(f"Eddystone-UID ns={ed[2:12].hex()} inst={ed[12:18].hex()} tx={struct.unpack('b', ed[1:2])[0]}")
        elif ftype == 0x10 and len(ed) >= 3:
            hits.append(f"Eddystone-URL {_decode_eddystone_url(ed[2:])}")
        elif ftype == 0x20 and len(ed) >= 14:
            vbatt, temp, adv_cnt, sec = struct.unpack(">HhII", ed[2:14])
            hits.append(f"Eddystone-TLM vbatt={vbatt}mV temp={temp / 256:.1f}C advCnt={adv_cnt} uptime={sec / 10:.0f}s")
        elif ftype == 0x30:
            hits.append(f"Eddystone-EID eid={ed[2:10].hex()}")
    return hits


def _decode_eddystone_url(data: bytes) -> str:
    if not data:
        return ""
    scheme = EDDYSTONE_URL_SCHEMES[data[0]] if data[0] < len(EDDYSTONE_URL_SCHEMES) else "?"
    rest = "".join(
        EDDYSTONE_URL_SUFFIXES[b] if b < len(EDDYSTONE_URL_SUFFIXES) else chr(b) for b in data[1:]
    )
    return scheme + rest


def id_encodings(payload: bytes) -> list[str]:
    hints: list[str] = []
    printable = payload.decode("ascii", errors="replace")
    if any(c.isdigit() for c in printable) and sum(c.isprintable() for c in printable) >= len(payload) * 0.8:
        hints.append(f"ascii={printable!r}")
    if 4 <= len(payload):
        for off in range(len(payload) - 3):
            le = int.from_bytes(payload[off:off + 4], "little")
            be = int.from_bytes(payload[off:off + 4], "big")
            for name, val in (("le32", le), ("be32", be)):
                if 100_000 <= val <= 99_999_999:
                    hints.append(f"{name}@{off}={val:08d}")
    bcd = payload.hex()
    if bcd.isdigit() and len(bcd) >= 6:
        hints.append(f"bcd={bcd}")
    return hints


def interval_estimate(ts: list[float]) -> str:
    if len(ts) < 3:
        return "n/a (too few frames)"
    deltas = [b - a for a, b in pairwise(ts) if b - a > 0.005]
    if not deltas:
        return "n/a"
    lower = sorted(deltas)[: max(1, len(deltas) // 4)]
    return f"~{statistics.median(lower) * 1000:.0f} ms (median {statistics.median(deltas) * 1000:.0f} ms, n={len(ts)})"


async def scan(seconds: float, min_rssi: int) -> dict[str, Seen]:
    seen: dict[str, Seen] = {}

    def cb(device: BLEDevice, adv: AdvertisementData) -> None:
        if adv.rssi is not None and adv.rssi < min_rssi:
            return
        s = seen.setdefault(device.address, Seen(device))
        s.timestamps.append(time.monotonic())
        if adv.rssi is not None:
            s.rssi.append(adv.rssi)
        s.adv = adv
        s.raw_variants.add(rebuild_raw(adv).hex())
        pd = adv.platform_data
        if len(pd) >= 2 and hasattr(pd[1], "get"):
            flag = pd[1].get("kCBAdvDataIsConnectable")
            if flag is not None:
                s.connectable = bool(flag)

    async with BleakScanner(detection_callback=cb):
        await asyncio.sleep(seconds)
    return seen


def report(seen: dict[str, Seen], show_all: bool) -> None:
    rows = sorted(seen.values(), key=lambda s: -(statistics.median(s.rssi) if s.rssi else -999))
    print(f"\n{len(rows)} unique addresses\n")
    for s in rows:
        adv = s.adv
        assert adv is not None
        frames = match_frames(adv)
        nordic = 0x0059 in adv.manufacturer_data
        if not show_all and not (frames or nordic or adv.manufacturer_data or adv.service_data):
            continue
        print("=" * 78)
        print(f"address   {s.device.address}  [{address_type(s.device.address)}]")
        print(f"name      {adv.local_name or '-'}")
        print(f"rssi      median {statistics.median(s.rssi):.0f} dBm" if s.rssi else "rssi      -")
        print(f"interval  {interval_estimate(s.timestamps)}")
        print(f"connect.  {s.connectable}")
        print(f"tx_power  {adv.tx_power}")
        for v in sorted(s.raw_variants):
            print(f"raw       {v}")
        if len(s.raw_variants) > 1:
            print(f"          ({len(s.raw_variants)} payload variants → rotating/frame-interleaved)")
        for u in adv.service_uuids:
            print(f"svc uuid  {u}")
        for u, d in adv.service_data.items():
            print(f"svc data  {u}: {d.hex()}  {id_encodings(d)}")
        for cid, d in adv.manufacturer_data.items():
            print(f"mfg data  0x{cid:04X} ({COMPANY_IDS.get(cid, '?')}): {d.hex()}  {id_encodings(d)}")
        for f in frames:
            print(f"frame     {f}")


async def gatt_dump(address: str) -> None:
    async with BleakClient(address) as client:
        for svc in client.services:
            print(f"service {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                line = f"  char {ch.uuid}  {ch.properties}"
                if "read" in ch.properties:
                    try:
                        line += f"  = {(await client.read_gatt_char(ch)).hex()}"
                    except Exception as e:
                        line += f"  (read failed: {e})"
                print(line)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-t", "--seconds", type=float, default=20)
    p.add_argument("--min-rssi", type=int, default=-100)
    p.add_argument("--all", action="store_true", help="also show devices with no mfg/service data")
    p.add_argument("--gatt", metavar="ADDRESS", help="read-only GATT dump of this one device (your own badge)")
    a = p.parse_args()
    if a.gatt:
        asyncio.run(gatt_dump(a.gatt))
        return
    report(asyncio.run(scan(a.seconds, a.min_rssi)), a.all)


if __name__ == "__main__":
    main()
