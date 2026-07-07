"""
Schlaf-Score-Berechnung für Somnoscope (Whitebox, deterministisch).

Verdichtet die wichtigsten Nachtkennzahlen zu einem einzelnen Score von
0 bis 100. Die Formel ist bewusst simpel, dokumentiert und ohne
ML-Blackbox gehalten (Kernprinzip 3: Whitebox) — jede Teilkomponente
wird geloggt, damit nachvollziehbar bleibt, *warum* eine Nacht so
bewertet wurde.

Formel (max. 100 Punkte):
    * **Effizienz (max. 45 P.):** ``sleep_efficiency_pct / 100 * 45``.
      Effizienz ist der stärkste Einzelprädiktor für subjektive Erholung.
    * **Tiefschlaf (max. 25 P.):** linear bis zum Zielwert 20 % Tiefschlaf-
      Anteil; mehr als 20 % bringt keine Zusatzpunkte (Sättigung).
    * **REM (max. 20 P.):** linear bis zum Zielwert 22 % REM-Anteil,
      ebenfalls gesättigt.
    * **Kontinuität (max. 10 P.):** Startwert 10, abzüglich
      ``0.15 P. pro WASO-Minute`` und ``1 P. pro Aufwachereignis``,
      nie unter 0.

Das Ergebnis wird auf [0, 100] geclamped und gerundet. Gleiche Eingaben
liefern immer denselben Score (keine Zufallsanteile).
"""

from __future__ import annotations

import logging

from core.constants import STAGE_DEEP, STAGE_REM

logger = logging.getLogger(__name__)

#: Gewichte/Zielwerte der Score-Formel (an einer Stelle, keine Magic Numbers).
_W_EFFICIENCY = 45.0
_W_DEEP = 25.0
_W_REM = 20.0
_W_CONTINUITY = 10.0
_TARGET_DEEP_PCT = 20.0
_TARGET_REM_PCT = 22.0
_PENALTY_PER_WASO_MIN = 0.15
_PENALTY_PER_AWAKENING = 1.0


def compute_sleep_score(
    stages_pct: dict[str, float],
    efficiency_pct: float,
    waso_min: float,
    awakenings: int,
) -> int:
    """
    Berechnet den Schlaf-Score (0..100) einer Nacht.

    Gewichtete Summe aus Schlafeffizienz, Tiefschlaf-Anteil, REM-Anteil und
    einer Kontinuitäts-Komponente (Abzug für WASO und Aufwachereignisse) —
    Details siehe Modul-Docstring. Deterministisch und geclamped.

    Args:
        stages_pct: Prozent-Anteile je Phase (Keys ``wake``/``light``/
            ``deep``/``rem``, bezogen auf die Zeit im Bett).
        efficiency_pct: Schlafeffizienz in Prozent (Gesamtschlaf / Zeit im
            Bett * 100).
        waso_min: Wachminuten nach dem Einschlafen (Wake After Sleep Onset).
        awakenings: Anzahl der Aufwachereignisse nach dem Einschlafen.

    Returns:
        Ganzzahliger Score im Bereich 0..100.

    Seiteneffekte:
        Loggt die Teil-Scores auf DEBUG-Level (Whitebox-Prinzip).
    """
    eff = max(0.0, min(100.0, float(efficiency_pct)))
    deep_pct = max(0.0, float(stages_pct.get(STAGE_DEEP, 0.0)))
    rem_pct = max(0.0, float(stages_pct.get(STAGE_REM, 0.0)))

    p_eff = eff / 100.0 * _W_EFFICIENCY
    p_deep = min(deep_pct / _TARGET_DEEP_PCT, 1.0) * _W_DEEP
    p_rem = min(rem_pct / _TARGET_REM_PCT, 1.0) * _W_REM
    p_cont = max(
        0.0,
        _W_CONTINUITY
        - max(0.0, float(waso_min)) * _PENALTY_PER_WASO_MIN
        - max(0, int(awakenings)) * _PENALTY_PER_AWAKENING,
    )

    total = p_eff + p_deep + p_rem + p_cont
    score = int(round(max(0.0, min(100.0, total))))
    logger.debug(
        "Sleep-Score: eff=%.1f + deep=%.1f + rem=%.1f + kontinuitaet=%.1f "
        "=> %.1f (geclamped: %d)",
        p_eff,
        p_deep,
        p_rem,
        p_cont,
        total,
        score,
    )
    return score
