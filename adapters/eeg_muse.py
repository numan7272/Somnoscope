"""
Adapter: EEG-Headband (Muse) lokal über BrainFlow, gestaget mit YASA (Feature C).

✅  ERFÜLLT KERNPRINZIP 1 vollständig: Ein EEG-Headband wie das **Muse S**
    streamt rohes 4-Kanal-EEG (TP9, AF7, AF8, TP10) direkt über BLE — komplett
    lokal, ohne Cloud. Genau dieses Rohsignal braucht die YASA/MNE-Pipeline
    (:func:`ml_pipeline.stage_raw_eeg`), für die Somnoscope gebaut ist.

Betriebsart — **Pull-Adapter**: Der Adapter überschreibt nur :meth:`poll`,
sodass die Standard-:meth:`~adapters.base_wearable.WearableAdapter.run`-Schleife
greift. Bei jedem Poll werden die seit dem letzten Aufruf angesammelten
BrainFlow-Samples in einen internen Puffer geholt; sobald genug Signal
(``min_stage_minutes``) vorliegt, staged YASA den **gesamten** Puffer neu und
der Adapter emittiert das Hypnogramm als zusammenhängende Schlafphasen-Segmente.

Rechen-/Speicherkosten:
    Der interne EEG-Puffer wächst über die Nacht (4 Kanäle × 256 Hz ≈ 1 kB/s,
    also ~30 MB über 8 h) und wird bei jedem Poll komplett neu gestaget. Das ist
    bewusst so gewählt: YASA braucht Kontext (mehrere Epochen), und die
    Report-Pipeline *upsertet* pro ``date`` — ein erneut gestagetes, längeres
    Hypnogramm ersetzt also das vorherige derselben Nacht sauber. Deshalb ist
    das Poll-Intervall bewusst gross (Default 1800 s / 30 min): häufigeres
    Re-Staging würde nur CPU kosten, ohne den Report zu verbessern.

Graceful Degradation:
    ``brainflow`` und ``numpy`` werden modulweit *weich* importiert — der reine
    ``import adapters.eeg_muse`` crasht also nie, auch wenn die optionalen
    Abhängigkeiten fehlen. Erst :meth:`open` wirft dann eine klare
    :class:`RuntimeError`, woraufhin die ``run``-Schleife den Adapter sauber
    überspringt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from core.constants import (
    ADAPTER_EEG_MUSE,
    METRIC_SLEEP_STAGE,
    SLEEP_STAGES,
    STAGE_LIGHT,
)
from ml_pipeline import stage_raw_eeg

from .base_wearable import WearableAdapter, WearableReading

# -- Optionale Abhängigkeiten (weiche Imports, Kernprinzip 2) -----------------
try:  # pragma: no cover — abhängig von der lokalen Installation
    import numpy as np
except ImportError:  # noqa: F401
    np = None  # type: ignore[assignment]

try:  # pragma: no cover — brainflow ist eine optionale Hardware-Abhängigkeit
    from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
except ImportError:  # noqa: F401
    BoardShim = None  # type: ignore[assignment]
    BoardIds = None  # type: ignore[assignment]
    BrainFlowInputParams = None  # type: ignore[assignment]


#: Länge einer Schlaf-Epoche in Sekunden (YASA/AASM-Standard).
_EPOCH_SECONDS = 30

#: BrainFlow liefert EEG in Mikrovolt; MNE/YASA erwarten Volt (siehe
#: :func:`ml_pipeline.stage_raw_eeg`). Skalierung beim Staging.
_UV_TO_V = 1e-6

#: Mapping der ``board``-Option auf die BrainFlow-Board-IDs. Lazy befüllt in
#: :func:`_board_id_for`, weil ``BoardIds`` ohne brainflow ``None`` ist.
_BOARD_ALIASES = ("muse_s", "muse_2")


def _board_id_for(board_name: str) -> Any:
    """
    Löst einen ``board``-Options-Wert in eine BrainFlow-Board-ID auf.

    Args:
        board_name: ``"muse_s"`` oder ``"muse_2"`` (case-insensitive).

    Returns:
        Die zugehörige ``BoardIds``-Konstante.

    Raises:
        RuntimeError: Bei unbekanntem Board-Namen (klar für den Nutzer).
    """
    mapping = {
        "muse_s": BoardIds.MUSE_S_BOARD,
        "muse_2": BoardIds.MUSE_2_BOARD,
    }
    key = board_name.strip().lower()
    if key not in mapping:
        raise RuntimeError(
            f"Unbekanntes Muse-Board '{board_name}'. "
            f"Erlaubt: {', '.join(_BOARD_ALIASES)}."
        )
    return mapping[key]


class MuseEEGAdapter(WearableAdapter):
    """
    Liest rohes EEG eines Muse-Headbands lokal über BrainFlow und staged es mit YASA.

    Relevante Options-Felder aus ``config.yaml`` (``wearable.adapters[]``):
        * ``mac_address`` (str): BLE-Adresse des Muse. Optional — ist sie leer
          (oder ein nicht aufgelöster ``${ENV:...}``-Platzhalter), sucht
          BrainFlow das Gerät selbst.
        * ``board`` (str): ``"muse_s"`` oder ``"muse_2"``. Default ``"muse_s"``.
        * ``min_stage_minutes`` (int): Mindest-Signaldauer im Puffer, bevor
          gestaget wird (YASA braucht Kontext). Default 30.
        * ``poll_interval_s`` (int): Poll-Intervall in Sekunden. Default 1800.

    Der Adapter ist ein **Pull-Adapter**: Er implementiert nur :meth:`poll`;
    die Basis-:meth:`~adapters.base_wearable.WearableAdapter.run`-Schleife ruft
    ihn im Intervall auf.
    """

    def __init__(self, options: Mapping[str, Any], timezone: str = "UTC") -> None:
        super().__init__(options, timezone)
        self._board_name: str = str(self._options.get("board", "muse_s"))
        self._mac: str = self._clean_mac(self._options.get("mac_address"))
        self._min_stage_minutes: int = int(
            self._options.get("min_stage_minutes", 30)
        )

        # -- Laufzeit-Zustand (erst in open() gesetzt) ----------------------
        self._board: Any = None
        self._board_id: Any = None
        self._sfreq: float = 0.0
        self._eeg_channels: list[int] = []
        self._ch_names: list[str] | None = None
        self._t0: datetime | None = None
        self._buffer: Any = None  # numpy-Array (n_kanäle, n_samples)

    @property
    def name(self) -> str:
        return ADAPTER_EEG_MUSE

    @property
    def poll_interval_s(self) -> int:
        """
        Poll-Intervall in Sekunden (Option ``poll_interval_s``, Default 1800).

        Bewusst gross: Der gesamte Puffer wird bei jedem Poll neu gestaget
        (siehe Modul-Docstring, „Rechen-/Speicherkosten"), häufigeres Pollen
        brächte keinen Report-Mehrwert.
        """
        return int(self._options.get("poll_interval_s", 1800))

    # -- Lebenszyklus --------------------------------------------------------

    async def open(self) -> None:
        """
        Verbindet sich per BrainFlow-BLE mit dem Muse und startet den Stream.

        Ablauf: :class:`BrainFlowInputParams` (mit optionaler ``mac_address``)
        aufbauen, :class:`BoardShim` für das konfigurierte Board erzeugen,
        ``prepare_session`` + ``start_stream`` (blockierend → ``asyncio.to_thread``).
        Danach werden Abtastrate, EEG-Kanal-Indizes und -Namen defensiv
        ermittelt und ein leerer EEG-Puffer angelegt.

        Raises:
            RuntimeError: Wenn ``brainflow`` bzw. ``numpy`` fehlen oder das
                Board unbekannt ist. Die Basis-``run``-Schleife überspringt den
                Adapter dann sauber (Graceful Degradation).
        """
        missing = [
            name
            for name, mod in (("brainflow", BoardShim), ("numpy", np))
            if mod is None
        ]
        if missing:
            raise RuntimeError(
                "Muse-EEG-Adapter (Feature C) benötigt die optionale(n) "
                f"Abhängigkeit(en) {', '.join(missing)}, die nicht installiert "
                f"sind. Installation: pip install {' '.join(missing)}."
            )

        self._board_id = _board_id_for(self._board_name)

        params = BrainFlowInputParams()
        if self._mac:
            params.mac_address = self._mac

        self._board = BoardShim(self._board_id, params)
        # BLE-Handshake ist blockierend → im Thread-Pool ausführen.
        await asyncio.to_thread(self._board.prepare_session)
        await asyncio.to_thread(self._board.start_stream)

        self._sfreq = float(BoardShim.get_sampling_rate(self._board_id))
        self._eeg_channels = list(BoardShim.get_eeg_channels(self._board_id))
        self._ch_names = self._resolve_ch_names(self._board_id, self._eeg_channels)
        self._t0 = datetime.now(self._tz())
        self._buffer = np.empty((len(self._eeg_channels), 0), dtype=float)

        self._log.info(
            "[%s] AKTIV — Muse-EEG lokal über BrainFlow (board=%s, %.0f Hz, "
            "%d EEG-Kanäle: %s). 100 %% lokal, keine Cloud.",
            self.name,
            self._board_name,
            self._sfreq,
            len(self._eeg_channels),
            ", ".join(self._ch_names or []),
        )

    async def close(self) -> None:
        """
        Stoppt den Stream und gibt die BrainFlow-Session frei (nie crashend).

        ``stop_stream``/``release_session`` sind blockierend und werden im
        Thread-Pool ausgeführt; Fehler werden nur geloggt, damit das Beenden
        des Adapters (bzw. der ``run``-Schleife) nie am Aufräumen scheitert.
        """
        board = self._board
        if board is None:
            return
        try:
            await asyncio.to_thread(board.stop_stream)
        except Exception:  # noqa: BLE001 — Aufräumen darf nie reissen
            self._log.debug("[%s] stop_stream() fehlgeschlagen.", self.name, exc_info=True)
        try:
            await asyncio.to_thread(board.release_session)
        except Exception:  # noqa: BLE001
            self._log.debug(
                "[%s] release_session() fehlgeschlagen.", self.name, exc_info=True
            )
        finally:
            self._board = None

    # -- Pull ----------------------------------------------------------------

    async def poll(self) -> list[WearableReading]:
        """
        Holt neue Samples, staged den Gesamtpuffer und emittiert Phasen-Segmente.

        Ablauf:
            1. ``get_board_data()`` leert den BrainFlow-Ringpuffer; die
               EEG-Kanalzeilen werden an den internen Puffer angehängt.
            2. Reicht die Pufferdauer noch nicht (< ``min_stage_minutes``), wird
               eine leere Liste zurückgegeben — die ``run``-Schleife versucht es
               beim nächsten Intervall erneut.
            3. Sonst staged :func:`ml_pipeline.stage_raw_eeg` den gesamten Puffer
               (30-s-Epochen); die Epochen-Labels werden zu zusammenhängenden
               Segmenten gemerged und als :class:`WearableReading`
               (``metric=sleep_stage``, mit ``start`` UND ``end``) emittiert.

        Returns:
            Liste von Schlafphasen-Segmenten (leer, solange zu wenig Signal
            vorliegt oder das Staging fehlschlägt).
        """
        if self._board is None or np is None:
            return []

        # 1) Neue Samples abholen (leert den BrainFlow-Ringpuffer).
        data = await asyncio.to_thread(self._board.get_board_data)
        if data is not None and getattr(data, "size", 0) > 0:
            eeg = np.asarray(data, dtype=float)[self._eeg_channels, :]
            self._buffer = (
                eeg
                if self._buffer is None or self._buffer.shape[1] == 0
                else np.concatenate([self._buffer, eeg], axis=1)
            )

        # 2) Genug Signal für ein sinnvolles Staging?
        n_samples = int(self._buffer.shape[1]) if self._buffer is not None else 0
        duration_min = (n_samples / self._sfreq / 60.0) if self._sfreq else 0.0
        if duration_min < self._min_stage_minutes:
            self._log.debug(
                "[%s] Puffer erst %.1f min (< %d min) — noch kein Staging.",
                self.name,
                duration_min,
                self._min_stage_minutes,
            )
            return []

        # 3) Gesamtpuffer staggen (YASA, CPU-lastig → intern via to_thread).
        try:
            labels = await stage_raw_eeg(
                self._buffer * _UV_TO_V, self._sfreq, self._ch_names
            )
        except Exception:  # noqa: BLE001 — Staging-Fehler darf die Schleife nicht killen
            self._log.exception(
                "[%s] EEG-Staging fehlgeschlagen — Poll liefert nichts.", self.name
            )
            return []

        segments = self._labels_to_segments(labels)
        self._log.info(
            "[%s] %d Epoche(n) gestaged → %d Phasen-Segment(e) (Puffer %.1f min).",
            self.name,
            len(labels),
            len(segments),
            duration_min,
        )
        return segments

    # -- Interna -------------------------------------------------------------

    def _labels_to_segments(self, labels: list[str]) -> list[WearableReading]:
        """
        Fasst eine Liste von Epochen-Labels (30 s) zu zusammenhängenden Segmenten.

        Aufeinanderfolgende, gleiche Phasen-Labels werden zu *einem* Segment
        (``start``..``end``) verschmolzen — so entsteht ein kompaktes Hypnogramm
        statt tausender 30-s-Punkte. Unbekannte Labels werden defensiv auf
        :data:`~core.constants.STAGE_LIGHT` normalisiert.

        Args:
            labels: Phasen-Label je 30-s-Epoche, in zeitlicher Reihenfolge.

        Returns:
            Liste von :class:`WearableReading` (``metric=sleep_stage``), jeweils
            mit ``start`` und ``end`` (tz-aware).
        """
        if not labels or self._t0 is None:
            return []

        readings: list[WearableReading] = []
        seg_start_idx = 0
        n = len(labels)
        for i in range(1, n + 1):
            if i == n or labels[i] != labels[seg_start_idx]:
                stage = self._normalize_stage(labels[seg_start_idx])
                seg_start = self._t0 + timedelta(seconds=seg_start_idx * _EPOCH_SECONDS)
                seg_end = self._t0 + timedelta(seconds=i * _EPOCH_SECONDS)
                readings.append(
                    WearableReading(
                        source=self.name,
                        metric=METRIC_SLEEP_STAGE,
                        start=seg_start,
                        end=seg_end,
                        value=stage,
                        unit=None,
                        raw={
                            "epochs": i - seg_start_idx,
                            "epoch_seconds": _EPOCH_SECONDS,
                            "board": self._board_name,
                            "source": "muse_eeg_yasa",
                        },
                    )
                )
                seg_start_idx = i
        return readings

    @staticmethod
    def _normalize_stage(label: str) -> str:
        """Sichert zu, dass nur gültige ``STAGE_*``-Werte emittiert werden."""
        return label if label in SLEEP_STAGES else STAGE_LIGHT

    @staticmethod
    def _clean_mac(raw: Any) -> str:
        """
        Normalisiert die ``mac_address``-Option.

        Ein leerer Wert oder ein nicht aufgelöster ``${ENV:...}``-Platzhalter
        (falls die Umgebungsvariable fehlt) wird als „nicht gesetzt" behandelt,
        damit BrainFlow das Gerät selbst sucht.
        """
        if not raw:
            return ""
        text = str(raw).strip()
        if not text or text.startswith("${"):
            return ""
        return text

    def _resolve_ch_names(self, board_id: Any, eeg_channels: list[int]) -> list[str]:
        """
        Ermittelt die EEG-Kanalnamen defensiv über BrainFlow.

        Fällt auf generische Namen (``EEG1``…) zurück, falls BrainFlow keine
        Namen liefert oder deren Anzahl nicht zur Kanalzahl passt — so bleibt
        das Staging robust gegen abweichende BrainFlow-Versionen.
        """
        try:
            names = list(BoardShim.get_eeg_names(board_id))
        except Exception:  # noqa: BLE001 — defensiv gegen BrainFlow-Versionsdrift
            names = []
        if len(names) != len(eeg_channels):
            names = [f"EEG{i + 1}" for i in range(len(eeg_channels))]
        return names

    def _tz(self) -> timezone:
        """Zeitzone auflösen, mit UTC-Fallback (analog zu den anderen Adaptern)."""
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self._timezone)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return timezone.utc
