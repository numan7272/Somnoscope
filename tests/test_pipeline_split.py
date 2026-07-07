"""
Tests für die Nacht-Aufteilung der Pipeline (Fitbit-Lookback-Fix).

Deckt den HIGH-Audit-Befund ab: Ein Poll-Batch mit MEHREREN Nächten (z.B.
Fitbit ``lookback_days >= 2``) muss in einzelne Nächte gesplittet und pro Nacht
ein eigener Report gebaut werden — statt zwei Nächte zu einem verzerrten Report
mit ~30 h „Bettzeit" zu verschmelzen.
"""

from __future__ import annotations

import datetime as dt
import types

import pytest

from adapters.base_wearable import WearableReading
from core.constants import (
    METRIC_HEART_RATE,
    METRIC_SLEEP_STAGE,
    STAGE_DEEP,
    STAGE_LIGHT,
    STAGE_REM,
    STAGE_WAKE,
)
from core.pipeline import SleepPipeline, _split_into_nights

_TZ = dt.timezone.utc

# Eine einfache Nacht 23:00 -> 07:00 (480 min): 70 min wach, Rest Schlaf.
_SEGMENTS = [
    (STAGE_WAKE, 0, 10),
    (STAGE_LIGHT, 10, 90),
    (STAGE_DEEP, 90, 180),
    (STAGE_REM, 180, 240),
    (STAGE_LIGHT, 240, 420),
    (STAGE_WAKE, 420, 480),
]


def _night(day: int, source: str = "test") -> list[WearableReading]:
    """Baut Readings (Phasen-Segmente + HR-Samples) für eine Nacht ab ``day`` 23:00."""
    base = dt.datetime(2026, 7, day, 23, 0, tzinfo=_TZ)
    out: list[WearableReading] = []
    for stage, a, b in _SEGMENTS:
        out.append(
            WearableReading(
                source=source,
                metric=METRIC_SLEEP_STAGE,
                start=base + dt.timedelta(minutes=a),
                end=base + dt.timedelta(minutes=b),
                value=stage,
                unit=None,
                raw={},
            )
        )
    for minute in range(0, 480, 60):
        out.append(
            WearableReading(
                source=source,
                metric=METRIC_HEART_RATE,
                start=base + dt.timedelta(minutes=minute),
                end=None,
                value=58.0,
                unit="bpm",
                raw={},
            )
        )
    return out


class _FakeStore:
    """In-Memory-Store, um SQLite-I/O in den Tests zu vermeiden."""

    def __init__(self) -> None:
        self.saved: list[dict] = []

    async def save_report(self, report: dict) -> None:
        self.saved.append(report)

    async def latest_report(self) -> dict | None:
        return self.saved[-1] if self.saved else None

    async def list_reports(self, limit: int = 30) -> list[dict]:
        return list(reversed(self.saved))[:limit]

    async def close(self) -> None:
        pass


class _BatchAdapter:
    """Minimaler Adapter, der einen vorgegebenen Batch einmal liefert."""

    name = "test"

    def __init__(self, readings: list[WearableReading]) -> None:
        self._readings = readings

    async def open(self) -> None:
        pass

    async def poll(self) -> list[WearableReading]:
        return self._readings

    async def close(self) -> None:
        pass


def _cfg() -> types.SimpleNamespace:
    """Minimale Config: ML an, DB/Coach aus."""
    return types.SimpleNamespace(
        ml_pipeline=types.SimpleNamespace(enabled=True),
        database=types.SimpleNamespace(enabled=False),
        llm_coach=types.SimpleNamespace(enabled=False),
    )


def test_split_into_nights_trennt_zwei_naechte() -> None:
    groups = _split_into_nights(_night(5) + _night(6))
    assert len(groups) == 2
    for group in groups:
        stages = [r for r in group if r.metric == METRIC_SLEEP_STAGE]
        span = (
            max(r.end for r in stages) - min(r.start for r in stages)
        ).total_seconds()
        assert span <= 9 * 3600  # ~8 h je Nacht, NICHT ~30 h über beide


def test_split_into_nights_einzelne_nacht_bleibt_eins() -> None:
    groups = _split_into_nights(_night(5))
    assert len(groups) == 1


def test_split_into_nights_ohne_phasen_leer() -> None:
    only_vitals = [r for r in _night(5) if r.metric != METRIC_SLEEP_STAGE]
    assert _split_into_nights(only_vitals) == []


@pytest.mark.asyncio
async def test_pipeline_zwei_naechte_ergeben_zwei_reports() -> None:
    store = _FakeStore()
    pipeline = SleepPipeline(_cfg(), store)
    await pipeline.run_once(_BatchAdapter(_night(5) + _night(6)))
    await pipeline.aclose()

    assert len(store.saved) == 2
    dates = {r["date"] for r in store.saved}
    assert dates == {"2026-07-05", "2026-07-06"}
    for report in store.saved:
        assert 0 <= report["sleep_score"] <= 100
        # Effizienz darf NICHT kollabieren (Bug: ~25-30 % bei verschmolzenen Nächten).
        assert report["sleep_efficiency_pct"] >= 60
        assert report["total_sleep_min"] <= 16 * 60


@pytest.mark.asyncio
async def test_pipeline_injiziert_klima() -> None:
    """Mit ClimateBuffer landen Raumklima-Mittelwerte im Report (Phase 3)."""
    from iot import ClimateBuffer

    buffer = ClimateBuffer()
    base = dt.datetime(2026, 7, 5, 23, 30, tzinfo=_TZ)  # innerhalb der Nacht
    for i in range(5):
        ts = base + dt.timedelta(minutes=i * 30)
        buffer.add("co2", 800.0 + i, ts)
        buffer.add("temperature", 20.0, ts)
        buffer.add("humidity", 50.0, ts)

    store = _FakeStore()
    pipeline = SleepPipeline(_cfg(), store, climate_buffer=buffer)
    await pipeline.run_once(_BatchAdapter(_night(5)))

    assert len(store.saved) == 1
    climate = store.saved[0]["climate"]
    assert climate["avg_temp"] == 20.0
    assert climate["avg_humidity"] == 50.0
    assert climate["avg_co2"] is not None


@pytest.mark.asyncio
async def test_pipeline_ohne_klimapuffer_bleibt_null() -> None:
    """Ohne ClimateBuffer bleibt report["climate"] bei Null-Werten."""
    store = _FakeStore()
    pipeline = SleepPipeline(_cfg(), store)  # kein climate_buffer
    await pipeline.run_once(_BatchAdapter(_night(5)))

    climate = store.saved[0]["climate"]
    assert climate == {"avg_co2": None, "avg_temp": None, "avg_humidity": None}
