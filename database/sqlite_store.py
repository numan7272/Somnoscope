"""
SQLite-basierter SleepReport-Store (Default-Persistenz von Somnoscope).

Nutzt ausschliesslich die Standardbibliothek :mod:`sqlite3` — keine neue
Abhängigkeit, keine Server-Installation, 100 % lokal (Kernprinzip 1: alle
Gesundheitsdaten bleiben auf dem Gerät).

Design:
    * **Ein Report = eine Zeile.** Der komplette SleepReport wird als
      JSON-Text in der Spalte ``json`` abgelegt; ``date`` ist Primary Key.
      Damit ist ``save_report`` ein natürlicher Upsert (``INSERT OR
      REPLACE``) und eine Nacht kann nach neuen Messdaten gefahrlos neu
      geschrieben werden.
    * **Asyncio-First:** ``sqlite3`` blockiert. Jeder DB-Zugriff läuft daher
      hinter ``await asyncio.to_thread(...)``. Die Verbindung wird mit
      ``check_same_thread=False`` geöffnet und über ein ``asyncio.Lock``
      serialisiert, damit sich Thread-Pool-Aufrufe nicht in die Quere kommen.
    * **Robust bei leerer DB:** ``latest_report``/``list_reports`` liefern
      ``None`` bzw. ``[]`` statt Exceptions.

DB-Datei: ``<cfg.system.data_dir>/somnoscope.db`` — das Verzeichnis wird bei
Bedarf angelegt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from database.store import SleepStore

if TYPE_CHECKING:  # nur für Type-Checker, kein Laufzeit-Import-Zyklus
    from core.config_loader import AppConfig

logger = logging.getLogger(__name__)

#: Dateiname der lokalen SQLite-Datenbank innerhalb von ``system.data_dir``.
DB_FILENAME = "somnoscope.db"

#: Schema der Report-Tabelle. ``json`` hält den vollständigen SleepReport.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    date         TEXT PRIMARY KEY,
    generated_at TEXT,
    json         TEXT NOT NULL
)
"""


class SQLiteStore(SleepStore):
    """
    :class:`~database.store.SleepStore`-Implementierung auf SQLite-Basis.

    Args:
        cfg: Geladene Anwendungskonfiguration (``core.config_loader.load_config()``).
            Genutzt wird ``cfg.system.data_dir`` als Ablageort der DB-Datei.

    Seiteneffekte:
        Der Konstruktor legt ``data_dir`` an (falls nötig), öffnet die
        SQLite-Datei und erstellt die Tabelle ``reports``, falls sie fehlt.
    """

    def __init__(self, cfg: AppConfig) -> None:
        data_dir = Path(cfg.system.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / DB_FILENAME

        # check_same_thread=False, weil alle Aufrufe via asyncio.to_thread in
        # wechselnden Worker-Threads laufen. Serialisierung übernimmt _lock.
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            self._db_path, check_same_thread=False
        )
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()
        logger.info("SQLiteStore geöffnet: %s", self._db_path.resolve())

    # -- Interne Helfer (laufen im Thread-Pool) ------------------------------

    def _require_conn(self) -> sqlite3.Connection:
        """
        Liefert die offene Verbindung oder wirft, wenn der Store geschlossen ist.

        Returns:
            Die aktive :class:`sqlite3.Connection`.

        Raises:
            RuntimeError: Wenn :meth:`close` bereits aufgerufen wurde.
        """
        if self._conn is None:
            raise RuntimeError("SQLiteStore ist bereits geschlossen.")
        return self._conn

    def _save_sync(self, date: str, generated_at: str | None, payload: str) -> None:
        """Blockierender Upsert — nur via ``asyncio.to_thread`` aufrufen."""
        conn = self._require_conn()
        conn.execute(
            "INSERT OR REPLACE INTO reports (date, generated_at, json) VALUES (?, ?, ?)",
            (date, generated_at, payload),
        )
        conn.commit()

    def _latest_sync(self) -> str | None:
        """Blockierendes Lesen des neuesten Reports (JSON-Text oder ``None``)."""
        conn = self._require_conn()
        row = conn.execute(
            "SELECT json FROM reports ORDER BY date DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def _list_sync(self, limit: int) -> list[str]:
        """Blockierendes Listen der jüngsten Reports (JSON-Texte, neueste zuerst)."""
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT json FROM reports ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r[0] for r in rows]

    # -- Öffentliche API (SleepStore) ----------------------------------------

    async def save_report(self, report: dict[str, Any]) -> None:
        """
        Speichert einen SleepReport per Upsert (Schlüssel: ``report["date"]``).

        Args:
            report: JSON-serialisierbares SleepReport-Dict mit mindestens
                dem Key ``"date"`` (``YYYY-MM-DD``).

        Raises:
            ValueError: Wenn ``date`` fehlt oder leer ist.
            TypeError: Wenn der Report nicht JSON-serialisierbar ist.

        Seiteneffekte:
            Schreibt (ersetzt ggf.) eine Zeile in der Tabelle ``reports``.
        """
        date = report.get("date")
        if not date or not isinstance(date, str):
            raise ValueError(
                "SleepReport ohne gültigen 'date'-Key (YYYY-MM-DD) kann "
                "nicht gespeichert werden."
            )
        generated_at = report.get("generated_at")
        payload = json.dumps(report, ensure_ascii=False)

        async with self._lock:
            await asyncio.to_thread(self._save_sync, date, generated_at, payload)
        logger.debug("SleepReport für %s gespeichert (Upsert).", date)

    async def latest_report(self) -> dict[str, Any] | None:
        """
        Liefert den neuesten SleepReport (höchstes ``date``).

        Returns:
            Das Report-Dict oder ``None`` bei leerer Datenbank.
        """
        async with self._lock:
            payload = await asyncio.to_thread(self._latest_sync)
        if payload is None:
            logger.debug("latest_report: Datenbank ist leer.")
            return None
        return json.loads(payload)

    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """
        Listet die jüngsten SleepReports, absteigend nach ``date``.

        Args:
            limit: Maximale Anzahl Reports (Default 30). Werte <= 0 ergeben
                eine leere Liste.

        Returns:
            Liste von Report-Dicts (neuester zuerst); leer bei leerer DB.
        """
        if limit <= 0:
            return []
        async with self._lock:
            payloads = await asyncio.to_thread(self._list_sync, limit)
        return [json.loads(p) for p in payloads]

    async def close(self) -> None:
        """
        Schliesst die SQLite-Verbindung (idempotent).

        Seiteneffekte:
            Nach dem Aufruf schlagen weitere Store-Methoden mit
            ``RuntimeError`` fehl.
        """
        async with self._lock:
            if self._conn is None:
                return
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)
        logger.info("SQLiteStore geschlossen: %s", self._db_path)
