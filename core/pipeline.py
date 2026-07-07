"""
Schlaf-Pipeline von Somnoscope — verdrahtet Adapter → ML → Persistenz → Coach.

Dies ist der Klebstoff zwischen den Phasen: ein aktiver Wearable-Adapter liefert
:class:`~adapters.base_wearable.WearableReading`, die Pipeline verdichtet einen
kompletten Nacht-Batch via :func:`ml_pipeline.build_report` zu einem SleepReport,
persistiert ihn über den :class:`~database.store.SleepStore` und stößt optional
den lokalen LLM-Coach an. Damit landet nach ``python main.py`` tatsächlich ein
Report in der Datenbank, den das Dashboard anzeigt.

Ein *Batch* ist das Ergebnis eines einzelnen ``poll()`` — bei den Pull-Adaptern
(Simulation, Fitbit) ist das bereits ein in sich geschlossenes Zeitfenster (eine
Nacht bzw. das ``lookback``-Fenster) inklusive Schlafphasen und Vitalwerten.
Deshalb wird der Report direkt aus dem Batch gebaut (kein Akkumulieren nötig);
reine Live-Vitalwert-Batches ohne Schlafphasen werden übersprungen.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from adapters.base_wearable import WearableReading
from core.constants import METRIC_SLEEP_STAGE

if TYPE_CHECKING:  # nur für Type-Checker
    from adapters.base_wearable import WearableAdapter
    from core.config_loader import AppConfig
    from database.store import SleepStore

logger = logging.getLogger(__name__)


class SleepPipeline:
    """
    Orchestriert die Verarbeitung eines Adapter-Batches bis in die Datenbank.

    Args:
        cfg: Geladene Anwendungskonfiguration.
        store: Offener SleepReport-Store (Ziel der Persistenz).

    Seiteneffekte:
        Initialisiert optional einen :class:`~database.influx_writer.InfluxWriter`,
        wenn ``database.enabled`` gesetzt ist (Graceful bei fehlender Dependency).
    """

    def __init__(self, cfg: AppConfig, store: SleepStore) -> None:
        self._cfg = cfg
        self._store = store
        self._influx = None
        if cfg.database.enabled:
            try:
                from database import InfluxWriter

                self._influx = InfluxWriter(cfg)
            except Exception:  # noqa: BLE001 — optionaler Kanal darf nicht reissen
                logger.exception(
                    "[pipeline] InfluxWriter nicht initialisierbar — Vitalwerte "
                    "werden nicht nach InfluxDB geschrieben."
                )

    async def run(self, adapter: WearableAdapter) -> None:
        """
        Läuft einen Adapter dauerhaft und verarbeitet jeden Poll-Batch.

        Nutzt die Standard-``run``-Schleife des Adapters mit :meth:`_handle_batch`
        als Batch-Callback. Läuft bis zum Abbruch (z.B. Strg-C).
        """
        await adapter.run(on_batch=self._handle_batch)

    async def run_once(self, adapter: WearableAdapter) -> None:
        """
        Führt genau einen Poll-Zyklus aus und verarbeitet ihn (für ``--once``/CI).

        Öffnet den Adapter, pollt einmal, verarbeitet den Batch und schließt
        wieder. Ein Fehler beim Öffnen überspringt den Adapter (Graceful
        Degradation), reisst aber die anderen nicht mit.
        """
        try:
            await adapter.open()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[pipeline] %s: Initialisierung fehlgeschlagen — übersprungen.",
                adapter.name,
            )
            return
        try:
            readings = await adapter.poll()
            await self._handle_batch(adapter.name, readings)
        except Exception:  # noqa: BLE001
            logger.exception("[pipeline] %s: Einzel-Poll fehlgeschlagen.", adapter.name)
        finally:
            await adapter.close()

    async def _handle_batch(
        self, source: str, readings: list[WearableReading]
    ) -> None:
        """
        Verarbeitet einen Poll-Batch: optional InfluxDB, dann Report + Coach.

        Args:
            source: Name des erzeugenden Adapters.
            readings: Die im aktuellen Poll gelieferten Messpunkte.

        Seiteneffekte:
            Schreibt ggf. nach InfluxDB, persistiert einen SleepReport im Store
            und loggt eine Coaching-Zeile.
        """
        if not readings:
            return

        # Optionaler Zeitreihen-Kanal (Grafana) — Fehler bleiben folgenlos.
        if self._influx is not None:
            try:
                await self._influx.write_readings(readings)
            except Exception:  # noqa: BLE001
                logger.exception("[pipeline] InfluxDB-Schreiben fehlgeschlagen.")

        if not self._cfg.ml_pipeline.enabled:
            return
        # Ein Report ergibt nur Sinn, wenn der Batch Schlafphasen enthält.
        if not any(r.metric == METRIC_SLEEP_STAGE for r in readings):
            logger.debug(
                "[pipeline] %s: Batch ohne Schlafphasen (%d Vitalwerte) — "
                "kein Report.",
                source,
                len(readings),
            )
            return

        from ml_pipeline import build_report

        report = await build_report(readings, source=source)
        if report is None:
            logger.info(
                "[pipeline] %s: kein Report erzeugt (zu wenig Schlafdaten).", source
            )
            return

        await self._store.save_report(report)
        logger.info(
            "[pipeline] Report gespeichert: %s | Score %s · Effizienz %.0f%% · "
            "Schlaf %.0f min · %d Phasen-Segmente",
            report.get("date"),
            report.get("sleep_score"),
            float(report.get("sleep_efficiency_pct") or 0),
            float(report.get("total_sleep_min") or 0),
            len(report.get("hypnogram") or []),
        )

        if self._cfg.llm_coach.enabled:
            await self._run_coach(report)

    async def _run_coach(self, report: dict) -> None:
        """Stößt den lokalen LLM-Coach an und loggt die erste Zeile (Whitebox)."""
        try:
            from llm_coach import generate_coaching

            history = await self._store.list_reports(limit=30)
            text = await generate_coaching(report, history, self._cfg)
            first_line = next(
                (ln.strip() for ln in (text or "").splitlines() if ln.strip()), ""
            )
            logger.info("[coach] %s", first_line[:200] or "(kein Text)")
        except Exception:  # noqa: BLE001 — Coaching ist optional, nie fatal
            logger.exception("[pipeline] Coaching fehlgeschlagen — übersprungen.")

    async def aclose(self) -> None:
        """Schließt optionale Ressourcen der Pipeline (InfluxWriter)."""
        if self._influx is not None:
            try:
                await self._influx.close()
            except Exception:  # noqa: BLE001
                logger.exception("[pipeline] InfluxWriter-Schließen fehlgeschlagen.")
