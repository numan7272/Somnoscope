"""
Tests fuer das FastAPI-Dashboard (:mod:`webui.app`).

Abgedeckt (via ``fastapi.testclient.TestClient``):
    * ``GET /api/report/latest`` -> 404 ``no_data`` bei leerem Store und
      bei fehlendem Store.
    * ``GET /api/report/latest`` -> 200 mit dem gespeicherten Report.
    * ``GET /api/reports`` -> Liste (leer bzw. gefuellt), ``limit``-Validierung.
    * ``GET /`` liefert das statische Dashboard (HTML).

Graceful: Fehlt ``fastapi`` (oder der TestClient-Unterbau ``httpx``), wird
die gesamte Datei uebersprungen — die Kern-Suite bleibt lauffaehig.

Der Store wird ueber ``monkeypatch`` von :func:`webui.app._open_store`
injiziert; es wird also NIE die echte ``config.yaml`` des Projekts gelesen.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="fastapi nicht installiert — WebUI-Tests uebersprungen")
pytest.importorskip("httpx", reason="httpx (TestClient-Unterbau) nicht installiert")

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from core.config_loader import AppConfig  # noqa: E402
from database.sqlite_store import SQLiteStore  # noqa: E402

# WICHTIG: ``webui/__init__.py`` re-exportiert ``app`` und ueberschattet damit
# das Submodul-Attribut ``webui.app`` (Paket-Attribut zeigt auf die
# FastAPI-Instanz statt aufs Modul). Deshalb das Modul explizit laden.
webui_app = importlib.import_module("webui.app")

if webui_app.app is None:  # pragma: no cover — defensiv
    pytest.skip("webui.app.app ist None — fastapi fehlt.", allow_module_level=True)


def _client_with_store(
    monkeypatch: pytest.MonkeyPatch, store: Any
) -> TestClient:
    """
    Baut einen TestClient, dessen Lifespan den uebergebenen Store oeffnet.

    Args:
        monkeypatch: Pytest-MonkeyPatch zum Ersetzen von ``_open_store``.
        store: Store-Instanz oder ``None`` (Dashboard ohne Daten).

    Returns:
        Ein noch nicht betretener :class:`TestClient` (als Context-Manager
        nutzen, damit der Lifespan-Handler laeuft).
    """
    monkeypatch.setattr(webui_app, "_open_store", lambda: store)
    return TestClient(webui_app.app)


def test_latest_report_404_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Store antwortet die API mit 404 no_data statt zu crashen."""
    with _client_with_store(monkeypatch, None) as client:
        resp = client.get("/api/report/latest")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "no_data"

        # Die Reports-Liste degradiert zu [].
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        assert resp.json() == []


def test_latest_report_404_when_db_empty(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """Leere Datenbank -> 404 no_data."""
    store = SQLiteStore(app_config)
    with _client_with_store(monkeypatch, store) as client:
        resp = client.get("/api/report/latest")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "no_data"


def test_latest_report_200_after_save(
    monkeypatch: pytest.MonkeyPatch,
    app_config: AppConfig,
    sample_report: dict,
) -> None:
    """Nach save_report liefert die API den Report mit Status 200."""

    async def _prepare() -> None:
        """Befuellt die DB vorab und schliesst den Schreib-Store wieder."""
        writer = SQLiteStore(app_config)
        await writer.save_report(sample_report)
        await writer.close()

    asyncio.run(_prepare())

    serving_store = SQLiteStore(app_config)
    with _client_with_store(monkeypatch, serving_store) as client:
        resp = client.get("/api/report/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == sample_report["date"]
        assert body["sleep_score"] == sample_report["sleep_score"]
        assert body["hypnogram"] == sample_report["hypnogram"]

        resp = client.get("/api/reports?limit=5")
        assert resp.status_code == 200
        reports = resp.json()
        assert len(reports) == 1
        assert reports[0]["date"] == sample_report["date"]


def test_reports_limit_validation(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """``limit`` ausserhalb von 1..365 wird von FastAPI mit 422 abgelehnt."""
    store = SQLiteStore(app_config)
    with _client_with_store(monkeypatch, store) as client:
        assert client.get("/api/reports?limit=0").status_code == 422
        assert client.get("/api/reports?limit=9999").status_code == 422


def test_index_serves_dashboard_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /`` liefert die statische Dashboard-Seite (HTML)."""
    with _client_with_store(monkeypatch, None) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
