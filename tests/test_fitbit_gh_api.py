"""
Tests fuer :mod:`adapters.fitbit_gh_api` — OHNE echtes CLI oder Netzwerk.

Die einzige Aussenweltschnittstelle des Adapters ist die Methode
``_run_ghealth`` (Subprozess-Aufruf). Sie wird hier per ``monkeypatch``
durch gecannte JSON-Strings ersetzt; getestet werden die reinen
Uebersetzungsschichten:

    * ``_parse_json``: dict-Wrapper (``dataPoints``), nackte Liste, JSON-Lines,
      Leerstring und Muell.
    * ``_translate``: Zeit-Extraktion (ISO/Unix-ms), Wert-Extraktion
      (flach und verschachtelt), ``raw`` bleibt vollstaendig erhalten.
    * ``poll()``-Integration ueber die gemockte ``_run_ghealth``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from adapters.fitbit_gh_api import FitbitGoogleHealthAdapter, _parse_dt
from core.constants import (
    ADAPTER_FITBIT_GH_API,
    METRIC_HEART_RATE,
    METRIC_SLEEP_STAGE,
)


def _adapter(**options) -> FitbitGoogleHealthAdapter:
    """Baut einen Adapter mit Test-Optionen (kein open(), kein CLI-Check)."""
    return FitbitGoogleHealthAdapter(options, timezone="UTC")


# ----------------------------------------------------------------------------
# _parse_json
# ----------------------------------------------------------------------------

def test_parse_json_datapoints_wrapper() -> None:
    """``{"dataPoints": [...]}``-Objekt wird zur DataPoint-Liste."""
    stdout = json.dumps(
        {"dataPoints": [{"startTime": "2026-07-06T23:00:00Z", "bpm": 55}]}
    )
    points = FitbitGoogleHealthAdapter._parse_json(stdout)
    assert points == [{"startTime": "2026-07-06T23:00:00Z", "bpm": 55}]


def test_parse_json_bare_list() -> None:
    """Nackte JSON-Liste wird durchgereicht; Nicht-Dicts fliegen raus."""
    stdout = json.dumps([{"a": 1}, "kein_dict", {"b": 2}, 3])
    points = FitbitGoogleHealthAdapter._parse_json(stdout)
    assert points == [{"a": 1}, {"b": 2}]


def test_parse_json_jsonl() -> None:
    """JSON-Lines (ein Objekt pro Zeile) inkl. Leer- und Muellzeilen."""
    stdout = '{"a": 1}\n\nnicht-json\n{"b": 2}\n'
    points = FitbitGoogleHealthAdapter._parse_json(stdout)
    assert points == [{"a": 1}, {"b": 2}]


def test_parse_json_empty_and_garbage() -> None:
    """Leerstring und reiner Muell ergeben eine leere Liste (kein Crash)."""
    assert FitbitGoogleHealthAdapter._parse_json("") == []
    assert FitbitGoogleHealthAdapter._parse_json("   \n  ") == []
    assert FitbitGoogleHealthAdapter._parse_json("voelliger quatsch") == []


def test_parse_json_data_key_fallback() -> None:
    """Alternativ-Wrapper ``{"data": [...]}`` wird ebenfalls akzeptiert."""
    stdout = json.dumps({"data": [{"x": 1}]})
    assert FitbitGoogleHealthAdapter._parse_json(stdout) == [{"x": 1}]


# ----------------------------------------------------------------------------
# _translate
# ----------------------------------------------------------------------------

def test_translate_sleep_segment() -> None:
    """Schlaf-DataPoint: Start/Ende ISO, Phase lowercased, raw erhalten."""
    dp = {
        "startTime": "2026-07-06T23:15:00Z",
        "endTime": "2026-07-06T23:45:00Z",
        "stage": "DEEP",
        "propietaeres_feld": {"quelle": "google"},
    }
    adapter = _adapter()
    readings = adapter._translate("sleep", METRIC_SLEEP_STAGE, [dp])

    assert len(readings) == 1
    r = readings[0]
    assert r.source == ADAPTER_FITBIT_GH_API
    assert r.metric == METRIC_SLEEP_STAGE
    assert r.value == "deep"
    assert r.start == datetime(2026, 7, 6, 23, 15, tzinfo=timezone.utc)
    assert r.end == datetime(2026, 7, 6, 23, 45, tzinfo=timezone.utc)
    assert r.unit is None
    # Whitebox-Prinzip: das Original-Payload bleibt vollstaendig in raw.
    assert r.raw is dp
    assert r.raw["propietaeres_feld"] == {"quelle": "google"}


def test_translate_heart_rate_nested_value() -> None:
    """Verschachtelter Wert (``bpm.value``) wird als float extrahiert."""
    dp = {"time": "2026-07-07T02:00:00+00:00", "bpm": {"value": 61}}
    readings = _adapter()._translate("heart-rate", METRIC_HEART_RATE, [dp])

    assert len(readings) == 1
    r = readings[0]
    assert r.value == 61.0
    assert isinstance(r.value, float)
    assert r.end is None
    assert r.unit == "bpm"
    assert r.raw is dp


def test_translate_unix_millis_timestamp() -> None:
    """Unix-Millisekunden-Timestamps werden tz-aware (UTC) geparst."""
    ts_ms = 1751842800000  # 2025-07-06T23:00:00Z
    dp = {"timestamp": ts_ms, "value": 55.5}
    readings = _adapter()._translate("heart-rate", METRIC_HEART_RATE, [dp])

    assert len(readings) == 1
    r = readings[0]
    assert r.start == datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    assert r.start.tzinfo is not None
    assert r.value == 55.5


def test_translate_skips_points_without_start() -> None:
    """DataPoints ohne erkennbaren Startzeitpunkt werden uebersprungen."""
    dps = [
        {"bpm": 60},  # kein Zeitfeld
        {"startTime": "kein-datum", "bpm": 61},  # unparsebar
        {"startTime": "2026-07-07T01:00:00Z", "bpm": 62},  # gueltig
    ]
    readings = _adapter()._translate("heart-rate", METRIC_HEART_RATE, dps)
    assert len(readings) == 1
    assert readings[0].value == 62.0


def test_translate_value_none_when_unknown_shape() -> None:
    """Unbekannte Wert-Struktur -> value=None, aber Reading bleibt (raw!)."""
    dp = {"startTime": "2026-07-07T01:00:00Z", "exotic": "shape"}
    readings = _adapter()._translate("heart-rate", METRIC_HEART_RATE, [dp])
    assert len(readings) == 1
    assert readings[0].value is None
    assert readings[0].raw is dp


# ----------------------------------------------------------------------------
# _parse_dt (Zeit-Helfer)
# ----------------------------------------------------------------------------

def test_parse_dt_variants() -> None:
    """ISO mit Z, ISO naive (-> UTC), Unix-Sekunden, None und Muell."""
    assert _parse_dt("2026-07-06T23:00:00Z") == datetime(
        2026, 7, 6, 23, 0, tzinfo=timezone.utc
    )
    naive = _parse_dt("2026-07-06T23:00:00")
    assert naive is not None and naive.tzinfo == timezone.utc

    secs = _parse_dt(1751842800)
    assert secs == datetime.fromtimestamp(1751842800, tz=timezone.utc)

    assert _parse_dt(None) is None
    assert _parse_dt("morgen frueh") is None


# ----------------------------------------------------------------------------
# poll() mit gemockter _run_ghealth (kein CLI, kein Netz)
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_with_canned_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """poll() uebersetzt gecannte CLI-Ausgaben in vereinheitlichte Readings."""
    adapter = _adapter(metrics=["sleep", "heart-rate"])

    canned: dict[str, str] = {
        "sleep": json.dumps(
            {
                "dataPoints": [
                    {
                        "startTime": "2026-07-06T23:00:00Z",
                        "endTime": "2026-07-06T23:30:00Z",
                        "stage": "LIGHT",
                    },
                    {
                        "startTime": "2026-07-06T23:30:00Z",
                        "endTime": "2026-07-07T00:10:00Z",
                        "stage": "DEEP",
                    },
                ]
            }
        ),
        # heart-rate kommt als JSON-Lines mit verschachteltem Wert.
        "heart-rate": (
            '{"time": "2026-07-06T23:05:00Z", "bpm": {"value": 58}}\n'
            '{"time": "2026-07-06T23:10:00Z", "bpm": {"value": 56}}\n'
        ),
    }
    calls: list[list[str]] = []

    async def fake_run_ghealth(args: list[str]) -> str:
        """Ersetzt den Subprozess-Aufruf durch gecannte Ausgaben."""
        calls.append(args)
        return canned[args[1]]  # args = ["data", <datatype>, "list", ...]

    monkeypatch.setattr(adapter, "_run_ghealth", fake_run_ghealth)

    readings = await adapter.poll()

    # Es wurde je Datentyp genau ein CLI-Aufruf abgesetzt; sleep mit --detail.
    assert len(calls) == 2
    sleep_call = next(c for c in calls if c[1] == "sleep")
    assert "--detail" in sleep_call

    stages = [r for r in readings if r.metric == METRIC_SLEEP_STAGE]
    hr = [r for r in readings if r.metric == METRIC_HEART_RATE]
    assert [s.value for s in stages] == ["light", "deep"]
    assert all(s.end is not None for s in stages)
    assert [h.value for h in hr] == [58.0, 56.0]
    assert all(h.end is None for h in hr)


@pytest.mark.asyncio
async def test_poll_survives_failing_datatype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein fehlschlagender Datentyp killt die anderen nicht (Graceful)."""
    adapter = _adapter(metrics=["sleep", "heart-rate"])

    async def fake_run_ghealth(args: list[str]) -> str:
        if args[1] == "sleep":
            raise RuntimeError("ghealth Exit 1: token expired")
        return '[{"time": "2026-07-06T23:05:00Z", "value": 60}]'

    monkeypatch.setattr(adapter, "_run_ghealth", fake_run_ghealth)

    readings = await adapter.poll()
    assert [r.metric for r in readings] == [METRIC_HEART_RATE]
    assert readings[0].value == 60.0


def test_unknown_metrics_are_filtered() -> None:
    """Tippfehler in ``metrics`` werden beim Konstruieren aussortiert."""
    adapter = _adapter(metrics=["sleep", "blutzucker", "heart-rate"])
    assert adapter._metrics == ("sleep", "heart-rate")
