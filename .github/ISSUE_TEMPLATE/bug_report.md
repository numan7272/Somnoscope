---
name: "🐛 Bug Report"
about: Einen reproduzierbaren Fehler melden
title: "[BUG] <Kurztitel>"
labels: ["bug", "triage"]
assignees: []
---

## 🐛 Beschreibung
<!-- Was läuft schief? Eine knappe Zusammenfassung in 1-3 Sätzen. -->

## ♻️ Reproduktionsschritte
<!-- So genau wie möglich, damit andere den Bug nachstellen können. -->
1.
2.
3.

## ✅ Erwartetes Verhalten
<!-- Was sollte stattdessen passieren? -->

## ❌ Tatsächliches Verhalten
<!-- Was passiert konkret? Fehler-Output, Screenshots, Log-Auszüge. -->

```text
<Stack-Trace oder Log-Output hier einfügen>
```

## 🧩 Betroffenes Modul
<!-- Welches Subsystem? Mehrfachauswahl möglich. -->
- [ ] `core/` (Config, Logger)
- [ ] `adapters/` (Wearable / BLE)
- [ ] `iot/` (MQTT / Klimasensoren)
- [ ] `ml_pipeline/` (Preprocessing / YASA)
- [ ] `database/` (InfluxDB)
- [ ] `llm_coach/` (LLM / RAG)
- [ ] Dokumentation
- [ ] Setup / Tooling

## 🖥️ Umgebung
- **OS:** <!-- z.B. Raspberry Pi OS 12, Windows 11, Ubuntu 24.04 -->
- **Python-Version:** <!-- `python --version` -->
- **Somnoscope-Commit / Branch:** <!-- `git rev-parse --short HEAD` -->
- **Hardware (falls relevant):** <!-- Fitbit Modell, ESP32 Variante, ... -->

## 📎 Zusatzkontext
<!-- Alles weitere: Vermutungen über Root Cause, verwandte Issues, Workarounds. -->
