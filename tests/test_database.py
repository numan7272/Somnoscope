"""
Tests fuer die Persistenz-Schicht (:mod:`database`, SQLiteStore).

Abgedeckt:
    * Factory :func:`database.create_store` liefert einen SQLiteStore.
    * Roundtrip ``save_report`` -> ``latest_report`` -> ``list_reports``
      in einer temporaeren Datenbank (``tmp_path``).
    * Upsert-Semantik (gleiches ``date`` ersetzt, dupliziert nicht).
    * Sortierung (neuester Report zuerst), ``limit``-Handling.
    * Persistenz ueber Store-Neustart hinweg; ``close`` ist idempotent.

Der Import von :mod:`database` zieht auch den optionalen InfluxWriter mit —
dank Graceful Degradation darf das ohne ``influxdb-client`` nicht crashen.
"""

from __future__ import annotations

import copy

import pytest

from core.config_loader import AppConfig
from database import SQLiteStore, create_store
from database.sqlite_store import DB_FILENAME


def _report_for(date: str, score: int = 80) -> dict:
    """Baut einen minimalen, JSON-serialisierbaren Test-Report."""
    return {
        "date": date,
        "source": "simulation",
        "generated_at": f"{date}T08:00:00+00:00",
        "sleep_score": score,
    }


@pytest.mark.asyncio
async def test_create_store_returns_sqlite(app_config: AppConfig) -> None:
    """Die Factory liefert immer den lokalen SQLiteStore als Default."""
    store = create_store(app_config)
    try:
        assert isinstance(store, SQLiteStore)
        db_file = app_config.system.data_dir / DB_FILENAME
        assert db_file.is_file(), "Die DB-Datei muss in data_dir angelegt werden."
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_roundtrip_save_latest_list(app_config: AppConfig) -> None:
    """save_report/latest_report/list_reports-Roundtrip in tmp_path."""
    store = create_store(app_config)
    try:
        assert await store.latest_report() is None
        assert await store.list_reports() == []

        r1 = _report_for("2026-07-05", score=70)
        r2 = _report_for("2026-07-06", score=85)
        await store.save_report(r1)
        await store.save_report(r2)

        latest = await store.latest_report()
        assert latest is not None
        assert latest["date"] == "2026-07-06"
        assert latest["sleep_score"] == 85

        history = await store.list_reports(limit=30)
        assert [r["date"] for r in history] == ["2026-07-06", "2026-07-05"]

        # limit wird respektiert; limit<=0 ergibt leere Liste.
        assert len(await store.list_reports(limit=1)) == 1
        assert await store.list_reports(limit=0) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_save_report_is_upsert(app_config: AppConfig) -> None:
    """Zweites Speichern desselben Datums ersetzt statt zu duplizieren."""
    store = create_store(app_config)
    try:
        await store.save_report(_report_for("2026-07-06", score=60))
        await store.save_report(_report_for("2026-07-06", score=92))

        history = await store.list_reports()
        assert len(history) == 1
        assert history[0]["sleep_score"] == 92
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_report_survives_store_restart(app_config: AppConfig) -> None:
    """Ein gespeicherter Report ist nach Store-Neustart noch da."""
    store = create_store(app_config)
    await store.save_report(_report_for("2026-07-06"))
    await store.close()

    reopened = create_store(app_config)
    try:
        latest = await reopened.latest_report()
        assert latest is not None
        assert latest["date"] == "2026-07-06"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_full_sleep_report_roundtrip(
    app_config: AppConfig, sample_report: dict
) -> None:
    """Ein echter build_report-Output uebersteht den Roundtrip verlustfrei."""
    store = create_store(app_config)
    try:
        original = copy.deepcopy(sample_report)
        await store.save_report(sample_report)
        loaded = await store.latest_report()
        assert loaded == original
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_save_without_date_raises(app_config: AppConfig) -> None:
    """Report ohne ``date``-Key wird mit ValueError abgelehnt."""
    store = create_store(app_config)
    try:
        with pytest.raises(ValueError):
            await store.save_report({"sleep_score": 50})
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_close_is_idempotent(app_config: AppConfig) -> None:
    """Mehrfaches close() ist erlaubt; danach wirft der Store RuntimeError."""
    store = create_store(app_config)
    await store.close()
    await store.close()  # zweiter Aufruf darf nicht werfen

    with pytest.raises(RuntimeError):
        await store.latest_report()
