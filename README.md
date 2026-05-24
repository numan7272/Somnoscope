# Somnoscope

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status: WIP](https://img.shields.io/badge/status-work--in--progress-orange.svg)](#roadmap)

> *Somnoscope* — literally a "sleep microscope." A local sleep tracker that fuses
> wearable, climate, and ML data — no cloud, no vendor lock-in, and no claim to be
> a finished product you can't audit yourself.

**Status:** Phase 1 — infrastructure foundation. Not production-ready yet.

## Motivation

Commercial sleep trackers give you a "sleep score" and a few colored bars, but:

- Raw data stays locked inside proprietary clouds.
- Scoring algorithms are not inspectable.
- Sensor fusion with room climate (CO₂, temperature, humidity) simply doesn't
  happen — even though that's often the actual reason for restless nights.

Somnoscope is an attempt to solve all three problems on a Raspberry Pi or any
ordinary Linux host. The system should still deliver useful analysis when only
a single wearable is connected — IoT sensors and the LLM coach are optional
plug-and-play modules.

## Features (planned / in progress)

| Feature                              | Status        | Module             |
|--------------------------------------|---------------|--------------------|
| Config-driven modularity             | ✅ Phase 1    | `core/`            |
| BLE wearable adapter                 | ⏳ Phase 2    | `adapters/`        |
| MQTT climate-sensor integration      | ⏳ Phase 3    | `iot/`             |
| SciPy preprocessing + EDF export     | ⏳ Phase 4    | `ml_pipeline/`     |
| YASA sleep-stage scoring             | ⏳ Phase 4    | `ml_pipeline/`     |
| InfluxDB persistence                 | ⏳ Phase 5    | `database/`        |
| Local LLM coach (Ollama)             | ⏳ Phase 6    | `llm_coach/`       |

## Architecture

```
[ BLE wearable ]──┐
                  ├──► [ adapters/ ] ──► [ ml_pipeline/ ] ──► [ database/ ] ──► [ Grafana ]
[ ESP32 climate ]─┤            │              │                       │
   (via MQTT)     │            │              │                       │
                  └──► [ iot/ ]                └──► [ llm_coach/ ] ────┘
```

Everything runs locally on a single host. Modules communicate through `asyncio`
queues and a central config loader.

## Quickstart

> ⚠️ Current functionality is limited to bootstrapping — see the roadmap below.

```bash
git clone https://github.com/numan7272/Somnoscope.git
cd Somnoscope

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
python main.py
```

`main.py` reads `config.yaml`, writes logs to `logs/somnoscope.log`, and prints
which modules are currently active to the terminal.

## Configuration

`config.yaml` controls every module. Excerpt:

```yaml
wearable:
  enabled: true
  device_type: "fitbit_ble"
  mac_address: "AA:BB:CC:DD:EE:FF"

climate_sensors:
  enabled: false   # module is skipped when false

llm_coach:
  enabled: false
```

Any optional module can be toggled on or off at any time without breaking the rest
of the system (graceful degradation).

## Tech Stack

* Python 3.10+ (`asyncio`)
* `bleak` for BLE
* `paho-mqtt` for IoT sensors
* `scipy`, `mne`, `yasa` for signal processing and sleep scoring
* `influxdb-client` for persistence
* Optional: `ollama` / `llama-cpp-python` for the local LLM coach

## Roadmap

- [x] Phase 1 — config loader, logger, bootstrap
- [ ] Phase 2 — BLE wearable adapter (Fitbit as the first reference)
- [ ] Phase 3 — MQTT subscriber for ESP32 climate data
- [ ] Phase 4 — preprocessing + YASA scoring
- [ ] Phase 5 — InfluxDB writer + Grafana dashboards
- [ ] Phase 6 — local LLM coach via RAG

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
expose the system beyond your own network (e.g., by putting Grafana on the public
internet), you are responsible for authentication, TLS, and GDPR/HIPAA compliance
yourself. The defaults are deliberately "localhost only."
