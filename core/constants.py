"""
Zentrale Konstanten für Somnoscope.

Hält Magic Strings an *einer* Stelle (siehe Coding-Konvention „Keine Magic
Strings"). Aktuell:

    * Bezeichner der Wearable-Adapter (wie sie in ``config.yaml`` unter
      ``wearable.adapters[].type`` stehen),
    * die vereinheitlichten Metrik-Namen, die *alle* Adapter in
      :class:`adapters.base_wearable.WearableReading` schreiben,
    * die Google-Health-API-Spezifika (Scopes, Datentyp-Endpunkte), die der
      optionale ``fitbit_gh_api``-Adapter benötigt.

Die Google-Health-Werte sind hier nur als Dokumentation/Default hinterlegt —
sie werden ausschliesslich vom optionalen Cloud-Adapter genutzt und berühren
den lokalen Kern des Systems nicht.
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# Adapter-Typen (config.yaml: wearable.adapters[].type)
# ----------------------------------------------------------------------------

#: Feature B — Fitbit Air über die Google Health API (Cloud, opt-in).
ADAPTER_FITBIT_GH_API = "fitbit_gh_api"

#: Feature C — EEG-Headband (Muse) lokal über BrainFlow. Slot vorbereitet.
ADAPTER_EEG_MUSE = "eeg_muse"

#: Historischer Platzhalter — NICHT implementierbar (Air-BLE ist verschlüsselt).
ADAPTER_FITBIT_BLE = "fitbit_ble"

#: Demo/Default — synthetische Schlafdaten ohne Hardware (adapters/simulation.py).
ADAPTER_SIMULATION = "simulation"


# ----------------------------------------------------------------------------
# Vereinheitlichte Metrik-Namen (Adapter -> WearableReading.metric)
# ----------------------------------------------------------------------------
# Jeder Adapter übersetzt seine proprietären Felder auf genau diese Namen,
# damit ML-Pipeline und InfluxDB-Writer geräteunabhängig bleiben.

METRIC_SLEEP_STAGE = "sleep_stage"
METRIC_HEART_RATE = "heart_rate"
METRIC_HRV = "hrv"
METRIC_SPO2 = "spo2"
METRIC_SKIN_TEMP = "skin_temp"


# ----------------------------------------------------------------------------
# Schlafphasen-Werte (WearableReading.value bei METRIC_SLEEP_STAGE,
# sowie Keys in SleepReport["stages_min"] / ["stages_pct"])
# ----------------------------------------------------------------------------

STAGE_WAKE = "wake"
STAGE_LIGHT = "light"
STAGE_DEEP = "deep"
STAGE_REM = "rem"

#: Alle Phasen in kanonischer Reihenfolge (für Reports und Prompts).
SLEEP_STAGES: tuple[str, ...] = (STAGE_WAKE, STAGE_LIGHT, STAGE_DEEP, STAGE_REM)


# ----------------------------------------------------------------------------
# Klima-Metriken (iot/ — MQTT-Klimasensoren, ESP32)
# ----------------------------------------------------------------------------
# Vereinheitlichte Metrik-Namen für Raumklima-Messwerte. Der MQTT-Subscriber
# übersetzt Topic-Suffixe (z.B. "bedroom/co2" -> "co2") auf genau diese Namen,
# damit Puffer, Report und InfluxDB-Writer topic-unabhängig bleiben.

METRIC_CO2 = "co2"
METRIC_TEMPERATURE = "temperature"
METRIC_HUMIDITY = "humidity"

#: Alle Klima-Metriken in kanonischer Reihenfolge.
CLIMATE_METRICS: tuple[str, ...] = (METRIC_CO2, METRIC_TEMPERATURE, METRIC_HUMIDITY)

#: Mapping: MQTT-Topic-Suffix (Segment nach dem letzten "/") -> Klima-Metrik.
#: "temp" ist als gebräuchliche Kurzform zusätzlich erlaubt.
TOPIC_SUFFIX_TO_METRIC: dict[str, str] = {
    "co2": METRIC_CO2,
    "temperature": METRIC_TEMPERATURE,
    "temp": METRIC_TEMPERATURE,
    "humidity": METRIC_HUMIDITY,
}

#: Keys des SleepReport["climate"]-Blocks (siehe ml_pipeline/report.py).
CLIMATE_KEY_AVG_CO2 = "avg_co2"
CLIMATE_KEY_AVG_TEMP = "avg_temp"
CLIMATE_KEY_AVG_HUMIDITY = "avg_humidity"

#: Mapping: Klima-Metrik -> zugehöriger SleepReport["climate"]-Key.
#: Achtung: "temperature" wird im Report bewusst zu "avg_temp" verkürzt.
METRIC_TO_CLIMATE_KEY: dict[str, str] = {
    METRIC_CO2: CLIMATE_KEY_AVG_CO2,
    METRIC_TEMPERATURE: CLIMATE_KEY_AVG_TEMP,
    METRIC_HUMIDITY: CLIMATE_KEY_AVG_HUMIDITY,
}


# ----------------------------------------------------------------------------
# Google Health API — nur für den optionalen fitbit_gh_api-Adapter
# ----------------------------------------------------------------------------

#: OAuth-2.0-Scopes (readonly) für Schlaf- und Health-Metrik-Daten.
#: Beide sind „Restricted" — im Test-Modus laufen Refresh-Tokens nach 7 Tagen ab.
GOOGLE_HEALTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
)

#: Datentyp-Endpunkte (kebab-case) der Google Health API v4.
#: Mapping: Endpunkt-Name -> vereinheitlichte Metrik.
GH_DATATYPE_TO_METRIC: dict[str, str] = {
    "sleep": METRIC_SLEEP_STAGE,
    "heart-rate": METRIC_HEART_RATE,
    "heart-rate-variability": METRIC_HRV,
    "oxygen-saturation": METRIC_SPO2,
    "core-body-temperature": METRIC_SKIN_TEMP,
}
