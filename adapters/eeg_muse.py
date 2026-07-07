"""
Adapter-Slot: EEG-Headband (Muse) lokal über BrainFlow (Feature C).

✅  ERFÜLLT KERNPRINZIP 1 vollständig: Ein EEG-Headband wie das **Muse S**
    streamt rohes 4-Kanal-EEG (TP9, AF7, AF8, TP10) direkt über BLE — komplett
    lokal, ohne Cloud. Genau dieses Rohsignal braucht die YASA/MNE-Pipeline
    (Bandpass 0.3–35 Hz), für die Somnoscope gebaut ist.

Status: **Slot vorbereitet, Implementierung offen.** Die konkrete BLE-/BrainFlow-
Anbindung ist bewusst noch nicht ausprogrammiert, weil sie echte Hardware zum
Testen braucht und mit ``brainflow`` eine neue Abhängigkeit einführt (siehe
Konvention „Keine ungefragte Erweiterung der Abhängigkeiten"). Der Adapter ist
hier registriert, in ``config.yaml`` als deaktiviertes optionales Feature
sichtbar und dokumentiert den geplanten Weg — so kann Feature C ohne
Architektur-Änderung nachgezogen werden.

Geplanter Implementierungsweg:
    1. ``brainflow`` (BoardShim, BrainFlowInputParams) verbindet sich per BLE mit
       dem Muse (``BoardIds.MUSE_S_BOARD`` bzw. ``MUSE_2_BOARD``).
    2. Als Stream-Adapter wird nicht :meth:`poll`, sondern :meth:`run`
       überschrieben: ein Ringpuffer sammelt EEG-Samples und speist sie als
       ``WearableReading`` (metric = roh-EEG-Kanal) in den Sink.
    3. Die ML-Pipeline (Phase 4) baut daraus ein MNE-``RawArray`` und lässt
       ``yasa.SleepStaging`` die Schlafphasen bestimmen.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.constants import ADAPTER_EEG_MUSE

from .base_wearable import WearableAdapter, WearableReading


class MuseEEGAdapter(WearableAdapter):
    """
    Platzhalter-Adapter für das Muse-EEG-Headband.

    Relevante (geplante) Options-Felder aus ``config.yaml``:
        * ``mac_address`` (str): BLE-Adresse des Muse.
        * ``board`` (str): ``"muse_s"`` oder ``"muse_2"``.

    Aktuell wirft :meth:`open` bewusst :class:`NotImplementedError`, damit die
    Basis-``run``-Schleife den Adapter sauber überspringt (Graceful Degradation),
    falls jemand ihn aktiviert, bevor die BrainFlow-Anbindung existiert.
    """

    @property
    def name(self) -> str:
        return ADAPTER_EEG_MUSE

    async def open(self) -> None:
        raise NotImplementedError(
            "Der Muse-EEG-Adapter (Feature C) ist als Slot vorbereitet, aber "
            "noch nicht implementiert. Geplant: BrainFlow-BLE-Anbindung + "
            "YASA-Staging. Bis dahin bleibt 'eeg_muse' in config.yaml deaktiviert."
        )

    async def poll(self) -> list[WearableReading]:
        return []
