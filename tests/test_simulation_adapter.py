"""
Tests fuer :mod:`adapters.simulation` (SimulationAdapter).

Abgedeckt:
    * ``poll()`` liefert Schlafphasen-Segmente (mit ``start`` UND ``end``)
      plus punktuelle Vitalwerte (``end=None``).
    * Plausibilitaet: Summe der Segment-Minuten entspricht der Zeit im Bett,
      Segmente sind lueckenlos und chronologisch, Segmentzahl moderat.
    * Determinismus via ``seed`` im ``replay``-Modus.
    * ``once``-Modus: erste Nacht komplett, danach nur Live-Vitalwerte.
"""

from __future__ import annotations

import pytest

from adapters.simulation import SimulationAdapter
from core.constants import (
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SLEEP_STAGE,
    METRIC_SPO2,
    SLEEP_STAGES,
)

#: Feste Optionen fuer reproduzierbare Test-Naechte.
_OPTS = {"mode": "replay", "seed": 42, "sleep_duration_h": 8.0}


def _stage_readings(readings: list) -> list:
    """Filtert die Schlafphasen-Segmente aus einer Reading-Liste."""
    return [r for r in readings if r.metric == METRIC_SLEEP_STAGE]


def _vital_readings(readings: list) -> list:
    """Filtert die punktuellen Vitalwerte aus einer Reading-Liste."""
    return [r for r in readings if r.metric != METRIC_SLEEP_STAGE]


@pytest.mark.asyncio
async def test_poll_delivers_stages_and_vitals() -> None:
    """Ein Poll im replay-Modus liefert Segmente UND Vitalwerte."""
    adapter = SimulationAdapter(_OPTS, timezone="UTC")
    readings = await adapter.poll()

    stages = _stage_readings(readings)
    vitals = _vital_readings(readings)
    assert stages, "Es muessen sleep_stage-Segmente vorhanden sein."
    assert vitals, "Es muessen Vitalwert-Samples vorhanden sein."

    for seg in stages:
        assert seg.source == "simulation"
        assert seg.end is not None, "Phasen-Segmente brauchen ein Intervall-Ende."
        assert seg.start.tzinfo is not None, "Zeitstempel muessen tz-aware sein."
        assert seg.end.tzinfo is not None
        assert seg.end > seg.start
        assert seg.value in SLEEP_STAGES

    vital_metrics = {r.metric for r in vitals}
    assert {METRIC_HEART_RATE, METRIC_HRV, METRIC_SPO2} <= vital_metrics
    for v in vitals:
        assert v.end is None, "Vitalwerte sind punktuell (end=None)."
        assert isinstance(v.value, float)
        assert v.start.tzinfo is not None


@pytest.mark.asyncio
async def test_stage_minutes_sum_matches_time_in_bed() -> None:
    """Die Summe der Segment-Minuten entspricht der konfigurierten Schlafdauer."""
    adapter = SimulationAdapter(_OPTS, timezone="UTC")
    readings = await adapter.poll()
    stages = _stage_readings(readings)

    total_min = sum((s.end - s.start).total_seconds() / 60.0 for s in stages)
    expected_min = _OPTS["sleep_duration_h"] * 60.0
    assert total_min == pytest.approx(expected_min, abs=1.0)

    # Segmente muessen lueckenlos und chronologisch aneinanderschliessen.
    ordered = sorted(stages, key=lambda s: s.start)
    for prev, nxt in zip(ordered, ordered[1:]):
        assert prev.end == nxt.start, "Hypnogramm darf keine Luecken/Overlaps haben."


@pytest.mark.asyncio
async def test_segment_count_is_moderate() -> None:
    """Segmentzahl plausibel: keine 30-s-Flacker, keine Mononacht."""
    adapter = SimulationAdapter(_OPTS, timezone="UTC")
    readings = await adapter.poll()
    stages = _stage_readings(readings)

    # 8 h mit Bouts von ~2-20 min: grob zwischen 5 und 150 Segmenten.
    assert 5 <= len(stages) <= 150

    # Vitalwerte im 5-Minuten-Raster: 8 h -> 96 Samples je Metrik.
    hr = [r for r in readings if r.metric == METRIC_HEART_RATE]
    assert len(hr) == int(8.0 * 60 / 5)


@pytest.mark.asyncio
async def test_replay_with_seed_is_deterministic() -> None:
    """Gleicher Seed -> identische Nacht (Segmente und Werte)."""
    r1 = await SimulationAdapter(_OPTS, timezone="UTC").poll()
    r2 = await SimulationAdapter(_OPTS, timezone="UTC").poll()

    s1 = [(s.value, s.start, s.end) for s in _stage_readings(r1)]
    s2 = [(s.value, s.start, s.end) for s in _stage_readings(r2)]
    assert s1 == s2

    v1 = [(v.metric, v.start, v.value) for v in _vital_readings(r1)]
    v2 = [(v.metric, v.start, v.value) for v in _vital_readings(r2)]
    assert v1 == v2


@pytest.mark.asyncio
async def test_once_mode_emits_night_only_first_poll() -> None:
    """``once``-Modus: Nacht nur beim ersten Poll, danach Live-Vitalwerte."""
    adapter = SimulationAdapter({"mode": "once", "seed": 1}, timezone="UTC")

    first = await adapter.poll()
    assert _stage_readings(first), "Erster Poll muss die Nacht enthalten."

    second = await adapter.poll()
    assert not _stage_readings(second), "Zweiter Poll darf keine Nacht mehr liefern."
    assert second, "Zweiter Poll liefert Live-Vitalwerte."
    assert all(r.end is None for r in second)


@pytest.mark.asyncio
async def test_vitals_within_physiological_ranges() -> None:
    """Puls/HRV/SpO2 der Nacht bleiben in physiologisch plausiblen Bereichen."""
    adapter = SimulationAdapter(_OPTS, timezone="UTC")
    readings = await adapter.poll()

    for r in readings:
        if r.metric == METRIC_HEART_RATE:
            assert 35.0 <= float(r.value) <= 110.0
        elif r.metric == METRIC_HRV:
            assert 10.0 <= float(r.value) <= 150.0
        elif r.metric == METRIC_SPO2:
            assert 85.0 <= float(r.value) <= 100.0
