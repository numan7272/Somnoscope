"""
Prompt-Builder für den lokalen LLM-Schlafcoach (Phase 6).

Übersetzt einen SleepReport (das zentrale, JSON-serialisierbare Austauschformat
der ML-Pipeline) plus optionale Historie vergangener Nächte in einen kompakten
Prompt für ein lokales LLM (Ollama) — wahlweise auf Deutsch oder Englisch
(``lang="de"`` bzw. ``lang="en"``, Default Deutsch).

Designentscheidungen:
    * **Kompakt statt Rohdaten-Dump:** Das lokale Modell bekommt nur die
      entscheidenden Kennzahlen (Score, Effizienz, Phasenverteilung, Latenz,
      WASO, Vitalwerte, HRV-Trend) — kein Hypnogramm und keine Zeitreihen,
      damit auch kleine Modelle mit kurzem Kontextfenster gut funktionieren.
    * **Whitebox:** Die Prompt-Erstellung wird geloggt (Kernprinzip 3), damit
      nachvollziehbar bleibt, welche Fakten der Coach gesehen hat.
    * **Defensive Feldzugriffe:** Fehlende oder ``None``-Felder im Report
      führen nie zu einem Crash, sondern zu einem "k. A."-/"n/a"-Platzhalter.
    * **Zweisprachigkeit (DE/EN):** System-Anweisung und Kennzahlen-Labels
      liegen je Sprache als Text-Bausteine vor; unbekannte Sprach-Codes
      fallen still auf Deutsch zurück (Graceful Degradation, kein Crash).
"""

from __future__ import annotations

import logging
from typing import Any

from core.constants import SLEEP_STAGES

logger = logging.getLogger(__name__)

#: Vom Coach unterstützte Sprachen; alles andere fällt auf Deutsch zurück.
SUPPORTED_LANGS: frozenset[str] = frozenset({"de", "en"})

#: Default-Sprache des Coaches (rückwärtskompatibel: bisher immer Deutsch).
DEFAULT_LANG: str = "de"

#: Deutsche Anzeigenamen der Schlafphasen für den Prompt.
_STAGE_LABELS_DE: dict[str, str] = {
    "wake": "Wach",
    "light": "Leichtschlaf",
    "deep": "Tiefschlaf",
    "rem": "REM-Schlaf",
}

#: Englische Anzeigenamen der Schlafphasen für den Prompt.
_STAGE_LABELS_EN: dict[str, str] = {
    "wake": "Awake",
    "light": "Light sleep",
    "deep": "Deep sleep",
    "rem": "REM sleep",
}

#: Phasen-Labels je Sprache (Key = normalisierter Sprach-Code).
_STAGE_LABELS: dict[str, dict[str, str]] = {
    "de": _STAGE_LABELS_DE,
    "en": _STAGE_LABELS_EN,
}

#: System-Anweisung (Deutsch), die dem Modell Rolle und Grenzen vorgibt.
_SYSTEM_INSTRUCTION = (
    "Du bist ein erfahrener, empathischer Schlafcoach. Analysiere die folgenden "
    "Schlafdaten einer Nacht und gib eine kurze, motivierende Rückmeldung auf "
    "Deutsch. Nenne 2-3 konkrete, umsetzbare Tipps für besseren Schlaf, die zu "
    "den Daten passen (z.B. Schlafenszeiten, Abendroutine, Raumklima, "
    "Bildschirmzeit, Koffein). Stelle KEINE medizinischen Diagnosen und "
    "empfiehl bei auffälligen Werten lediglich, ärztlichen Rat einzuholen. "
    "Antworte in maximal 200 Wörtern, direkt an die Person gerichtet (Du-Form)."
)

#: System-Anweisung (Englisch) — inhaltlich identisch zur deutschen Fassung.
_SYSTEM_INSTRUCTION_EN = (
    "You are an experienced, empathetic sleep coach. Analyse the following "
    "sleep data from one night and give short, motivating feedback in English. "
    "Offer 2-3 concrete, actionable tips for better sleep that fit the data "
    "(e.g. bedtimes, evening routine, room climate, screen time, caffeine). "
    "Do NOT make any medical diagnoses; if values look unusual, only recommend "
    "seeking medical advice. Answer in at most 200 words, addressed directly "
    "to the person (second person)."
)

