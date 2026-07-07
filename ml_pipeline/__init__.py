"""
ML-/Analyse-Pipeline von Somnoscope (Phase 4).

Verdichtet die geräteunabhängigen :class:`adapters.base_wearable.WearableReading`
einer Nacht zu einem **SleepReport** — dem zentralen, JSON-serialisierbaren
Austauschformat für Datenbank, Dashboard und LLM-Coach.

Öffentliche API:
    * :func:`build_report` — Readings -> SleepReport-dict (oder ``None``).
    * :func:`compute_sleep_score` — Kennzahlen -> Score 0..100.
    * :func:`stage_raw_eeg` — Zukunftspfad: rohes EEG -> Phasen (MNE+YASA).
"""

from __future__ import annotations

from .preprocessor import stage_raw_eeg
from .report import build_report
from .scorer import compute_sleep_score

__all__ = ["build_report", "compute_sleep_score", "stage_raw_eeg"]
