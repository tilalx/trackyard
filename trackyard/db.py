from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    receiver  TEXT    NOT NULL,
    adapter_id TEXT   NOT NULL,
    target_id TEXT    NOT NULL,
    address   TEXT    NOT NULL,
    rssi      INTEGER NOT NULL,
    tx_power  INTEGER,
    raw_adv   BLOB
);
CREATE INDEX IF NOT EXISTS idx_sightings_target_ts ON sightings(target_id, ts);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_receiver_ts ON sightings(receiver, ts);

CREATE TABLE IF NOT EXISTS ground_truth (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    target_id TEXT    NOT NULL,
    zone      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ground_truth_target_ts ON ground_truth(target_id, ts);

CREATE TABLE IF NOT EXISTS density (
    minute    INTEGER NOT NULL,
    receiver  TEXT    NOT NULL,
    unique_count INTEGER NOT NULL,
    PRIMARY KEY (minute, receiver)
);
"""


@dataclass(frozen=True)
class Sighting:
    ts: float
    receiver: str
    adapter_id: str
    target_id: str
    address: str
    rssi: int
    tx_power: int | None = None
    raw_adv: bytes | None = None


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64 MB
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_column("sightings", "tx_power", "INTEGER")

    def _ensure_column(self, table: str, column: str, col_type: str) -> None:
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            self.conn.commit()

    def insert_sightings(self, rows: list[Sighting]) -> None:
        if not rows:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT INTO sightings (ts, receiver, adapter_id, target_id, address, rssi, tx_power, raw_adv)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(r.ts, r.receiver, r.adapter_id, r.target_id, r.address, r.rssi, r.tx_power, r.raw_adv) for r in rows],
            )
            self.conn.commit()

    def set_density(self, minute: int, receiver: str, unique_count: int) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO density (minute, receiver, unique_count) VALUES (?,?,?)"
                " ON CONFLICT(minute, receiver) DO UPDATE SET unique_count=excluded.unique_count",
                (minute, receiver, unique_count),
            )
            self.conn.commit()

    def sightings_for(self, target_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sightings WHERE target_id=? ORDER BY ts", (target_id,)
        ).fetchall()

    def all_sightings(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sightings ORDER BY ts").fetchall()

    def iter_sightings(self):
        cur = self.conn.execute("SELECT * FROM sightings ORDER BY ts")
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            yield from batch

    def distinct_targets(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT target_id FROM sightings ORDER BY target_id").fetchall()
        return [r["target_id"] for r in rows]

    def receiver_names(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT receiver FROM sightings ORDER BY receiver").fetchall()
        return [r["receiver"] for r in rows]

    def last_seen(self, target_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sightings WHERE target_id=? ORDER BY ts DESC LIMIT 1", (target_id,)
        ).fetchone()

    def recent_rssi(self, target_id: str, limit: int = 30) -> list[tuple[float, int, str, int | None]]:
        rows = self.conn.execute(
            "SELECT ts, rssi, receiver, tx_power FROM sightings WHERE target_id=? ORDER BY ts DESC LIMIT ?",
            (target_id, limit),
        ).fetchall()
        return [(r["ts"], r["rssi"], r["receiver"], r["tx_power"]) for r in reversed(rows)]

    def bulk_last_seen(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.* FROM sightings s "
            "INNER JOIN (SELECT target_id, MAX(ts) AS max_ts FROM sightings GROUP BY target_id) g "
            "ON s.target_id = g.target_id AND s.ts = g.max_ts "
            "ORDER BY s.target_id"
        ).fetchall()

    def bulk_recent_rssi(self, since_ts: float) -> dict[str, list[tuple[float, int, str, int | None]]]:
        rows = self.conn.execute(
            "SELECT target_id, ts, rssi, receiver, tx_power FROM sightings WHERE ts >= ? ORDER BY target_id, ts",
            (since_ts,),
        ).fetchall()
        out: dict[str, list[tuple[float, int, str, int | None]]] = {}
        for r in rows:
            out.setdefault(r["target_id"], []).append((r["ts"], r["rssi"], r["receiver"], r["tx_power"]))
        return out

    def density_total_window(self, since_ts: float) -> int:
        minute_floor = int(since_ts // 60)
        row = self.conn.execute(
            "SELECT MAX(unique_count) AS m FROM density WHERE minute >= ?", (minute_floor,)
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def density_latest_per_receiver(self, since_ts: float) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT receiver, unique_count FROM density d WHERE minute >= ? AND minute = "
            "(SELECT MAX(minute) FROM density WHERE receiver = d.receiver)",
            (int(since_ts // 60),),
        ).fetchall()
        return {r["receiver"]: int(r["unique_count"]) for r in rows}

    def tag_stats(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT target_id, COUNT(*) AS cnt, MIN(ts) AS first_seen, MAX(ts) AS last_seen "
            "FROM sightings GROUP BY target_id ORDER BY cnt DESC"
        ).fetchall()
        return [{
            "target_id": r["target_id"],
            "sightings": r["cnt"],
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "nearby_tags": 0,
        } for r in rows]

    def proximity_analysis(self, bucket_seconds: int = 300, lookback_hours: float = 2.0) -> tuple[dict[str, int], list[dict]]:
        since = time.time() - lookback_hours * 3600
        cur = self.conn.execute(
            "SELECT target_id, CAST(ts / ? AS INTEGER) AS bucket, receiver "
            "FROM sightings WHERE ts >= ? GROUP BY target_id, bucket, receiver",
            (bucket_seconds, since),
        )
        slot_targets: dict[tuple[int, str], list[str]] = {}
        while True:
            batch = cur.fetchmany(10000)
            if not batch:
                break
            for r in batch:
                slot_targets.setdefault((r["bucket"], r["receiver"]), []).append(r["target_id"])
        nearby: dict[str, set[str]] = {}
        pair_counts: dict[tuple[str, str], int] = {}
        for targets in slot_targets.values():
            n = len(targets)
            if n < 2:
                continue
            tset = set(targets)
            for tid in targets:
                if tid not in nearby:
                    nearby[tid] = set()
                nearby[tid].update(tset)
            if n <= 50:
                targets.sort()
                for i, a in enumerate(targets):
                    for b in targets[i + 1:]:
                        pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
        nearby_counts = {tid: len(s) - 1 for tid, s in nearby.items()}
        pairs = [{"a": k[0], "b": k[1], "count": v} for k, v in pair_counts.items() if v >= 2]
        pairs.sort(key=lambda x: x["count"], reverse=True)
        return nearby_counts, pairs[:50]

    def encounter_counts(self, gap_seconds: float = 60.0, lookback_hours: float = 24.0) -> dict[str, int]:
        since = time.time() - lookback_hours * 3600
        cur = self.conn.execute(
            "SELECT target_id, ts FROM sightings WHERE ts >= ? ORDER BY target_id, ts",
            (since,),
        )
        counts: dict[str, int] = {}
        prev_id: str | None = None
        prev_ts: float = 0.0
        while True:
            batch = cur.fetchmany(10000)
            if not batch:
                break
            for r in batch:
                tid, ts = r["target_id"], r["ts"]
                if tid != prev_id:
                    counts[tid] = 1
                    prev_id = tid
                elif ts - prev_ts > gap_seconds:
                    counts[tid] += 1
                prev_ts = ts
        return counts

    def rssi_distribution(self, bucket_size: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT CAST(rssi / ? AS INTEGER) * ? AS bucket, COUNT(*) AS cnt "
            "FROM sightings GROUP BY bucket ORDER BY bucket",
            (bucket_size, bucket_size),
        ).fetchall()
        return [{"rssi": r["bucket"], "count": r["cnt"]} for r in rows]

    def hourly_activity(self) -> list[dict]:
        """Hour-of-day buckets in the server's local time (DST-aware per sighting)."""
        rows = self.conn.execute(
            "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour, "
            "COUNT(*) AS cnt, COUNT(DISTINCT target_id) AS tags "
            "FROM sightings GROUP BY hour ORDER BY hour"
        ).fetchall()
        return [{"hour": r["hour"], "count": r["cnt"], "tags": r["tags"]} for r in rows]

    def sightings_histogram(self, bucket_seconds: int = 60, since: float = 0.0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, COUNT(*) AS cnt, "
            "COUNT(DISTINCT target_id) AS tags "
            "FROM sightings WHERE ts >= ? GROUP BY bucket ORDER BY bucket",
            (bucket_seconds, bucket_seconds, since),
        ).fetchall()
        return [{"ts": r["bucket"], "count": r["cnt"], "tags": r["tags"]} for r in rows]

    def zone_flow(self, cfg_gap: float = 60.0, window: int = 10, lookback_hours: float = 4.0) -> list[dict]:
        since = time.time() - lookback_hours * 3600
        rows = self.conn.execute(
            "SELECT target_id, CAST(ts / ? AS INTEGER) AS bucket, receiver, "
            "SUM(rssi) AS sum_rssi, COUNT(*) AS cnt "
            "FROM sightings WHERE ts >= ? GROUP BY target_id, bucket, receiver "
            "ORDER BY target_id, bucket",
            (window, since),
        ).fetchall()
        transitions: dict[tuple[str, str], int] = {}
        prev_tid: str | None = None
        prev_zone: str | None = None
        prev_bucket: int = 0
        bucket_best: dict[str, float] = {}
        cur_bucket: int = -1

        def _flush_bucket(tid: str, bucket: int) -> None:
            nonlocal prev_zone, prev_bucket
            if not bucket_best:
                return
            best = max(bucket_best, key=bucket_best.get)  # type: ignore[arg-type]
            if prev_zone is not None and best != prev_zone and (bucket - prev_bucket) * window <= cfg_gap:
                key = (prev_zone, best)
                transitions[key] = transitions.get(key, 0) + 1
            prev_zone = best
            prev_bucket = bucket

        for r in rows:
            tid, bucket, receiver = r["target_id"], r["bucket"], r["receiver"]
            avg_rssi = r["sum_rssi"] / r["cnt"]
            if tid != prev_tid:
                if prev_tid is not None:
                    _flush_bucket(prev_tid, cur_bucket)
                prev_tid = tid
                prev_zone = None
                cur_bucket = bucket
                bucket_best = {}
            if bucket != cur_bucket:
                _flush_bucket(tid, cur_bucket)
                cur_bucket = bucket
                bucket_best = {}
            bucket_best[receiver] = avg_rssi

        if prev_tid is not None:
            _flush_bucket(prev_tid, cur_bucket)

        result = [{"from": k[0], "to": k[1], "count": v} for k, v in transitions.items()]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result[:30]

    def route_density(self, bucket_seconds: int = 60, lookback_hours: float = 24.0) -> list[dict]:
        since = time.time() - lookback_hours * 3600
        rows = self.conn.execute(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, receiver, "
            "COUNT(DISTINCT target_id) AS unique_tags, COUNT(*) AS sightings, "
            "AVG(rssi) AS avg_rssi, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM sightings WHERE ts >= ? "
            "GROUP BY bucket, receiver ORDER BY bucket",
            (bucket_seconds, bucket_seconds, since),
        ).fetchall()
        return [{"ts": r["bucket"], "receiver": r["receiver"],
                 "unique_tags": r["unique_tags"], "sightings": r["sightings"],
                 "avg_rssi": round(r["avg_rssi"]), "first_ts": r["first_ts"],
                 "last_ts": r["last_ts"]} for r in rows]

    def change_token(self) -> tuple:
        # A lone MIN/MAX on the rowid is a single b-tree lookup (combined in one SELECT it
        # becomes a full scan). MIN moves on purge, MAX on insert.
        lo = self.conn.execute("SELECT MIN(id) FROM sightings").fetchone()[0]
        hi = self.conn.execute("SELECT MAX(id) FROM sightings").fetchone()[0]
        d = self.conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(unique_count),0) AS u, MAX(minute) AS m FROM density").fetchone()
        return (lo, hi, d["c"], d["u"], d["m"])

    def target_series(
        self, target_id: str, since: float, until: float = float("inf")
    ) -> list[tuple[float, int, str, int | None]]:
        rows = self.conn.execute(
            "SELECT ts, rssi, receiver, tx_power FROM sightings WHERE target_id=? AND ts>=? AND ts<=? ORDER BY ts",
            (target_id, since, until),
        ).fetchall()
        return [(r["ts"], r["rssi"], r["receiver"], r["tx_power"]) for r in rows]

    def target_rssi_buckets(self, target_id: str, since: float, bucket_seconds: int) -> list[tuple[float, str, float, int]]:
        rows = self.conn.execute(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS b, receiver, AVG(rssi) AS a, COUNT(*) AS c "
            "FROM sightings WHERE target_id=? AND ts>=? GROUP BY b, receiver ORDER BY b",
            (bucket_seconds, bucket_seconds, target_id, since),
        ).fetchall()
        return [(r["b"], r["receiver"], round(r["a"], 1), r["c"]) for r in rows]

    def peers(self, target_id: str, since: float, bucket_seconds: int = 300, limit: int = 15) -> list[dict]:
        """Other targets heard by the same receiver in the same time bucket, ranked by shared buckets."""
        rows = self.conn.execute(
            "WITH mine AS (SELECT DISTINCT CAST(ts / ? AS INTEGER) AS b, receiver FROM sightings "
            "  WHERE target_id=? AND ts>=?) "
            "SELECT s.target_id, COUNT(DISTINCT m.b || '|' || m.receiver) AS shared FROM mine m "
            "JOIN sightings s ON s.receiver = m.receiver AND s.ts >= m.b * ? AND s.ts < (m.b + 1) * ? "
            "WHERE s.target_id != ? GROUP BY s.target_id ORDER BY shared DESC LIMIT ?",
            (bucket_seconds, target_id, since, bucket_seconds, bucket_seconds, target_id, limit),
        ).fetchall()
        return [{"target_id": r["target_id"], "shared": r["shared"]} for r in rows]

    def receiver_health(self, names: list[str], since_ts: float) -> list[dict]:
        """Last packet time and packets since ``since_ts`` per receiver (two index lookups each)."""
        out = []
        for name in names:
            last = self.conn.execute("SELECT MAX(ts) FROM sightings WHERE receiver=?", (name,)).fetchone()[0]
            recent = self.conn.execute(
                "SELECT COUNT(*) FROM sightings WHERE receiver=? AND ts>=?", (name, since_ts)
            ).fetchone()[0]
            out.append({"receiver": name, "last_ts": last, "recent": int(recent)})
        return out

    def sightings_between(self, t0: float, t1: float) -> dict[str, list[tuple[float, int, str, int | None]]]:
        rows = self.conn.execute(
            "SELECT target_id, ts, rssi, receiver, tx_power FROM sightings WHERE ts >= ? AND ts <= ? ORDER BY ts",
            (t0, t1),
        ).fetchall()
        out: dict[str, list[tuple[float, int, str, int | None]]] = {}
        for r in rows:
            out.setdefault(r["target_id"], []).append((r["ts"], r["rssi"], r["receiver"], r["tx_power"]))
        return out

    def presence_spans(self, target_ids: list[str], gap_seconds: float) -> dict[str, list[list[float]]]:
        """Continuous [start, end] spans per target, split wherever it went unheard for > gap_seconds."""
        out: dict[str, list[list[float]]] = {}
        for tid in target_ids:
            spans: list[list[float]] = []
            cur = self.conn.execute("SELECT ts FROM sightings WHERE target_id=? ORDER BY ts", (tid,))
            while batch := cur.fetchmany(10000):
                for (ts,) in batch:
                    if spans and ts - spans[-1][1] <= gap_seconds:
                        spans[-1][1] = ts
                    else:
                        spans.append([ts, ts])
            out[tid] = [[round(a, 1), round(b, 1)] for a, b in spans]
        return out

    def add_truth(self, ts: float, target_id: str, zone: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO ground_truth (ts, target_id, zone) VALUES (?,?,?)", (ts, target_id, zone)
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def truths(self, target_id: str | None = None) -> list[sqlite3.Row]:
        if target_id is None:
            return self.conn.execute("SELECT * FROM ground_truth ORDER BY ts").fetchall()
        return self.conn.execute(
            "SELECT * FROM ground_truth WHERE target_id=? ORDER BY ts", (target_id,)
        ).fetchall()

    def reader(self) -> Database:
        """A second connection for long reads, so they don't hold this connection's mutex."""
        if self.path == ":memory:" or self.path.startswith("file::memory:"):
            return self
        return Database(self.path)

    def counts(self) -> tuple[int, int]:
        s = self.conn.execute("SELECT COUNT(*) AS c FROM sightings").fetchone()["c"]
        d = self.conn.execute("SELECT COUNT(*) AS c FROM density").fetchone()["c"]
        return int(s), int(d)

    def purge_old(self, retention_days: int, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cutoff = now - retention_days * 86400
        with self._lock:
            cur = self.conn.execute("DELETE FROM sightings WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM ground_truth WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM density WHERE minute < ?", (int(cutoff // 60),))
            self.conn.commit()
            return cur.rowcount

    def close(self) -> None:
        self.conn.close()