#: System-Anweisungen je Sprache.
_SYSTEM_INSTRUCTIONS: dict[str, str] = {
    "de": _SYSTEM_INSTRUCTION,
    "en": _SYSTEM_INSTRUCTION_EN,
}

#: "keine Angabe"-Platzhalter je Sprache (für fehlende Report-Felder).
_NA: dict[str, str] = {"de": "k. A.", "en": "n/a"}

#: Sprachabhängige Text-Bausteine des Prompts (Zeilen-Templates + HRV-Texte).
_PROMPT_TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "unknown": "unbekannt",
        "intro": "Schlafdaten der Nacht vom {date} (Quelle: {source}):",
        "score": "- Sleep-Score: {score} / 100",
        "duration": "- Schlafdauer: {total} (Zeit im Bett: {in_bed})",
        "efficiency": "- Schlafeffizienz: {value}",
        "latency": "- Einschlaflatenz: {value}",
        "waso": "- Wach nach dem Einschlafen (WASO): {value}",
        "stages": "- Phasen: {value}",
        "pulse": "- Puls: Ø {avg}, min. {min}",
        "spo2": "- SpO2: Ø {value}",
        "hrv": "- HRV-Trend: {value}",
        "history_header": "Vorherige Nächte (zum Vergleich):",
        "history_line": "- {date}: Score {score}, Schlaf {sleep}, Effizienz {eff}",
        "closing": "Gib jetzt dein Coaching-Feedback:",
        "hrv_no_data": "keine HRV-Daten",
        "hrv_no_baseline": "{current:.0f} ms (keine Vergleichsdaten)",
        "hrv_rising": "steigend",
        "hrv_falling": "fallend",
        "hrv_stable": "stabil",
        "hrv_trend": "{direction} ({current:.0f} ms vs. Ø {baseline:.0f} ms der Vortage)",
    },
    "en": {
        "unknown": "unknown",
        "intro": "Sleep data for the night of {date} (source: {source}):",
        "score": "- Sleep score: {score} / 100",
        "duration": "- Sleep duration: {total} (time in bed: {in_bed})",
        "efficiency": "- Sleep efficiency: {value}",
        "latency": "- Sleep onset latency: {value}",
        "waso": "- Wake after sleep onset (WASO): {value}",
        "stages": "- Stages: {value}",
        "pulse": "- Heart rate: avg {avg}, min. {min}",
        "spo2": "- SpO2: avg {value}",
        "hrv": "- HRV trend: {value}",
        "history_header": "Previous nights (for comparison):",
        "history_line": "- {date}: score {score}, sleep {sleep}, efficiency {eff}",
        "closing": "Now give your coaching feedback:",
        "hrv_no_data": "no HRV data",
        "hrv_no_baseline": "{current:.0f} ms (no comparison data)",
        "hrv_rising": "rising",
        "hrv_falling": "falling",
        "hrv_stable": "stable",
        "hrv_trend": "{direction} ({current:.0f} ms vs. avg {baseline:.0f} ms of the previous nights)",
    },
}


def normalize_lang(lang: str | None) -> str:
    """
    Normalisiert einen Sprach-Code auf eine unterstützte Coach-Sprache.

    Unbekannte, leere oder nicht-string Werte fallen still auf Deutsch
    zurück (Graceful Degradation) — es wird nie eine Exception geworfen.

    Args:
        lang: Gewünschter Sprach-Code (z.B. ``"de"``, ``"en"``, ``"EN"``)
            oder ``None``.

    Returns:
        ``"de"`` oder ``"en"`` (Default: :data:`DEFAULT_LANG`).
    """
    if isinstance(lang, str):
        candidate = lang.strip().lower()
        if candidate in SUPPORTED_LANGS:
            return candidate
    if lang not in (None, DEFAULT_LANG):
        logger.debug(
            "Unbekannter Coach-Sprach-Code %r — falle auf %r zurück.",
            lang,
            DEFAULT_LANG,
        )
    return DEFAULT_LANG


