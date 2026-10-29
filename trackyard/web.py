from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .config import Config, load_config
from .db import Database
from .status import DeviceStatus, device_statuses, statuses_at
from .timeline import build_timeline
from .truth import evaluate

STATIC_DIR = Path(__file__).parent / "static"
_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
}
_ROUTE_HOURS = (1, 3, 6, 12, 24, 72, 168)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


@dataclass(frozen=True)
class Payload:
    """A response body serialized and gzipped once, then served to every client."""

    body: bytes
    gz: bytes | None
    etag: str
    media_type: str = "application/json"

    @classmethod
    def of(cls, body: bytes, media_type: str = "application/json") -> Payload:
        etag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
        compressible = media_type.startswith(("text/", "application/json"))
        return cls(body, gzip.compress(body, 6) if compressible else None, etag, media_type)

    @classmethod
    def json(cls, obj: Any) -> Payload:
        return cls.of(_dumps(obj).encode())

    def response(self, request: Request) -> Response:
        headers = {"ETag": self.etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
        if self.etag in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        if self.gz is not None and "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
            return Response(self.gz, media_type=self.media_type, headers=headers)
        return Response(self.body, media_type=self.media_type, headers=headers)


class Refreshing:
    """A value recomputed in a background thread; readers get the last value without waiting.

    Only the very first read blocks. The TTL stretches to 5x the compute time so an
    expensive query can never occupy more than ~20% of a core.
    """

    def __init__(self, compute: Callable[[], Any], min_ttl: float):
        self._compute = compute
        self._min_ttl = min_ttl
        self._compute_lock = threading.Lock()
        self._flag_lock = threading.Lock()
        self._running = False
        self._expires = 0.0
        self.value: Any = None
        self.version = 0

    def get(self) -> Any:
        if self.value is None:
            with self._compute_lock:
                if self.value is None:
                    self._refresh()
        else:
            self.kick()
        return self.value

    def kick(self) -> None:
        if time.monotonic() < self._expires:
            return
        with self._flag_lock:
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._background, daemon=True).start()

    def _background(self) -> None:
        try:
            with self._compute_lock:
                self._refresh()
        except Exception as exc:
            self._expires = time.monotonic() + self._min_ttl  # back off instead of retrying every tick
            print(f"trackyard web: background refresh failed: {exc!r}")
        finally:
            self._running = False

    def _refresh(self) -> None:
        t0 = time.monotonic()
        value = self._compute()
        took = time.monotonic() - t0
        self.value = value
        self.version += 1
        self._expires = time.monotonic() + max(self._min_ttl, took * 5)


class LiveState:
    """Latest per-badge live rows, rebuilt only when the DB changed, and at most every
    `min_interval` seconds (or 2x the build time, whichever is longer)."""

    def __init__(self, build: Callable[[], dict], token: Callable[[], Any], min_interval: float):
        self._build = build
        self._token = token
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_build = 0.0
        self._last_token: Any = object()
        self.seq = 0
        self.snap: dict | None = None

    def current(self) -> tuple[int, dict]:
        with self._lock:
            if self.snap is None or time.monotonic() >= self._next_build:
                tok = self._token()
                if self.snap is None or tok != self._last_token:
                    t0 = time.monotonic()
                    self.snap = self._build()
                    took = time.monotonic() - t0
                    self._last_token = tok
                    self.seq += 1
                    self._next_build = time.monotonic() + max(self._min_interval, took * 2)
            return self.seq, self.snap


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {_dumps(data)}\n\n"


LIVE_COLS = ("target_id", "label", "zone", "rssi", "distance_m", "last_seen", "stale")
_STRUCTURAL = ("label", "zone", "stale")


def _compact(row: dict) -> list:
    out = [row[k] for k in LIVE_COLS]
    if out[1] == out[0]:
        out[1] = None
    return out


