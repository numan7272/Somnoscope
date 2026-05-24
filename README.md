# Somnoscope

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status: WIP](https://img.shields.io/badge/status-work--in--progress-orange.svg)](#roadmap)

> *Somnoscope* — wörtlich "Schlaf-Mikroskop". Ein lokaler Schlaftracker, der
> Wearable-, Klima- und ML-Daten zusammenführt, ohne Cloud, ohne Vendor-Lock-in
> und ohne den Anspruch, ein Endergebnis zu sein, das man nicht selbst auditieren kann.

**Status:** Phase 1 — Infrastruktur-Fundament. Noch nicht produktiv nutzbar.

## Motivation

Kommerzielle Schlaftracker liefern einen "Sleep Score" und ein paar Balken, aber:

- Die Rohdaten bleiben in proprietären Clouds.
- Die Scoring-Algorithmen sind nicht einsehbar.
- Sensor-Fusion mit dem Raumklima (CO₂, Temperatur, Luftfeuchte) findet schlicht nicht
  statt — obwohl genau das oft der Grund für unruhigen Schlaf ist.

Somnoscope ist der Versuch, diese drei Probleme auf einem Raspberry Pi oder einem
gewöhnlichen Linux-Host zu lösen. Das System soll auch dann sinnvolle Auswertungen
liefern, wenn nur ein Wearable verbunden ist — IoT-Sensoren und LLM-Coach sind
optionale Plug-and-Play-Module.

## Features (geplant / in Arbeit)

| Feature                          | Status        | Modul              |
|----------------------------------|---------------|--------------------|
| Config-getriebene Modularität    | ✅ Phase 1    | `core/`            |
| BLE-Wearable-Adapter             | ⏳ Phase 2    | `adapters/`        |
| MQTT-Klimasensor-Integration     | ⏳ Phase 3    | `iot/`             |
| SciPy-Preprocessing + EDF-Export | ⏳ Phase 4    | `ml_pipeline/`     |
| YASA-Schlafphasen-Scoring        | ⏳ Phase 4    | `ml_pipeline/`     |
| InfluxDB-Persistenz              | ⏳ Phase 5    | `database/`        |
| Lokaler LLM-Coach (Ollama)       | ⏳ Phase 6    | `llm_coach/`       |

## Architektur

```
[ BLE-Wearable ]──┐
                  ├──► [ adapters/ ] ──► [ ml_pipeline/ ] ──► [ database/ ] ──► [ Grafana ]
[ ESP32 Klima ]───┤            │              │                       │
   (via MQTT)     │            │              │                       │
                  └──► [ iot/ ]                └──► [ llm_coach/ ] ────┘
```

Alle Schritte laufen lokal auf einer Maschine. Die einzelnen Module kommunizieren über
`asyncio`-Queues und einen zentralen Config-Loader.

## Quickstart

> ⚠️ Funktionsumfang ist aktuell auf Bootstrap beschränkt — siehe Roadmap.

```bash
git clone https://github.com/numan7272/Somnoscope.git
cd Somnoscope

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
python main.py
```

`main.py` liest die `config.yaml`, schreibt Logs nach `logs/somnoscope.log` und gibt
im Terminal aus, welche Module laut Config aktiv sind.

## Konfiguration

`config.yaml` steuert alle Module. Auszug:

```yaml
wearable:
  enabled: true
  device_type: "fitbit_ble"
  mac_address: "AA:BB:CC:DD:EE:FF"

climate_sensors:
  enabled: false   # Modul wird übersprungen, wenn false

llm_coach:
  enabled: false
```

Jedes optionale Modul kann jederzeit aktiviert oder deaktiviert werden, ohne den Rest
zu brechen.

## Tech-Stack

* Python 3.10+ (`asyncio`)
* `bleak` für BLE
* `paho-mqtt` für IoT-Sensoren
* `scipy`, `mne`, `yasa` für Signal-Processing und Sleep-Scoring
* `influxdb-client` für Persistenz
* Optional: `ollama` / `llama-cpp-python` für lokalen LLM-Coach

## Roadmap

- [x] Phase 1 — Config-Loader, Logger, Bootstrap
- [ ] Phase 2 — BLE-Wearable-Adapter (Fitbit als erste Referenz)
- [ ] Phase 3 — MQTT-Subscriber für ESP32-Klimadaten
- [ ] Phase 4 — Preprocessing + YASA-Scoring
- [ ] Phase 5 — InfluxDB-Writer + Grafana-Dashboards
- [ ] Phase 6 — Lokaler LLM-Coach via RAG

## Mitmachen

Bugs und Ideen gehen über [GitHub Issues](https://github.com/numan7272/Somnoscope/issues).
Den vollständigen Workflow beschreibt [CONTRIBUTING.md](CONTRIBUTING.md). Kurzfassung:

1. **Issue zuerst** — auch für kleine Bugs, mit Repro-Schritten und erwartetem Verhalten.
2. **Branch pro Issue** — Format: `fix/issue-<nr>-kurztitel` bzw. `feat/issue-<nr>-kurztitel`.
3. **Root Cause + Lösung im Issue dokumentieren.**
4. **Issue erst beim Merge schließen** — nicht beim PR-Open.

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Datenschutz-Hinweis

Somnoscope verarbeitet Gesundheitsdaten. Auch wenn alles lokal läuft: Wer das System
über das eigene Netz hinaus exponiert (z.B. Grafana ins Internet stellt), ist selbst
für Auth, TLS und DSGVO-Konformität verantwortlich. Die Defaults sind bewusst
"localhost only".
