"""
EEG-Preprocessing für Roh-Signale (Zukunftspfad: Muse-Headband, Feature C).

Dieses Modul ist der vorbereitete Slot für **automatisches Sleep-Staging aus
rohem EEG** mit MNE (Signalcontainer/Filter) und YASA (Staging-Modell).
Solange kein EEG-Adapter Daten liefert, wird es nicht benötigt — die
Report-Pipeline (:mod:`ml_pipeline.report`) arbeitet direkt mit den bereits
gestageten ``sleep_stage``-Segmenten der Wearable-Adapter.

Graceful Degradation: ``numpy``/``mne``/``yasa`` werden weich importiert.
Fehlt eine der Bibliotheken, crasht **nicht der Import** dieses Moduls,
sondern erst der Aufruf von :func:`stage_raw_eeg` — mit klarer Log-Meldung
und ``NotImplementedError``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.constants import STAGE_DEEP, STAGE_LIGHT, STAGE_REM, STAGE_WAKE

logger = logging.getLogger(__name__)

# -- Optionale Abhängigkeiten (weiche Imports, Kernprinzip 2) -----------------
try:  # pragma: no cover — abhängig von der lokalen Installation
    import numpy as np
except ImportError:  # noqa: F401
    np = None  # type: ignore[assignment]

try:  # pragma: no cover
    import mne
except ImportError:
    mne = None  # type: ignore[assignment]

try:  # pragma: no cover
    import yasa
except ImportError:
    yasa = None  # type: ignore[assignment]

#: Mapping der YASA/AASM-Stadien auf das 4-Phasen-Modell von Somnoscope.
_YASA_TO_STAGE: dict[str, str] = {
    "W": STAGE_WAKE,
    "N1": STAGE_LIGHT,
    "N2": STAGE_LIGHT,
    "N3": STAGE_DEEP,
    "R": STAGE_REM,
}


async def stage_raw_eeg(
    samples: Any,
    sfreq: float,
    ch_names: list[str] | None = None,
) -> list[str]:
    """
    Staged rohes EEG in Schlafphasen (30-s-Epochen) via MNE + YASA.

    Zukunftspfad für das Muse-Headband (Feature C): Das rohe EEG wird in
    ein :class:`mne.io.RawArray` verpackt und von ``yasa.SleepStaging``
    epochweise klassifiziert. Die YASA-Stadien (W/N1/N2/N3/R) werden auf
    das 4-Phasen-Modell (``wake``/``light``/``deep``/``rem``) abgebildet.
    Die CPU-lastige Klassifikation läuft via ``asyncio.to_thread``.

    Args:
        samples: 2D-Array-artige EEG-Daten in der Form
            ``(n_kanäle, n_samples)``, Werte in Volt.
        sfreq: Abtastrate in Hz (Muse: 256 Hz).
        ch_names: Kanalnamen; Default sind die vier Muse-Elektroden
            ``["TP9", "AF7", "AF8", "TP10"]``.

    Returns:
        Eine Phase (``wake``/``light``/``deep``/``rem``) pro 30-s-Epoche,
        in zeitlicher Reihenfolge.

    Raises:
        NotImplementedError: Wenn ``numpy``, ``mne`` oder ``yasa`` nicht
            installiert sind (mit klarer Log-Meldung statt Import-Crash).

    Seiteneffekte:
        Loggt fehlende Abhängigkeiten bzw. die Anzahl gestageter Epochen.
    """
    missing = [
        name
        for name, mod in (("numpy", np), ("mne", mne), ("yasa", yasa))
        if mod is None
    ]
    if missing:
        logger.error(
            "stage_raw_eeg: EEG-Staging nicht verfügbar — fehlende optionale "
            "Abhängigkeit(en): %s. Installation: pip install %s",
            ", ".join(missing),
            " ".join(missing),
        )
        raise NotImplementedError(
            f"Roh-EEG-Staging benötigt {', '.join(missing)} (nicht installiert)."
        )

    stages = await asyncio.to_thread(_stage_sync, samples, sfreq, ch_names)
    logger.info("stage_raw_eeg: %d Epochen (30 s) gestaged.", len(stages))
    return stages


def _stage_sync(samples: Any, sfreq: float, ch_names: list[str] | None) -> list[str]:
    """Synchroner Kern: MNE-RawArray bauen, YASA staggen, Phasen mappen."""
    names = ch_names or ["TP9", "AF7", "AF8", "TP10"]
    data = np.asarray(samples, dtype=float)
    info = mne.create_info(ch_names=names, sfreq=sfreq, ch_types="eeg")
    raw = mne.io.RawArray(data, info, verbose="ERROR")
    staging = yasa.SleepStaging(raw, eeg_name=names[0])
    predicted = staging.predict()
    return [_YASA_TO_STAGE.get(str(stage), STAGE_LIGHT) for stage in predicted]
