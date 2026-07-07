"""
Tests fuer den Status-Endpoint ``GET /api/status`` (:mod:`webui.app`).

Abgedeckt (via ``fastapi.testclient.TestClient``):
    * 200 mit exakten Top-Level-Keys und korrekten Typen (Contract).
    * ``data``-Block: ``night_count``/``date_from``/``date_to``/
      ``latest_score`` aus einem Fake-Store mit mehreren Reports.
    * Leerer Store bzw. gar kein Store -> 200 mit ``night_count = 0`` und
      ``None``-Feldern (Graceful Degradation).
    * Ohne Config -> leere Adapter-Liste, alle Module ``false``, Zeitzone
      ``"UTC"``.
    * ``adapters``/``modules``/``timezone`` spiegeln die (echte, aus dem
      ``app_config``-Fixture geladene) Konfiguration.

Graceful: Fehlt ``fastapi`` (oder der TestClient-Unterbau ``httpx``), wird
die gesamte Datei uebersprungen — die Kern-Suite bleibt lauffaehig.

Config und Store werden ueber ``monkeypatch`` von
:func:`webui.app._load_cfg` bzw. :func:`webui.app._open_store` injiziert;
es wird also NIE die echte ``config.yaml`` des Projekts gelesen.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi", reason="fastapi nicht installiert — WebUI-Tests uebersprungen")
pytest.importorskip("httpx", reason="httpx (TestClient-Unterbau) nicht installiert")

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from core.config_loader import AppConfig  # noqa: E402

# WICHTIG: ``webui/__init__.py`` re-exportiert ``app`` und ueberschattet damit
# das Submodul-Attribut ``webui.app`` — deshalb das Modul explizit laden.
webui_app = importlib.import_module("webui.app")

if webui_app.app is None:  # pragma: no cover — defensiv
    pytest.skip("webui.app.app ist None — fastapi fehlt.", allow_module_level=True)

#: Erwartete Top-Level-Keys des Status-Contracts.
_STATUS_KEYS = {"app", "timezone", "adapters", "modules", "data"}

#: Erwartete Keys des ``data``-Blocks.
_DATA_KEYS = {"night_count", "date_from", "date_to", "latest_score"}

#: Erwartete Keys des ``modules``-Blocks.
_MODULE_KEYS = {"wearable", "climate_sensors", "database", "ml_pipeline", "llm_coach"}

#: Fake-Reports fuer den Status-Test — neueste Nacht zuerst (Store-Contract);
#: das mittlere Datum liegt absichtlich NICHT am Rand, damit die
#: Datums-Spannen-Berechnung (min/max) wirklich geprueft wird.
_FAKE_REPORTS: list[dict[str, Any]] = [
    {"date": "2026-07-06", "sleep_score": 82, "total_sleep_min": 431},
    {"date": "2026-07-05", "sleep_score": 74, "total_sleep_min": 402},
    {"date": "2026-07-03", "sleep_score": 91, "total_sleep_min": 465},
]


class _FakeStore:
    """
    Minimaler In-Memory-Fake des SleepStore-Interfaces fuer die Status-Tests.

    Implementiert nur die vom Lifespan und ``/api/status`` benoetigten
    Methoden (``list_reports``/``close``) — kein SQLite, keine Platte.

    Attributes:
        reports: Die servierten Report-Dicts (neueste zuerst).
    """

    def __init__(self, reports: list[dict[str, Any]]) -> None:
        """
        Args:
            reports: Report-Dicts, die ``list_reports`` liefern soll
                (neueste zuerst, wie der echte Store).
        """
        self.reports = reports

    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """Gibt maximal ``limit`` Reports zurueck (neueste zuerst)."""
        return self.reports[:limit]

    async def close(self) -> None:
        """No-Op — der Lifespan-Handler ruft close() beim Shutdown."""


def _client(
    monkeypatch: pytest.MonkeyPatch, store: Any, cfg: Any
) -> TestClient:
    """
    Baut einen TestClient, dessen Lifespan Store und Config injiziert bekommt.

    Args:
        monkeypatch: Pytest-MonkeyPatch zum Ersetzen von ``_open_store``
            und ``_load_cfg``.
        store: Store-Instanz oder ``None`` (Dashboard ohne Daten).
        cfg: ``AppConfig``-Instanz oder ``None`` (Dashboard ohne Config).

    Returns:
        Ein noch nicht betretener :class:`TestClient` (als Context-Manager
        nutzen, damit der Lifespan-Handler laeuft).
    """
    monkeypatch.setattr(webui_app, "_open_store", lambda: store)
    monkeypatch.setattr(webui_app, "_load_cfg", lambda: cfg)
    return TestClient(webui_app.app)


def test_status_contract_keys_and_types(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """200 + exakt die Contract-Keys mit den erwarteten Typen."""
    with _client(monkeypatch, _FakeStore(_FAKE_REPORTS), app_config) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()

        assert set(body) == _STATUS_KEYS
        assert set(body["app"]) == {"name", "version"}
        assert set(body["data"]) == _DATA_KEYS
        assert set(body["modules"]) == _MODULE_KEYS

        assert body["app"]["name"] == "Somnoscope"
        assert isinstance(body["app"]["version"], str)
        assert body["app"]["version"] == webui_app.app.version
        assert isinstance(body["timezone"], str)
        assert isinstance(body["adapters"], list)
        for entry in body["adapters"]:
            assert set(entry) == {"type", "enabled"}
            assert isinstance(entry["type"], str)
            assert isinstance(entry["enabled"], bool)
        for flag in body["modules"].values():
            assert isinstance(flag, bool)
        assert isinstance(body["data"]["night_count"], int)


def test_status_data_block_from_reports(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """night_count/date_from/date_to/latest_score aus dem Fake-Store."""
    with _client(monkeypatch, _FakeStore(_FAKE_REPORTS), app_config) as client:
        data = client.get("/api/status").json()["data"]
        assert data["night_count"] == 3
        assert data["date_from"] == "2026-07-03"
        assert data["date_to"] == "2026-07-06"
        # latest = neueste Nacht (erster Eintrag), nicht der beste Score.
        assert data["latest_score"] == 82


def test_status_empty_store(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """Leerer Store -> 200 mit night_count 0 und None-Feldern."""
    with _client(monkeypatch, _FakeStore([]), app_config) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "night_count": 0,
            "date_from": None,
            "date_to": None,
            "latest_score": None,
        }


def test_status_without_store_and_cfg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Store UND ohne Config -> 200 mit vollstaendigen Defaults."""
    with _client(monkeypatch, None, None) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == _STATUS_KEYS
        assert body["timezone"] == "UTC"
        assert body["adapters"] == []
        assert body["modules"] == {name: False for name in _MODULE_KEYS}
        assert body["data"] == {
            "night_count": 0,
            "date_from": None,
            "date_to": None,
            "latest_score": None,
        }


def test_status_mirrors_config(
    monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    """adapters/modules/timezone spiegeln exakt die geladene Config."""
    with _client(monkeypatch, _FakeStore([]), app_config) as client:
        body = client.get("/api/status").json()

        assert body["timezone"] == app_config.system.timezone
        assert body["adapters"] == [
            {"type": adapter.type, "enabled": adapter.enabled}
            for adapter in app_config.wearable.adapters
        ]
        # Das Basis-Fixture hat genau einen aktiven Simulation-Adapter.
        assert body["adapters"] == [{"type": "simulation", "enabled": True}]

        enabled = set(app_config.enabled_modules())
        assert body["modules"] == {
            name: name in enabled for name in _MODULE_KEYS
        }
        # Wearable ist im Fixture aktiv — mindestens dieses Flag ist True.
        assert body["modules"]["wearable"] is True
