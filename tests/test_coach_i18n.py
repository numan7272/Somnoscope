"""
Tests fuer die Zweisprachigkeit (DE/EN) des LLM-Schlafcoaches.

Abgedeckt:
    * :func:`llm_coach.coach._fallback_summary` liefert auf Englisch einen
      vollstaendigen, sinnvollen Text (Score-Einordnung, Tipps, Disclaimer)
      ohne deutsche Woerter — und auf Deutsch weiterhin den deutschen Text.
    * Ungueltige Sprach-Codes fallen still auf Deutsch zurueck
      (Graceful Degradation, keine Exception).
    * :func:`llm_coach.prompt_builder.build_coach_prompt` baut den Prompt
      inkl. englischer System-Anweisung und Kennzahlen-Labels.
    * ``GET /api/coaching?lang=en`` (TestClient, Ollama nicht erreichbar ->
      regelbasierter Fallback) liefert englischen Text und cached getrennt
      von ``?lang=de``; ungueltige ``lang``-Werte antworten deutsch.

Graceful: Die API-Tests werden uebersprungen, wenn ``fastapi``/``httpx``
fehlen — die reinen Coach-Tests laufen unabhaengig davon.

Es wird NIE ein echter Netzwerk-Call gemacht: ``_call_ollama_blocking``
wird per ``monkeypatch`` durch einen URLError-Werfer ersetzt (lokales
Ollama "nicht erreichbar").
"""

from __future__ import annotations

import asyncio
import types
import urllib.error
from typing import Any

import pytest

import llm_coach.coach as coach_mod
from llm_coach.prompt_builder import build_coach_prompt

# ---------------------------------------------------------------------------
# Marker-Woerter: eindeutig deutsch bzw. eindeutig englisch (case-sensitiv,
# bewusst so gewaehlt, dass keines Substring eines Worts der anderen Sprache
# ist — z.B. ist "Tips" KEIN Substring von "Tipps").
# ---------------------------------------------------------------------------
_GERMAN_MARKERS = (
    "Zusammenfassung",
    "geschlafen",
    "Tipps:",
    "Hinweis:",
    "Diagnose",
    "ärztlichen",
    "Schlafeffizienz",
)
_ENGLISH_MARKERS = (
    "Sleep summary",
    "Tips:",
    "medical diagnosis",
    "medical advice",
)


def _make_report() -> dict[str, Any]:
    """
    Baut einen minimalen SleepReport, der alle Fallback-Tipps triggert.

    Returns:
        Report-Dict mit mittlerem Score, niedrigem Tiefschlaf-/REM-Anteil,
        hoher Einschlaflatenz und hohem WASO (=> Tipps + Einordnung).
    """
    return {
        "date": "2026-07-06",
        "source": "simulation",
        "sleep_score": 65,
        "total_sleep_min": 372,
        "time_in_bed_min": 452,
        "sleep_efficiency_pct": 74.0,
        "sleep_latency_min": 42.0,
        "waso_min": 58.0,
        "stages_min": {"wake": 60, "light": 200, "deep": 40, "rem": 72},
        "stages_pct": {"wake": 13.0, "light": 54.0, "deep": 11.0, "rem": 16.0},
        "vitals": {"avg_hr": 58.0, "min_hr": 47.0, "avg_hrv": 52.0, "avg_spo2": 96.4},
    }


def _coach_cfg() -> types.SimpleNamespace:
    """
    Minimale Config-Attrappe fuer den Coach (nur ``llm_coach``-Block).

    Returns:
        ``SimpleNamespace`` mit ``llm_coach.enabled/ollama_url/model`` —
        genau die Felder, die ``generate_coaching`` und der Endpoint lesen.
    """
    return types.SimpleNamespace(
        llm_coach=types.SimpleNamespace(
            enabled=True,
            backend="ollama",
            ollama_url="http://127.0.0.1:1",
            model="test-model",
            context_window_days=7,
        )
    )


def _raise_unreachable(*_args: Any, **_kwargs: Any) -> str:
    """Ersatz fuer ``_call_ollama_blocking``: Ollama 'nicht erreichbar'."""
    raise urllib.error.URLError("Test: lokales Ollama nicht erreichbar")


# ---------------------------------------------------------------------------
# _fallback_summary: EN / DE / ungueltige Sprache
# ---------------------------------------------------------------------------


def test_fallback_summary_en_is_english_and_complete() -> None:
    """EN-Fallback: englischer Text mit Score, Tipps und Disclaimer."""
    text = coach_mod._fallback_summary(_make_report(), "en")

    for marker in _ENGLISH_MARKERS:
        assert marker in text, f"Englischer Marker fehlt: {marker!r}"
    # Score-Einordnung + Wert muessen enthalten sein.
    assert "score 65/100" in text
    assert "a solid night with room for improvement" in text
    # Kennzahlen: Schlafdauer + Effizienz.
    assert "6.2 hours" in text
    assert "74 %" in text
    # Mindestens ein konkreter Tipp als Bullet.
    assert "\n- " in text

    for marker in _GERMAN_MARKERS:
        assert marker not in text, f"Deutsches Wort im EN-Text: {marker!r}"


