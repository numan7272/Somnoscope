"""
LLM-Coach: Coaching-Text aus einem SleepReport über ein LOKALES Ollama.

Ruft ``POST {ollama_url}/api/generate`` mit ``stream=false`` auf — bewusst nur
mit :mod:`urllib.request` aus der Standardbibliothek, damit keine neue
Abhängigkeit (httpx/requests) nötig ist. Der blockierende HTTP-Call läuft via
``asyncio.to_thread`` (Kernprinzip: Asyncio-First).

Graceful Degradation (Kernprinzip 2): Ist Ollama nicht erreichbar, das Modell
nicht geladen oder läuft ein Timeout auf, crasht NICHTS — stattdessen liefert
:func:`_fallback_summary` eine regelbasierte deutsche Zusammenfassung aus den
Report-Kennzahlen, damit der Nutzer immer nützlichen Text bekommt.

Privacy (Kernprinzip 1): Es wird ausschliesslich der lokale Ollama-Endpunkt
angesprochen — niemals eine Cloud.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from core.constants import STAGE_DEEP, STAGE_REM

from .prompt_builder import build_coach_prompt

if TYPE_CHECKING:
    from core.config_loader import AppConfig

logger = logging.getLogger(__name__)

#: Timeout für den lokalen Ollama-Call in Sekunden. Grosszügig, weil kleine
#: Modelle auf schwacher Hardware (Raspberry Pi) durchaus lange brauchen.
_OLLAMA_TIMEOUT_S = 120.0

#: Grenzwerte für die regelbasierte Bewertung im Fallback.
_SCORE_GOOD = 80
_SCORE_OK = 60
_EFFICIENCY_GOOD_PCT = 85.0
_DEEP_LOW_PCT = 13.0
_REM_LOW_PCT = 18.0
_LATENCY_HIGH_MIN = 30.0
_WASO_HIGH_MIN = 45.0


def _call_ollama_blocking(url: str, payload: dict, timeout_s: float) -> str:
    """
    Führt den blockierenden HTTP-POST gegen den lokalen Ollama-Server aus.

    Läuft ausschliesslich in einem Worker-Thread (via ``asyncio.to_thread``),
    damit der Event-Loop frei bleibt.

    Args:
        url: Vollständige Endpunkt-URL, z.B.
            ``http://localhost:11434/api/generate``.
        payload: Request-Body für Ollama (``model``, ``prompt``, ``stream``).
        timeout_s: Socket-Timeout in Sekunden.

    Returns:
        Der generierte Text aus dem ``response``-Feld der Ollama-Antwort.

    Raises:
        urllib.error.URLError: Ollama nicht erreichbar oder Timeout.
        ValueError: Antwort ist kein gültiges JSON oder ohne ``response``-Feld.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    text = data.get("response")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Ollama-Antwort enthält kein 'response'-Textfeld.")
    return text.strip()


def _fallback_summary(report: dict) -> str:
    """
    Regelbasierte deutsche Zusammenfassung als Fallback ohne LLM.

    Bewertet die wichtigsten Kennzahlen (Score, Effizienz, Tiefschlaf-/REM-
    Anteil, Einschlaflatenz, WASO) mit einfachen Schwellwerten und formuliert
    daraus eine kurze, freundliche Rückmeldung samt 1-3 konkreter Tipps —
    komplett ohne Modell, damit der Coach auch ohne laufendes Ollama immer
    nützlichen Text liefert.

    Args:
        report: SleepReport-Dict der aktuellen Nacht. Fehlende Felder werden
            toleriert und einfach ausgelassen.

    Returns:
        Mehrzeiliger deutscher Zusammenfassungstext (keine medizinische
        Diagnose, nur allgemeine Schlafhygiene-Hinweise).
    """
    lines: list[str] = []
    date = report.get("date", "letzte Nacht")
    score = report.get("sleep_score")
    total = report.get("total_sleep_min")
    efficiency = report.get("sleep_efficiency_pct")
    latency = report.get("sleep_latency_min")
    waso = report.get("waso_min")
    stages_pct = report.get("stages_pct") or {}

    # -- Kopfzeile mit Gesamteinschätzung ------------------------------------
    if isinstance(score, (int, float)):
        if score >= _SCORE_GOOD:
            verdict = "eine richtig gute Nacht"
        elif score >= _SCORE_OK:
            verdict = "eine solide Nacht mit Luft nach oben"
        else:
            verdict = "eine eher unruhige Nacht"
        lines.append(
            f"Schlaf-Zusammenfassung für {date}: {verdict} "
            f"(Score {score:.0f}/100)."
        )
    else:
        lines.append(f"Schlaf-Zusammenfassung für {date}.")

    # -- Kennzahlen -----------------------------------------------------------
    if isinstance(total, (int, float)):
        hours = total / 60.0
        lines.append(f"Du hast insgesamt {hours:.1f} Stunden geschlafen.")
    if isinstance(efficiency, (int, float)):
        if efficiency >= _EFFICIENCY_GOOD_PCT:
            lines.append(
                f"Deine Schlafeffizienz von {efficiency:.0f} % ist gut — du hast "
                "die Zeit im Bett effektiv genutzt."
            )
        else:
            lines.append(
                f"Deine Schlafeffizienz lag bei {efficiency:.0f} % — "
                "ein Teil der Zeit im Bett war Wachzeit."
            )

    # -- Tipps aus Auffälligkeiten --------------------------------------------
    tips: list[str] = []
    deep_pct = stages_pct.get(STAGE_DEEP)
    rem_pct = stages_pct.get(STAGE_REM)
    if isinstance(deep_pct, (int, float)) and deep_pct < _DEEP_LOW_PCT:
        tips.append(
            f"Dein Tiefschlaf-Anteil war mit {deep_pct:.0f} % eher niedrig. "
            "Ein kühles, dunkles Schlafzimmer und Verzicht auf Alkohol am "
            "Abend können den Tiefschlaf fördern."
        )
    if isinstance(rem_pct, (int, float)) and rem_pct < _REM_LOW_PCT:
        tips.append(
            f"Der REM-Anteil lag bei {rem_pct:.0f} %. Regelmässige "
            "Schlafenszeiten und ausreichend Gesamtschlaf helfen, mehr "
            "REM-Phasen am Morgen mitzunehmen."
        )
    if isinstance(latency, (int, float)) and latency > _LATENCY_HIGH_MIN:
        tips.append(
            f"Du hast rund {latency:.0f} Minuten zum Einschlafen gebraucht. "
            "Eine feste Abendroutine ohne Bildschirm in der letzten Stunde "
            "kann das Einschlafen erleichtern."
        )
    if isinstance(waso, (int, float)) and waso > _WASO_HIGH_MIN:
        tips.append(
            f"Du warst nachts etwa {waso:.0f} Minuten wach. Achte auf gute "
            "Luft (Lüften vor dem Schlafen) und vermeide Koffein am "
            "Nachmittag."
        )
    if not tips:
        tips.append(
            "Bleib bei deiner aktuellen Routine — regelmässige Zeiten, ein "
            "kühles Schlafzimmer und wenig Bildschirm am Abend zahlen sich aus."
        )

    lines.append("")
    lines.append("Tipps:")
    lines.extend(f"- {tip}" for tip in tips[:3])
    lines.append("")
    lines.append(
        "Hinweis: Dies ist eine automatische Auswertung und keine medizinische "
        "Diagnose. Bei anhaltenden Schlafproblemen hole bitte ärztlichen Rat ein."
    )
    return "\n".join(lines)


