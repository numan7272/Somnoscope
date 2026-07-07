"""
Tests fuer den Daten-Export (:mod:`analytics.export` + WebUI-Endpoints).

Abgedeckt:
    * :func:`analytics.export.reports_to_csv` — Header + exakte
      Spaltenreihenfolge, eine Zeile je Nacht, chronologische Sortierung,
      fehlende Felder -> leere Zellen, korrektes CSV-Quoting bei
      Sonderzeichen, leere Liste -> nur Header.
    * :func:`analytics.export.reports_to_json` — parsebar,
      ``exported_report_count`` stimmt, Reports chronologisch.
    * ``GET /api/export/reports.csv`` / ``.json`` via
      ``fastapi.testclient.TestClient`` mit monkeypatchtem Store — Status
      200, Content-Type, Content-Disposition, Header-Zeile, leerer Store,
      ``days``-Validierung (422).

Graceful: Die Endpoint-Tests werden uebersprungen, wenn ``fastapi`` oder
``httpx`` fehlen (``pytest.importorskip`` in der Client-Factory); die reinen
Export-Funktionstests laufen immer.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import pytest

from analytics.export import CSV_COLUMNS, reports_to_csv, reports_to_json

#: Erwarteter, stabiler Spalten-Contract des CSV-Exports.
EXPECTED_COLUMNS = [
    "date",
    "source",
    "sleep_score",
    "sleep_efficiency_pct",
    "total_sleep_min",
    "sleep_latency_min",
    "waso_min",
    "wake_min",
    "light_min",
    "deep_min",
    "rem_min",
    "avg_hr",
    "min_hr",
    "avg_hrv",
    "avg_spo2",
    "avg_co2",
    "avg_temp",
    "avg_humidity",
]


def _make_report(date: str, **overrides: Any) -> dict[str, Any]:
    """
    Baut einen vollstaendigen Beispiel-SleepReport fuer Export-Tests.

    Args:
        date: ``YYYY-MM-DD``-Datum der Nacht.
        **overrides: Top-Level-Keys, die den Default ueberschreiben.

    Returns:
        SleepReport-Dict mit allen exportrelevanten Bloecken.
    """
    report: dict[str, Any] = {
        "date": date,
        "source": "simulation",
        "sleep_score": 82,
        "sleep_efficiency_pct": 91.5,
        "total_sleep_min": 432,
        "sleep_latency_min": 12,
        "waso_min": 28,
        "stages_min": {"wake": 28, "light": 210, "deep": 120, "rem": 102},
        "stages_pct": {"wake": 6.0, "light": 48.6, "deep": 27.8, "rem": 23.6},
        "vitals": {"avg_hr": 58.2, "min_hr": 49, "avg_hrv": 64.1, "avg_spo2": 96.4},
        "climate": {"avg_co2": 640.0, "avg_temp": 19.2, "avg_humidity": 48.0},
    }
    report.update(overrides)
    return report


def _parse_csv(text: str) -> list[list[str]]:
    """
    Parst CSV-Text zurueck in Zeilen-Listen (fuer robuste Assertions).

    Args:
        text: Der von :func:`reports_to_csv` erzeugte CSV-String.

    Returns:
        Liste der Zeilen, jede Zeile als Liste von Zell-Strings.
    """
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# reports_to_csv
# ---------------------------------------------------------------------------


def test_csv_header_and_column_order_exact() -> None:
    """Header-Zeile entspricht exakt dem Spalten-Contract."""
    rows = _parse_csv(reports_to_csv([]))
    assert rows[0] == EXPECTED_COLUMNS
    # Auch die Modulkonstante haelt den Contract ein.
    assert list(CSV_COLUMNS) == EXPECTED_COLUMNS


def test_csv_empty_list_yields_header_only() -> None:
    """Leere Report-Liste -> genau eine Zeile (nur der Header)."""
    rows = _parse_csv(reports_to_csv([]))
    assert len(rows) == 1


def test_csv_one_row_per_night_with_values() -> None:
    """Jede Nacht ergibt genau eine Datenzeile mit korrekt gemappten Werten."""
    report = _make_report("2026-07-01")
    rows = _parse_csv(reports_to_csv([report]))
    assert len(rows) == 2
    row = dict(zip(rows[0], rows[1]))
    assert row["date"] == "2026-07-01"
    assert row["source"] == "simulation"
    assert row["sleep_score"] == "82"
    assert row["sleep_efficiency_pct"] == "91.5"
    assert row["wake_min"] == "28"
    assert row["light_min"] == "210"
    assert row["deep_min"] == "120"
    assert row["rem_min"] == "102"
    assert row["avg_hr"] == "58.2"
    assert row["min_hr"] == "49"
    assert row["avg_spo2"] == "96.4"
    assert row["avg_co2"] == "640.0"
    assert row["avg_temp"] == "19.2"
    assert row["avg_humidity"] == "48.0"


def test_csv_sorted_chronologically_ascending() -> None:
    """Eingabe neueste-zuerst (Store-Ordnung) -> Ausgabe aelteste-zuerst."""
    reports = [
        _make_report("2026-07-03"),
        _make_report("2026-07-01"),
        _make_report("2026-07-02"),
    ]
    rows = _parse_csv(reports_to_csv(reports))
    dates = [row[0] for row in rows[1:]]
    assert dates == ["2026-07-01", "2026-07-02", "2026-07-03"]


def test_csv_missing_fields_become_empty_cells() -> None:
    """Fehlende Keys, None-Werte und fehlende Bloecke -> leere Zellen."""
    sparse: dict[str, Any] = {
        "date": "2026-07-01",
        "source": None,
        "sleep_score": 70,
        # sleep_efficiency_pct fehlt komplett.
        "stages_min": {"wake": 30},  # light/deep/rem fehlen.
        # vitals fehlt komplett; climate ist kaputt (kein Dict).
        "climate": "defekt",
    }
    rows = _parse_csv(reports_to_csv([sparse]))
    row = dict(zip(rows[0], rows[1]))
    assert row["date"] == "2026-07-01"
    assert row["source"] == ""
    assert row["sleep_score"] == "70"
    assert row["sleep_efficiency_pct"] == ""
    assert row["wake_min"] == "30"
    assert row["light_min"] == ""
    assert row["deep_min"] == ""
    assert row["rem_min"] == ""
    assert row["avg_hr"] == ""
    assert row["avg_co2"] == ""
    # Zeilenlaenge bleibt trotz Luecken stabil.
    assert len(rows[1]) == len(EXPECTED_COLUMNS)


def test_csv_quoting_of_special_characters() -> None:
    """Kommas, Anfuehrungszeichen und Umbrueche ueberleben den Round-Trip."""
    tricky = 'simu,lation "beta"\nzeile2'
    report = _make_report("2026-07-01", source=tricky)
    text = reports_to_csv([report])
    rows = _parse_csv(text)
    assert len(rows) == 2  # Der Umbruch erzeugt KEINE zusaetzliche Datenzeile.
    assert rows[1][1] == tricky
    # Das Roh-CSV quotet das Feld tatsaechlich (RFC-4180-Stil).
    assert '"simu,lation ""beta""' in text


# ---------------------------------------------------------------------------
# reports_to_json
# ---------------------------------------------------------------------------


def test_json_parsable_with_correct_count() -> None:
    """JSON ist parsebar und exported_report_count stimmt."""
    reports = [_make_report("2026-07-02"), _make_report("2026-07-01")]
    payload = json.loads(reports_to_json(reports))
    assert payload["exported_report_count"] == 2
    assert len(payload["reports"]) == 2


def test_json_reports_chronological_and_complete() -> None:
    """Reports erscheinen chronologisch und in voller Tiefe."""
    newer = _make_report("2026-07-02", sleep_score=90)
    older = _make_report("2026-07-01", sleep_score=60)
    payload = json.loads(reports_to_json([newer, older]))
    dates = [r["date"] for r in payload["reports"]]
    assert dates == ["2026-07-01", "2026-07-02"]
    # Volle Reports (inkl. verschachtelter Bloecke), nicht nur CSV-Spalten.
    assert payload["reports"][0]["stages_pct"]["deep"] == 27.8
    assert payload["reports"][1]["sleep_score"] == 90


def test_json_empty_list() -> None:
    """Leere Liste -> count 0 und leeres reports-Array."""
    payload = json.loads(reports_to_json([]))
    assert payload == {"exported_report_count": 0, "reports": []}


# ---------------------------------------------------------------------------
# WebUI-Endpoints (/api/export/*) — uebersprungen ohne fastapi/httpx
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimaler In-Memory-Store fuer die Endpoint-Tests (async-Contract)."""

    def __init__(self, reports: list[dict[str, Any]]) -> None:
        """
        Args:
            reports: Reports, die ``list_reports`` liefern soll
                (neueste zuerst, wie der echte Store).
        """
        self._reports = reports
        self.last_limit: int | None = None

    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """Gibt maximal ``limit`` Reports zurueck und merkt sich ``limit``."""
        self.last_limit = limit
        return self._reports[:limit]

    async def close(self) -> None:
        """No-Op — erfuellt den Lifespan-Contract der App."""


