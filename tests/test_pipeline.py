"""
Tests fuer :class:`core.pipeline.SleepPipeline` (Adapter → ML → Persistenz).

Abgedeckt:
    * ``run_once()`` mit dem SimulationAdapter (mode=replay, fester Seed)
      speichert genau **einen** Report mit plausiblen Werten — sowohl im
      leichten In-Memory-Fake-Store als auch im echten SQLiteStore
      (``tmp_path`` via ``data_dir``).
    * ``_handle_batch()`` ohne ``sleep_stage``-Readings speichert nichts
      (reine Vitalwert-Batches erzeugen keinen Report).
    * ``ml_pipeline.enabled=false`` speichert nichts, selbst wenn der Batch
      Schlafphasen enthaelt.
    * Ein Adapter, dessen ``open()`` wirft, wird von ``run_once()`` graceful
      uebersprungen — kein Fehler nach aussen, keine Persistenz.

Wo SQLite-I/O nichts zur Aussage beitraegt, kommt ein Fake-Store
(In-Memory-Liste) zum Einsatz, um die Tests schnell und isoliert zu halten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adapters.base_wearable import WearableAdapter, WearableReading
from adapters.simulation import SimulationAdapter
from tests.conftest import SIM_SEED, base_config_yaml
from core.config_loader import AppConfig, load_config
from core.constants import METRIC_SLEEP_STAGE
from core.pipeline import SleepPipeline
from database import create_store
from database.store import SleepStore

#: Alle Phasen-Namen, die in stages_min/stages_pct erwartet werden.
_STAGE_KEYS = {"wake", "light", "deep", "rem"}


# ----------------------------------------------------------------------------
# Test-Doubles
# ----------------------------------------------------------------------------

class FakeStore(SleepStore):
    """
    Leichter In-Memory-Store fuer Pipeline-Tests (kein SQLite-I/O).

    Attributes:
        saved: Alle via :meth:`save_report` persistierten Reports in
            Speicher-Reihenfolge.
    """

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save_report(self, report: dict[str, Any]) -> None:
        """Haengt den Report an die In-Memory-Liste an."""
        self.saved.append(report)

    async def latest_report(self) -> dict[str, Any] | None:
        """Gibt den zuletzt gespeicherten Report zurueck (oder ``None``)."""
        return self.saved[-1] if self.saved else None

    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """Gibt die juengsten Reports zurueck (neuester zuerst)."""
        if limit <= 0:
            return []
        return list(reversed(self.saved))[:limit]

    async def close(self) -> None:
        """Nichts zu schliessen — In-Memory."""


class BrokenOpenAdapter(WearableAdapter):
    """Adapter, dessen ``open()`` immer wirft (Graceful-Degradation-Test)."""

    polled: bool = False

    @property
    def name(self) -> str:
        """Eindeutiger Name des Test-Adapters."""
        return "broken_open"

    async def open(self) -> None:
        """Simuliert eine fehlgeschlagene Initialisierung (z.B. kein Geraet)."""
        raise RuntimeError("Sensor nicht erreichbar (Testfall).")

    async def poll(self) -> list[WearableReading]:
        """Darf nach fehlgeschlagenem ``open()`` nie erreicht werden."""
        self.polled = True
        return []


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

def _ml_enabled_yaml(tmp_path: Path) -> str:
    """
    Erweitert die Basis-Config um einen aktivierten ``ml_pipeline``-Block.

    Args:
        tmp_path: Temporaeres Verzeichnis fuer ``data_dir``/``log_dir``.

    Returns:
        YAML-Text mit aktivem ML-Modul (Coach/DB bleiben deaktiviert).
    """
    return base_config_yaml(tmp_path) + """
ml_pipeline:
  enabled: true

llm_coach:
  enabled: false

database:
  enabled: false
"""


@pytest.fixture()
def ml_config(tmp_path: Path) -> AppConfig:
    """Geladene AppConfig mit ``ml_pipeline.enabled=true`` und tmp-``data_dir``."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_ml_enabled_yaml(tmp_path), encoding="utf-8")
    return load_config(cfg_path)


@pytest.fixture()
def sim_adapter() -> SimulationAdapter:
    """Deterministischer Replay-SimulationAdapter (fester Seed)."""
    return SimulationAdapter(
        {"mode": "replay", "seed": SIM_SEED}, timezone="UTC"
    )


def _assert_plausible_report(report: dict[str, Any]) -> None:
    """Prueft Kernfelder eines SleepReports auf Plausibilitaet."""
    assert isinstance(report.get("date"), str) and report["date"]
    assert report.get("source") == "simulation"

    score = report.get("sleep_score")
    assert isinstance(score, int)
    assert 0 <= score <= 100

    total_sleep = float(report.get("total_sleep_min") or 0)
    assert 0 < total_sleep <= 16 * 60, "Schlafdauer muss in (0, 16h] liegen."

    efficiency = float(report.get("sleep_efficiency_pct") or 0)
    assert 0 < efficiency <= 100

    stages_min = report.get("stages_min") or {}
    assert _STAGE_KEYS <= set(stages_min), "Alle vier Phasen muessen vorkommen."
    assert all(float(v) >= 0 for v in stages_min.values())

    hypnogram = report.get("hypnogram") or []
    assert hypnogram, "Eine komplette Nacht muss Phasen-Segmente liefern."
    assert all({"stage", "start", "end"} <= set(seg) for seg in hypnogram)


