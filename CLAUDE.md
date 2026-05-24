# Projektkontext: Somnoscope — Open-Source Edge-AI Sleep Tracker

Du bist ein erfahrener Software-Architekt und Python-Entwickler. Wir bauen ein lokales, modulares
Schlaftracking-Ökosystem. Das Ziel ist es, kommerzielle "Blackbox"-Wearables aufzubrechen,
Sensor-Fusion mit IoT-Geräten zu betreiben und die Daten lokal mit Machine Learning auszuwerten.

---

## 🎯 Kernprinzipien (strikt einhalten!)

1. **Edge AI (Privacy First):** Alle Gesundheitsdaten bleiben lokal. KEINE Cloud-API-Calls für
   Bio-Signale, Schlafdaten oder LLM-Inferenz. Externe Calls sind ausschließlich für
   `pip install` und Github-Operationen erlaubt.
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

## 📂 Ziel-Ordnerstruktur

```text
/
├── CLAUDE.md               # Projektkontext für Claude (diese Datei)
├── README.md               # Benutzer-/Contributor-Doku
├── requirements.txt        # Python-Abhängigkeiten
├── config.yaml             # Zentrale Steuerung (welche Module sind aktiv?)
├── main.py                 # Einstiegspunkt, lädt Config und startet asynchrone Tasks
├── /core
│   ├── __init__.py
│   ├── config_loader.py    # Parst und validiert die config.yaml
│   └── logger.py           # Zentrales Logging-Setup
├── /adapters
│   ├── base_wearable.py    # Abstrakte Basisklasse
│   ├── fitbit_ble.py       # Spezifischer Parser
│   └── apple_watch_udp.py  # Spezifischer Parser
├── /iot
│   └── mqtt_subscriber.py  # Empfängt CO2/Temp/Luftfeuchte vom ESP32
├── /ml_pipeline
│   ├── preprocessor.py     # Filtert Rauschen (SciPy), konvertiert zu EDF
│   └── scorer.py           # YASA-Implementierung für Schlafphasen
├── /database
│   └── influx_writer.py    # Schreibt Metriken in die InfluxDB
├── /llm_coach
│   └── prompt_builder.py   # RAG-Logik, kombiniert ML-JSON mit LLM-Prompt
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

* **Phase 1 (aktuell):** Infrastruktur-Fundament — Config-Loader, Logger, Bootstrap
* **Phase 2:** BLE-Adapter — `base_wearable.py` und ein erster konkreter Parser
* **Phase 3:** IoT-Integration — MQTT-Subscriber für ESP32-Klimadaten
* **Phase 4:** ML-Pipeline — SciPy-Preprocessing + YASA-Scoring
* **Phase 5:** Persistenz — InfluxDB-Writer + Grafana-Dashboards
* **Phase 6:** LLM-Coach — RAG-Pipeline mit lokalem Modell

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