def _fmt(value: Any, unit: str = "", digits: int = 0, na: str = "k. A.") -> str:
    """
    Formatiert einen Kennzahlwert robust für den Prompt.

    Args:
        value: Zahl (int/float), String oder ``None``.
        unit: Optionale Einheit, die angehängt wird (z.B. ``" min"``, ``" %"``).
        digits: Nachkommastellen für numerische Werte.
        na: Platzhalter für fehlende Werte (sprachabhängig, Default deutsch).

    Returns:
        Formatierter String; ``na`` (z.B. ``"k. A."`` bzw. ``"n/a"``), wenn
        ``value`` fehlt oder nicht numerisch interpretierbar ist.
    """
    if value is None:
        return na
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def _stages_line(report: dict, lang: str = "de") -> str:
    """
    Baut eine kompakte Zeile mit der Phasenverteilung (Minuten + Prozent).

    Args:
        report: SleepReport-Dict mit ``stages_min`` und ``stages_pct``.
        lang: Normalisierter Sprach-Code (``"de"`` oder ``"en"``).

    Returns:
        Zeile wie ``"Wach: 25 min (5 %), Leichtschlaf: 240 min (50 %), ..."``
        bzw. die englische Entsprechung.
    """
    labels = _STAGE_LABELS.get(lang, _STAGE_LABELS_DE)
    na = _NA.get(lang, _NA["de"])
    stages_min = report.get("stages_min") or {}
    stages_pct = report.get("stages_pct") or {}
    parts: list[str] = []
    for stage in SLEEP_STAGES:
        label = labels.get(stage, stage)
        minutes = _fmt(stages_min.get(stage), " min", na=na)
        pct = _fmt(stages_pct.get(stage), " %", na=na)
        parts.append(f"{label}: {minutes} ({pct})")
    return ", ".join(parts)


def _hrv_trend(report: dict, history: list[dict] | None, lang: str = "de") -> str:
    """
    Leitet einen einfachen HRV-Trend aus der Historie ab.

    Vergleicht die durchschnittliche HRV der aktuellen Nacht mit dem Mittel
    der Historie (frühere Nächte). Eine Abweichung von mehr als 5 % gilt als
    steigend bzw. fallend.

    Args:
        report: SleepReport der aktuellen Nacht.
        history: Frühere SleepReports (beliebige Reihenfolge) oder ``None``.
        lang: Normalisierter Sprach-Code (``"de"`` oder ``"en"``).

    Returns:
        Kurzer Trend-Text in der gewählten Sprache, z.B.
        ``"steigend (62 ms vs. Ø 55 ms der Vortage)"`` bzw.
        ``"rising (62 ms vs. avg 55 ms of the previous nights)"`` oder der
        jeweilige "keine Vergleichsdaten"-Hinweis.
    """
    texts = _PROMPT_TEXTS.get(lang, _PROMPT_TEXTS["de"])
    current = (report.get("vitals") or {}).get("avg_hrv")
    if not isinstance(current, (int, float)):
        return texts["hrv_no_data"]

    past_values = [
        v
        for h in (history or [])
        if h.get("date") != report.get("date")
        and isinstance(v := (h.get("vitals") or {}).get("avg_hrv"), (int, float))
    ]
    if not past_values:
        return texts["hrv_no_baseline"].format(current=current)

    baseline = sum(past_values) / len(past_values)
    if baseline <= 0:
        return texts["hrv_no_baseline"].format(current=current)

    delta_pct = (current - baseline) / baseline * 100.0
    if delta_pct > 5.0:
        direction = texts["hrv_rising"]
    elif delta_pct < -5.0:
        direction = texts["hrv_falling"]
    else:
        direction = texts["hrv_stable"]
    return texts["hrv_trend"].format(
        direction=direction, current=current, baseline=baseline
    )