class LiveHub:
    """One producer for all SSE clients.

    The live state is built once per DB change and fanned out as small deltas. Zone,
    stale and label changes go out immediately; RSSI / distance / last-seen of a single
    badge at most every `field_interval` seconds, so thousands of noisy badges don't
    turn every update into a full snapshot.
    """

    def __init__(self, live: LiveState, analytics: Refreshing, meta: Callable[[], dict], poll: float, field_interval: float = 5.0):
        self._live = live
        self._analytics = analytics
        self._meta_fn = meta
        self._poll = poll
        self._field_interval = field_interval
        self._subs: dict[asyncio.Queue[str], bool] = {}  # queue -> has received a full state
        self._task: asyncio.Task | None = None
        self._live_seq = -1
        self._snap: dict | None = None
        self._meta: dict = {}
        self._sent: dict[str, tuple[dict, float]] = {}
        self._seq = 0
        self._totals: tuple = ()
        self._receivers: list = []
        self._analytics_v = 0
        self._full_cache: tuple[int, str] = (-1, "")
        self._last_flush = 0.0

    @property
    def clients(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._subs[q] = self._snap is not None
        if self._snap is not None:
            q.put_nowait(self._full())
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.pop(q, None)

    def _full(self) -> str:
        if self._full_cache[0] != self._seq:
            assert self._snap is not None
            state = full_state(self._seq, self._snap, self._meta, self._analytics.version, compact=True)
            self._full_cache = (self._seq, _sse("status", state))
        return self._full_cache[1]

    def _offer(self, q: asyncio.Queue[str], msg: str) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # Slow client: drop its backlog and resync it with the latest full state.
            while not q.empty():
                q.get_nowait()
            q.put_nowait(self._full())

    def _broadcast(self, msg: str) -> None:
        for q, synced in list(self._subs.items()):
            if synced:
                self._offer(q, msg)

    async def _run(self) -> None:
        while self._subs:
            try:
                seq, snap = await asyncio.to_thread(self._live.current)
                fresh = seq != self._live_seq
                if fresh:
                    self._live_seq, self._snap = seq, snap
                    self._meta = await asyncio.to_thread(self._meta_fn)
                if fresh or time.monotonic() - self._last_flush >= 1.0:
                    self._flush()
                self._analytics.kick()
                if self._analytics.version != self._analytics_v:
                    self._analytics_v = self._analytics.version
                    self._broadcast(_sse("analytics", {"v": self._analytics_v}))
            except Exception as exc:  # keep streaming to everyone even if one build fails
                print(f"trackyard web: live update failed: {exc!r}")
            await asyncio.sleep(self._poll)

    def _flush(self) -> None:
        if self._snap is None:
            return
        now = time.monotonic()
        self._last_flush = now
        rows: dict[str, dict] = self._snap["rows"]
        upsert = []
        for tid, row in rows.items():
            prev = self._sent.get(tid)
            if (
                prev is None
                or any(prev[0][k] != row[k] for k in _STRUCTURAL)
                or (prev[0] != row and now - prev[1] >= self._field_interval)
            ):
                upsert.append(_compact(row))
                self._sent[tid] = (row, now)
        remove = [tid for tid in self._sent if tid not in rows]
        for tid in remove:
            del self._sent[tid]
        totals = (self._snap["total_sightings"], len(rows))
        receivers_changed = self._meta.get("receivers") != self._receivers
        unsynced = [q for q, synced in self._subs.items() if not synced]
        if not (upsert or remove or receivers_changed or totals != self._totals or unsynced):
            return
        base = self._seq
        self._seq += 1
        self._totals = totals
        self._receivers = self._meta.get("receivers", [])
        delta: dict[str, Any] = {
            "seq": self._seq,
            "base": base,
            "server_time": self._snap["server_time"],
            "upsert": upsert,
            "remove": remove,
            "total_sightings": totals[0],
            "unique_tags": totals[1],
        }
        if receivers_changed:
            delta["receivers"] = self._receivers
        self._broadcast(_sse("delta", delta))
        for q in unsynced:
            self._subs[q] = True
            self._offer(q, self._full())


def full_state(seq: int, snap: dict, meta: dict, analytics_v: int, compact: bool = False) -> dict:
    rows = snap["rows"]
    out = {
        "seq": seq,
        "server_time": snap["server_time"],
        "targets": [_compact(r) for r in rows.values()] if compact else list(rows.values()),
        "total_sightings": snap["total_sightings"],
        "unique_tags": len(rows),
        "analytics_v": analytics_v,
        **meta,
    }
    if compact:
        out["cols"] = list(LIVE_COLS)
    return out


def _finite(v: float | None, digits: int) -> float | None:
    return round(v, digits) if v is not None and math.isfinite(v) else None


def _live_row(d: DeviceStatus) -> dict:
    return {
        "target_id": d.device_id,
        "label": d.label,
        "zone": d.zone,
        "rssi": d.rssi,
        "distance_m": _finite(d.distance_m, 1),
        "last_seen": _finite(d.last_seen, 1),
        "stale": d.stale,
    }


def _receivers_for_map(cfg: Config, db: Database) -> list[dict]:
    out = [{"name": r.name, "location": r.location, "x": r.x, "y": r.y} for r in cfg.receivers]
    known = {r["name"] for r in out}
    for name in db.receiver_names():
        if name not in known:
            known.add(name)
            out.append({"name": name, "location": "", "x": None, "y": None})
    return out


def _load_static() -> dict[str, Payload]:
    out: dict[str, Payload] = {}
    for path in STATIC_DIR.iterdir():
        media = _MEDIA_TYPES.get(path.suffix)
        if media:
            out[path.name] = Payload.of(path.read_bytes(), media)
    return out


def create_app(
    config: Config | None = None,
    db: Database | None = None,
    stream_interval: float = 0.25,
    push_interval: float = 1.0,
    field_interval: float = 5.0,
) -> FastAPI:
    cfg = config or load_config()
    database = db or Database(cfg.db_path)
    live_db = database.reader()
    analytics_db = database.reader()
    detail_db = database.reader()
    app = FastAPI(title="trackyard web")
    static = _load_static()

    def labels() -> dict[str, str]:
        return {str(k): str(v) or str(k) for k, v in cfg.targets.items()}

    last_known: dict = {}

    def build_live() -> dict:
        rows = {d.device_id: _live_row(d) for d in device_statuses(live_db, cfg, last_known=last_known)}
        sighting_count, _ = live_db.counts()
        return {"rows": rows, "total_sightings": sighting_count, "server_time": round(time.time(), 2)}

    receivers_cache = Refreshing(lambda: _receivers_for_map(cfg, analytics_db), min_ttl=10.0)

    def meta() -> dict:
        return {
            "receivers": receivers_cache.get(),
            "map_image": cfg.map_image,
            "gap_seconds": cfg.localizer.gap_seconds,
        }

    def compute_analytics() -> dict:
        lab = labels()
        gap = cfg.localizer.gap_seconds
        encounters = analytics_db.encounter_counts(gap)
        nearby_counts, colocation = analytics_db.proximity_analysis()
        tag_stats = analytics_db.tag_stats()
        for s in tag_stats:
            s["label"] = lab.get(s["target_id"], s["target_id"])
            s["encounters"] = encounters.get(s["target_id"], 0)
            s["nearby_tags"] = nearby_counts.get(s["target_id"], 0)
            s["first_seen"] = _finite(s["first_seen"], 1)
            s["last_seen"] = _finite(s["last_seen"], 1)
        utc_offset = time.localtime().tm_gmtoff
        data = {
            "generated_at": round(time.time(), 1),
            "utc_offset_minutes": utc_offset // 60,
            "tag_stats": tag_stats,
            "colocation": colocation,
            "zone_flow": analytics_db.zone_flow(gap, cfg.localizer.window_seconds),
            "rssi_distribution": analytics_db.rssi_distribution(),
            "hourly_activity": analytics_db.hourly_activity(),
            "histogram": analytics_db.sightings_histogram(since=time.time() - 7 * 86400),
            "presence": analytics_db.presence_spans([s["target_id"] for s in tag_stats[:15]], gap),
        }
        return {"data": data, "payload": Payload.json(data)}

    analytics = Refreshing(compute_analytics, min_ttl=30.0)
    live = LiveState(build_live, live_db.change_token, push_interval)
    hub = LiveHub(live, analytics, meta, stream_interval, field_interval)
    live_payload: list[tuple[int, Payload | None]] = [(-1, None)]  # one slot, replaced atomically

    def route_payload(hours: int) -> Payload:
        bucket = 60 if hours <= 6 else 300 if hours <= 24 else 900
        rows = analytics_db.route_density(bucket_seconds=bucket, lookback_hours=hours)
        receivers = sorted({r["receiver"] for r in rows})
        idx = {name: i for i, name in enumerate(receivers)}
        return Payload.json({
            "hours": hours,
            "bucket": bucket,
            "receivers": receivers,
            "rows": [[r["ts"], idx[r["receiver"]], r["unique_tags"], r["sightings"], r["avg_rssi"]] for r in rows],
        })

    route_caches = {h: Refreshing(lambda h=h: route_payload(h), min_ttl=15.0) for h in _ROUTE_HOURS}

    @app.get("/api/status")
    def status() -> Response:
        """Everything in one document (live + analytics); kept for scripts and the CLI."""
        seq, snap = live.current()
        out = full_state(seq, snap, meta(), analytics.version)
        out.update(analytics.get()["data"])
        return Response(_dumps(out), media_type="application/json")

    @app.get("/api/live")
    def live_api(request: Request) -> Response:
        seq, snap = live.current()
        cached_seq, payload = live_payload[0]
        if payload is None or cached_seq != seq:
            payload = Payload.json(full_state(seq, snap, meta(), analytics.version, compact=True))
            live_payload[0] = (seq, payload)
        return payload.response(request)

    @app.get("/api/analytics")
    def analytics_api(request: Request) -> Response:
        return analytics.get()["payload"].response(request)

    @app.get("/api/route")
    def route_api(request: Request, hours: float = 24.0) -> Response:
        snapped = min(_ROUTE_HOURS, key=lambda h: abs(h - hours))
        return route_caches[snapped].get().response(request)

    detail_cache: dict[tuple[str, float], tuple[float, Payload]] = {}

    @app.get("/api/tag/{target_id}")
    def tag_detail(target_id: str, request: Request, hours: float = 6.0) -> Response:
        hours = round(max(0.25, min(hours, 168.0)), 2)
        key = (target_id, hours)
        hit = detail_cache.get(key)
        if hit is None or hit[0] < time.monotonic():
            if len(detail_cache) > 512:
                detail_cache.clear()
            hit = (time.monotonic() + 5.0, build_tag_detail(target_id, hours))
            detail_cache[key] = hit
        return hit[1].response(request)

    def build_tag_detail(target_id: str, hours: float) -> Payload:
        last = detail_db.last_seen(target_id)
        if last is None:
            raise HTTPException(404, "unknown target")
        since = last["ts"] - hours * 3600
        series = detail_db.target_series(target_id, since)
        bucket = max(10, int(hours * 3600 / 240))
        buckets = detail_db.target_rssi_buckets(target_id, since, bucket)
        per_rx: dict[str, dict] = {}
        for ts, rssi, rx, _ in series:
            e = per_rx.setdefault(rx, {"receiver": rx, "sightings": 0, "rssi_sum": 0, "best": -999, "last": 0.0})
            e["sightings"] += 1
            e["rssi_sum"] += rssi
            e["best"] = max(e["best"], rssi)
            e["last"] = max(e["last"], ts)
        receivers = sorted(
            ({"receiver": e["receiver"], "sightings": e["sightings"], "avg_rssi": round(e["rssi_sum"] / e["sightings"], 1),
              "best_rssi": e["best"], "last_seen": round(e["last"], 1)} for e in per_rx.values()),
            key=lambda e: -e["sightings"],
        )
        lab = labels()
        peers = detail_db.peers(target_id, since=max(since, last["ts"] - 2 * 3600))
        for p in peers:
            p["label"] = lab.get(p["target_id"], p["target_id"])
        segments = [asdict(s) for s in build_timeline(series, cfg)]
        for s in segments:
            for k in ("enter_ts", "leave_ts", "dwell_seconds"):
                s[k] = round(s[k], 1)
        return Payload.json({
            "target_id": target_id,
            "label": lab.get(target_id, target_id),
            "hours": hours,
            "since": round(since, 1),
            "last_seen": round(last["ts"], 1),
            "sightings_in_range": len(series),
            "segments": segments,
            "receivers": receivers,
            "rssi": {"bucket": bucket, "points": [[b, rx, a, c] for b, rx, a, c in buckets]},
            "peers": peers,
        })

    receiver_health = Refreshing(
        lambda: analytics_db.receiver_health([r["name"] for r in receivers_cache.get()], time.time() - 300),
        min_ttl=3.0,
    )

    @app.get("/api/receivers")
    def receivers_api() -> dict:
        """Per receiver: last packet time and packets in the last 5 minutes."""
        return {"server_time": round(time.time(), 2), "window_seconds": 300, "receivers": receiver_health.get()}

    replay_cache: dict[int, Payload] = {}

    @app.get("/api/at")
    def at_api(request: Request, ts: float) -> Response:
        """Badge states as they were at ``ts`` (snapped to 10 s), in the compact live row format."""
        if not math.isfinite(ts):
            raise HTTPException(422, "ts must be a finite unix timestamp")
        at = int(ts // 10 * 10)
        hit = replay_cache.get(at)
        if hit is None:
            if len(replay_cache) > 256:
                replay_cache.clear()
            rows = [_compact(_live_row(d)) for d in statuses_at(detail_db, cfg, at)]
            hit = replay_cache[at] = Payload.json({"at": at, "cols": list(LIVE_COLS), "targets": rows})
        return hit.response(request)

    @app.get("/api/truth/{target_id}")
    def truth_api(target_id: str) -> dict:
        return evaluate(detail_db, cfg, target_id)

    @app.post("/api/truth")
    async def add_truth(request: Request) -> dict:
        """Record where a badge really is right now. JSON only, so a cross-site form can't post here."""
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "send application/json")
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(422, "send a JSON object")
        target_id, zone = str(body.get("target_id", "")).strip(), str(body.get("zone", "")).strip()
        if not target_id or not zone:
            raise HTTPException(422, "target_id and zone are required")
        zones = {r["name"] for r in _receivers_for_map(cfg, detail_db)}
        if zone not in zones:
            raise HTTPException(422, f"unknown zone {zone!r}; expected one of: {', '.join(sorted(zones))}")
        if detail_db.last_seen(target_id) is None:
            raise HTTPException(404, "unknown target")
        raw_ts = body.get("ts")
        try:
            ts = time.time() if raw_ts is None else float(raw_ts)
        except (TypeError, ValueError):
            raise HTTPException(422, "ts must be a unix timestamp") from None
        if not math.isfinite(ts):
            raise HTTPException(422, "ts must be a finite unix timestamp")
        database.add_truth(ts, target_id, zone)
        return evaluate(detail_db, cfg, target_id)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "stream_clients": hub.clients, "live_seq": live.seq, "analytics_v": analytics.version}

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        """Server-Sent Events: a full 'status' event, then 'delta' events with only the
        badges that changed, plus an 'analytics' event when the aggregates refresh."""

        async def gen():
            q = hub.subscribe()
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        yield await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/")
    def index(request: Request) -> Response:
        return static["index.html"].response(request)

    @app.get("/static/{name}")
    def static_file(name: str, request: Request) -> Response:
        p = static.get(name)
        if p is None:
            raise HTTPException(404)
        return p.response(request)

    return app
