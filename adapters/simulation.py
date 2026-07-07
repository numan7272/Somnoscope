"""
Simulations-Adapter: realistische Schlafdaten ohne Hardware (Feature „Demo").

Zweck: Somnoscope soll **end-to-end lauffähig** sein, auch wenn gerade kein
Fitbit, kein Muse-Headband und kein ESP32 angeschlossen ist — für Entwicklung,
CI, Dashboard-Demos und QA. Der Adapter erzeugt eine plausible Nacht:

    * eine Hypnogramm-Sequenz (Wach / Leicht / Tief / REM) in 30-s-Epochen,
      zusammengefasst zu zusammenhängenden Phasen-Segmenten,
    * dazu passende Puls-, HRV- und SpO2-Verläufe (tiefe Phasen → niedriger
      Puls, höhere HRV; REM/Wach → höher/variabler).

100 % lokal, ohne externe Abhängigkeiten (nur ``random``/``math`` aus der
Standardbibliothek). Erfüllt damit Kernprinzip 1.

Betrieb (Option ``mode``):
    * ``"once"`` (Default): Beim ersten Poll wird die letzte Nacht komplett
      ausgegeben; folgende Polls liefern nur noch „Live"-Vitalwerte des
      aktuellen Moments.
    * ``"replay"``: Bei jedem Poll wird die Nacht neu erzeugt (nützlich für
      Tests, die deterministische Wiederholbarkeit brauchen — via ``seed``).
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from datetime import datetime, time, timedelta, timezone
from typing import Any

from core.constants import (
    ADAPTER_SIMULATION,
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SLEEP_STAGE,
    METRIC_SPO2,
    STAGE_DEEP,
    STAGE_LIGHT,
    STAGE_REM,
    STAGE_WAKE,
)

from .base_wearable import WearableAdapter, WearableReading

#: Schlafphasen — aus core.constants (Single Source of Truth, verhindert Drift
#: zwischen Simulation und Report-Builder).
_STAGE_WAKE = STAGE_WAKE
_STAGE_LIGHT = STAGE_LIGHT
_STAGE_DEEP = STAGE_DEEP
_STAGE_REM = STAGE_REM

#: Grober Puls-/HRV-/SpO2-Charakter je Phase: (puls_bpm, hrv_ms, spo2_%).
_STAGE_VITALS: dict[str, tuple[float, float, float]] = {
    _STAGE_WAKE: (68.0, 45.0, 97.0),
    _STAGE_LIGHT: (60.0, 60.0, 97.0),
    _STAGE_DEEP: (52.0, 85.0, 96.0),
    _STAGE_REM: (64.0, 50.0, 96.5),
}

_EPOCH_SECONDS = 30


class SimulationAdapter(WearableAdapter):
    """
    Erzeugt synthetische, aber plausible Schlaf- und Vitaldaten.

    Relevante Options-Felder aus ``config.yaml``:
        * ``mode`` (str): ``"once"`` oder ``"replay"``. Default ``"once"``.
        * ``poll_interval_s`` (int): Intervall der Live-Vitalwerte. Default 60.
        * ``seed`` (int | None): Fixer Zufalls-Seed für reproduzierbare Nächte.
        * ``sleep_duration_h`` (float): Schlafdauer in Stunden. Default 8.0.
        * ``bedtime`` (str): Zubettgeh-Zeit ``"HH:MM"`` (lokal). Default ``"23:00"``.
    """

    def __init__(self, options: Mapping[str, Any], timezone: str = "UTC") -> None:
        super().__init__(options, timezone)
        self._mode: str = str(self._options.get("mode", "once"))
        self._duration_h: float = float(self._options.get("sleep_duration_h", 8.0))
        self._bedtime_str: str = str(self._options.get("bedtime", "23:00"))
        seed = self._options.get("seed")
        self._seed: int | None = int(seed) if seed is not None else None
        self._emitted_nights: set[str] = set()

    @property
    def name(self) -> str:
        return ADAPTER_SIMULATION

    @property
    def poll_interval_s(self) -> int:
        # Kürzeres Default-Intervall, damit Live-Vitalwerte sichtbar werden.
        return int(self._options.get("poll_interval_s", 60))

    async def open(self) -> None:
        self._log.info(
            "[simulation] AKTIV — synthetische Schlafdaten (mode=%s, dauer=%.1fh). "
            "Keine echte Hardware/Cloud im Spiel.",
            self._mode,
            self._duration_h,
        )

    async def poll(self) -> list[WearableReading]:
        """Erste Nacht komplett (``once``) bzw. jedes Mal (``replay``), sonst Live-Vitals."""
        night_start = self._last_night_start()
        night_key = night_start.date().isoformat()

        if self._mode == "replay" or night_key not in self._emitted_nights:
            self._emitted_nights.add(night_key)
            return self._generate_night(night_start)

        return self._generate_live_sample()

    # -- Nacht-Generierung ---------------------------------------------------

    def _generate_night(self, night_start: datetime) -> list[WearableReading]:
        """
        Baut eine vollständige Nacht: Hypnogramm-Segmente + Vitalwert-Samples.

        Args:
            night_start: tz-aware Startzeitpunkt (Zubettgehen).

        Returns:
            Liste von :class:`WearableReading` (Phasen-Segmente mit ``end`` sowie
            punktuelle Puls/HRV/SpO2-Samples).
        """
        rng = random.Random(self._seed if self._seed is not None else night_key_seed(night_start))
        n_epochs = int(self._duration_h * 3600 / _EPOCH_SECONDS)
        stages = self._simulate_hypnogram(rng, n_epochs)

        readings: list[WearableReading] = []

        # 1) Phasen zu zusammenhängenden Segmenten zusammenfassen (kompaktes Hypnogramm).
        seg_start_idx = 0
        for i in range(1, n_epochs + 1):
            if i == n_epochs or stages[i] != stages[seg_start_idx]:
                seg_start = night_start + timedelta(seconds=seg_start_idx * _EPOCH_SECONDS)
                seg_end = night_start + timedelta(seconds=i * _EPOCH_SECONDS)
                readings.append(
                    WearableReading(
                        source=self.name,
                        metric=METRIC_SLEEP_STAGE,
                        start=seg_start,
                        end=seg_end,
                        value=stages[seg_start_idx],
                        unit=None,
                        raw={"epochs": i - seg_start_idx, "simulated": True},
                    )
                )
                seg_start_idx = i

        # 2) Vitalwerte alle 5 Minuten, abgeleitet aus der jeweiligen Phase.
        step = int(300 / _EPOCH_SECONDS)  # 5-Minuten-Raster
        for i in range(0, n_epochs, step):
            ts = night_start + timedelta(seconds=i * _EPOCH_SECONDS)
            hr, hrv, spo2 = self._vitals_for_stage(rng, stages[i])
            readings.append(self._sample(METRIC_HEART_RATE, ts, hr, "bpm"))
            readings.append(self._sample(METRIC_HRV, ts, hrv, "ms"))
            readings.append(self._sample(METRIC_SPO2, ts, spo2, "%"))

        self._log.info(
            "[simulation] Nacht erzeugt: %d Phasen-Segmente, Start %s.",
            sum(1 for r in readings if r.metric == METRIC_SLEEP_STAGE),
            night_start.isoformat(),
        )
        return readings

    def _simulate_hypnogram(self, rng: random.Random, n_epochs: int) -> list[str]:
        """
        Erzeugt eine Phasen-Sequenz über ~90-Minuten-Schlafzyklen.

        Realistisch heisst hier: Phasen halten in **Bouts** von mehreren Minuten
        an (statt sekündlich zu flackern). Früh in der Nacht dominiert Tiefschlaf,
        später REM; gelegentlich kurze nächtliche Wach-Momente.
        """
        stages: list[str] = []
        cycle_epochs = int(90 * 60 / _EPOCH_SECONDS)

        while len(stages) < n_epochs:
            i = len(stages)
            phase = (i % cycle_epochs) / cycle_epochs
            progress = i / max(n_epochs - 1, 1)  # 0..1 über die ganze Nacht

            if progress < 0.03:
                stage = _STAGE_WAKE  # Einschlafphase
            elif phase < 0.1:
                stage = _STAGE_LIGHT
            elif phase < 0.45:
                # Tiefschlaf-Anteil sinkt im Nachtverlauf.
                stage = _STAGE_DEEP if rng.random() > progress * 0.7 else _STAGE_LIGHT
            elif phase < 0.7:
                stage = _STAGE_LIGHT
            else:
                # REM-Anteil steigt im Nachtverlauf.
                stage = _STAGE_REM if rng.random() < 0.4 + progress * 0.4 else _STAGE_LIGHT

            # Seltener, kurzer nächtlicher Wach-Moment (Arousal).
            if 0.05 < progress < 0.95 and rng.random() < 0.04:
                bout = rng.randint(1, 3)  # 0.5–1.5 min wach
                stage = _STAGE_WAKE
            else:
                # Typische Bout-Länge je Phase (in Epochen à 30 s).
                bout = {
                    _STAGE_DEEP: rng.randint(20, 40),   # 10–20 min
                    _STAGE_REM: rng.randint(16, 36),    # 8–18 min
                    _STAGE_LIGHT: rng.randint(10, 30),  # 5–15 min
                    _STAGE_WAKE: rng.randint(4, 10),    # 2–5 min
                }[stage]

            stages.extend([stage] * bout)

        stages = stages[:n_epochs]
        # Aufwachphase am Ende.
        for k in range(max(0, n_epochs - 3), n_epochs):
            stages[k] = _STAGE_WAKE
        return stages

    def _vitals_for_stage(
        self, rng: random.Random, stage: str
    ) -> tuple[float, float, float]:
        """Liefert (Puls, HRV, SpO2) für eine Phase inkl. leichtem Rauschen."""
        base_hr, base_hrv, base_spo2 = _STAGE_VITALS.get(stage, _STAGE_VITALS[_STAGE_LIGHT])
        hr = round(base_hr + rng.uniform(-3, 3), 1)
        hrv = round(base_hrv + rng.uniform(-8, 8), 1)
        spo2 = round(min(100.0, base_spo2 + rng.uniform(-1.0, 1.0)), 1)
        return hr, hrv, spo2

    # -- Live-Betrieb --------------------------------------------------------

    def _generate_live_sample(self) -> list[WearableReading]:
        """Erzeugt einen einzelnen aktuellen Vitalwert-Satz (Wach am Tag)."""
        rng = random.Random()
        now = datetime.now(self._tz())
        # Tagsüber: wacher Ruhepuls mit leichter zirkadianer Schwingung.
        drift = 4 * math.sin(now.hour / 24 * 2 * math.pi)
        hr = round(66 + drift + rng.uniform(-4, 4), 1)
        hrv = round(48 + rng.uniform(-10, 10), 1)
        spo2 = round(min(100.0, 97.5 + rng.uniform(-1.0, 1.0)), 1)
        return [
            self._sample(METRIC_HEART_RATE, now, hr, "bpm"),
            self._sample(METRIC_HRV, now, hrv, "ms"),
            self._sample(METRIC_SPO2, now, spo2, "%"),
        ]

    # -- Helfer --------------------------------------------------------------

    def _sample(
        self, metric: str, ts: datetime, value: float, unit: str
    ) -> WearableReading:
        """Baut ein punktuelles Vitalwert-Reading (ohne Intervall-Ende)."""
        return WearableReading(
            source=self.name,
            metric=metric,
            start=ts,
            end=None,
            value=value,
            unit=unit,
            raw={"simulated": True},
        )

    def _tz(self) -> timezone:
        """Zeitzone auflösen, mit UTC-Fallback (siehe fitbit_gh_api-Adapter)."""
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self._timezone)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return timezone.utc

    def _last_night_start(self) -> datetime:
        """Berechnet den Zubettgeh-Zeitpunkt der letzten Nacht (tz-aware)."""
        tz = self._tz()
        now = datetime.now(tz)
        try:
            hh, mm = (int(x) for x in self._bedtime_str.split(":"))
        except ValueError:
            hh, mm = 23, 0
        bedtime = time(hour=hh, minute=mm)
        # Zubettgehen „gestern" um bedtime (bzw. vorletzte Nacht, wenn es noch
        # vor dem heutigen bedtime ist — dann ist die jüngste vollständige Nacht
        # die von vorgestern auf gestern).
        candidate = datetime.combine((now - timedelta(days=1)).date(), bedtime, tzinfo=tz)
        return candidate


def night_key_seed(night_start: datetime) -> int:
    """Stabiler Seed pro Nacht, damit dieselbe Nacht innerhalb eines Laufs gleich bleibt."""
    return int(night_start.strftime("%Y%m%d"))
