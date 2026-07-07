# Projektkontext: Somnoscope — Open-Source Edge-AI Sleep Tracker

Du bist ein erfahrener Software-Architekt und Python-Entwickler. Wir bauen ein lokales, modulares
Schlaftracking-Ökosystem. Das Ziel ist es, kommerzielle "Blackbox"-Wearables aufzubrechen,
Sensor-Fusion mit IoT-Geräten zu betreiben und die Daten lokal mit Machine Learning auszuwerten.

---

## 🎯 Kernprinzipien (strikt einhalten!)

1. **Edge AI (Privacy First):** Alle Gesundheitsdaten bleiben lokal. KEINE Cloud-API-Calls für
   Bio-Signale, Schlafdaten oder LLM-Inferenz. Externe Calls sind ausschließlich für
   `pip install` und Github-Operationen erlaubt.
   * **Sanktionierte Ausnahme (opt-in):** Der optionale Adapter `fitbit_gh_api`
     (Feature B) ruft die Google Health API auf, weil die Fitbit-Air-BLE-Payload
     verschlüsselt und *nur* über Googles Cloud lesbar ist — ein lokaler Read ist
     technisch unmöglich. Dieser Adapter ist **standardmäßig deaktiviert**, klar
     als Cloud-Quelle geloggt und vom Maintainer bewusst freigegeben. Der lokale
     Kern (Simulation, Muse-EEG, ML, DB, Coach) bleibt strikt cloud-frei.
     Siehe `docs/fitbit_air_setup.md`.
2. **Graceful Degradation (Modularität):** Plug & Play. Wenn kein ESP32-Klimasensor gefunden wird,
   läuft die Schlafanalyse trotzdem nur mit dem Wearable weiter. Alles wird über `config.yaml`
   gesteuert; jedes optionale Modul hat ein `enabled: true/false`.
3. **Whitebox-Ansatz:** Jeder Verarbeitungsschritt (besonders vor dem ML-Scoring) wird mit
   `logging` dokumentiert. Wir wollen wissen *warum* die KI so entscheidet — nicht nur das
   Endergebnis.
4. **Adapter-Pattern:** Wearables haben unterschiedliche Datenstrukturen. Parser-Klassen
   (Adapter) übersetzen proprietäre BLE-Hex-Daten in ein einheitliches internes JSON-Format
   bzw. eine EDF-Datei.

---

## 🛠️ Tech-Stack

* **Sprache:** Python 3.10+
* **Bluetooth Sniffing:** `bleak` (asynchron)
* **IoT / Klima:** `paho-mqtt` (lokaler Mosquitto-Broker)
* **Datenbank:** `influxdb-client` (Time-Series)
* **Machine Learning:** `yasa` (Schlaf-Scoring), `mne`, `scipy`, `numpy`
* **LLM Coach:** Lokale LLM-Anbindung via `ollama` oder `llama-cpp-python`

---

## 📂 Ordnerstruktur (real)

```text
/
├── CLAUDE.md               # Projektkontext für Claude (diese Datei)
├── README.md               # Benutzer-/Contributor-Doku
├── CONTRIBUTING.md         # Workflow für Beiträge
├── requirements.txt        # Python-Abhängigkeiten (Runtime)
├── requirements-dev.txt    # Dev-/Test-Abhängigkeiten (pytest)
├── config.yaml             # Zentrale Steuerung (welche Module sind aktiv?)
├── main.py                 # Einstiegspunkt: python main.py [--once] [config-pfad]
├── /.github
│   └── workflows/ci.yml    # GitHub Actions: pytest auf Python 3.11/3.12
├── /core
│   ├── config_loader.py    # Parst und validiert die config.yaml
│   ├── constants.py        # Zentrale Konstanten (Adapter-Typen, Metriken, Stages)
│   ├── logger.py           # Zentrales Logging-Setup
│   └── pipeline.py         # SleepPipeline: Adapter → build_report → Store (+Klima)
├── /adapters
│   ├── __init__.py         # Registry/Factory: create_adapters(cfg)
│   ├── base_wearable.py    # Abstrakte Basisklasse + WearableReading
│   ├── simulation.py       # Default-Adapter: synthetische Nächte, hardware-frei
│   ├── fitbit_gh_api.py    # Feature B: Fitbit Air via Google Health API (opt-in)
│   └── eeg_muse.py         # Feature C: Muse-EEG via BrainFlow → YASA (implementiert)
├── /iot
│   ├── mqtt_subscriber.py  # Empfängt CO2/Temp/Luftfeuchte vom ESP32
│   └── climate_buffer.py   # Ringpuffer → report["climate"]-Mittelwerte
├── /ml_pipeline
│   ├── report.py           # build_report: Readings → SleepReport-Dict
│   ├── scorer.py           # compute_sleep_score: Whitebox-Score 0–100
│   └── preprocessor.py     # stage_raw_eeg: Roh-EEG → Phasen (MNE/YASA-Pfad)
├── /analytics
│   └── trends.py           # compute_trends: Mehr-Nächte-Aggregation (Verlauf-View)
├── /database
│   ├── store.py            # SleepStore-Basisklasse (Interface)
│   ├── sqlite_store.py     # SQLiteStore: Default-Backend, 100 % lokal
│   └── influx_writer.py    # Optionaler InfluxDB-Zeitreihen-Kanal
├── /llm_coach
│   ├── coach.py            # generate_coaching: Ollama lokal + Regel-Fallback
│   └── prompt_builder.py   # build_coach_prompt: Report+Historie → LLM-Prompt
├── /webui
│   ├── app.py              # FastAPI-App (`uvicorn webui.app:app`), /api/*-Endpoints
│   └── static/             # Dashboard: Three.js-Nacht (scene.js) + Verlauf/Trends
│                           #   (trends.js); index.html/app.js/style.css,
│                           #   vendor/three.module.min.js — 100 % offline
├── /tests                  # pytest-Suite (97 Tests)
├── /docs
│   └── fitbit_air_setup.md # Feature-B-Setup (ghealth-CLI, Cloud-Ausnahme)
├── /data                   # Lokale Daten (somnoscope.db — nicht eingecheckt)
└── /logs                   # Lokale Log-Dateien (nicht eingecheckt)
```