def _client_with_store(monkeypatch: pytest.MonkeyPatch, store: Any) -> Any:
    """
    Baut einen TestClient, dessen Lifespan den uebergebenen Store oeffnet.

    Ueberspringt den Test, wenn ``fastapi``/``httpx`` fehlen. Config-Laden
    wird ebenfalls gepatcht — es wird nie die echte ``config.yaml`` gelesen.

    Args:
        monkeypatch: Pytest-MonkeyPatch fuer ``_open_store``/``_load_cfg``.
        store: Store-Instanz oder ``None`` (App ohne Daten).

    Returns:
        Ein noch nicht betretener ``TestClient`` (als Context-Manager nutzen).
    """
    pytest.importorskip("fastapi", reason="fastapi nicht installiert")
    pytest.importorskip("httpx", reason="httpx (TestClient-Unterbau) fehlt")
    import importlib

    from fastapi.testclient import TestClient

    # webui/__init__.py re-exportiert ``app`` und ueberschattet das Submodul —
    # daher explizit das Modul laden (wie in tests/test_webui.py).
    webui_app = importlib.import_module("webui.app")
    if webui_app.app is None:  # pragma: no cover — defensiv
        pytest.skip("webui.app.app ist None — fastapi fehlt.")
    monkeypatch.setattr(webui_app, "_open_store", lambda: store)
    monkeypatch.setattr(webui_app, "_load_cfg", lambda: None)
    return TestClient(webui_app.app)