def test_fallback_summary_en_good_night_without_tips_data() -> None:
    """EN-Fallback ohne Auffaelligkeiten: Routine-Tipp + Disclaimer."""
    report = {
        "date": "2026-07-05",
        "sleep_score": 91,
        "total_sleep_min": 468,
        "sleep_efficiency_pct": 94.0,
        "stages_pct": {"deep": 20.0, "rem": 22.0},
    }
    text = coach_mod._fallback_summary(report, "en")
    assert "a really good night" in text
    assert "score 91/100" in text
    assert "Stick with your current routine" in text
    assert "medical diagnosis" in text
    for marker in _GERMAN_MARKERS:
        assert marker not in text


def test_fallback_summary_de_stays_german() -> None:
    """DE-Fallback (explizit und als Default) bleibt deutsch."""
    explicit = coach_mod._fallback_summary(_make_report(), "de")
    default = coach_mod._fallback_summary(_make_report())
    assert explicit == default, "Default-Sprache muss 'de' bleiben (rueckwaerts-kompatibel)."

    assert "Schlaf-Zusammenfassung für 2026-07-06" in explicit
    assert "Tipps:" in explicit
    assert "keine medizinische" in explicit
    assert "ärztlichen Rat" in explicit
    for marker in ("Sleep summary", "medical advice", "You slept"):
        assert marker not in explicit


@pytest.mark.parametrize("bad_lang", ["fr", "EN-US", "", "xx", "1", "deutsch"])
def test_fallback_summary_invalid_lang_falls_back_to_german(bad_lang: str) -> None:
    """Unbekannte Sprach-Codes -> deutscher Text, keine Exception."""
    report = _make_report()
    assert coach_mod._fallback_summary(report, bad_lang) == coach_mod._fallback_summary(
        report, "de"
    )


# ---------------------------------------------------------------------------
# build_coach_prompt: Sprache der System-Anweisung + Labels
# ---------------------------------------------------------------------------


def test_build_coach_prompt_en_has_english_instruction_and_labels() -> None:
    """EN-Prompt: englische System-Anweisung + englische Kennzahlen-Labels."""
    history = [
        {
            "date": "2026-07-05",
            "sleep_score": 78,
            "total_sleep_min": 421,
            "sleep_efficiency_pct": 91.0,
            "vitals": {"avg_hrv": 48.0},
        }
    ]
    prompt = build_coach_prompt(_make_report(), history, "en")

    assert "You are an experienced, empathetic sleep coach" in prompt
    assert "medical diagnoses" in prompt
    assert "Sleep data for the night of 2026-07-06" in prompt
    assert "- Sleep efficiency:" in prompt
    assert "- Wake after sleep onset (WASO):" in prompt
    assert "Deep sleep" in prompt
    assert "Previous nights (for comparison):" in prompt
    assert "Now give your coaching feedback:" in prompt
    # Keine deutschen Bausteine im englischen Prompt.
    for marker in ("Du bist", "Schlafdaten", "Tiefschlaf", "Effizienz", "k. A."):
        assert marker not in prompt


def test_build_coach_prompt_default_and_invalid_lang_stay_german() -> None:
    """Default-Aufruf (alt) und ungueltige Sprache liefern deutschen Prompt."""
    report = _make_report()
    legacy = build_coach_prompt(report, None)
    invalid = build_coach_prompt(report, None, "es")
    explicit_de = build_coach_prompt(report, None, "de")

    assert legacy == explicit_de, "Alte Signatur muss unveraendert deutsch bleiben."
    assert invalid == explicit_de, "Ungueltige Sprache muss auf Deutsch zurueckfallen."
    assert "Du bist ein erfahrener, empathischer Schlafcoach" in legacy
    assert "Schlafdaten der Nacht vom 2026-07-06" in legacy
    assert "Gib jetzt dein Coaching-Feedback:" in legacy


# ---------------------------------------------------------------------------
# generate_coaching: lang wird bis in den Fallback durchgereicht
# ---------------------------------------------------------------------------


