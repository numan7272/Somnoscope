"""
Tests fuer die Trend-Analyse (:func:`analytics.compute_trends`) und den
zugehoerigen WebUI-Endpoint ``GET /api/trends``.

Abgedeckt:
    * Mehrere synthetische SleepReports -> korrekte ``n_nights``,
      chronologisch sortierte ``series`` mit gleich langen Listen,
      plausible ``averages`` und ``stage_distribution_pct`` (0..100),
      korrekte ``best_night``/``worst_night``, ``score_stddev`` sowie
      JSON-Serialisierbarkeit (``json.dumps``).
    * Robustheit (Graceful Degradation): leere Liste -> ``n_nights = 0``,
      genau ein Report, Reports mit fehlenden/ungueltigen Feldern
      (kein Crash, ungueltige Eintraege werden uebersprungen).
    * ``GET /api/trends`` via ``fastapi.testclient.TestClient`` mit
      monkeypatchtem Store: 200 + vollstaendiger Key-Contract bei Daten,
      200 mit ``n_nights = 0`` bei leerem Store.

Die WebUI-Tests ueberspringen sich selbst via ``pytest.importorskip``,
wenn ``fastapi``/``httpx`` fehlen — die reinen ``compute_trends``-Tests
laufen dann trotzdem (Graceful Degradation der Suite).
"""

from __future__ import annotations

import json
import math
import statistics
from typing import Any

import pytest

from analytics import compute_trends
from core.constants import SLEEP_STAGES

# Keys, die der compute_trends-Contract auf oberster Ebene garantiert.
TREND_KEYS: frozenset[str] = frozenset(
    {
        "n_nights",
        "range",
        "averages",
        "stage_distribution_pct",
        "series",
        "best_night",
        "worst_night",
        "consistency",
    }
)

#: Keys der gleich langen Zeitreihen-Listen in ``series``.
SERIES_KEYS: tuple[str, ...] = (
    "date",
    "sleep_score",
    "sleep_efficiency_pct",
    "total_sleep_min",
    "avg_hrv",
    "stages_pct",
)