def test_export_csv_endpoint_headers_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CSV-Endpoint: 200, Content-Type, Attachment-Header, Header-Zeile."""
    store = _FakeStore([_make_report("2026-07-02"), _make_report("2026-07-01")])
    with _client_with_store(monkeypatch, store) as client:
        resp = client.get("/api/export/reports.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "charset=utf-8" in resp.headers["content-type"]
        assert (
            resp.headers["content-disposition"]
            == 'attachment; filename="somnoscope-reports.csv"'
        )
        rows = _parse_csv(resp.text)
        assert rows[0] == EXPECTED_COLUMNS
        assert [row[0] for row in rows[1:]] == ["2026-07-01", "2026-07-02"]


def test_export_csv_endpoint_days_param_reaches_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``days`` wird als Limit an den Store durchgereicht (Default 365)."""
    store = _FakeStore([_make_report("2026-07-01")])
    with _client_with_store(monkeypatch, store) as client:
        client.get("/api/export/reports.csv?days=7")
        assert store.last_limit == 7
        client.get("/api/export/reports.csv")
        assert store.last_limit == 365


def test_export_json_endpoint_headers_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-Endpoint: 200, Content-Type, Attachment-Header, valider Body."""
    store = _FakeStore([_make_report("2026-07-01")])
    with _client_with_store(monkeypatch, store) as client:
        resp = client.get("/api/export/reports.json")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert (
            resp.headers["content-disposition"]
            == 'attachment; filename="somnoscope-reports.json"'
        )
        payload = resp.json()
        assert payload["exported_report_count"] == 1
        assert payload["reports"][0]["date"] == "2026-07-01"


def test_export_endpoints_empty_store_return_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fehlender Store (None) -> 200 mit leerem Export statt Fehler."""
    with _client_with_store(monkeypatch, None) as client:
        resp = client.get("/api/export/reports.csv")
        assert resp.status_code == 200
        assert _parse_csv(resp.text) == [EXPECTED_COLUMNS]

        resp = client.get("/api/export/reports.json")
        assert resp.status_code == 200
        assert resp.json() == {"exported_report_count": 0, "reports": []}


def test_export_days_out_of_range_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``days`` ausserhalb von 1..365 wird mit 422 abgelehnt."""
    with _client_with_store(monkeypatch, None) as client:
        assert client.get("/api/export/reports.csv?days=0").status_code == 422
        assert client.get("/api/export/reports.csv?days=366").status_code == 422
        assert client.get("/api/export/reports.json?days=0").status_code == 422
        assert client.get("/api/export/reports.json?days=9999").status_code == 422
