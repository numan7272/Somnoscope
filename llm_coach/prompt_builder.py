"""
Prompt-Builder für den lokalen LLM-Schlafcoach (Phase 6).

Übersetzt einen SleepReport (das zentrale, JSON-serialisierbare Austauschformat
der ML-Pipeline) plus optionale Historie vergangener Nächte in einen kompakten,
deutschen Prompt für ein lokales LLM (Ollama).

Designentscheidungen:
    * **Kompakt statt Rohdaten-Dump:** Das lokale Modell bekommt nur die
      entscheidenden Kennzahlen (Score, Effizienz, Phasenverteilung, Latenz,
      WASO, Vitalwerte, HRV-Trend) — kein Hypnogramm und keine Zeitreihen,
      damit auch kleine Modelle mit kurzem Kontextfenster gut funktionieren.
    * **Whitebox:** Die Prompt-Erstellung wird geloggt (Kernprinzip 3), damit
      nachvollziehbar bleibt, welche Fakten der Coach gesehen hat.
    * **Defensive Feldzugriffe:** Fehlende oder ``None``-Felder im Report
      führen nie zu einem Crash, sondern zu einem "k. A."-Platzhalter.
"""

from __future__ import annotations

import logging
from typing import Any

from core.constants import SLEEP_STAGES

logger = logging.getLogger(__name__)

#: Deutsche Anzeigenamen der Schlafphasen für den Prompt.
_STAGE_LABELS_DE: dict[str, str] = {
    "wake": "Wach",
    "light": "Leichtschlaf",
    "deep": "Tiefschlaf",
    "rem": "REM-Schlaf",
}

#: System-Anweisung, die dem Modell seine Rolle und Grenzen vorgibt.
_SYSTEM_INSTRUCTION = (
    "Du bist ein erfahrener, empathischer Schlafcoach. Analysiere die folgenden "
    "Schlafdaten einer Nacht und gib eine kurze, motivierende Rückmeldung auf "
    "Deutsch. Nenne 2-3 konkrete, umsetzbare Tipps für besseren Schlaf, die zu "
    "den Daten passen (z.B. Schlafenszeiten, Abendroutine, Raumklima, "
    "Bildschirmzeit, Koffein). Stelle KEINE medizinischen Diagnosen und "
    "empfiehl bei auffälligen Werten lediglich, ärztlichen Rat einzuholen. "
    "Antworte in maximal 200 Wörtern, direkt an die Person gerichtet (Du-Form)."
)


def _fmt(value: Any, unit: str = "", digits: int = 0) -> str:
    """
    Formatiert einen Kennzahlwert robust für den Prompt.

    Args:
        value: Zahl (int/float), String oder ``None``.
        unit: Optionale Einheit, die angehängt wird (z.B. ``" min"``, ``" %"``).
        digits: Nachkommastellen für numerische Werte.

    Returns:
        Formatierter String; ``"k. A."`` (keine Angabe), wenn ``value`` fehlt
        oder nicht numerisch interpretierbar ist.
    """
    if value is None:
        return "k. A."
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def _stages_line(report: dict) -> str:
    """
    Baut eine kompakte Zeile mit der Phasenverteilung (Minuten + Prozent).

    Args:
        report: SleepReport-Dict mit ``stages_min`` und ``stages_pct``.

    Returns:
        Zeile wie ``"Wach: 25 min (5 %), Leichtschlaf: 240 min (50 %), ..."``.
    """
    stages_min = report.get("stages_min") or {}
    stages_pct = report.get("stages_pct") or {}
    parts: list[str] = []
    for stage in SLEEP_STAGES:
        label = _STAGE_LABELS_DE.get(stage, stage)
        minutes = _fmt(stages_min.get(stage), " min")
        pct = _fmt(stages_pct.get(stage), " %")
        parts.append(f"{label}: {minutes} ({pct})")
    return ", ".join(parts)


def _hrv_trend(report: dict, history: list[dict] | None) -> str:
    """
    Leitet einen einfachen HRV-Trend aus der Historie ab.

    Vergleicht die durchschnittliche HRV der aktuellen Nacht mit dem Mittel
    der Historie (frühere Nächte). Eine Abweichung von mehr als 5 % gilt als
    steigend bzw. fallend.

    Args:
        report: SleepReport der aktuellen Nacht.
        history: Frühere SleepReports (beliebige Reihenfolge) oder ``None``.

    Returns:
        Kurzer deutscher Trend-Text, z.B.
        ``"steigend (62 ms vs. Ø 55 ms der Vortage)"`` oder ``"keine
        Vergleichsdaten"``.
    """
    current = (report.get("vitals") or {}).get("avg_hrv")
    if not isinstance(current, (int, float)):
        return "keine HRV-Daten"

    past_values = [
        v
        for h in (history or [])
        if h.get("date") != report.get("date")
        and isinstance(v := (h.get("vitals") or {}).get("avg_hrv"), (int, float))
    ]
    if not past_values:
        return f"{current:.0f} ms (keine Vergleichsdaten)"

    baseline = sum(past_values) / len(past_values)
    if baseline <= 0:
        return f"{current:.0f} ms (keine Vergleichsdaten)"

    delta_pct = (current - baseline) / baseline * 100.0
    if delta_pct > 5.0:
        direction = "steigend"
    elif delta_pct < -5.0:
        direction = "fallend"
    else:
        direction = "stabil"
    return f"{direction} ({current:.0f} ms vs. Ø {baseline:.0f} ms der Vortage)"


