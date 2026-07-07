"""
Pytest-Testsuite fuer Somnoscope.

Dieses Paket enthaelt die automatisierten Tests fuer Config-Loader, Adapter,
ML-Pipeline, Persistenz und WebUI. Die Tests laufen mit den Basis-
Abhaengigkeiten plus ``pytest``, ``pytest-asyncio`` und optional ``fastapi``;
schwergewichtige Optionals (yasa, mne, ollama, influxdb-client) werden NICHT
vorausgesetzt (Graceful Degradation, Kernprinzip 2).
"""

from __future__ import annotations
