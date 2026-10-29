from __future__ import annotations

import statistics
import time
from itertools import pairwise

from .config import Config
from .db import Database
from .timefmt import fmt_local
from .timeline import build_timeline
from .truth import evaluate


def _fmt(ts: float) -> str:
    return fmt_local(ts)


def _dur(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def generate(db: Database, cfg: Config) -> str:
    total_count, _ = db.counts()
    summary = db.conn.execute(
        "SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM sightings"
    ).fetchone()
    labels = {str(k): str(v) or str(k) for k, v in cfg.targets.items()}
    target_ids = db.distinct_targets()
    targets = [(tid, labels.get(tid, tid)) for tid in target_ids]
    lines: list[str] = []
    w = lines.append

    w("# BLE Badge Tracking — Disclosure PoC")
    w("")
    w(f"_Generated {_fmt(time.time())}_")
    w("")

    w("## Capture summary")
    w("")
    if total_count:
        span = summary["last_ts"] - summary["first_ts"]
        w(f"- Advertisements captured: **{total_count}**")
        w(f"- Capture window: {_fmt(summary['first_ts'])} → {_fmt(summary['last_ts'])} ({_dur(span)})")
    else:
        w("- No badge advertisements captured.")
    w(f"- Distinct targets: **{len(target_ids)}**")
    w("")

    w("## Signal characteristics")
    w("")
    for tid, label in targets:
        s = db.sightings_for(tid)
        ts = [r["ts"] for r in s]
        deltas = [b - a for a, b in pairwise(ts) if b - a > 0.01]
        interval = f"~{statistics.median(deltas) * 1000:.0f} ms" if deltas else "n/a"
        addrs = {r["address"] for r in s}
        stability = "stable" if len(addrs) == 1 else f"{len(addrs)} addresses (rotating or multi-receiver)"
        w(f"- **{label}** (`{tid}`): {len(s)} adv, interval {interval}, "
          f"RSSI {min(r['rssi'] for r in s)}..{max(r['rssi'] for r in s)} dBm, address {stability}")
    if not targets:
        w("- (no target data)")
    w("")

    w("## Example timeline")
    w("")
    if targets:
        tid, label = targets[0]
        segs = build_timeline(db.recent_rssi(tid, limit=100000), cfg)
        w(f"Target **{label}** (`{tid}`):")
        w("")
        w("| segment | zone | enter | leave | dwell |")
        w("|---|---|---|---|---|")
        for seg in segs:
            zone = "— (gap, not seen)" if seg.kind == "gap" else (seg.zone or "unknown")
            w(f"| {seg.kind} | {zone} | {_fmt(seg.enter_ts)} | {_fmt(seg.leave_ts)} | {_dur(seg.dwell_seconds)} |")
    else:
        w("_No target captured._")
    w("")

    w("## Measured localization accuracy")
    w("")
    acc = evaluate(db, cfg)
    if acc["n"]:
        w(f"Ground-truth marks (where a badge really was, recorded on site): **{acc['n']}**. "
          f"The localizer put the badge in the right zone for **{acc['correct']}** of them "
          f"(**{acc['accuracy'] * 100:.0f}%**).")
        w("")
        zones = sorted(set(acc["confusion"]) | {p for row in acc["confusion"].values() for p in row})
        w("| really in \\ localizer said | " + " | ".join(z or "not heard" for z in zones) + " |")
        w("|---|" + "---|" * len(zones))
        for actual in sorted(acc["confusion"]):
            row = acc["confusion"][actual]
            w(f"| {actual} | " + " | ".join(str(row.get(z, 0)) for z in zones) + " |")
    else:
        w("_No ground-truth marks recorded. Mark a badge's real zone from the dashboard's detail panel to measure this._")
    w("")

    w("## What an attacker learns")
    w("")
    w("The badge number is broadcast in cleartext inside a standard iBeacon frame "
      "roughly every 2 seconds, decodable with a commodity phone app and no pairing. "
      "With receivers placed at known locations, an attacker reconstructs a movement "
      "timeline — which room a badge holder was in, when they arrived, how long they "
      "stayed — for any badge in range. Because the badge number maps to the attendee "
      "record, the timeline is tied to a named person.")
    w("")
    w("Receiver placement used for this capture:")
    w("")
    used = db.receiver_names()
    locations = {rec.name: rec.location for rec in cfg.receivers}
    if used:
        for name in used:
            loc = locations.get(name)
            w(f"- `{name}` — {loc or 'single node'}")
    else:
        w("- (no capture data)")
    w("")
    w("### Recommended mitigations")
    w("")
    w("- Rotate the advertised identifier (resolvable private identifiers), so a "
      "captured value cannot be re-identified across time or space.")
    w("- Decouple the advertised value from the attendee record; resolve only "
      "server-side under access control.")
    w("- Reduce TX power / advertising rate to shrink the capture radius.")
    return "\n".join(lines) + "\n"