async def generate_coaching(
    report: dict,
    history: list[dict] | None,
    cfg: AppConfig,
) -> str:
    """
    Erzeugt einen deutschen Coaching-Text zum übergebenen SleepReport.

    Baut mit :func:`llm_coach.prompt_builder.build_coach_prompt` einen Prompt
    und schickt ihn an das LOKALE Ollama
    (``POST {cfg.llm_coach.ollama_url}/api/generate``, ``stream=false``).
    Der blockierende urllib-Call läuft in ``asyncio.to_thread``. Es wird
    niemals ein Cloud-Dienst angesprochen.

    Seiteneffekte: Loggt Erfolg bzw. Fallback-Grund (Whitebox-Prinzip).

    Args:
        report: SleepReport-Dict der aktuellen Nacht (siehe
            ``ml_pipeline.build_report``).
        history: Frühere SleepReports für Trend-Kontext (z.B. aus
            ``database.list_reports``) oder ``None``.
        cfg: Geladene App-Konfiguration (``core.config_loader.AppConfig``);
            genutzt werden ``cfg.llm_coach.ollama_url`` und
            ``cfg.llm_coach.model``.

    Returns:
        Coaching-Text vom lokalen LLM — oder bei jedem Fehler (Ollama nicht
        erreichbar, Timeout, kaputte Antwort, fehlendes Modell) die
        regelbasierte Zusammenfassung aus :func:`_fallback_summary`.
        Es wird nie eine Exception nach aussen geworfen.
    """
    try:
        prompt = build_coach_prompt(report, history)
    except Exception:  # noqa: BLE001 — Coach darf das System nie reissen
        logger.exception("Prompt-Bau fehlgeschlagen — nutze Fallback-Zusammenfassung.")
        return _fallback_summary(report)

    url = f"{cfg.llm_coach.ollama_url.rstrip('/')}/api/generate"
    payload = {
        "model": cfg.llm_coach.model,
        "prompt": prompt,
        "stream": False,
    }

    try:
        text = await asyncio.to_thread(
            _call_ollama_blocking, url, payload, _OLLAMA_TIMEOUT_S
        )
        logger.info(
            "Coaching-Text vom lokalen Modell '%s' erhalten (%d Zeichen).",
            cfg.llm_coach.model,
            len(text),
        )
        return text
    except urllib.error.URLError as exc:
        logger.warning(
            "Lokales Ollama unter %s nicht erreichbar (%s) — "
            "nutze regelbasierte Fallback-Zusammenfassung.",
            url,
            exc.reason if hasattr(exc, "reason") else exc,
        )
    except (TimeoutError, OSError) as exc:
        logger.warning(
            "Timeout/IO-Fehler beim Ollama-Call an %s (%s) — nutze Fallback.",
            url,
            exc,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Unerwartete Ollama-Antwort (%s) — nutze Fallback.", exc
        )
    except Exception:  # noqa: BLE001 — letzte Verteidigungslinie
        logger.exception("Unerwarteter Fehler im LLM-Coach — nutze Fallback.")

    return _fallback_summary(report)