# ----------------------------------------------------------------------------
# run_once: Happy Path
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_once_saves_exactly_one_report_fake_store(
    ml_config: AppConfig, sim_adapter: SimulationAdapter
) -> None:
    """run_once() mit Simulationsnacht speichert genau einen plausiblen Report."""
    store = FakeStore()
    pipeline = SleepPipeline(ml_config, store)
    try:
        await pipeline.run_once(sim_adapter)
    finally:
        await pipeline.aclose()

    assert len(store.saved) == 1, "Genau ein Report pro Replay-Nacht."
    _assert_plausible_report(store.saved[0])


@pytest.mark.asyncio
async def test_run_once_persists_report_in_real_sqlite_store(
    ml_config: AppConfig, sim_adapter: SimulationAdapter
) -> None:
    """run_once() persistiert den Report auch im echten SQLiteStore (tmp_path)."""
    store = create_store(ml_config)
    pipeline = SleepPipeline(ml_config, store)
    try:
        await pipeline.run_once(sim_adapter)

        reports = await store.list_reports(limit=10)
        assert len(reports) == 1
        _assert_plausible_report(reports[0])
        latest = await store.latest_report()
        assert latest is not None
        assert latest["date"] == reports[0]["date"]
    finally:
        await pipeline.aclose()
        await store.close()


@pytest.mark.asyncio
async def test_run_once_is_deterministic_with_seed(
    ml_config: AppConfig,
) -> None:
    """Gleicher Seed im Replay-Modus ergibt denselben Report (Score/Datum)."""
    first = FakeStore()
    second = FakeStore()
    pipeline_a = SleepPipeline(ml_config, first)
    pipeline_b = SleepPipeline(ml_config, second)
    try:
        await pipeline_a.run_once(
            SimulationAdapter({"mode": "replay", "seed": SIM_SEED}, timezone="UTC")
        )
        await pipeline_b.run_once(
            SimulationAdapter({"mode": "replay", "seed": SIM_SEED}, timezone="UTC")
        )
    finally:
        await pipeline_a.aclose()
        await pipeline_b.aclose()

    assert len(first.saved) == len(second.saved) == 1
    assert first.saved[0]["sleep_score"] == second.saved[0]["sleep_score"]
    assert first.saved[0]["date"] == second.saved[0]["date"]


# ----------------------------------------------------------------------------
# _handle_batch: Filter-Logik
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_batch_without_sleep_stages_saves_nothing(
    ml_config: AppConfig, night_readings: list[WearableReading]
) -> None:
    """Ein reiner Vitalwert-Batch (ohne sleep_stage) erzeugt keinen Report."""
    vitals_only = [
        r for r in night_readings if r.metric != METRIC_SLEEP_STAGE
    ]
    assert vitals_only, "Die Simulationsnacht muss Vitalwerte enthalten."

    store = FakeStore()
    pipeline = SleepPipeline(ml_config, store)
    try:
        await pipeline._handle_batch("simulation", vitals_only)
    finally:
        await pipeline.aclose()

    assert store.saved == [], "Ohne Schlafphasen darf nichts gespeichert werden."


@pytest.mark.asyncio
async def test_handle_batch_empty_readings_saves_nothing(
    ml_config: AppConfig,
) -> None:
    """Ein leerer Batch ist ein No-Op."""
    store = FakeStore()
    pipeline = SleepPipeline(ml_config, store)
    try:
        await pipeline._handle_batch("simulation", [])
    finally:
        await pipeline.aclose()

    assert store.saved == []


@pytest.mark.asyncio
async def test_ml_pipeline_disabled_saves_nothing(
    app_config: AppConfig, night_readings: list[WearableReading]
) -> None:
    """Bei ml_pipeline.enabled=false wird auch eine volle Nacht verworfen."""
    # ``app_config`` aus conftest hat keinen ml_pipeline-Block → enabled=false.
    assert app_config.ml_pipeline.enabled is False

    store = FakeStore()
    pipeline = SleepPipeline(app_config, store)
    try:
        await pipeline._handle_batch("simulation", night_readings)
    finally:
        await pipeline.aclose()

    assert store.saved == [], "Deaktivierte ML-Pipeline darf nichts speichern."


# ----------------------------------------------------------------------------
# run_once: Graceful Degradation
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_once_skips_adapter_with_failing_open(
    ml_config: AppConfig,
) -> None:
    """Wirft open(), wird der Adapter uebersprungen — kein Fehler, kein Report."""
    store = FakeStore()
    pipeline = SleepPipeline(ml_config, store)
    broken = BrokenOpenAdapter({}, timezone="UTC")
    try:
        # Darf keine Exception nach aussen propagieren.
        await pipeline.run_once(broken)
    finally:
        await pipeline.aclose()

    assert broken.polled is False, "Nach fehlgeschlagenem open() kein poll()."
    assert store.saved == []
