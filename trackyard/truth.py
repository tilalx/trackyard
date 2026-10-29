from __future__ import annotations

from .config import Config
from .db import Database
from .localizer import localize


def predicted_zone(db: Database, cfg: Config, target_id: str, ts: float) -> str | None:
    """Zone the localizer assigns to ``target_id`` at ``ts`` (None if it was not heard around then)."""
    w = cfg.localizer.window_seconds
    lookback = w * (cfg.localizer.hysteresis_windows + 3)
    windows = localize(db.target_series(target_id, ts - lookback, ts + w), cfg)
    before = [wz for wz in windows if wz.window_start <= ts]
    return (before[-1].zone or None) if before else None


def evaluate(db: Database, cfg: Config, target_id: str | None = None) -> dict:
    """Compare ground-truth marks with the localizer's zone at the same moment."""
    marks = []
    confusion: dict[str, dict[str, int]] = {}
    for m in db.truths(target_id):
        pred = predicted_zone(db, cfg, m["target_id"], m["ts"])
        marks.append({"ts": round(m["ts"], 1), "target_id": m["target_id"], "zone": m["zone"],
                      "predicted": pred, "ok": pred == m["zone"]})
        row = confusion.setdefault(m["zone"], {})
        row[pred or ""] = row.get(pred or "", 0) + 1
    correct = sum(m["ok"] for m in marks)
    return {
        "n": len(marks),
        "correct": correct,
        "accuracy": round(correct / len(marks), 3) if marks else None,
        "confusion": confusion,
        "marks": marks[-50:],
    }
