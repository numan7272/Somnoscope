"""
Tests fuer :func:`ml_pipeline.build_report` (SleepReport-Contract).

Abgedeckt:
    * Vollstaendiger Key-Contract des SleepReports.
    * Wertebereiche: Effizienz in [0, 100], stages_pct-Summe ~100,
      sleep_score als int in [0, 100].
    * JSON-Serialisierbarkeit (``json.dumps`` ohne default-Hook).
    * Konsistenz der Kennzahlen (TIB = Schlaf + Wach, Latenz/WASO >= 0).
    * ``None``-Rueckgabe bei leerer/zu duenner Datenlage.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_wearable import WearableReading
from core.constants import (
    METRIC_SLEEP_STAGE,
    SLEEP_STAGES,
    STAGE_LIGHT,
    STAGE_WAKE,
)
from ml_pipeline import build_report

#: Alle Pflicht-Keys des SleepReport-Contracts.
_REQUIRED_KEYS = {
    "date",
    "source",
    "generated_at",
    "sleep_onset",
    "final_wake",
    "time_in_bed_min",
    "total_sleep_min",
    "sleep_efficiency_pct",
    "sleep_latency_min",
    "waso_min",
    "stages_min",
    "stages_pct",
    "sleep_score",
    "vitals",
    "hypnogram",
    "series",
    "climate",
}


def test_report_contract_keys(sample_report: dict) -> None:
    """Der Report enthaelt alle Contract-Keys."""
    assert _REQUIRED_KEYS <= set(sample_report.keys())
    assert set(sample_report["stages_min"].keys()) == set(SLEEP_STAGES)
    assert set(sample_report["stages_pct"].keys()) == set(SLEEP_STAGES)
    assert set(sample_report["vitals"].keys()) == {
        "avg_hr",
        "min_hr",
        "avg_hrv",
        "avg_spo2",
    }
    assert set(sample_report["climate"].keys()) == {
        "avg_co2",
        "avg_temp",
        "avg_humidity",
    }
    assert set(sample_report["series"].keys()) == {
        "heart_rate",
        "hrv",
        "spo2",
        "skin_temp",
    }


def test_report_value_ranges(sample_report: dict) -> None:
    """Effizienz, Prozentsumme und Score liegen in den erwarteten Bereichen."""
    assert 0.0 <= sample_report["sleep_efficiency_pct"] <= 100.0

    pct_sum = sum(sample_report["stages_pct"].values())
    assert pct_sum == pytest.approx(100.0, abs=1.0)

    score = sample_report["sleep_score"]
    assert isinstance(score, int)
    assert 0 <= score <= 100

    assert sample_report["sleep_latency_min"] >= 0.0
    assert sample_report["waso_min"] >= 0.0
    assert sample_report["total_sleep_min"] <= sample_report["time_in_bed_min"]


def test_report_metric_consistency(sample_report: dict) -> None:
    """Schlaf- und Wachminuten summieren sich zur Zeit im Bett."""
    stages_min = sample_report["stages_min"]
    total_from_stages = sum(stages_min.values())
    assert total_from_stages == pytest.approx(
        sample_report["time_in_bed_min"], abs=1.0
    )
    sleep_from_stages = sum(
        m for stage, m in stages_min.items() if stage != STAGE_WAKE
    )
    assert sleep_from_stages == pytest.approx(
        sample_report["total_sleep_min"], abs=1.0
    )


def test_report_is_json_serializable(sample_report: dict) -> None:
    """``json.dumps`` funktioniert ohne default-Hook (keine datetime-Objekte)."""
    payload = json.dumps(sample_report, ensure_ascii=False)
    roundtrip = json.loads(payload)
    assert roundtrip["date"] == sample_report["date"]


def test_report_times_are_iso_strings(sample_report: dict) -> None:
    """Alle Zeiten sind parsebare ISO-8601-Strings, das Datum YYYY-MM-DD."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", sample_report["date"])
    for key in ("generated_at", "sleep_onset", "final_wake"):
        parsed = datetime.fromisoformat(sample_report[key])
        assert parsed.tzinfo is not None, f"{key} muss tz-aware sein."

    onset = datetime.fromisoformat(sample_report["sleep_onset"])
    wake = datetime.fromisoformat(sample_report["final_wake"])
    assert wake > onset

    for seg in sample_report["hypnogram"]:
        assert seg["stage"] in SLEEP_STAGES
        start = datetime.fromisoformat(seg["start"])
        end = datetime.fromisoformat(seg["end"])
        assert end > start

    for series_name, points in sample_report["series"].items():
        for p in points:
            datetime.fromisoformat(p["t"])
            assert isinstance(p["v"], float), f"{series_name}: v muss float sein."


@pytest.mark.asyncio
async def test_report_source_override(
    night_readings: list[WearableReading],
) -> None:
    """Der ``source``-Parameter ueberschreibt die Reading-Quelle."""
    report = await build_report(night_readings, source="mein_test")
    assert report is not None
    assert report["source"] == "mein_test"


@pytest.mark.asyncio
async def test_report_source_falls_back_to_readings(
    night_readings: list[WearableReading],
) -> None:
    """Ohne ``source``-Parameter kommt die Quelle aus den Readings."""
    report = await build_report(night_readings)
    assert report is not None
    assert report["source"] == "simulation"


# ----------------------------------------------------------------------------
# Degradation: zu wenig Daten
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_readings_yield_none() -> None:
    """Leere Eingabe -> None (kein Crash, kein leerer Report)."""
    assert await build_report([]) is None


@pytest.mark.asyncio
async def test_single_segment_yields_none() -> None:
    """Ein einzelnes Segment reicht nicht fuer einen Report."""
    start = datetime(2026, 7, 6, 23, 0, tzinfo=timezone.utc)
    reading = WearableReading(
        source="test",
        metric=METRIC_SLEEP_STAGE,
        start=start,
        end=start + timedelta(hours=1),
        value=STAGE_LIGHT,
    )
    assert await build_report([reading]) is None


@pytest.mark.asyncio
async def test_too_short_night_yields_none() -> None:
    """Unter 30 Minuten Zeit im Bett gibt es keinen Report."""
    start = datetime(2026, 7, 6, 23, 0, tzinfo=timezone.utc)
    readings = [
        WearableReading(
            source="test",
            metric=METRIC_SLEEP_STAGE,
            start=start,
            end=start + timedelta(minutes=5),
            value=STAGE_WAKE,
        ),
        WearableReading(
            source="test",
            metric=METRIC_SLEEP_STAGE,
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=15),
            value=STAGE_LIGHT,
        ),
    ]
    assert await build_report(readings) is None


@pytest.mark.asyncio
async def test_wake_only_night_yields_none() -> None:
    """Eine Nacht nur aus Wach-Segmenten ergibt keinen Report."""
    start = datetime(2026, 7, 6, 23, 0, tzinfo=timezone.utc)
    readings = [
        WearableReading(
            source="test",
            metric=METRIC_SLEEP_STAGE,
            start=start + timedelta(hours=i),
            end=start + timedelta(hours=i + 1),
            value=STAGE_WAKE,
        )
        for i in range(3)
    ]
    assert await build_report(readings) is None