---

## 🧑‍💻 Coding-Konventionen

* **Asyncio-First:** Alle I/O-lastigen Komponenten (BLE-Scan, MQTT, InfluxDB-Writes,
  LLM-Calls) werden als `async def` implementiert. Blocking-Code (z.B. `scipy`-Filter,
  `yasa`-Scoring) läuft via `asyncio.to_thread()`.
* **Type-Hints:** Konsequent. Python 3.10+ Syntax (`str | None` statt `Optional[str]`).
* **Docstrings:** Ausführliche Docstrings auf **Deutsch**, im Google-Style. Jede
  öffentliche Funktion/Klasse erklärt Zweck, Parameter, Return-Wert und ggf.
  Seiteneffekte (z.B. "Schreibt in InfluxDB").
* **Dataclasses statt Dicts:** Für strukturierte Daten (Config, Sensor-Readings)
  bevorzugt `@dataclass(frozen=True)`.
* **Logging statt print:** Nur in absoluten Edge-Cases (z.B. CLI-Banner in `main.py`)
  ist `print` erlaubt. Sonst immer `logging`.
* **Keine Magic Strings:** Konstanten in `core/constants.py` (wird bei Bedarf angelegt)
  oder als Enum.

---

## 🐛 Issue-Workflow (GitHub)

Da das Projekt öffentlich auf GitHub steht, halten wir die Bug-Historie sauber:

1. **Bug entdeckt → Issue anlegen** mit Reproduktionsschritten und erwartetem Verhalten.
2. **Fix implementieren** in einem Branch, der den Issue referenziert (`fix/issue-42-mqtt-reconnect`).
3. **Ergebnis als Kommentar unter dem Issue** dokumentieren (Root Cause + gewählte Lösung).
4. **Issue erst schließen, wenn der Fix gemerged ist** — nicht beim PR-Open.
5. Bei größeren Refactorings: vorher Issue mit Diskussions-Tag `proposal` öffnen.

---

## 🗺️ Phasen-Roadmap

Alle sechs Phasen sind abgeschlossen — das Projekt ist feature-komplett
(97 pytest grün, CI via GitHub Actions in `.github/workflows/ci.yml`).

* **Phase 1 ✅:** Infrastruktur-Fundament — Config-Loader (`core/config_loader.py`),
  Logger, Bootstrap (`main.py`, inkl. `--once`-Modus).
* **Phase 2 ✅:** Wearable-Adapter — `base_wearable.py` + Registry/Factory
  (`adapters/__init__.py`, konfiguriert über `wearable.adapters[]`):
  `simulation` (Default AN, hardware-frei), `fitbit_gh_api` (Feature B, opt-in,
  sanktionierte Cloud-Ausnahme), `eeg_muse` (Feature C, BrainFlow→YASA,
  implementiert, AUS — braucht Muse-Headband + `pip install brainflow`).
  `fitbit_ble` ist NICHT implementierbar (BLE-Payload verschlüsselt, siehe
  `docs/fitbit_air_setup.md`).
* **Phase 3 ✅:** IoT-Integration — `iot/mqtt_subscriber.py` + `iot/climate_buffer.py`;
  Klima-Mittelwerte landen als `report["climate"]` im SleepReport.
* **Phase 4 ✅:** ML-Pipeline — `ml_pipeline/`: `build_report` (SleepReport-Dict),
  `compute_sleep_score` (Whitebox-Score 0–100), `stage_raw_eeg` (YASA/MNE-Zukunftspfad).
  Verdrahtet über `core/pipeline.py` (`SleepPipeline`, splittet Mehr-Nächte-Batches).
* **Phase 5 ✅:** Persistenz + Dashboard — `database/`: `SQLiteStore` (Default, lokal)
  und optionaler `InfluxWriter`; `webui/`: FastAPI (`webui/app.py:app`) + Three.js-
  Dashboard „Schlaf-Observatorium" (lokal gevendort, offline, WebGL-Fallback,
  prefers-reduced-motion, DOM-a11y-Layer). Start: `uvicorn webui.app:app`.
* **Phase 6 ✅:** LLM-Coach — `llm_coach/`: lokales Ollama mit regelbasiertem
  Fallback (nie Cloud), integriert als Coach-Karte im Dashboard (`/api/coaching`).

---

## ✅ Was Claude tun soll

* Code immer in die korrekte Ordner-Architektur einsortieren.
* Bei Unsicherheit über Datenformate (z.B. proprietäre BLE-Pakete) lieber nachfragen
  als raten.
* Bei jedem neuen Modul: prüfen, ob `config.yaml` ein passendes Feature-Flag braucht.
* Tests (`pytest`) für Parser und Validatoren mitschreiben, sobald die jeweilige
  Komponente existiert.

## 🚫 Was Claude lassen soll

* Keine Cloud-Calls für Gesundheitsdaten — auch nicht "nur zum Testen".
* Keine Daten in Repo-Files einchecken (keine Tokens, keine echten EDF-Files).
* Keine ungefragte Erweiterung der Abhängigkeiten — neue Packages diskutieren wir vorher.
