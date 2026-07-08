"""
Trend-Analyse über mehrere SleepReports hinweg.

Verdichtet eine Liste von SleepReport-Dicts (siehe
:func:`ml_pipeline.report.build_report`) zu einem JSON-serialisierbaren
Trend-Dict mit fixem Key-Contract: Kennzahlen-Mittelwerte, chronologische
Zeitreihen für das Dashboard, mittlere Phasenverteilung, beste/schlechteste
Nacht und ein einfaches Konsistenzmaß (Streuung des Sleep-Scores).

:func:`compute_trends` ist eine **reine, deterministische Funktion** ohne
I/O — sie liest weder Datenbank noch Config. Blockierende Aufrufer (z.B.
die WebUI) können sie bei großen Historien via ``asyncio.to_thread``
auslagern (Asyncio-First-Konvention).

Robustheit (Graceful Degradation):
    * Leere Eingabeliste -> ``n_nights = 0``, leere Serien, ``null``-Averages.
    * Nur Einträge mit gültigem ``date`` (``YYYY-MM-DD``) **und** gültigem
      ``sleep_score`` zählen; alles andere wird geloggt und übersprungen.
    * Fehlende optionale Felder (``vitals``, ``stages_pct`` …) führen zu
      ``null``-Serienwerten bzw. Null-Phasenanteilen, nie zu Exceptions.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime

from core.constants import SLEEP_STAGES

logger = logging.getLogger(__name__)

#: Erwartetes Datumsformat des SleepReport-``date``-Keys.
_DATE_FORMAT = "%Y-%m-%d"

#: Kennzahlen, die als Mittelwert in ``averages`` landen. Mapping:
#: Averages-Key -> Extraktor-Pfad (Top-Level-Key oder ("vitals", Sub-Key)).
_AVERAGE_KEYS: tuple[str, ...] = (
    "sleep_score",
    "sleep_efficiency_pct",
    "total_sleep_min",
    "avg_hrv",
)


def _is_valid_date(value: object) -> bool:
    """
    Prüft, ob ein Wert ein gültiger ``YYYY-MM-DD``-Datumsstring ist.

    Args:
        value: Beliebiger Kandidat aus einem Report-Dict.

    Returns:
        ``True``, wenn ``value`` ein String im Format ``YYYY-MM-DD`` ist
        und ein reales Kalenderdatum bezeichnet, sonst ``False``.
    """
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, _DATE_FORMAT)
    except ValueError:
        return False
    # Round-Trip: nur die kanonische, nullgepolsterte Form (z.B. "2026-01-09")
    # gilt — sonst bräche die rein lexikografische Sortierung an Werten wie
    # "2026-1-9" (die strptime zwar akzeptiert, die aber falsch einsortieren).
    return parsed.strftime(_DATE_FORMAT) == value


def _as_number(value: object) -> float | None:
    """
    Konvertiert einen Report-Wert defensiv in eine endliche Zahl.

    Bool-Werte werden bewusst verworfen (``True`` ist in Python ein ``int``,
    aber nie ein sinnvoller Messwert), ebenso ``NaN``/``inf``.

    Args:
        value: Beliebiger Kandidat (z.B. aus ``report["vitals"]``).

    Returns:
        Den Wert als ``float`` — oder ``None``, wenn er fehlt bzw. keine
        endliche Zahl ist.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _is_valid_score(value: object) -> bool:
    """
    Prüft, ob ein Wert ein gültiger Sleep-Score ist.

    Args:
        value: Kandidat aus ``report["sleep_score"]``.

    Returns:
        ``True`` für ganzzahlige, endliche Werte (kein ``bool``),
        sonst ``False``.
    """
    number = _as_number(value)
    return number is not None and float(number).is_integer()


def _extract_metric(report: dict, key: str) -> float | None:
    """
    Liest eine Trend-Kennzahl defensiv aus einem SleepReport.

    ``avg_hrv`` liegt im verschachtelten ``vitals``-Block, alle anderen
    Kennzahlen auf Top-Level.

    Args:
        report: Ein (bereits als gültig eingestufter) SleepReport.
        key: Einer der Keys aus :data:`_AVERAGE_KEYS`.

    Returns:
        Den Wert als ``float`` oder ``None``, wenn er fehlt/ungültig ist.
    """
    if key == "avg_hrv":
        vitals = report.get("vitals")
        if not isinstance(vitals, dict):
            return None
        return _as_number(vitals.get("avg_hrv"))
    return _as_number(report.get(key))


def _extract_stages(report: dict) -> dict[str, float] | None:
    """
    Liest die Phasenverteilung (``stages_pct``) defensiv aus einem Report.

    Args:
        report: Ein (bereits als gültig eingestufter) SleepReport.

    Returns:
        Dict mit genau den Keys aus :data:`core.constants.SLEEP_STAGES`
        (fehlende Phasen als ``0.0``) — oder ``None``, wenn der Report
        gar keinen brauchbaren ``stages_pct``-Block enthält.
    """
    stages = report.get("stages_pct")
    if not isinstance(stages, dict):
        return None
    normalized: dict[str, float] = {}
    any_value = False
    for stage in SLEEP_STAGES:
        value = _as_number(stages.get(stage))
        if value is not None:
            any_value = True
        normalized[stage] = value if value is not None else 0.0
    return normalized if any_value else None


