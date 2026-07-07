"""
Thread-/async-sicherer Ringpuffer fuer Klima-Messwerte (ESP32 via MQTT).

Der :class:`ClimateBuffer` haelt die letzten Messwerte der drei
Klima-Metriken (CO2, Temperatur, Luftfeuchte) im Speicher und liefert
Zeitfenster-Mittelwerte im exakten ``SleepReport["climate"]``-Format
(Keys ``avg_co2`` / ``avg_temp`` / ``avg_humidity``, siehe
:mod:`ml_pipeline.report`).

Designentscheidungen:
    * **Ringpuffer pro Metrik:** ``collections.deque`` mit ``maxlen``
      begrenzt die Punktanzahl hart; zusaetzlich werden Punkte aelter als
      ``max_age`` (Default: 3 Tage) beim Einfuegen verworfen.
    * **threading.Lock statt asyncio.Lock:** ``paho-mqtt`` ruft seine
      Callbacks aus einem eigenen Netzwerk-Thread auf, waehrend die
      Auswertung im Asyncio-Event-Loop laeuft. Ein klassischer Lock schuetzt
      beide Seiten; alle Operationen sind kurz und blockieren nicht spuerbar.
    * **Frozen Dataclass fuer Messpunkte:** Messwerte sind immutable
      (Konvention „Dataclasses statt Dicts").
    * **UTC-Normalisierung:** Zeitstempel werden beim Einfuegen und beim
      Abfragen auf timezone-aware UTC normalisiert (naiv = lokale Zeit),
      damit Vergleiche nie an gemischten naiv/aware-Datetimes scheitern.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.constants import (
    CLIMATE_METRICS,
    METRIC_CO2,
    METRIC_HUMIDITY,
    METRIC_TEMPERATURE,
    METRIC_TO_CLIMATE_KEY,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Metrik-Namen kommen zentral aus core/constants.py (keine Magic Strings).
# Die CLIMATE_METRIC_*-Aliase bleiben fuer die bestehende iot-API erhalten.
# ----------------------------------------------------------------------------

CLIMATE_METRIC_CO2 = METRIC_CO2
CLIMATE_METRIC_TEMPERATURE = METRIC_TEMPERATURE
CLIMATE_METRIC_HUMIDITY = METRIC_HUMIDITY

#: Mapping Metrik -> Key im ``SleepReport["climate"]``-Block (Alias auf
#: :data:`core.constants.METRIC_TO_CLIMATE_KEY`).
METRIC_TO_REPORT_KEY: dict[str, str] = METRIC_TO_CLIMATE_KEY

#: Harte Obergrenze an Punkten pro Metrik (bei 10-s-Takt ~ 3,5 Tage).
DEFAULT_MAX_POINTS_PER_METRIC = 30_000

#: Punkte aelter als dieses Fenster werden beim Einfuegen verworfen.
DEFAULT_MAX_AGE = timedelta(days=3)


def _to_utc(ts: datetime) -> datetime:
    """
    Normalisiert einen Zeitstempel auf timezone-aware UTC.

    Naive Zeitstempel werden als lokale Zeit interpretiert. So sind alle
    Vergleiche im Puffer konsistent — egal ob Aufrufer naive oder aware
    ``datetime``-Objekte uebergeben (gemischte Vergleiche wuerfen sonst
    ``TypeError``).

    Args:
        ts: Beliebiger Zeitstempel (naiv oder timezone-aware).

    Returns:
        Derselbe Zeitpunkt als aware ``datetime`` in UTC.
    """
    if ts.tzinfo is None:
        ts = ts.astimezone()  # naiv -> lokale Zeitzone annehmen
    return ts.astimezone(timezone.utc)


@dataclass(frozen=True)
class ClimatePoint:
    """
    Ein einzelner Klima-Messwert.

    Attributes:
        metric: Metrik-Name (einer aus :data:`CLIMATE_METRICS`).
        value: Messwert (ppm, Grad Celsius bzw. Prozent).
        ts: Zeitstempel der Messung.
    """

    metric: str
    value: float
    ts: datetime


class ClimateBuffer:
    """
    Thread-/async-sicherer Ringpuffer der letzten Klima-Messwerte.

    Der MQTT-Netzwerk-Thread schreibt via :meth:`add`, der Report-Builder
    liest via :meth:`averages_between` — beide Seiten sind ueber einen
    internen Lock synchronisiert.
    """

    def __init__(
        self,
        max_points_per_metric: int = DEFAULT_MAX_POINTS_PER_METRIC,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        """
        Initialisiert den Puffer mit Groessen- und Altersbegrenzung.

        Args:
            max_points_per_metric: Harte Obergrenze an Punkten je Metrik
                (aelteste Punkte fallen zuerst raus).
            max_age: Punkte, die aelter als ``neuester Punkt - max_age``
                sind, werden beim Einfuegen entfernt.
        """
        self._lock = threading.Lock()
        self._max_age = max_age
        self._series: dict[str, deque[ClimatePoint]] = {
            metric: deque(maxlen=max_points_per_metric)
            for metric in CLIMATE_METRICS
        }

    def add(self, metric: str, value: float, ts: datetime) -> None:
        """
        Fuegt einen Messwert hinzu (thread-sicher).

        Unbekannte Metriken werden verworfen und nur per Debug-Log
        vermerkt (Graceful Degradation — ein falsch konfiguriertes Topic
        darf den Betrieb nicht stoeren).

        Args:
            metric: Metrik-Name, einer aus :data:`CLIMATE_METRICS`.
            value: Messwert als float.
            ts: Zeitstempel der Messung.

        Seiteneffekte:
            Entfernt beim Einfuegen Punkte, die aelter als ``max_age``
            (relativ zum neuesten Zeitstempel der Metrik) sind.
        """
        if metric not in self._series:
            logger.debug("ClimateBuffer: unbekannte Metrik '%s' verworfen.", metric)
            return
        point = ClimatePoint(metric=metric, value=float(value), ts=_to_utc(ts))
        with self._lock:
            series = self._series[metric]
            series.append(point)
            self._prune_old(series)

    def averages_between(self, start: datetime, end: datetime) -> dict:
        """
        Berechnet Mittelwerte je Metrik ueber ein Zeitfenster.

        Beruecksichtigt alle Punkte mit ``start <= ts <= end`` (inklusive
        Grenzen). Metriken ohne Daten im Fenster liefern ``None``.

        Args:
            start: Fensterbeginn (inklusive).
            end: Fensterende (inklusive).

        Returns:
            Dict im exakten ``SleepReport["climate"]``-Format::

                {"avg_co2": float | None,
                 "avg_temp": float | None,
                 "avg_humidity": float | None}
        """
        lo, hi = _to_utc(start), _to_utc(end)
        result: dict[str, float | None] = {
            key: None for key in METRIC_TO_REPORT_KEY.values()
        }
        with self._lock:
            for metric, report_key in METRIC_TO_REPORT_KEY.items():
                values = [
                    p.value for p in self._series[metric] if lo <= p.ts <= hi
                ]
                if values:
                    result[report_key] = sum(values) / len(values)
        return result

    def _prune_old(self, series: deque[ClimatePoint]) -> None:
        """
        Entfernt Punkte aelter als ``max_age`` relativ zum neuesten Punkt.

        Muss unter gehaltenem ``self._lock`` aufgerufen werden.

        Args:
            series: Die Deque genau einer Metrik.
        """
        if not series:
            return
        cutoff = series[-1].ts - self._max_age
        while series and series[0].ts < cutoff:
            series.popleft()