def test_generate_coaching_en_fallback_when_ollama_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama nicht erreichbar -> englischer regelbasierter Fallback."""
    monkeypatch.setattr(coach_mod, "_call_ollama_blocking", _raise_unreachable)
    text = asyncio.run(
        coach_mod.generate_coaching(_make_report(), None, _coach_cfg(), "en")
    )
    assert text == coach_mod._fallback_summary(_make_report(), "en")
    assert "Sleep summary" in text


def test_generate_coaching_default_stays_german(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alte Signatur (ohne lang) liefert weiter den deutschen Fallback."""
    monkeypatch.setattr(coach_mod, "_call_ollama_blocking", _raise_unreachable)
    text = asyncio.run(coach_mod.generate_coaching(_make_report(), None, _coach_cfg()))
    assert text == coach_mod._fallback_summary(_make_report(), "de")
    assert "Schlaf-Zusammenfassung" in text


# ---------------------------------------------------------------------------
# GET /api/coaching?lang=... (TestClient; Store + Config gemockt)
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimaler async Store: genau ein Report, keine echte DB."""

    def __init__(self, report: dict[str, Any]) -> None:
        self._report = report

    async def latest_report(self) -> dict[str, Any]:
        return self._report

    async def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        return [self._report]

    async def close(self) -> None:
        return None


def _webui_client(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]):
    """
    Baut einen TestClient mit gemocktem Store + Coach-Config (lazy Import).

    Ueberspringt den aufrufenden Test, wenn ``fastapi``/``httpx`` fehlen.

    Args:
        monkeypatch: Pytest-MonkeyPatch fuer ``_open_store``/``_load_cfg``.
        report: Der Report, den der Fake-Store liefern soll.

    Returns:
        Tupel ``(TestClient, webui_app_modul)`` — Client noch nicht betreten.
    """
    pytest.importorskip("fastapi", reason="fastapi nicht installiert")
    pytest.importorskip("httpx", reason="httpx (TestClient-Unterbau) nicht installiert")
    import importlib

    from fastapi.testclient import TestClient

    webui_app = importlib.import_module("webui.app")
    if webui_app.app is None:  # pragma: no cover — defensiv
        pytest.skip("webui.app.app ist None — fastapi fehlt.")

    monkeypatch.setattr(webui_app, "_open_store", lambda: _FakeStore(report))
    monkeypatch.setattr(webui_app, "_load_cfg", _coach_cfg)
    return TestClient(webui_app.app), webui_app


def test_api_coaching_lang_en_uses_english_fallback_and_separate_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """?lang=en -> englischer Text; DE/EN werden getrennt gecacht."""
    calls: list[str] = []

    def _counting_unreachable(url: str, payload: dict, timeout_s: float) -> str:
        calls.append(str(payload.get("prompt", "")))
        raise urllib.error.URLError("Test: lokales Ollama nicht erreichbar")

    monkeypatch.setattr(coach_mod, "_call_ollama_blocking", _counting_unreachable)
    report = _make_report()
    client, webui_app = _webui_client(monkeypatch, report)

    with client:
        resp_en = client.get("/api/coaching?lang=en")
        assert resp_en.status_code == 200
        body_en = resp_en.json()
        assert body_en["enabled"] is True
        assert body_en["date"] == report["date"]
        assert "Sleep summary" in body_en["text"]
        assert "medical advice" in body_en["text"]
        for marker in _GERMAN_MARKERS:
            assert marker not in body_en["text"]

        resp_de = client.get("/api/coaching?lang=de")
        assert resp_de.status_code == 200
        body_de = resp_de.json()
        assert "Schlaf-Zusammenfassung" in body_de["text"]
        assert body_de["text"] != body_en["text"], "DE/EN duerfen sich nicht mischen."

        # Zweiter EN-Aufruf: identischer Text aus dem Cache, KEIN neuer
        # Generierungslauf (Ollama-Call-Zaehler bleibt bei 2: 1x en, 1x de).
        resp_en2 = client.get("/api/coaching?lang=en")
        assert resp_en2.json()["text"] == body_en["text"]
        assert len(calls) == 2

        # Cache-Keys enthalten die Sprache (date|generated_at|lang).
        cache = webui_app.app.state.coaching_cache
        suffixes = {key.rsplit("|", 1)[-1] for key in cache}
        assert suffixes == {"de", "en"}


def test_api_coaching_invalid_lang_falls_back_to_german(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """?lang=xx -> deutscher Text, gecacht unter dem de-Key (kein 422)."""
    monkeypatch.setattr(coach_mod, "_call_ollama_blocking", _raise_unreachable)
    report = _make_report()
    client, webui_app = _webui_client(monkeypatch, report)

    with client:
        resp = client.get("/api/coaching?lang=xx")
        assert resp.status_code == 200
        assert "Schlaf-Zusammenfassung" in resp.json()["text"]

        # Identisch zum expliziten de-Aufruf und unter demselben Key gecacht.
        resp_de = client.get("/api/coaching?lang=de")
        assert resp_de.json()["text"] == resp.json()["text"]
        assert all(key.endswith("|de") for key in webui_app.app.state.coaching_cache)
