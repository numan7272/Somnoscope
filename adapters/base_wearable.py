"""
Abstrakte Basis für alle Wearable-Adapter (Adapter-Pattern, Kernprinzip 4).

Ein Adapter übersetzt eine *proprietäre* Datenquelle (verschlüsselte BLE-Pakete,
eine Cloud-API, ein EEG-Stream) in einen einheitlichen Strom von
:class:`WearableReading`-Objekten. ML-Pipeline, Datenbank und LLM-Coach kennen
danach nur noch dieses neutrale Format — nie das konkrete Gerät.

Zwei Betriebsarten werden abgedeckt:

    * **Pull** (z.B. ``fitbit_gh_api``): periodisch eine Cloud-API abfragen.
      Solche Adapter implementieren nur :meth:`WearableAdapter.poll`; die
      Standard-:meth:`WearableAdapter.run`-Schleife ruft sie im konfigurierten
      Intervall auf.
    * **Push/Stream** (z.B. ``eeg_muse``): kontinuierlicher BLE-Notify-Stream.
      Solche Adapter überschreiben :meth:`WearableAdapter.run` und speisen den
      ``sink`` direkt aus dem Callback.

Alle I/O-lastigen Schritte sind ``async`` (Kernprinzip: Asyncio-First).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Callback, an den ein Adapter jeden einzelnen Messpunkt übergibt (z.B. Logging).
ReadingSink = Callable[["WearableReading"], Awaitable[None]]

#: Callback für einen kompletten Poll-Batch: (Adapter-Name, Readings). Wird von
#: :class:`core.pipeline.SleepPipeline` genutzt, um pro Nacht einen Report zu bauen.
BatchSink = Callable[[str, "list[WearableReading]"], Awaitable[None]]


@dataclass(frozen=True)
class WearableReading:
    """
    Ein einzelner, geräteunabhängiger Messpunkt.

    Attributes:
        source: Name des erzeugenden Adapters (z.B. ``"fitbit_gh_api"``).
        metric: Vereinheitlichte Metrik — einer der ``METRIC_*``-Werte aus
            :mod:`core.constants` (z.B. ``"heart_rate"``, ``"sleep_stage"``).
        start: Zeitstempel (tz-aware) des Messbeginns.
        end: Ende des Intervalls (z.B. einer Schlafphase). ``None`` bei
            punktuellen Samples.
        value: Numerischer Messwert (z.B. Puls) oder kategorischer Wert
            (z.B. Schlafphasen-Name ``"deep"``). ``None``, wenn nur ein
            Intervall ohne Skalarwert vorliegt.
        unit: Physikalische Einheit, sofern sinnvoll (``"bpm"``, ``"ms"``,
            ``"%"``, ``"°C"``). ``None`` bei kategorischen Werten.
        raw: Ungefiltertes Original-Payload der Quelle. Bleibt zu
            Whitebox-Zwecken (Kernprinzip 3) erhalten, damit spätere Stufen
            auch Felder auslesen können, die der Adapter nicht explizit
            übersetzt hat.
    """

    source: str
    metric: str
    start: datetime
    end: datetime | None
    value: float | str | None
    unit: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class WearableAdapter(ABC):
    """
    Basisklasse für konkrete Wearable-Adapter.

    Unterklassen müssen mindestens :attr:`name` und :meth:`poll` bereitstellen.
    Pull-Adapter kommen mit der Standard-:meth:`run`-Schleife aus; Stream-Adapter
    überschreiben :meth:`run`.

    Args:
        options: Der adapter-spezifische Options-Block aus ``config.yaml``
            (``wearable.adapters[].*`` ohne ``type``/``enabled``).
        timezone: Zeitzonen-Name aus ``system.timezone``. Adapter, die
            Datumsgrenzen berechnen (z.B. „seit gestern"), nutzen ihn.
    """

    def __init__(self, options: Mapping[str, Any], timezone: str = "UTC") -> None:
        self._options = dict(options)
        self._timezone = timezone
        self._log = logging.getLogger(f"somnoscope.adapters.{self.name}")

    # -- Von Unterklassen zu implementieren ---------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Eindeutiger Adapter-Name (identisch zum ``type`` in der config.yaml)."""

    @abstractmethod
    async def poll(self) -> list[WearableReading]:
        """
        Holt einen Schwung neuer Messpunkte (Pull-Modell).

        Wird von :meth:`run` periodisch aufgerufen. Stream-Adapter, die
        :meth:`run` überschreiben, dürfen hier eine leere Liste zurückgeben.

        Returns:
            Liste vereinheitlichter :class:`WearableReading`. Leere Liste, wenn
            gerade nichts Neues vorliegt.
        """

    # -- Standard-Lebenszyklus (überschreibbar) -----------------------------

    async def open(self) -> None:
        """
        Einmalige Initialisierung vor dem ersten :meth:`poll` (Verbindung,
        Voraussetzungs-Checks). Default: nichts zu tun.

        Raises:
            Exception: Wenn der Adapter nicht startklar ist. :meth:`run` fängt
                das ab und überspringt den Adapter (Graceful Degradation).
        """

    async def close(self) -> None:
        """Ressourcen freigeben (Verbindung schliessen). Default: nichts zu tun."""

    @property
    def poll_interval_s(self) -> int:
        """Poll-Intervall in Sekunden (Option ``poll_interval_s``, Default 1800)."""
        return int(self._options.get("poll_interval_s", 1800))

    async def run(
        self,
        sink: ReadingSink | None = None,
        on_batch: BatchSink | None = None,
    ) -> None:
        """
        Standard-Poll-Schleife: ``open`` → wiederholt ``poll`` → ``on_batch``/``sink``.

        Läuft, bis die Task von aussen abgebrochen wird (``CancelledError``,
        z.B. per Strg-C). Einzelne fehlgeschlagene Polls beenden die Schleife
        **nicht** — sie werden geloggt und beim nächsten Intervall erneut
        versucht (Graceful Degradation, Kernprinzip 2).

        Args:
            sink: Optionaler Async-Callback pro einzelnem Messpunkt (z.B. Logging).
            on_batch: Optionaler Async-Callback für den kompletten Poll-Batch
                ``(name, readings)`` — von der Pipeline genutzt, um pro Nacht
                einen Report zu bauen.
        """
        if sink is None and on_batch is None:
            self._log.warning(
                "[%s] kein Konsument (sink/on_batch) übergeben — Adapter wird "
                "nicht gestartet.",
                self.name,
            )
            return

        try:
            await self.open()
        except Exception:  # noqa: BLE001 — bewusst breit: Adapter darf System nicht reissen
            self._log.exception(
                "[%s] Initialisierung fehlgeschlagen — Adapter wird übersprungen.",
                self.name,
            )
            return

        self._log.info(
            "[%s] gestartet (Poll-Intervall=%ds).", self.name, self.poll_interval_s
        )
        try:
            while True:
                try:
                    readings = await self.poll()
                except Exception:  # noqa: BLE001
                    self._log.exception(
                        "[%s] poll() fehlgeschlagen — nächster Versuch in %ds.",
                        self.name,
                        self.poll_interval_s,
                    )
                    readings = []

                try:
                    if on_batch is not None and readings:
                        await on_batch(self.name, readings)
                    if sink is not None:
                        for reading in readings:
                            await sink(reading)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — ein Batch-Fehler darf die Schleife nicht beenden
                    self._log.exception(
                        "[%s] Batch-Verarbeitung fehlgeschlagen — nächster "
                        "Versuch in %ds.",
                        self.name,
                        self.poll_interval_s,
                    )
                else:
                    if readings:
                        self._log.info(
                            "[%s] %d Messpunkt(e) verarbeitet.",
                            self.name,
                            len(readings),
                        )

                await asyncio.sleep(self.poll_interval_s)
        except asyncio.CancelledError:
            self._log.info("[%s] wird beendet.", self.name)
            raise
        finally:
            await self.close()
