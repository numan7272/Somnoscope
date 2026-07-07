"""
Daten-Export für SleepReports (CSV und JSON).

Wandelt eine Liste von SleepReport-Dicts (siehe
:func:`ml_pipeline.report.build_report`) in portable Export-Formate um:

    * :func:`reports_to_csv`  — eine Zeile je Nacht, stabile Spaltenreihenfolge,
      fehlende Werte als leere Zellen (tabellenkalkulations-freundlich).
    * :func:`reports_to_json` — vollständige Reports als JSON-Objekt mit
      ``exported_report_count`` (verlustfrei, maschinenlesbar).

Beide Funktionen sind **reine, deterministische Funktionen** ohne I/O — sie
lesen weder Datenbank noch Config und nutzen ausschließlich die
Standardbibliothek. Blockierende Aufrufer (z.B. die WebUI) können sie bei
großen Historien via ``asyncio.to_thread`` auslagern (Asyncio-First).

Robustheit (Graceful Degradation):
    * Leere Eingabeliste -> CSV nur mit Header-Zeile bzw. JSON mit
      ``exported_report_count = 0``.
    * Fehlende/``None``-Felder -> leere CSV-Zellen, nie Exceptions.
    * Nicht-Dict-Einträge werden geloggt und übersprungen.
    * Die Ausgabe ist immer chronologisch aufsteigend nach ``date`` sortiert,
      unabhängig von der Eingabe-Reihenfolge (der Store liefert neueste zuerst).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from core.constants import (
    CLIMATE_KEY_AVG_CO2,
    CLIMATE_KEY_AVG_HUMIDITY,
    CLIMATE_KEY_AVG_TEMP,
    SLEEP_STAGES,
)

logger = logging.getLogger(__name__)

#: Top-Level-Kennzahlen des SleepReport in Export-Reihenfolge.
_SCALAR_KEYS: tuple[str, ...] = (
    "sleep_score",
    "sleep_efficiency_pct",
    "total_sleep_min",
    "sleep_latency_min",
    "waso_min",
)

#: Keys des ``vitals``-Blocks in Export-Reihenfolge.
_VITALS_KEYS: tuple[str, ...] = ("avg_hr", "min_hr", "avg_hrv", "avg_spo2")

#: Keys des ``climate``-Blocks in Export-Reihenfolge (aus core.constants).
_CLIMATE_KEYS: tuple[str, ...] = (
    CLIMATE_KEY_AVG_CO2,
    CLIMATE_KEY_AVG_TEMP,
    CLIMATE_KEY_AVG_HUMIDITY,
)

#: Stabile CSV-Spaltenreihenfolge (Contract — NICHT umsortieren, externe
#: Konsumenten wie Tabellenkalkulationen und Notebooks verlassen sich darauf).
CSV_COLUMNS: tuple[str, ...] = (
    "date",
    "source",
    *_SCALAR_KEYS,
    *(f"{stage}_min" for stage in SLEEP_STAGES),
    *_VITALS_KEYS,
    *_CLIMATE_KEYS,
)


def _clean_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filtert Nicht-Dict-Einträge heraus und sortiert chronologisch aufsteigend.

    Der Store liefert Reports neueste-zuerst; Exporte sollen aber
    chronologisch lesbar sein (älteste Nacht oben). Sortiert wird rein
    lexikografisch über den ``date``-String (``YYYY-MM-DD`` sortiert damit
    korrekt); Einträge ohne ``date`` landen deterministisch am Anfang.

    Args:
        reports: Rohe Liste von SleepReport-Dicts (beliebige Reihenfolge).

    Returns:
        Neue, chronologisch aufsteigend sortierte Liste; Nicht-Dict-Einträge
        werden geloggt und verworfen (Graceful Degradation).
    """
    cleaned: list[dict[str, Any]] = []
    for entry in reports:
        if isinstance(entry, dict):
            cleaned.append(entry)
        else:
            logger.warning(
                "Export: Nicht-Dict-Eintrag (%r) übersprungen.", type(entry).__name__
            )
    cleaned.sort(key=lambda report: str(report.get("date") or ""))
    return cleaned