def make_report(
    date: str,
    sleep_score: int,
    *,
    sleep_efficiency_pct: float = 90.0,
    total_sleep_min: float = 420.0,
    avg_hrv: float | None = 55.0,
    stages_pct: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Baut einen synthetischen SleepReport im Format von ``build_report``.

    Args:
        date: Nacht-Datum als ``YYYY-MM-DD``-String.
        sleep_score: Whitebox-Score 0..100.
        sleep_efficiency_pct: Schlafeffizienz in Prozent.
        total_sleep_min: Gesamtschlafzeit in Minuten.
        avg_hrv: Mittlere HRV fuer den ``vitals``-Block (``None`` moeglich).
        stages_pct: Phasenverteilung; Default ist eine plausible Nacht,
            deren Anteile sich zu 100 % summieren.

    Returns:
        SleepReport-Dict mit den fuer die Trend-Analyse relevanten Keys.
    """
    if stages_pct is None:
        stages_pct = {"wake": 5.0, "light": 50.0, "deep": 25.0, "rem": 20.0}
    return {
        "date": date,
        "sleep_score": sleep_score,
        "sleep_efficiency_pct": sleep_efficiency_pct,
        "total_sleep_min": total_sleep_min,
        "sleep_latency_min": 12.0,
        "waso_min": 20.0,
        "stages_pct": stages_pct,
        "vitals": {
            "avg_hr": 58.0,
            "min_hr": 47.0,
            "avg_hrv": avg_hrv,
            "avg_spo2": 96.5,
        },
        "sleep_onset": f"{date}T23:10:00+00:00",
        "final_wake": f"{date}T07:05:00+00:00",
    }


def _sample_reports() -> list[dict[str, Any]]:
    """
    Liefert drei synthetische Reports in „Store-Reihenfolge" (neueste zuerst).

    Returns:
        Liste von SleepReports mit bekannten Scores fuer deterministische
        Assertions (best/worst/Streuung).
    """
    return [
        make_report("2026-01-03", 91, avg_hrv=60.0),
        make_report("2026-01-02", 55, avg_hrv=40.0),
        make_report("2026-01-01", 76, avg_hrv=50.0),
    ]


# ---------------------------------------------------------------------------
# compute_trends: Happy Path mit mehreren Naechten
# ---------------------------------------------------------------------------


def test_trends_n_nights_and_range() -> None:
    """Drei gueltige Naechte -> n_nights == 3 und korrekter Datumsbereich."""
    trends = compute_trends(_sample_reports())
    assert trends["n_nights"] == 3
    assert trends["range"] == {"from": "2026-01-01", "to": "2026-01-03"}


def test_trends_contract_keys_exact() -> None:
    """Das Ergebnis enthaelt exakt die vertraglich zugesicherten Keys."""
    trends = compute_trends(_sample_reports())
    assert set(trends.keys()) == TREND_KEYS
    assert set(trends["series"].keys()) == set(SERIES_KEYS)
    assert set(trends["averages"].keys()) == {
        "sleep_score",
        "sleep_efficiency_pct",
        "total_sleep_min",
        "avg_hrv",
    }
    assert set(trends["stage_distribution_pct"].keys()) == set(SLEEP_STAGES)
    assert set(trends["consistency"].keys()) == {"score_stddev"}


def test_trends_series_chronological_and_equal_length() -> None:
    """Series ist aufsteigend nach Datum sortiert, alle Listen gleich lang."""
    trends = compute_trends(_sample_reports())
    series = trends["series"]
    assert series["date"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert series["date"] == sorted(series["date"])
    lengths = {key: len(series[key]) for key in SERIES_KEYS}
    assert set(lengths.values()) == {3}, f"Ungleiche Serienlaengen: {lengths}"
    # Werte folgen der chronologischen Sortierung, nicht der Eingabe.
    assert series["sleep_score"] == [76, 55, 91]
    assert series["avg_hrv"] == [50.0, 40.0, 60.0]


def test_trends_averages_plausible() -> None:
    """Averages sind exakte Mittelwerte und liegen im plausiblen Bereich."""
    trends = compute_trends(_sample_reports())
    averages = trends["averages"]
    assert averages["sleep_score"] == pytest.approx((91 + 55 + 76) / 3)
    assert averages["avg_hrv"] == pytest.approx(50.0)
    assert averages["sleep_efficiency_pct"] == pytest.approx(90.0)
    assert averages["total_sleep_min"] == pytest.approx(420.0)
    assert 0.0 <= averages["sleep_score"] <= 100.0
    assert 0.0 <= averages["sleep_efficiency_pct"] <= 100.0


def test_trends_stage_distribution_plausible() -> None:
    """Mittlere Phasenanteile liegen in 0..100 und summieren sich zu ~100 %."""
    trends = compute_trends(_sample_reports())
    distribution = trends["stage_distribution_pct"]
    for stage in SLEEP_STAGES:
        assert 0.0 <= distribution[stage] <= 100.0
    assert sum(distribution.values()) == pytest.approx(100.0)
    # Alle Naechte haben dieselbe Verteilung -> Mittel == Einzelverteilung.
    assert distribution["deep"] == pytest.approx(25.0)


def test_trends_best_and_worst_night() -> None:
    """best_night/worst_night zeigen auf die Naechte mit Max-/Min-Score."""
    trends = compute_trends(_sample_reports())
    assert trends["best_night"] == {"date": "2026-01-03", "sleep_score": 91}
    assert trends["worst_night"] == {"date": "2026-01-02", "sleep_score": 55}


def test_trends_score_stddev() -> None:
    """score_stddev entspricht der Populations-Standardabweichung der Scores."""
    trends = compute_trends(_sample_reports())
    expected = statistics.pstdev([91, 55, 76])
    stddev = trends["consistency"]["score_stddev"]
    assert stddev == pytest.approx(expected)
    assert stddev > 0.0


def test_trends_json_serializable() -> None:
    """Das komplette Trend-Dict laesst sich verlustfrei nach JSON dumpen."""
    trends = compute_trends(_sample_reports())
    payload = json.dumps(trends)
    assert json.loads(payload) == trends


def test_trends_input_order_irrelevant() -> None:
    """Aufsteigend und absteigend uebergebene Reports liefern dasselbe Ergebnis."""
    reports = _sample_reports()
    assert compute_trends(reports) == compute_trends(list(reversed(reports)))


# ---------------------------------------------------------------------------
# compute_trends: Robustheit / Graceful Degradation
# ---------------------------------------------------------------------------


def test_trends_empty_list() -> None:
    """Leere Eingabe -> n_nights 0, leere Serien, null-Averages, keine Crashes."""
    trends = compute_trends([])
    assert trends["n_nights"] == 0
    assert trends["range"] == {"from": None, "to": None}
    assert all(value is None for value in trends["averages"].values())
    assert all(series == [] for series in trends["series"].values())
    assert trends["best_night"] is None
    assert trends["worst_night"] is None
    assert trends["consistency"]["score_stddev"] is None
    assert trends["stage_distribution_pct"] == {
        stage: 0.0 for stage in SLEEP_STAGES
    }
    json.dumps(trends)


def test_trends_single_report() -> None:
    """Genau ein Report -> Nacht ist zugleich best und worst, Streuung 0."""
    trends = compute_trends([make_report("2026-02-01", 82)])
    assert trends["n_nights"] == 1
    assert trends["range"] == {"from": "2026-02-01", "to": "2026-02-01"}
    assert trends["best_night"] == trends["worst_night"]
    assert trends["best_night"] == {"date": "2026-02-01", "sleep_score": 82}
    assert trends["averages"]["sleep_score"] == pytest.approx(82.0)
    assert trends["consistency"]["score_stddev"] == pytest.approx(0.0)


def test_trends_missing_optional_fields_do_not_crash() -> None:
    """Fehlende vitals/stages/Kennzahlen -> None-Serienwerte statt Exception."""
    sparse = {"date": "2026-03-01", "sleep_score": 60}
    full = make_report("2026-03-02", 80)
    trends = compute_trends([sparse, full])
    assert trends["n_nights"] == 2
    series = trends["series"]
    assert series["date"] == ["2026-03-01", "2026-03-02"]
    # Sparse-Nacht: fehlende Werte werden zu None bzw. Null-Phasen-Dict.
    assert series["sleep_efficiency_pct"][0] is None
    assert series["total_sleep_min"][0] is None
    assert series["avg_hrv"][0] is None
    assert series["stages_pct"][0] == {stage: 0.0 for stage in SLEEP_STAGES}
    # Averages beruhen nur auf Naechten mit vorhandenem Wert.
    assert trends["averages"]["avg_hrv"] == pytest.approx(55.0)
    assert trends["averages"]["sleep_score"] == pytest.approx(70.0)
    json.dumps(trends)


def test_trends_invalid_entries_are_skipped() -> None:
    """Nur dicts mit gueltigem date UND sleep_score zaehlen als Nacht."""
    reports: list[Any] = [
        make_report("2026-04-02", 77),
        {"date": "kein-datum", "sleep_score": 50},  # ungueltiges Datum
        {"date": "2026-04-03"},  # sleep_score fehlt
        {"date": "2026-04-04", "sleep_score": None},  # score ungueltig
        {"sleep_score": 88},  # date fehlt
        "kein-dict",  # falscher Typ
        None,
    ]
    trends = compute_trends(reports)
    assert trends["n_nights"] == 1
    assert trends["series"]["date"] == ["2026-04-02"]
    assert trends["best_night"] == {"date": "2026-04-02", "sleep_score": 77}
    json.dumps(trends)


def test_trends_null_vitals_hrv() -> None:
    """avg_hrv == null im vitals-Block -> None in der Serie, kein Crash."""
    trends = compute_trends([make_report("2026-05-01", 65, avg_hrv=None)])
    assert trends["series"]["avg_hrv"] == [None]
    assert trends["averages"]["avg_hrv"] is None


def test_trends_non_finite_values_ignored() -> None:
    """NaN/inf-Kennzahlen werden verworfen statt die Averages zu vergiften."""
    report = make_report("2026-06-01", 70)
    report["sleep_efficiency_pct"] = math.nan
    report["total_sleep_min"] = math.inf
    trends = compute_trends([report])
    assert trends["series"]["sleep_efficiency_pct"] == [None]
    assert trends["series"]["total_sleep_min"] == [None]
    assert trends["averages"]["sleep_efficiency_pct"] is None
    assert trends["averages"]["total_sleep_min"] is None
    json.dumps(trends)


# ---------------------------------------------------------------------------
# WebUI: GET /api/trends (fastapi/httpx optional — importorskip)
# ---------------------------------------------------------------------------


class _FakeStore:
    """
    Minimaler In-Memory-Store fuer die WebUI-Tests (kein SQLite noetig).

    Implementiert genau die vom ``/api/trends``-Endpoint und vom
    Lifespan-Handler benutzte async-Oberflaeche (``list_reports``/``close``).
    """

    def __init__(self, reports: list[dict[str, Any]]) -> None:
        """
        Args:
            reports: Vorbefuellte Reports, neueste zuerst (Store-Konvention).
        """
        self._reports = reports
        self.closed = False

    async def list_reports(self, limit: int) -> list[dict[str, Any]]:
        """Gibt hoechstens ``limit`` Reports zurueck (neueste zuerst)."""
        return list(self._reports[: max(limit, 0)])

    async def latest_report(self) -> dict[str, Any] | None:
        """Gibt den neuesten Report oder ``None`` zurueck."""
        return self._reports[0] if self._reports else None

    async def close(self) -> None:
        """Markiert den Store als geschlossen (Lifespan-Vertrag)."""
        self.closed = True


def _trends_client(
    monkeypatch: pytest.MonkeyPatch, reports: list[dict[str, Any]]
) -> Any:
    """
    Baut einen TestClient mit monkeypatchtem In-Memory-Store.

    Ueberspringt den aufrufenden Test via ``pytest.importorskip``, wenn
    ``fastapi`` oder ``httpx`` fehlen — die reinen Analytics-Tests dieser
    Datei bleiben davon unberuehrt.

    Args:
        monkeypatch: Pytest-MonkeyPatch zum Ersetzen von ``_open_store``.
        reports: Reports, die der Fake-Store liefern soll (neueste zuerst).

    Returns:
        Ein noch nicht betretener ``TestClient`` (als Context-Manager
        nutzen, damit der Lifespan-Handler laeuft).
    """
    pytest.importorskip(
        "fastapi", reason="fastapi nicht installiert — /api/trends-Test uebersprungen"
    )
    pytest.importorskip(
        "httpx", reason="httpx (TestClient-Unterbau) nicht installiert"
    )
    import importlib

    from fastapi.testclient import TestClient

    # WICHTIG: ``webui/__init__.py`` re-exportiert ``app`` und ueberschattet
    # damit das Submodul-Attribut ``webui.app`` — Modul explizit laden.
    webui_app = importlib.import_module("webui.app")
    if webui_app.app is None:  # pragma: no cover — defensiv
        pytest.skip("webui.app.app ist None — fastapi fehlt.")

    monkeypatch.setattr(webui_app, "_open_store", lambda: _FakeStore(reports))
    return TestClient(webui_app.app)


def test_api_trends_with_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gefuellter Store -> 200, Contract-Keys und korrekte Aggregation."""
    with _trends_client(monkeypatch, _sample_reports()) as client:
        resp = client.get("/api/trends")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == TREND_KEYS
        assert body["n_nights"] == 3
        assert body["series"]["date"] == [
            "2026-01-01",
            "2026-01-02",
            "2026-01-03",
        ]
        assert body["best_night"] == {"date": "2026-01-03", "sleep_score": 91}
        assert body["range"] == {"from": "2026-01-01", "to": "2026-01-03"}


def test_api_trends_empty_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leerer Store -> trotzdem 200 mit n_nights == 0 (Empty-State)."""
    with _trends_client(monkeypatch, []) as client:
        resp = client.get("/api/trends")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == TREND_KEYS
        assert body["n_nights"] == 0
        assert body["best_night"] is None
        assert all(value is None for value in body["averages"].values())


def test_api_trends_days_param_limits_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``days`` begrenzt die Historie; ungueltige Werte -> 422 (Validation)."""
    with _trends_client(monkeypatch, _sample_reports()) as client:
        resp = client.get("/api/trends?days=2")
        assert resp.status_code == 200
        body = resp.json()
        # Store liefert neueste zuerst -> die 2 juengsten Naechte.
        assert body["n_nights"] == 2
        assert body["series"]["date"] == ["2026-01-02", "2026-01-03"]

        assert client.get("/api/trends?days=0").status_code == 422
        assert client.get("/api/trends?days=9999").status_code == 422
