"""
Optionaler InfluxDB-Writer für Vitalwert-Readings (Zeitreihen).

Schreibt punktuelle :class:`adapters.base_wearable.WearableReading`-Messwerte
(Herzfrequenz, HRV, SpO2, Hauttemperatur) in eine lokale InfluxDB, damit sie
z.B. in Grafana als Zeitreihen visualisiert werden können.

**Nicht erforderlich fürs Dashboard** — die primäre Persistenz ist der
:class:`database.sqlite_store.SQLiteStore`. Dieses Modul ist ein reines
Zusatz-Feature (Phase 5, ``database.enabled`` in ``config.yaml``).

Graceful Degradation:
    * Fehlt das Paket ``influxdb-client``, crasht dieses Modul beim Import
      **nicht** — der Writer wird zum No-op und meldet das klar per Log.
    * Ist ``database.enabled=false`` oder fehlt der Token, ebenfalls No-op.
    * Einzelne fehlgeschlagene Writes werden geloggt und verworfen; sie
      reissen das System nicht.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from core.constants import (
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SKIN_TEMP,
    METRIC_SPO2,
)

if TYPE_CHECKING:  # nur für Type-Checker
    from adapters.base_wearable import WearableReading
    from core.config_loader import AppConfig

logger = logging.getLogger(__name__)

# -- Optionale Abhängigkeit: influxdb-client ---------------------------------
try:
    from influxdb_client import InfluxDBClient, Point  # type: ignore[import-untyped]
    from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore[import-untyped]

    _INFLUX_AVAILABLE = True
except ImportError:  # pragma: no cover — abhängig von der Umgebung
    InfluxDBClient = None  # type: ignore[assignment]
    Point = None  # type: ignore[assignment]
    SYNCHRONOUS = None  # type: ignore[assignment]
    _INFLUX_AVAILABLE = False

#: Metriken, die als numerische Zeitreihe nach InfluxDB geschrieben werden.
#: Schlafphasen (``sleep_stage``) sind kategorische Intervalle und gehören in
#: den SleepReport (SQLite), nicht in die Vitalwert-Zeitreihe.
VITAL_METRICS: frozenset[str] = frozenset(
    {METRIC_HEART_RATE, METRIC_HRV, METRIC_SPO2, METRIC_SKIN_TEMP}
)

#: InfluxDB-Measurement-Name für alle Vitalwerte.
MEASUREMENT_VITALS = "vitals"


class InfluxWriter:
    """
    Schreibt Vitalwert-Readings als Punkte in eine InfluxDB (optional).

    Der Writer ist bewusst fehlertolerant konstruiert: Fehlt die Bibliothek,
    die Konfiguration oder der Token, entsteht ein funktionsloser (aber
    gefahrloser) No-op-Writer — das restliche System läuft unverändert weiter.

    Args:
        cfg: Geladene Anwendungskonfiguration. Genutzt wird der
            ``database``-Block (``url``, ``org``, ``bucket``, Token via ENV).

    Seiteneffekte:
        Baut bei erfüllten Voraussetzungen eine Verbindung zur InfluxDB auf.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self._bucket = cfg.database.bucket
        self._org = cfg.database.org
        self._client: Any = None
        self._write_api: Any = None

        if not _INFLUX_AVAILABLE:
            logger.warning(
                "InfluxWriter: Paket 'influxdb-client' ist nicht installiert — "
                "Vitalwerte werden NICHT nach InfluxDB geschrieben (No-op). "
                "Installation: pip install influxdb-client"
            )
            return
        if not cfg.database.enabled:
            logger.info(
                "InfluxWriter: database.enabled=false — Writer bleibt inaktiv (No-op)."
            )
            return
        token = cfg.database.token
        if not token:
            logger.warning(
                "InfluxWriter: Umgebungsvariable '%s' ist nicht gesetzt — "
                "Writer bleibt inaktiv (No-op).",
                cfg.database.token_env_var,
            )
            return

        try:
            self._client = InfluxDBClient(
                url=cfg.database.url, token=token, org=self._org
            )
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
            logger.info(
                "InfluxWriter verbunden: %s (org=%s, bucket=%s).",
                cfg.database.url,
                self._org,
                self._bucket,
            )
        except Exception:  # noqa: BLE001 — Verbindungsfehler darf System nicht reissen
            logger.exception(
                "InfluxWriter: Verbindung zu InfluxDB fehlgeschlagen — "
                "Writer bleibt inaktiv (No-op)."
            )
            self._client = None
            self._write_api = None

    @property
    def active(self) -> bool:
        """``True``, wenn der Writer tatsächlich schreiben kann (kein No-op)."""
        return self._write_api is not None

    async def write_readings(self, readings: list[WearableReading]) -> int:
        """
        Schreibt alle numerischen Vitalwert-Readings nach InfluxDB.

        Nicht-Vitalmetriken (z.B. ``sleep_stage``) und nicht-numerische Werte
        werden still übersprungen. Ist der Writer inaktiv (fehlende Bibliothek/
        Config/Token), passiert nichts.

        Args:
            readings: Beliebige Liste von :class:`WearableReading`; gefiltert
                wird intern auf :data:`VITAL_METRICS`.

        Returns:
            Anzahl tatsächlich geschriebener Punkte (0 bei No-op oder Fehler).

        Seiteneffekte:
            Schreibt Punkte in das konfigurierte InfluxDB-Bucket.
        """
        if not self.active:
            logger.debug(
                "InfluxWriter inaktiv — %d Reading(s) verworfen.", len(readings)
            )
            return 0

        points = []
        for r in readings:
            if r.metric not in VITAL_METRICS:
                continue
            if not isinstance(r.value, (int, float)):
                continue
            point = (
                Point(MEASUREMENT_VITALS)
                .tag("source", r.source)
                .tag("metric", r.metric)
                .field("value", float(r.value))
                .time(r.start)
            )
            if r.unit:
                point = point.tag("unit", r.unit)
            points.append(point)

        if not points:
            return 0

        try:
            await asyncio.to_thread(
                self._write_api.write,
                bucket=self._bucket,
                org=self._org,
                record=points,
            )
        except Exception:  # noqa: BLE001 — einzelner Write-Fehler ist nicht fatal
            logger.exception(
                "InfluxWriter: Schreiben von %d Punkt(en) fehlgeschlagen — "
                "Daten werden verworfen, System läuft weiter.",
                len(points),
            )
            return 0

        logger.debug("InfluxWriter: %d Vitalwert-Punkt(e) geschrieben.", len(points))
        return len(points)

    async def close(self) -> None:
        """
        Schliesst die InfluxDB-Verbindung (idempotent, No-op-sicher).

        Seiteneffekte:
            Gibt Client-Ressourcen frei; danach ist :attr:`active` ``False``.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        self._write_api = None
        try:
            await asyncio.to_thread(client.close)
        except Exception:  # noqa: BLE001
            logger.exception("InfluxWriter: Fehler beim Schliessen (ignoriert).")
        logger.info("InfluxWriter geschlossen.")