def _history_lines(
    report: dict,
    history: list[dict] | None,
    max_nights: int = 7,
    lang: str = "de",
) -> list[str]:
    """
    Fasst frühere Nächte als Kurzzeilen zusammen.

    Args:
        report: Aktueller SleepReport (wird aus der Historie ausgefiltert).
        history: Frühere SleepReports oder ``None``.
        max_nights: Maximale Anzahl an Historien-Zeilen im Prompt.
        lang: Normalisierter Sprach-Code (``"de"`` oder ``"en"``).

    Returns:
        Liste von Zeilen wie ``"- 2026-07-05: Score 78, Schlaf 421 min,
        Effizienz 91 %"`` (bzw. englisch). Leer, wenn keine Historie vorliegt.
    """
    texts = _PROMPT_TEXTS.get(lang, _PROMPT_TEXTS["de"])
    na = _NA.get(lang, _NA["de"])
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
            texts["history_line"].format(
                date=past.get("date", "?"),
                score=_fmt(past.get("sleep_score"), na=na),
                sleep=_fmt(past.get("total_sleep_min"), " min", na=na),
                eff=_fmt(past.get("sleep_efficiency_pct"), " %", na=na),
            )
        )
    return lines[:max_nights]


def build_coach_prompt(
    report: dict,
    history: list[dict] | None,
    lang: str = "de",
) -> str:
    """
    Baut den vollständigen Coaching-Prompt für das lokale LLM (DE oder EN).

    Der Prompt besteht aus einer System-Anweisung (Rolle: empathischer
    Schlafcoach, keine Diagnosen), den Kennzahlen der aktuellen Nacht
    (Score, Dauer, Effizienz, Latenz, WASO, Phasenverteilung, Vitalwerte,
    HRV-Trend) und optional einer Kurz-Historie der Vortage — komplett in
    der gewählten Sprache.

    Args:
        report: SleepReport-Dict der aktuellen Nacht (zentrales
            Austauschformat, siehe ``ml_pipeline.build_report``).
        history: Liste früherer SleepReports (z.B. aus
            ``database.list_reports``) oder ``None``.
        lang: Gewünschte Prompt-Sprache (``"de"`` oder ``"en"``); unbekannte
            Codes fallen still auf Deutsch zurück (rückwärtskompatibler
            Default: ``"de"``).

    Returns:
        Fertiger Prompt-String für ``/api/generate`` von Ollama.
    """
    lang = normalize_lang(lang)
    texts = _PROMPT_TEXTS[lang]
    na = _NA[lang]
    vitals = report.get("vitals") or {}

    lines: list[str] = [
        _SYSTEM_INSTRUCTIONS[lang],
        "",
        texts["intro"].format(
            date=report.get("date", texts["unknown"]),
            source=report.get("source", texts["unknown"]),
        ),
        texts["score"].format(score=_fmt(report.get("sleep_score"), na=na)),
        texts["duration"].format(
            total=_fmt(report.get("total_sleep_min"), " min", na=na),
            in_bed=_fmt(report.get("time_in_bed_min"), " min", na=na),
        ),
        texts["efficiency"].format(
            value=_fmt(report.get("sleep_efficiency_pct"), " %", 1, na=na)
        ),
        texts["latency"].format(
            value=_fmt(report.get("sleep_latency_min"), " min", na=na)
        ),
        texts["waso"].format(value=_fmt(report.get("waso_min"), " min", na=na)),
        texts["stages"].format(value=_stages_line(report, lang)),
        texts["pulse"].format(
            avg=_fmt(vitals.get("avg_hr"), " bpm", na=na),
            min=_fmt(vitals.get("min_hr"), " bpm", na=na),
        ),
        texts["spo2"].format(value=_fmt(vitals.get("avg_spo2"), " %", 1, na=na)),
        texts["hrv"].format(value=_hrv_trend(report, history, lang)),
    ]

    history_lines = _history_lines(report, history, lang=lang)
    if history_lines:
        lines.append("")
        lines.append(texts["history_header"])
        lines.extend(history_lines)

    lines.append("")
    lines.append(texts["closing"])

    prompt = "\n".join(lines)
    logger.debug(
        "Coach-Prompt gebaut (Sprache %s, %d Zeichen, %d Historien-Nächte).",
        lang,
        len(prompt),
        len(history_lines),
    )
    return prompt