def compute_trends(reports: list[dict]) -> dict:
    """
    Berechnet Mehr-Nächte-Trends aus einer Liste von SleepReports.

    Reine, deterministische Funktion ohne Seiteneffekte (außer Logging).
    Die Eingabereihenfolge ist egal — die Zeitreihen in ``series`` werden
    chronologisch aufsteigend nach ``date`` sortiert. Nur Einträge, die ein
    Dict mit gültigem ``date`` (``YYYY-MM-DD``) und gültigem ``sleep_score``
    sind, zählen als Nacht; alle anderen werden übersprungen (Whitebox:
    jeder Skip wird geloggt).

    Args:
        reports: SleepReport-Dicts in beliebiger Reihenfolge, z.B. aus
            ``store.list_reports(limit=...)``.

    Returns:
        JSON-serialisierbares Trend-Dict mit exakt diesen Keys:

        * ``n_nights``: Anzahl gültiger Nächte.
        * ``range``: ``{"from": str|None, "to": str|None}`` — erstes/letztes
          Datum der gültigen Nächte.
        * ``averages``: Mittelwerte über Nächte mit vorhandenem Wert
          (``sleep_score``, ``sleep_efficiency_pct``, ``total_sleep_min``,
          ``avg_hrv``), jeweils ``None`` ohne Datenbasis.
        * ``stage_distribution_pct``: mittlere Phasenanteile über Nächte
          mit ``stages_pct``-Block (sonst ``0.0`` je Phase).
        * ``series``: chronologisch sortierte, gleich lange Listen
          (``date``, ``sleep_score``, ``sleep_efficiency_pct``,
          ``total_sleep_min``, ``avg_hrv``, ``stages_pct``); fehlende
          Werte als ``None`` bzw. Null-Phasen-Dict.
        * ``best_night`` / ``worst_night``: ``{"date", "sleep_score"}`` der
          Nacht mit höchstem/niedrigstem Score (bei Gleichstand die
          chronologisch frühere) — ``None`` ohne gültige Nächte.
        * ``consistency``: ``{"score_stddev": float|None}`` —
          Populations-Standardabweichung der Scores (``0.0`` bei genau
          einer Nacht, ``None`` ohne Nächte).
    """
    valid: list[dict] = []
    skipped = 0
    for entry in reports or []:
        if (
            isinstance(entry, dict)
            and _is_valid_date(entry.get("date"))
            and _is_valid_score(entry.get("sleep_score"))
        ):
            valid.append(entry)
        else:
            skipped += 1
    if skipped:
        logger.info(
            "compute_trends: %d von %d Reports übersprungen (ungültiges "
            "date/sleep_score).",
            skipped,
            len(reports or []),
        )

    # Chronologisch aufsteigend; bei Datums-Duplikaten bleibt die
    # Eingabereihenfolge stabil erhalten (sorted ist stabil).
    valid.sort(key=lambda r: r["date"])

    series: dict[str, list] = {
        "date": [],
        "sleep_score": [],
        "sleep_efficiency_pct": [],
        "total_sleep_min": [],
        "avg_hrv": [],
        "stages_pct": [],
    }
    metric_values: dict[str, list[float]] = {key: [] for key in _AVERAGE_KEYS}
    stage_rows: list[dict[str, float]] = []
    scores: list[tuple[str, int]] = []

    for report in valid:
        date = report["date"]
        score = int(report["sleep_score"])
        scores.append((date, score))
        series["date"].append(date)
        series["sleep_score"].append(score)
        metric_values["sleep_score"].append(float(score))

        for key in ("sleep_efficiency_pct", "total_sleep_min", "avg_hrv"):
            value = _extract_metric(report, key)
            series[key].append(value)
            if value is not None:
                metric_values[key].append(value)

        stages = _extract_stages(report)
        if stages is not None:
            stage_rows.append(stages)
            series["stages_pct"].append(dict(stages))
        else:
            series["stages_pct"].append({stage: 0.0 for stage in SLEEP_STAGES})

    averages: dict[str, float | None] = {
        key: (statistics.fmean(values) if values else None)
        for key, values in metric_values.items()
    }

    stage_distribution: dict[str, float] = {
        stage: (
            statistics.fmean(row[stage] for row in stage_rows)
            if stage_rows
            else 0.0
        )
        for stage in SLEEP_STAGES
    }

    best_night: dict | None = None
    worst_night: dict | None = None
    if scores:
        # max/min liefern bei Gleichstand das erste (= chronologisch
        # früheste) Element — deterministisch.
        best_date, best_score = max(scores, key=lambda item: item[1])
        worst_date, worst_score = min(scores, key=lambda item: item[1])
        best_night = {"date": best_date, "sleep_score": best_score}
        worst_night = {"date": worst_date, "sleep_score": worst_score}

    score_stddev: float | None = None
    if scores:
        score_stddev = statistics.pstdev(score for _, score in scores)

    logger.debug(
        "compute_trends: %d gültige Nächte (%s bis %s) aggregiert.",
        len(valid),
        series["date"][0] if series["date"] else "-",
        series["date"][-1] if series["date"] else "-",
    )

    return {
        "n_nights": len(valid),
        "range": {
            "from": series["date"][0] if series["date"] else None,
            "to": series["date"][-1] if series["date"] else None,
        },
        "averages": averages,
        "stage_distribution_pct": stage_distribution,
        "series": series,
        "best_night": best_night,
        "worst_night": worst_night,
        "consistency": {"score_stddev": score_stddev},
    }