def _history_lines(report: dict, history: list[dict] | None, max_nights: int = 7) -> list[str]:
    """
    Fasst frühere Nächte als Kurzzeilen zusammen.

    Args:
        report: Aktueller SleepReport (wird aus der Historie ausgefiltert).
        history: Frühere SleepReports oder ``None``.
        max_nights: Maximale Anzahl an Historien-Zeilen im Prompt.

    Returns:
        Liste von Zeilen wie ``"- 2026-07-05: Score 78, Schlaf 421 min,
        Effizienz 91 %"``. Leer, wenn keine Historie vorliegt.
    """
    # Explizit nach Datum absteigend sortieren — der Aufrufer garantiert keine
    # Reihenfolge, wir wollen aber die *jüngsten* Nächte im Prompt.
    ordered = sorted(
        (p for p in (history or []) if isinstance(p, dict)),
        key=lambda p: p.get("date") or "",
        reverse=True,
    )
    lines: list[str] = []
    for past in ordered:
        if past.get("date") == report.get("date"):
            continue
        lines.append(
            f"- {past.get('date', '?')}: Score {_fmt(past.get('sleep_score'))}, "
            f"Schlaf {_fmt(past.get('total_sleep_min'), ' min')}, "
            f"Effizienz {_fmt(past.get('sleep_efficiency_pct'), ' %')}"
        )
    return lines[:max_nights]


def build_coach_prompt(report: dict, history: list[dict] | None) -> str:
    """
    Baut den vollständigen deutschen Coaching-Prompt für das lokale LLM.

    Der Prompt besteht aus einer System-Anweisung (Rolle: empathischer
    Schlafcoach, keine Diagnosen), den Kennzahlen der aktuellen Nacht
    (Score, Dauer, Effizienz, Latenz, WASO, Phasenverteilung, Vitalwerte,
    HRV-Trend) und optional einer Kurz-Historie der Vortage.

    Args:
        report: SleepReport-Dict der aktuellen Nacht (zentrales
            Austauschformat, siehe ``ml_pipeline.build_report``).
        history: Liste früherer SleepReports (z.B. aus
            ``database.list_reports``) oder ``None``.

    Returns:
        Fertiger Prompt-String für ``/api/generate`` von Ollama.
    """
    vitals = report.get("vitals") or {}

    lines: list[str] = [
        _SYSTEM_INSTRUCTION,
        "",
        f"Schlafdaten der Nacht vom {report.get('date', 'unbekannt')} "
        f"(Quelle: {report.get('source', 'unbekannt')}):",
        f"- Sleep-Score: {_fmt(report.get('sleep_score'))} / 100",
        f"- Schlafdauer: {_fmt(report.get('total_sleep_min'), ' min')} "
        f"(Zeit im Bett: {_fmt(report.get('time_in_bed_min'), ' min')})",
        f"- Schlafeffizienz: {_fmt(report.get('sleep_efficiency_pct'), ' %', 1)}",
        f"- Einschlaflatenz: {_fmt(report.get('sleep_latency_min'), ' min')}",
        f"- Wach nach dem Einschlafen (WASO): {_fmt(report.get('waso_min'), ' min')}",
        f"- Phasen: {_stages_line(report)}",
        f"- Puls: Ø {_fmt(vitals.get('avg_hr'), ' bpm')}, "
        f"min. {_fmt(vitals.get('min_hr'), ' bpm')}",
        f"- SpO2: Ø {_fmt(vitals.get('avg_spo2'), ' %', 1)}",
        f"- HRV-Trend: {_hrv_trend(report, history)}",
    ]

    history_lines = _history_lines(report, history)
    if history_lines:
        lines.append("")
        lines.append("Vorherige Nächte (zum Vergleich):")
        lines.extend(history_lines)

    lines.append("")
    lines.append("Gib jetzt dein Coaching-Feedback:")

    prompt = "\n".join(lines)
    logger.debug(
        "Coach-Prompt gebaut (%d Zeichen, %d Historien-Nächte).",
        len(prompt),
        len(history_lines),
    )
    return prompt
