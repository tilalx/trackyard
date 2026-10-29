from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import typer

from .config import load_config
from .db import Database

app = typer.Typer(help="BLE badge tracker.", no_args_is_help=True)


def _load():
    cfg = load_config()
    return cfg, Database(cfg.db_path)


def _print_startup_diag(cfg, db: Database) -> None:
    import sys

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console(stderr=True)
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()

    table.add_row("platform", f"{sys.platform} ({__import__('platform').machine()})")

    ble_ok = True
    try:
        import bleak  # noqa: F401
        table.add_row("bluetooth", "[green]bleak available[/]")
    except ImportError:
        ble_ok = False
        table.add_row("bluetooth", "[red]bleak not installed[/]")

    if cfg.badge_ibeacon_uuid:
        table.add_row("badge filter", f"iBeacon UUID {cfg.badge_ibeacon_uuid}")
    else:
        table.add_row("badge filter", "[yellow]none — every iBeacon in range is recorded (set badge_ibeacon_uuid)[/]")

    rx_names = [r.name for r in cfg.receivers]
    table.add_row("receivers", f"{len(rx_names)} configured ({', '.join(rx_names)})")

    s_count, d_count = db.counts()
    table.add_row("database", f"{s_count:,} sightings, {d_count:,} density rows")

    status = "[green]ready[/]" if ble_ok else "[red]BLE unavailable[/]"
    console.print(Panel(table, title=f"[bold]trackyard[/] — {status}", border_style="dim", expand=False))


_PURGE_INTERVAL = 3600.0


def _start_retention(cfg, db: Database) -> threading.Event:
    """Purge rows older than retention_days now, then hourly, until the returned event is set."""
    stop = threading.Event()

    def loop() -> None:
        while True:
            try:
                n = db.purge_old(cfg.retention_days)
                if n:
                    typer.echo(f"retention: purged {n:,} sighting(s) older than {cfg.retention_days} days", err=True)
            except Exception as exc:
                typer.echo(f"retention: purge failed: {exc!r}", err=True)
            if stop.wait(_PURGE_INTERVAL):
                return

    threading.Thread(target=loop, name="retention", daemon=True).start()
    return stop


@app.command(help="Scan + web dashboard together. Ctrl-C to stop.")
def run(
    receiver: str = typer.Option("mac", help="Label stored with each sighting."),
    adapter: str = typer.Option("", help="BLE adapter (leave empty for the default)."),
    host: str = typer.Option(None, help="Web dashboard bind address."),
    port: int = typer.Option(None, help="Web dashboard port."),
    seconds: float = typer.Option(None, help="Stop after N seconds (default: run until Ctrl-C)."),
):
    import time

    import uvicorn

    from .scanner import run_scan
    from .sinks import SqliteSink
    from .web import create_app

    cfg, db = _load()
    host, port = host or cfg.collector_host, port or cfg.collector_port
    web_app = create_app(cfg, db)

    server = uvicorn.Server(uvicorn.Config(
        web_app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=3,  # live-stream connections never finish on their own
    ))
    web_thread = threading.Thread(target=server.run, daemon=True)
    web_thread.start()
    # uvicorn exits its thread quietly when it can't bind; don't start scanning behind a dead dashboard.
    deadline = time.monotonic() + 10
    while not server.started and web_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        typer.echo(f"error: dashboard could not start on {host}:{port} (port in use?). Try --port.", err=True)
        raise typer.Exit(1)

    sink = SqliteSink(db)
    stop_retention = _start_retention(cfg, db)
    _print_startup_diag(cfg, db)
    typer.echo(f"Scanning as '{receiver}' — dashboard at http://{host}:{port}/")
    try:
        asyncio.run(run_scan(cfg, receiver, adapter, sink, duration=seconds))
    except KeyboardInterrupt:
        pass
    finally:
        stop_retention.set()
        sink.close()
        server.should_exit = True


@app.command(help="Continuous passive scan only. Stores all detected badges.")
def scan(
    receiver: str = typer.Option("mac", help="Label stored with each sighting."),
    adapter: str = typer.Option("", help="BLE adapter (leave empty for the default)."),
    seconds: float = typer.Option(None, help="Stop after N seconds (default: run until Ctrl-C)."),
):
    from .scanner import run_scan
    from .sinks import SqliteSink

    cfg, db = _load()
    sink = SqliteSink(db)
    stop_retention = _start_retention(cfg, db)
    _print_startup_diag(cfg, db)
    typer.echo(f"Scanning as '{receiver}'.")
    try:
        asyncio.run(run_scan(cfg, receiver, adapter, sink, duration=seconds))
    except KeyboardInterrupt:
        pass
    finally:
        stop_retention.set()
        sink.close()


@app.command(help="Serve the local web dashboard (reads the local SQLite; only ground-truth marks are written).")
def web(
    host: str = typer.Option(None),
    port: int = typer.Option(None),
):
    import uvicorn

    from .web import create_app

    cfg, db = _load()
    uvicorn.run(create_app(cfg, db), host=host or cfg.collector_host, port=port or cfg.collector_port, timeout_graceful_shutdown=3)


@app.command(help="Per-target timeline: zone / enter / leave / dwell, plus gaps.")
def timeline(
    target: str = typer.Argument(..., help="Badge id, e.g. TN_00412345."),
    fmt: str = typer.Option("table", "--format", help="table | csv | json"),
    out: Path = typer.Option(None, help="Write export to this file instead of stdout."),
):
    from .timefmt import fmt_local
    from .timeline import build_timeline, to_csv, to_json

    cfg, db = _load()
    label = cfg.targets.get(target, target)
    segs = build_timeline(db.recent_rssi(target, limit=10**9), cfg)
    if fmt == "json":
        text = to_json(target, label, segs)
    elif fmt == "csv":
        text = to_csv(segs)
    else:
        rows = [
            f"{s.kind:4} {s.zone or '(gap)':16} {fmt_local(s.enter_ts, '%Y-%m-%d %H:%M:%S')}–{fmt_local(s.leave_ts, '%H:%M:%S')}"
            f"  {int(s.dwell_seconds)}s"
            for s in segs
        ]
        text = "\n".join([f"times in {fmt_local(segs[0].enter_ts, '%Z (UTC%z)')}", *rows]) if rows else "(no data)"
    if out:
        out.write_text(text)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@app.command(help="Terminal dashboard: targets, last seen, zone, RSSI sparkline.")
def live():
    from .live import run

    cfg, db = _load()
    run(db, cfg)


@app.command(help="Delete rows older than retention_days now (scan/run also do this at startup and hourly).")
def purge():
    cfg, db = _load()
    n = db.purge_old(cfg.retention_days)
    typer.echo(f"purged {n} sighting row(s) older than {cfg.retention_days} days")


if __name__ == "__main__":
    app()
