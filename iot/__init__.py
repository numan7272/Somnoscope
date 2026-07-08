"""
IoT-Modul: MQTT-Anbindung der ESP32-Klimasensoren (Phase 3).

Oeffentliche API:
    * :class:`ClimateBuffer` — thread-/async-sicherer Ringpuffer der
      letzten Klima-Messwerte mit ``SleepReport["climate"]``-kompatiblen
      Fenster-Mittelwerten.
    * :class:`MqttSubscriber` — asynchroner MQTT-Client, der die
      konfigurierten Topics abonniert und in den Buffer schreibt.
    * :func:`topic_to_metric` / :func:`parse_payload` — reine, testbare
      Hilfsfunktionen fuer Topic-Mapping und Payload-Parsing.
"""

from __future__ import annotations

from iot.climate_buffer import (
    CLIMATE_METRIC_CO2,
    CLIMATE_METRIC_HUMIDITY,
    CLIMATE_METRIC_TEMPERATURE,
    CLIMATE_METRICS,
    METRIC_TO_REPORT_KEY,
    ClimateBuffer,
    ClimatePoint,
)
from iot.mqtt_subscriber import MqttSubscriber, parse_payload, topic_to_metric

__all__ = [
    "CLIMATE_METRICS",
    "CLIMATE_METRIC_CO2",
    "CLIMATE_METRIC_HUMIDITY",
    "CLIMATE_METRIC_TEMPERATURE",
    "METRIC_TO_REPORT_KEY",
    "ClimateBuffer",
    "ClimatePoint",
    "MqttSubscriber",
    "parse_payload",
    "topic_to_metric",
]