def _cell(value: Any) -> Any:
    """
    Normalisiert einen Report-Wert für eine CSV-Zelle.

    Args:
        value: Beliebiger Wert aus dem Report (Zahl, String oder ``None``).

    Returns:
        Leerer String bei ``None`` (fehlender Messwert -> leere Zelle),
        sonst der Wert. String-Zellen, die mit ``=``, ``+``, ``-`` oder ``@``
        beginnen, werden mit einem führenden Apostroph neutralisiert
        (Defense-in-Depth gegen Formel-/CSV-Injection in Tabellenkalkulationen;
        Zahlen bleiben unangetastet).
    """
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _report_to_row(report: dict[str, Any]) -> list[Any]:
    """
    Baut aus einem SleepReport die CSV-Zeile in :data:`CSV_COLUMNS`-Reihenfolge.

    Fehlende Keys oder Nicht-Dict-Unterblöcke (``stages_min``, ``vitals``,
    ``climate``) führen zu leeren Zellen, nie zu Exceptions.

    Args:
        report: Ein einzelnes SleepReport-Dict.

    Returns:
        Zellenliste, positionsgleich zu :data:`CSV_COLUMNS`.
    """
    stages = report.get("stages_min")
    vitals = report.get("vitals")
    climate = report.get("climate")
    stages = stages if isinstance(stages, dict) else {}
    vitals = vitals if isinstance(vitals, dict) else {}
    climate = climate if isinstance(climate, dict) else {}

    row: list[Any] = [_cell(report.get("date")), _cell(report.get("source"))]
    row.extend(_cell(report.get(key)) for key in _SCALAR_KEYS)
    row.extend(_cell(stages.get(stage)) for stage in SLEEP_STAGES)
    row.extend(_cell(vitals.get(key)) for key in _VITALS_KEYS)
    row.extend(_cell(climate.get(key)) for key in _CLIMATE_KEYS)
    return row


def reports_to_csv(reports: list[dict[str, Any]]) -> str:
    """
    Serialisiert SleepReports als CSV-String (eine Zeile je Nacht).

    Erste Zeile ist der Header (:data:`CSV_COLUMNS`), danach folgen die
    Nächte chronologisch aufsteigend nach ``date``. Fehlende oder
    ``None``-Werte werden zu leeren Zellen; Quoting übernimmt das
    ``csv``-Modul (Sonderzeichen wie Komma/Anführungszeichen sind sicher).

    Args:
        reports: Liste von SleepReport-Dicts (beliebige Reihenfolge, z.B.
            neueste-zuerst aus ``store.list_reports``).

    Returns:
        CSV-Text mit ``\\n``-Zeilenenden; bei leerer Liste nur die
        Header-Zeile.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    cleaned = _clean_reports(reports)
    for report in cleaned:
        writer.writerow(_report_to_row(report))
    logger.debug("Export: %d Report(s) als CSV serialisiert.", len(cleaned))
    return buffer.getvalue()


def reports_to_json(reports: list[dict[str, Any]]) -> str:
    """
    Serialisiert SleepReports als JSON-String (volle Reports, verlustfrei).

    Das Ergebnis ist ein Objekt der Form::

        {"exported_report_count": <int>, "reports": [<SleepReport>, ...]}

    Die Reports erscheinen chronologisch aufsteigend nach ``date`` und
    unverändert in voller Tiefe (inkl. ``hypnogram`` etc.).

    Args:
        reports: Liste von SleepReport-Dicts (beliebige Reihenfolge).

    Returns:
        JSON-Text (``ensure_ascii=False``, ``indent=2``).
    """
    cleaned = _clean_reports(reports)
    payload = {"exported_report_count": len(cleaned), "reports": cleaned}
    logger.debug("Export: %d Report(s) als JSON serialisiert.", len(cleaned))
    return json.dumps(payload, ensure_ascii=False, indent=2)
