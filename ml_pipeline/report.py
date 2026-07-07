"""
Report-Builder: verdichtet rohe :class:`WearableReading`-Ströme zu einem
SleepReport (zentrales Austauschformat von Somnoscope).

Der SleepReport ist ein **reines, JSON-serialisierbares dict** mit fixem
Key-Contract (siehe :func:`build_report`): Zeiten als ISO-8601-Strings,
niemals ``datetime``-Objekte. Datenbank-Layer, Dashboard und LLM-Coach
konsumieren ausschliesslich dieses Format — nie die Roh-Readings.

Ablauf:
    1. Schlafphasen-Segmente (``sleep_stage``) einsammeln und sortieren.
    2. Kennzahlen ableiten: Zeit im Bett, Gesamtschlaf, Effizienz,
       Einschlaflatenz, WASO, Minuten/Prozente je Phase, Aufwachereignisse.
    3. Vitalwert-Serien (Puls/HRV/SpO2/Hauttemperatur) zu ``series`` und
       Aggregaten (``vitals``) verdichten.
    4. Score über :func:`ml_pipeline.scorer.compute_sleep_score` berechnen.

Die CPU-Arbeit läuft via ``asyncio.to_thread`` (Asyncio-First), jeder
Rechenschritt wird geloggt (Whitebox-Prinzip).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from adapters.base_wearable import WearableReading
from core.constants import (
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SKIN_TEMP,
    METRIC_SLEEP_STAGE,
    METRIC_SPO2,
    SLEEP_STAGES,
    STAGE_WAKE,
)

from .scorer import compute_sleep_score

logger = logging.getLogger(__name__)

#: Mindestanzahl an Phasen-Segmenten, unterhalb derer kein sinnvoller
#: Report möglich ist (z.B. nur ein einzelnes Wach-Segment).
_MIN_STAGE_SEGMENTS = 2

#: Mindest-Zeit im Bett in Minuten — kürzere „Nächte" werden verworfen.
_MIN_TIME_IN_BED_MIN = 30.0

#: Vitalmetriken, die in ``series`` aufgenommen werden (fixe Reihenfolge).
_SERIES_METRICS: tuple[str, ...] = (
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SPO2,
    METRIC_SKIN_TEMP,
)


async def build_report(
    readings: list[WearableReading], source: str | None = None
) -> dict | None:
    """
    Baut aus rohen Wearable-Readings einen vollständigen SleepReport.

    Die eigentliche Aggregation ist reine CPU-Arbeit und läuft in einem
    Worker-Thread (``asyncio.to_thread``), damit der Event-Loop frei bleibt.

    Args:
        readings: Alle Messpunkte einer Nacht — Phasen-Segmente
            (``sleep_stage`` mit ``start`` **und** ``end``) plus punktuelle
            Vitalwerte (``heart_rate``/``hrv``/``spo2``/``skin_temp``).
        source: Optionaler Quellen-Name für den Report. Fällt auf die
            ``source`` des ersten Phasen-Segments zurück.

    Returns:
        SleepReport-dict exakt im Contract-Format (JSON-serialisierbar,
        alle Zeiten als ISO-8601-Strings) — oder ``None``, wenn die Eingabe
        leer ist bzw. zu wenige/zu kurze Schlafphasen-Daten enthält.

    Seiteneffekte:
        Loggt Zwischenschritte und den Grund, falls kein Report gebaut
        werden kann (Whitebox-Prinzip).
    """
    if not readings:
        logger.warning("build_report: keine Readings übergeben — kein Report.")
        return None
    return await asyncio.to_thread(_build_report_sync, readings, source)


# ----------------------------------------------------------------------------
# Synchroner Kern (läuft im Worker-Thread)
# ----------------------------------------------------------------------------

def _build_report_sync(
    readings: list[WearableReading], source: str | None
) -> dict | None:
    """Synchroner Kern von :func:`build_report` — siehe dort für Details."""
    segments = _extract_stage_segments(readings)
    if len(segments) < _MIN_STAGE_SEGMENTS:
        logger.warning(
            "build_report: nur %d gültige(s) Schlafphasen-Segment(e) "
            "(mindestens %d nötig) — kein Report.",
            len(segments),
            _MIN_STAGE_SEGMENTS,
        )
        return None

    bed_start: datetime = segments[0][1]
    bed_end: datetime = segments[-1][2]
    time_in_bed_min = _minutes(bed_end - bed_start)
    if time_in_bed_min < _MIN_TIME_IN_BED_MIN:
        logger.warning(
            "build_report: Zeit im Bett nur %.1f min (< %.0f min) — kein Report.",
            time_in_bed_min,
            _MIN_TIME_IN_BED_MIN,
        )
        return None

    # -- Schlaf-Beginn / -Ende ------------------------------------------------
    sleep_onset = next(
        (start for stage, start, _end in segments if stage != STAGE_WAKE), None
    )
    if sleep_onset is None:
        logger.warning(
            "build_report: Nacht besteht ausschliesslich aus Wach-Segmenten "
            "— kein Report."
        )
        return None
    final_wake = max(
        end for stage, _start, end in segments if stage != STAGE_WAKE
    )

    # -- Minuten je Phase, WASO, Aufwachereignisse -----------------------------
    stages_min: dict[str, float] = {stage: 0.0 for stage in SLEEP_STAGES}
    waso_min = 0.0
    awakenings = 0
    for stage, start, end in segments:
        duration = _minutes(end - start)
        stages_min[stage] = stages_min.get(stage, 0.0) + duration
        if stage == STAGE_WAKE and sleep_onset <= start < final_wake:
            waso_min += duration
            awakenings += 1

    total_sleep_min = sum(
        m for stage, m in stages_min.items() if stage != STAGE_WAKE
    )
    sleep_efficiency_pct = (
        total_sleep_min / time_in_bed_min * 100.0 if time_in_bed_min > 0 else 0.0
    )
    sleep_latency_min = _minutes(sleep_onset - bed_start)
    stages_pct = {
        stage: (m / time_in_bed_min * 100.0 if time_in_bed_min > 0 else 0.0)
        for stage, m in stages_min.items()
    }

    logger.info(
        "build_report: TIB=%.1f min, Schlaf=%.1f min, Eff=%.1f %%, "
        "Latenz=%.1f min, WASO=%.1f min, Aufwachereignisse=%d.",
        time_in_bed_min,
        total_sleep_min,
        sleep_efficiency_pct,
        sleep_latency_min,
        waso_min,
        awakenings,
    )

    # -- Vitalwerte -------------------------------------------------------------
    series = _extract_series(readings)
    hr_values = [p["v"] for p in series[METRIC_HEART_RATE]]
    hrv_values = [p["v"] for p in series[METRIC_HRV]]
    spo2_values = [p["v"] for p in series[METRIC_SPO2]]
    vitals: dict[str, float | None] = {
        "avg_hr": _rounded_mean(hr_values),
        "min_hr": round(min(hr_values), 1) if hr_values else None,
        "avg_hrv": _rounded_mean(hrv_values),
        "avg_spo2": _rounded_mean(spo2_values),
    }

    # -- Score & Zusammenbau -----------------------------------------------------
    sleep_score = compute_sleep_score(
        stages_pct=stages_pct,
        efficiency_pct=sleep_efficiency_pct,
        waso_min=waso_min,
        awakenings=awakenings,
    )

    report_source = source or segments_source(readings) or "unknown"
    return {
        "date": _evening_date(sleep_onset),
        "source": report_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sleep_onset": sleep_onset.isoformat(),
        "final_wake": final_wake.isoformat(),
        "time_in_bed_min": round(time_in_bed_min, 1),
        "total_sleep_min": round(total_sleep_min, 1),
        "sleep_efficiency_pct": round(sleep_efficiency_pct, 1),
        "sleep_latency_min": round(sleep_latency_min, 1),
        "waso_min": round(waso_min, 1),
        "stages_min": {s: round(m, 1) for s, m in stages_min.items()},
        "stages_pct": {s: round(p, 1) for s, p in stages_pct.items()},
        "sleep_score": sleep_score,
        "vitals": vitals,
        "hypnogram": [
            {"stage": stage, "start": start.isoformat(), "end": end.isoformat()}
            for stage, start, end in segments
        ],
        "series": series,
        # Klimadaten (ESP32/MQTT) werden erst in einer späteren Phase in die
        # Pipeline eingespeist — bis dahin explizit null (Graceful Degradation).
        "climate": {"avg_co2": None, "avg_temp": None, "avg_humidity": None},
    }


# ----------------------------------------------------------------------------
# Helfer
# ----------------------------------------------------------------------------

def _extract_stage_segments(
    readings: list[WearableReading],
) -> list[tuple[str, datetime, datetime]]:
    """
    Filtert und sortiert die Schlafphasen-Segmente einer Nacht.

    Verwirft unbrauchbare Einträge (fehlendes ``end``, unbekannte Phase,
    Ende vor Beginn) mit Log-Hinweis, statt zu crashen.

    Args:
        readings: Roh-Readings gemischter Metriken.

    Returns:
        Nach Startzeit sortierte Liste von ``(phase, start, end)``-Tupeln.
    """
    segments: list[tuple[str, datetime, datetime]] = []
    dropped = 0
    for r in readings:
        if r.metric != METRIC_SLEEP_STAGE:
            continue
        if r.end is None or not isinstance(r.value, str) or r.value not in SLEEP_STAGES:
            dropped += 1
            continue
        if r.end <= r.start:
            dropped += 1
            continue
        segments.append((r.value, r.start, r.end))
    if dropped:
        logger.warning(
            "build_report: %d unbrauchbare(s) sleep_stage-Reading(s) verworfen.",
            dropped,
        )
    segments.sort(key=lambda seg: seg[1])
    return segments


def _extract_series(
    readings: list[WearableReading],
) -> dict[str, list[dict[str, str | float]]]:
    """
    Baut die zeitsortierten Vitalwert-Serien für den Report.

    Args:
        readings: Roh-Readings gemischter Metriken.

    Returns:
        Mapping ``metric -> [{"t": ISO-8601, "v": float}, ...]`` für alle
        Serien-Metriken; fehlende Metriken ergeben leere Listen.
    """
    buckets: dict[str, list[tuple[datetime, float]]] = {
        metric: [] for metric in _SERIES_METRICS
    }
    for r in readings:
        if r.metric in buckets and isinstance(r.value, (int, float)):
            buckets[r.metric].append((r.start, float(r.value)))
    series: dict[str, list[dict[str, str | float]]] = {}
    for metric, points in buckets.items():
        points.sort(key=lambda p: p[0])
        series[metric] = [{"t": ts.isoformat(), "v": v} for ts, v in points]
    return series


def segments_source(readings: list[WearableReading]) -> str | None:
    """
    Ermittelt den Quellen-Namen aus dem ersten Schlafphasen-Reading.

    Args:
        readings: Roh-Readings gemischter Metriken.

    Returns:
        ``source`` des ersten ``sleep_stage``-Readings oder ``None``.
    """
    for r in readings:
        if r.metric == METRIC_SLEEP_STAGE:
            return r.source
    return None


def _evening_date(sleep_onset: datetime) -> str:
    """
    Bestimmt das Abend-Datum der Nacht (``YYYY-MM-DD``).

    Liegt der Einschlafzeitpunkt nach Mitternacht (vor 12:00 Uhr lokal),
    zählt die Nacht noch zum Vortag.

    Args:
        sleep_onset: tz-aware Einschlafzeitpunkt.

    Returns:
        ISO-Datum des Abends, an dem die Nacht begann.
    """
    if sleep_onset.hour < 12:
        return (sleep_onset - timedelta(days=1)).date().isoformat()
    return sleep_onset.date().isoformat()


def _minutes(delta: timedelta) -> float:
    """Rechnet ein ``timedelta`` in (Gleitkomma-)Minuten um."""
    return delta.total_seconds() / 60.0


def _rounded_mean(values: list[float]) -> float | None:
    """Arithmetisches Mittel, auf 1 Nachkommastelle gerundet; ``None`` bei leer."""
    if not values:
        return None
    return round(sum(values) / len(values), 1)
