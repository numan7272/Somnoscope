"""
Persistenz-Schicht von Somnoscope (Phase 5).

Öffentliche API::

    from database import create_store

    store = create_store(cfg)              # cfg = core.config_loader.load_config()
    await store.save_report(report)        # Upsert nach report["date"]
    latest = await store.latest_report()   # dict | None
    history = await store.list_reports(30) # list[dict], neueste zuerst
    await store.close()

Default-Backend ist immer :class:`~database.sqlite_store.SQLiteStore`
(Standardbibliothek, 100 % lokal, keine Server-Installation). Der optionale
:class:`~database.influx_writer.InfluxWriter` ergänzt Vitalwert-Zeitreihen
für Grafana, ist aber fürs Dashboard nicht erforderlich.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from database.influx_writer import InfluxWriter
from database.sqlite_store import SQLiteStore
from database.store import SleepStore

if TYPE_CHECKING:  # nur für Type-Checker
    from core.config_loader import AppConfig

logger = logging.getLogger(__name__)

__all__ = ["create_store", "SleepStore", "SQLiteStore", "InfluxWriter"]


def create_store(cfg: AppConfig) -> SleepStore:
    """
    Fabrik für den SleepReport-Store der Anwendung.

    Liefert immer einen :class:`SQLiteStore` — die lokale SQLite-Datei unter
    ``<cfg.system.data_dir>/somnoscope.db`` ist die verbindliche Quelle für
    Dashboard und LLM-Coach. InfluxDB (falls aktiviert) ist ein separater,
    optionaler Zeitreihen-Kanal und ersetzt den SQLite-Store nicht.

    Args:
        cfg: Geladene Anwendungskonfiguration (``core.config_loader.load_config()``).

    Returns:
        Ein einsatzbereiter :class:`~database.store.SleepStore`.

    Seiteneffekte:
        Legt ``cfg.system.data_dir`` an (falls nötig) und öffnet/initialisiert
        die SQLite-Datenbankdatei.
    """
    logger.debug("create_store: verwende SQLiteStore (Default-Backend).")
    return SQLiteStore(cfg)
