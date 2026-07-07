"""
Abstrakte Storage-Schnittstelle für Somnoscope-SleepReports.

Definiert den Vertrag, den jede konkrete Persistenz-Implementierung
(z.B. :class:`database.sqlite_store.SQLiteStore`) erfüllen muss. Die restliche
Anwendung (Dashboard, LLM-Coach, main.py) spricht ausschliesslich gegen diese
Schnittstelle — welche Datenbank dahinter liegt, ist austauschbar
(Adapter-Pattern, Kernprinzip 4).

Das gespeicherte Objekt ist immer ein **SleepReport**: ein reines,
JSON-serialisierbares ``dict`` mit ISO-8601-Zeitstrings (siehe
``ml_pipeline.build_report``). Der Schlüssel für Upserts ist
``report["date"]`` — das Abend-Datum der Nacht im Format ``YYYY-MM-DD``.

Alle Methoden sind ``async`` (Asyncio-First); blockierende DB-Arbeit gehört in
den Implementierungen hinter ``await asyncio.to_thread(...)``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SleepStore(ABC):
    """
    Basisklasse aller SleepReport-Stores.

    Implementierungen müssen idempotent speichern (Upsert nach ``date``) und
    bei leerer Datenbank robust ``None`` bzw. leere Listen liefern — niemals
    Exceptions wegen "keine Daten".
    """

    @abstractmethod
    async def save_report(self, report: dict[str, Any]) -> None:
        """
        Speichert einen SleepReport (Upsert).

        Existiert bereits ein Report mit demselben ``report["date"]``, wird er
        vollständig ersetzt — so kann eine Nacht nach neuen Messdaten
        gefahrlos neu gescort werden.

        Args:
            report: JSON-serialisierbares SleepReport-Dict. Muss mindestens
                den Key ``"date"`` (``YYYY-MM-DD``) enthalten.

        Raises:
            ValueError: Wenn ``report`` keinen gültigen ``date``-Key hat.

        Seiteneffekte:
            Schreibt in die jeweilige Persistenz (z.B. SQLite-Datei).
        """

    @abstractmethod
    async def latest_report(self) -> dict[str, Any] | None:
        """
        Liefert den neuesten SleepReport (höchstes ``date``).

        Returns:
            Das Report-Dict oder ``None``, wenn noch kein Report existiert.
        """

    @abstractmethod
    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """
        Listet die jüngsten SleepReports, absteigend nach ``date`` sortiert.

        Args:
            limit: Maximale Anzahl zurückgegebener Reports (Default 30,
                also etwa ein Monat Schlafhistorie).

        Returns:
            Liste von Report-Dicts (neuester zuerst). Leere Liste, wenn die
            Datenbank leer ist.
        """

    @abstractmethod
    async def close(self) -> None:
        """
        Gibt alle Ressourcen frei (Verbindungen, File-Handles).

        Nach ``close()`` darf der Store nicht weiterverwendet werden.
        Mehrfaches Aufrufen ist erlaubt und wirkungslos (idempotent).
        """
