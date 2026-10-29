<div align="center">

# 📛 trackyard

**Your conference badge is broadcasting where you are. This tool shows how much that gives away.**

A passive Bluetooth Low Energy tracker for iBeacon-based event badges, with a live
floor map and movement timelines.

<sub><i>track</i> + lan<i>yard</i>: the badge on your lanyard is the tracker.</sub>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![Status](https://img.shields.io/badge/status-proof%20of%20concept-orange)

</div>

---

> [!WARNING]
> **Security research tool. Read [Responsible use](#responsible-use) before you run it.**
> Badge ids are tied to named attendees, so a movement trace is personal data.
> Track only badges whose holders have agreed to it and keep the data short-lived.

## Why this exists

Many events hand out BLE badges that advertise a **fixed iBeacon identifier in
cleartext**, about every two seconds, to anyone listening. No pairing, no
special hardware: a laptop or a Raspberry Pi is enough.

Put a few receivers at known spots and those advertisements turn into a
timeline: *which room a badge holder was in, when they arrived and how long
they stayed*. Because the badge number maps to the attendee record, the
timeline belongs to a named person.

trackyard demonstrates this end to end, so organisers and vendors can see the
problem for themselves and fix it.

## Features

| | |
|---|---|
| 📡 **Passive scanning** | Listens for iBeacon frames with [bleak](https://github.com/hbldh/bleak) on macOS (CoreBluetooth) and Linux (BlueZ). Never connects to or writes to a badge. |
| 🎯 **Fleet filter** | Records only badges with your event's iBeacon UUID; `discover.py` helps you find it. |
| 🗺️ **Zone localizer** | RSSI windows, a path-loss distance model, weighted fusion across receivers and hysteresis, so badges don't flicker between rooms. |
| 🖥️ **Web dashboard** | Live floor map, zone occupancy, per-badge detail drawer, receiver health, analytics, and a time scrubber that replays any past moment. |
| ✅ **Ground truth** | Mark where a badge really was to measure the localizer's accuracy with a confusion matrix. |
| ⏱️ **Timelines and exports** | Enter / leave / dwell per badge as a table, CSV or JSON. |
| 🧹 **Retention built in** | Old sightings are purged at startup and hourly. |
| 🔒 **Local only** | SQLite on disk, dashboard bound to `127.0.0.1`, fonts bundled, no telemetry, no outbound requests (unless you set an external `map_image`). |

## How it works

```
 badge ──iBeacon adv──▶ receiver (BLE adapter) ──▶ IngestEngine ──▶ SQLite (WAL)
                        `trackyard scan`          fleet-UUID filter      │
                                                                          ▼
                       dashboard · timeline · report ◀── localizer (RSSI → zone)
```

1. **Scan.** Each receiver runs `trackyard scan` and stores every matching
   advertisement: time, receiver, badge id (`TN_<major><minor>`), RSSI.
2. **Localize.** Sightings are bucketed into windows (default 10 s). Each badge is
   placed in the zone of the receiver that hears it best, smoothed by hysteresis.
3. **Present.** The dashboard, `timeline`, `live` and `report` commands read the
   same database.

## Quick start

Requires Python 3.11+ and a Bluetooth adapter. With [uv](https://docs.astral.sh/uv/):

```bash
git clone <this repo> trackyard && cd trackyard
uv sync

# create your local config from the examples (the real files are git-ignored)
cp config.example.yaml config.yaml
cp receivers.example.yaml receivers.yaml
cp targets.example.yaml targets.yaml

# 1. find your badge's iBeacon UUID and put it into config.yaml
uv run python discover.py -t 30 --min-rssi -70

# 2. scan and open the dashboard
uv run trackyard run
# → http://127.0.0.1:8000/
```

With plain pip, run `python -m venv .venv && . .venv/bin/activate && pip install -e .`
and drop the `uv run` prefix.

> [!NOTE]
> **macOS:** grant Bluetooth access to your terminal app the first time
> (System Settings → Privacy & Security → Bluetooth). CoreBluetooth hides real
> MAC addresses, which doesn't matter here: the badge id comes from the payload.
>
> **Linux:** BlueZ must be running. Pass `--adapter hci0` (or `hci1`, …) to select an adapter.

## Commands

| Command | What it does |
|---|---|
| `trackyard run` | Scan **and** serve the dashboard in one process. |
| `trackyard scan --receiver pi-a --adapter hci0` | Scan only, labelling sightings with this receiver name. |
| `trackyard web` | Serve the dashboard from an existing database. |
| `trackyard live` | Terminal dashboard: last seen, zone, RSSI sparkline. |
| `trackyard timeline TN_00412345 --format csv --out t.csv` | Zone / enter / leave / dwell timeline for one badge. |
| `trackyard purge` | Delete sightings older than `retention_days` right now. |

Every command finds `config.yaml` by walking up from the current directory, or
uses `$TRACKYARD_DIR` if set. All `scan`/`run` options are listed under `--help`.

## Configuration

Three YAML files in the project directory. Only the `*.example.yaml` versions are
tracked in git.

<details>
<summary><b><code>config.yaml</code></b>: filter, localizer, retention, storage, web</summary>

```yaml
badge_ibeacon_uuid: "00000000-0000-0000-0000-000000000000"  # your fleet's UUID

localizer:
  window_seconds: 10       # RSSI aggregation window
  hysteresis_windows: 2    # windows a new zone must win before a badge "moves"
  gap_seconds: 60          # silence after which a badge counts as gone
  distance:
    enabled: true
    tx_power_at_1m: -59
    path_loss_n: 2.5

retention_days: 7

storage:
  db_path: "trackyard.sqlite"

web:
  host: "127.0.0.1"
  port: 8000
  # map_image: "https://example.org/floorplan.png"
```

If `badge_ibeacon_uuid` is empty, **every** iBeacon in range is recorded, including
unrelated devices. The startup banner warns about this.
</details>

<details>
<summary><b><code>receivers.yaml</code></b>: scan nodes and their map positions</summary>

```yaml
receivers:
  - name: pi-a
    location: "Hall A"
    adapter: hci0
    x: 20      # % of map width  (optional)
    y: 35      # % of map height (optional)
```
</details>

<details>
<summary><b><code>targets.yaml</code></b>: friendly labels for badges</summary>

```yaml
targets:
  TN_00412345: "My badge"
```

Unlabelled badges appear under their id.
</details>

### Several receivers

Zone accuracy grows with the number of receivers. Run one `scan` per adapter,
each with its own `--receiver` name matching `receivers.yaml`, all writing to the
same database (SQLite in WAL mode handles several local writers), plus one
`trackyard web`:

```bash
trackyard scan --receiver pi-a --adapter hci0 &
trackyard scan --receiver pi-b --adapter hci1 &
trackyard web
```

## Responsible use

trackyard is published to **demonstrate a privacy weakness so it gets fixed**, not
to monitor people.

- **Consent.** Only track badges whose holders have agreed, starting with your own.
  `targets.yaml` labels are for consenting participants only.
- **Bystanders.** With the fleet filter set, the scanner still records *every* badge
  of that fleet in range, not only the labelled ones. Keep captures short, choose
  locations deliberately and delete data you don't need.
- **Data protection.** A badge id linked to a timeline is personal data under the
  GDPR and similar laws. You need a lawful basis, a stated purpose, minimal
  retention (`retention_days`) and secure storage. Never publish raw captures;
  share aggregated or anonymised results.
- **Event rules.** Get permission from the organiser before scanning at their venue.
- **Dashboard exposure.** The web UI has no authentication. Keep it on
  `127.0.0.1`, or put it behind a VPN or authenticating reverse proxy.

You alone are responsible for complying with the law where you use this
software. It is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).

## Mitigations for badge vendors

- Rotate the advertised identifier (e.g. resolvable private identifiers), so a
  captured value cannot be linked across time or place.
- Decouple the advertised value from the attendee record; resolve it only
  server-side, under access control.
- Lower TX power and advertising rate to shrink the capture radius.
- Let attendees switch the beacon off, or opt out of beacon badges entirely.

## Development

```bash
uv sync --extra dev
uv run pytest           # test suite, no Bluetooth hardware needed
uv run ruff check .     # lint
```

Layout:

```
trackyard/
  badge.py       iBeacon frame parsing, badge-id format
  scanner.py     bleak scan loop → IngestEngine → sink
  sinks.py       batched SQLite writer
  db.py          schema, queries, retention
  localizer.py   RSSI windows, distance model, hysteresis
  status.py      current / historical zone per badge
  timeline.py    enter / leave / dwell segments, CSV / JSON export
  truth.py       ground-truth marks and accuracy
  live.py        terminal dashboard (rich)
  web.py         FastAPI app: JSON API + server-sent events
  static/        dashboard (vanilla JS, no build step)
discover.py      one-off BLE survey to identify a badge's advertisement format
tests/
```

## License

[MIT](LICENSE) © 2026 tilalx.

Bundled IBM Plex fonts are under the SIL Open Font License 1.1 and Carbon colour
tokens under Apache 2.0; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Not affiliated with or endorsed by IBM, any badge vendor or any event organiser.
