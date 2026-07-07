"""
LLM-Coach-Modul von Somnoscope (Phase 6) — 100 % lokal.

Erzeugt aus einem SleepReport (zentrales Austauschformat der ML-Pipeline)
einen deutschen, empathischen Coaching-Text über ein LOKAL laufendes
Ollama-Modell. Ist Ollama nicht verfügbar, fällt das Modul auf eine
regelbasierte Zusammenfassung zurück (Graceful Degradation) — es kommt
also immer nützlicher Text zurück, und es wird niemals eine Cloud
angesprochen (Kernprinzip 1).

Öffentliche API:
    * :func:`generate_coaching` — async, Report + Historie + Config -> Text.
    * :func:`build_coach_prompt` — baut den Prompt (z.B. für Debug/Whitebox).
"""

from __future__ import annotations

from .coach import generate_coaching
from .prompt_builder import build_coach_prompt

__all__ = ["generate_coaching", "build_coach_prompt"]
