---
name: "🐛 Bug Report"
about: Report a reproducible failure
title: "[BUG] <short title>"
labels: ["bug", "triage"]
assignees: []
---

## 🐛 Description
<!-- What's going wrong? A short summary in 1-3 sentences. -->

## ♻️ Reproduction steps
<!-- As precise as possible so others can reproduce the bug. -->
1.
2.
3.

## ✅ Expected behavior
<!-- What should happen instead? -->

## ❌ Actual behavior
<!-- What happens concretely? Error output, screenshots, log excerpts. -->

```text
<stack trace or log output here>
```

## 🧩 Affected module
<!-- Which subsystem? Multiple selections allowed. -->
- [ ] `core/` (config, logger)
- [ ] `adapters/` (wearable / BLE)
- [ ] `iot/` (MQTT / climate sensors)
- [ ] `ml_pipeline/` (preprocessing / YASA)
- [ ] `database/` (InfluxDB)
- [ ] `llm_coach/` (LLM / RAG)
- [ ] Documentation
- [ ] Setup / tooling

## 🖥️ Environment
- **OS:** <!-- e.g., Raspberry Pi OS 12, Windows 11, Ubuntu 24.04 -->
- **Python version:** <!-- `python --version` -->
- **Somnoscope commit / branch:** <!-- `git rev-parse --short HEAD` -->
- **Hardware (if relevant):** <!-- Fitbit model, ESP32 variant, ... -->

## 📎 Additional context
<!-- Anything else: hypotheses about root cause, related issues, workarounds. -->
