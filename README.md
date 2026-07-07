# Somnoscope

[![CI](https://github.com/numan7272/Somnoscope/actions/workflows/ci.yml/badge.svg)](https://github.com/numan7272/Somnoscope/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status: feature-complete](https://img.shields.io/badge/status-feature--complete-brightgreen.svg)](#roadmap)

> *Somnoscope* — literally a "sleep microscope." A local sleep tracker that fuses
> wearable, climate, and ML data — no cloud, no vendor lock-in, and every
> processing step auditable on your own machine.

**Status:** Feature-complete — all six roadmap phases are implemented, covered by
97 passing tests, and run end-to-end out of the box (no hardware required).

## Motivation

Commercial sleep trackers give you a "sleep score" and a few colored bars, but:

- Raw data stays locked inside proprietary clouds.
- Scoring algorithms are not inspectable.
- Sensor fusion with room climate (CO₂, temperature, humidity) simply doesn't
  happen — even though that's often the actual reason for restless nights.

Somnoscope solves all three problems on a Raspberry Pi or any ordinary
Linux/Windows/macOS host. The system delivers useful analysis with nothing but
the built-in simulation adapter — real wearables, IoT sensors, and the LLM coach
are optional plug-and-play modules.

## Features

| Feature                                                | Status | Module            |
|--------------------------------------------------------|--------|-------------------|
| Config-driven modularity (per-module `enabled` flags)   | ✅     | `core/`           |
| Simulation adapter — synthetic nights, zero hardware    | ✅     | `adapters/`       |
| Fitbit Air via Google Health API (Feature B, opt-in)    | ✅     | `adapters/`       |
| Muse EEG adapter via BrainFlow → YASA (Feature C, opt-in) | ✅     | `adapters/`       |
| Multi-adapter hybrid operation (`wearable.adapters[]`)  | ✅     | `adapters/`       |
| Sleep pipeline: adapter → report → store                | ✅     | `core/pipeline.py`|
| SleepReport builder + whitebox sleep score (0–100)      | ✅     | `ml_pipeline/`    |
| Raw-EEG staging path (MNE + YASA)                       | ✅     | `ml_pipeline/`    |
| SQLite persistence (default, fully local)               | ✅     | `database/`       |
| InfluxDB time-series writer (optional, for Grafana)     | ✅     | `database/`       |
| MQTT climate-sensor fusion (ESP32 → `report["climate"]`)| ✅     | `iot/`            |
| Three.js web dashboard "Schlaf-Observatorium"           | ✅     | `webui/`          |
| Local LLM coach (Ollama) with rule-based fallback       | ✅     | `llm_coach/`      |
| GitHub Actions CI (pytest on Python 3.11/3.12)          | ✅     | `.github/`        |

### Wearable adapters

- **`simulation`** *(default: on)* — generates a realistic synthetic night
  (sleep stages + vitals) so the whole system works out of the box: for
  development, CI, dashboard demos, and QA. No hardware needed.
- **`fitbit_gh_api`** *(Feature B, default: off)* — pulls Fitbit Air data
  (sleep, heart rate, HRV, SpO₂) through the Google Health API via the
  [`ghealth`](https://github.com/Google-Health-API/google-health-cli) CLI.
  ⚠️ This is the project's single, deliberately sanctioned **cloud exception**:
  the Fitbit Air's BLE payload is end-to-end encrypted and can only be decrypted
  by Google's cloud, so a local BLE read (`fitbit_ble`) is technically
  impossible. The adapter is strictly opt-in and clearly logged as a cloud
  source. Setup guide: [docs/fitbit_air_setup.md](docs/fitbit_air_setup.md).
- **`eeg_muse`** *(Feature C, default: off)* — Muse EEG headband via BrainFlow,
  staged with YASA (`ml_pipeline.stage_raw_eeg`). 100 % local raw 4-channel EEG:
  streams over BLE, buffers the night, and emits sleep-stage segments. Fully
  implemented; enable it with a Muse S/2 and `pip install brainflow numpy mne yasa`.
  (Staging *quality* needs real hardware to validate.)

Multiple adapters can run simultaneously (hybrid operation) — each entry in
`wearable.adapters[]` is toggled independently.

### Web dashboard

`webui/` serves a FastAPI backend plus a Three.js frontend — the
"Schlaf-Observatorium": the last night rendered as a 3D night sky with score
orb, hypnogram, vitals, and history. Fully offline (Three.js is vendored
locally, no CDN), with a WebGL fallback, `prefers-reduced-motion` support, and a
parallel DOM layer for accessibility. The coach card asynchronously loads
advice from the local LLM coach.

The dashboard has two views (masthead switcher): **Diese Nacht** (the Three.js
night scene for the latest report) and **Verlauf** (multi-night trends — score
trend, stage distribution over time, efficiency/HRV, best night & consistency).

It's an installable **PWA**: "add to home screen" on mobile, with a service
worker that caches the app shell so the dashboard opens offline (data still
comes from your local API).

Your data is yours: export all nights as **CSV** or **JSON** from the dashboard
footer (or `GET /api/export/reports.csv?days=N` / `.json`).

API endpoints: `GET /` (dashboard), `GET /api/report/latest`,
`GET /api/reports?limit=N`, `GET /api/trends?days=N`, `GET /api/coaching`,
`GET /api/export/reports.csv?days=N`, `GET /api/export/reports.json?days=N`.

## Architecture

```
[ simulation ]────┐
[ fitbit_gh_api ]─┼──► [ adapters/ ] ──► [ core/pipeline.py ] ──► [ ml_pipeline/ ] ──► [ database/ ]
[ eeg_muse ]──────┘         (unified            SleepPipeline          build_report        SQLiteStore
                          WearableReading)          │                  + sleep score      (+ InfluxDB opt.)
[ ESP32 climate ]──► [ iot/ ] ── ClimateBuffer ─────┘                                          │
     (MQTT)                    (report["climate"])                                             ▼
                                                              [ llm_coach/ ] ◄──── [ webui/ FastAPI + Three.js ]
                                                             (local Ollama or          http://127.0.0.1:8000
                                                             rule-based fallback)
```

Everything runs locally on a single host. Active adapters feed the
`SleepPipeline`, which splits multi-night batches, condenses each night via
`ml_pipeline.build_report` into a SleepReport (optionally enriched with room
climate from the MQTT buffer), and persists it in the local SQLite store. The
dashboard and the LLM coach read from that same store.

## Quickstart

```bash
git clone https://github.com/numan7272/Somnoscope.git
cd Somnoscope

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt

# Capture one night (simulation adapter, no hardware) and exit:
python main.py --once

# Seed N simulated past nights (to populate the trends/"Verlauf" view):
python main.py --backfill 30

# Or run as a continuous tracker daemon:
python main.py

# Start the dashboard (separate process):
uvicorn webui.app:app              # → http://127.0.0.1:8000
```

`main.py` reads `config.yaml`, logs to `logs/somnoscope.log`, runs the active
adapters through the pipeline, and writes SleepReports to
`data/somnoscope.db` — which the dashboard then visualizes. `--once` processes
exactly one poll cycle per adapter (ideal for cron/CI); a custom config path can
be passed as a positional argument.

## Configuration

`config.yaml` controls every module; each optional module has an
`enabled: true/false` flag and can be toggled without breaking the rest of the
system (graceful degradation). Wearables are configured as a list of
independently switchable adapters:

```yaml
wearable:
  enabled: true
  adapters:
    - type: "simulation"        # default: on — synthetic nights, no hardware
      enabled: true
    - type: "fitbit_gh_api"     # Feature B: opt-in cloud exception (see docs)
      enabled: false
    - type: "eeg_muse"          # Feature C: local EEG via BrainFlow → YASA
      enabled: false

climate_sensors:
  enabled: false                # ESP32 via MQTT (local Mosquitto broker)

database:
  enabled: false                # optional InfluxDB channel; SQLite is always on

llm_coach:
  enabled: true                 # local Ollama; rule-based fallback, never cloud
```

Note: `fitbit_ble` is **not implementable** — the Fitbit Air encrypts its BLE
payload end-to-end and only Google's cloud can decrypt it. Use the opt-in
`fitbit_gh_api` adapter instead (see
[docs/fitbit_air_setup.md](docs/fitbit_air_setup.md)).

## Docker / Self-Hosting

The whole stack also runs containerized — still 100 % local, no cloud involved:

```bash
docker compose up -d --build
# → Dashboard: http://localhost:8000
```

This starts three services (see [docker-compose.yml](docker-compose.yml)):

| Service     | What it does                                                        |
|-------------|---------------------------------------------------------------------|
| `web`       | FastAPI dashboard (`uvicorn webui.app:app`), published on port 8000 |
| `tracker`   | The tracker daemon (`python main.py`), writes SleepReports          |
| `mosquitto` | Local MQTT broker for ESP32 climate sensors, port 1883             |

`web` and `tracker` are built from the **same image** and both mount your local
`config.yaml` read-only, so the usual `enabled` flags keep working — edit the
file and `docker compose restart web tracker`.

**Data persistence & shared SQLite:** both app containers share the named
volume `somnoscope-data` mounted at `/app/data` — that's where the common
SQLite database (`somnoscope.db`) lives. The tracker writes reports, the
dashboard reads them; the data survives container rebuilds and restarts
(`docker compose down` keeps it, `docker compose down -v` deletes it). To seed
demo history for the "Verlauf" view:

```bash
docker compose run --rm tracker python main.py --backfill 30
```

**Enabling climate sensors (MQTT):** the Mosquitto broker is always running
(config in [docker/mosquitto.conf](docker/mosquitto.conf), anonymous access for
the local homelab). Point your ESP32 at port 1883 of the Docker host and set in
`config.yaml`:

```yaml
climate_sensors:
  enabled: true
  broker_host: "mosquitto"   # Compose service name instead of "localhost"
```

**Enabling the LLM coach (Ollama):** the optional `ollama` profile runs a local
Ollama server with a persistent model volume:

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.1:8b-instruct-q4_K_M
```

Then set `llm_coach.ollama_url: "http://ollama:11434"` in `config.yaml`.
Without the profile the coach falls back to its rule-based German summary —
never the cloud.

**Optional InfluxDB channel:** `docker compose --profile influxdb up -d` starts
an InfluxDB v2 instance; set `database.enabled: true` and
`database.url: "http://influxdb:8086"` in `config.yaml` (SQLite remains the
primary store either way).

The privacy notice below applies unchanged: the compose file publishes ports
8000/1883 on the host — keep them inside your own network or add
authentication/TLS yourself.

## Tech Stack

* Python 3.10+ (`asyncio`-first)
* `fastapi` + `uvicorn` for the dashboard backend, Three.js (vendored) for the 3D frontend
* SQLite (standard library) as the default store, `influxdb-client` optional
* `paho-mqtt` for ESP32 climate sensors
* `scipy`, `numpy`, `mne`, `yasa` for signal processing and sleep scoring
* Optional: local [Ollama](https://ollama.com) for the LLM coach (rule-based fallback built in)
* Optional: `ghealth` CLI (Feature B) and `brainflow` (Feature C)

## Tests & CI

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # 97 tests
```

Every push and pull request runs the full pytest suite on Python 3.11 and 3.12
via GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) — the
badge at the top reflects the current status.

## Roadmap

All six phases are complete:

- [x] Phase 1 — config loader, logger, bootstrap
- [x] Phase 2 — wearable adapters (simulation, `fitbit_gh_api`, `eeg_muse`) via the adapter registry
- [x] Phase 3 — MQTT subscriber + climate buffer for ESP32 climate data
- [x] Phase 4 — ML pipeline: SleepReport builder, sleep score, raw-EEG staging path (MNE/YASA)
- [x] Phase 5 — persistence (SQLite default, InfluxDB optional) + FastAPI/Three.js dashboard
- [x] Phase 6 — local LLM coach (Ollama + rule-based fallback), integrated into the dashboard

## Core principles

1. **Edge AI / privacy first** — all health data stays local; no cloud calls for
   bio-signals or LLM inference. The single sanctioned exception is the opt-in
   `fitbit_gh_api` adapter (see above), which is off by default.
2. **Graceful degradation** — every optional module can fail or be disabled
   without taking the rest of the system down.
3. **Whitebox approach** — every processing step (especially before ML scoring)
   is logged; you can see *why* a score came out the way it did.
4. **Adapter pattern** — device-specific parsers translate proprietary data into
   a unified internal format (`WearableReading`).

## Contributing

Bugs and ideas go through [GitHub Issues](https://github.com/numan7272/Somnoscope/issues).
The full workflow is documented in [CONTRIBUTING.md](CONTRIBUTING.md) (currently in
German — English translation coming soon). Short version:

1. **Open an issue first** — even for small bugs, with reproduction steps and
   expected behavior.
2. **One branch per issue** — format: `fix/issue-<nr>-shortname` or
   `feat/issue-<nr>-shortname`.
3. **Document root cause and chosen solution in the issue.**
4. **Close the issue only when the PR is merged** — not when it's opened.

## License

MIT — see [LICENSE](LICENSE).

## Privacy notice

Somnoscope processes health data. Even though everything runs locally: if you
expose the system beyond your own network (e.g., by putting the dashboard or
Grafana on the public internet), you are responsible for authentication, TLS,
and GDPR/HIPAA compliance yourself. The defaults are deliberately "localhost
only" — and if you enable the optional `fitbit_gh_api` adapter, your Fitbit Air
data takes one documented hop through Google's cloud (see
[docs/fitbit_air_setup.md](docs/fitbit_air_setup.md)).
