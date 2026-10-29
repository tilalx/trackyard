from __future__ import annotations

import sys
import threading

from .db import Database, Sighting
from .scanner import Sink

_FLUSH_INTERVAL = 0.5
_FLUSH_SIZE = 500
_MAX_BUFFER = 200_000  # rows kept for retry while the DB is failing; oldest dropped beyond this


class SqliteSink(Sink):

    def __init__(self, db: Database, *, batch: bool = True):
        self.db = db
        self._batch = batch
        self._buf: list[Sighting] = []
        self._buf_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._closed = False
        self._failing = False
        self.dropped = 0
        if batch:
            self._schedule_flush()

    def add_sighting(self, s: Sighting) -> None:
        if not self._batch:
            self.db.insert_sightings([s])
            return
        with self._buf_lock:
            self._buf.append(s)
            full = len(self._buf) >= _FLUSH_SIZE
        if full:
            self.flush()

    def flush(self) -> None:
        """Write buffered rows. On a DB error the rows go back into the buffer for the next
        attempt (bounded by _MAX_BUFFER), so a locked or full disk never kills the sink."""
        with self._flush_lock:
            with self._buf_lock:
                batch = self._buf
                self._buf = []
            if not batch:
                return
            try:
                self.db.insert_sightings(batch)
            except Exception as exc:
                with self._buf_lock:
                    self._buf = batch + self._buf
                    over = len(self._buf) - _MAX_BUFFER
                    if over > 0:
                        del self._buf[:over]
                        self.dropped += over
                    pending = len(self._buf)
                if not self._failing:
                    self._failing = True
                    print(f"trackyard: writing sightings failed, will retry ({pending} buffered): {exc!r}", file=sys.stderr)
                return
            if self._failing:
                self._failing = False
                lost = f", {self.dropped} dropped while failing" if self.dropped else ""
                print(f"trackyard: writing sightings recovered{lost}", file=sys.stderr)

    def _schedule_flush(self) -> None:
        def _tick():
            try:
                self.flush()
            finally:
                with self._buf_lock:
                    if not self._closed:
                        self._schedule_flush()
        self._timer = threading.Timer(_FLUSH_INTERVAL, _tick)
        self._timer.daemon = True
        self._timer.start()

    def close(self) -> None:
        with self._buf_lock:
            self._closed = True
            timer = self._timer
        if timer:
            timer.cancel()
        self.flush()
