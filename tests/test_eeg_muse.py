"""
Tests fuer :mod:`adapters.eeg_muse` (MuseEEGAdapter) — OHNE echte Hardware.

Der Adapter streamt eigentlich rohes 4-Kanal-EEG eines Muse-Headbands ueber
``brainflow`` und staged es mit YASA. Beide Aussenweltschnittstellen werden
hier ersetzt, sodass die Tests komplett hardware- und brainflow-frei laufen:

    * **Fake-Board** (:class:`_FakeBoard`): liefert deterministisches
      synthetisches EEG als numpy-Array ueber ``get_board_data`` — genau das,
      was der Adapter aus dem BrainFlow-Ringpuffer erwartet.
    * **Gemocktes** :func:`ml_pipeline.stage_raw_eeg`: gibt eine bekannte
      Epochen-Label-Sequenz zurueck, damit die reine Segmentierungs- und
      Zeitstempel-Logik von :meth:`MuseEEGAdapter.poll` isoliert pruefbar ist.

Abgedeckt:
    * Graceful Degradation: ``import adapters.eeg_muse`` crasht nie ohne
      ``brainflow``; :meth:`open` wirft dann klar ``RuntimeError`` und die
      Basis-:meth:`run`-Schleife ueberspringt den Adapter sauber.
    * Reine Helfer (``_normalize_stage``, ``_clean_mac``, ``_labels_to_segments``)
      ohne numpy/brainflow.
    * Kern-Logik von ``poll()`` mit Fake-Board + gemocktem Staging:
      unterhalb ``min_stage_minutes`` -> ``[]``; oberhalb -> zusammenhaengende
      ``sleep_stage``-Segmente im 30-s-Raster ab ``self._t0``.
    * ``close()`` ist idempotent und nie crashend.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone

import pytest

import adapters.eeg_muse as eeg_muse
from adapters.eeg_muse import MuseEEGAdapter
from core.constants import (
    ADAPTER_EEG_MUSE,
    METRIC_SLEEP_STAGE,
    STAGE_DEEP,
    STAGE_LIGHT,
    STAGE_REM,
    STAGE_WAKE,
)

#: Abtastrate des Muse (Hz) — wie in der echten BrainFlow-Konfiguration.
_SFREQ = 256.0

#: Laenge einer Schlaf-Epoche (s), identisch zum Adapter-Konstante.
_EPOCH_SECONDS = 30

#: Fester Startzeitpunkt (tz-aware) fuer reproduzierbare Segment-Zeitstempel.
_T0 = datetime(2026, 7, 7, 23, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test-Doubles
# ---------------------------------------------------------------------------


class _FakeBoard:
    """
    Minimaler BrainFlow-Board-Ersatz fuer die Puffer-/Poll-Logik.

    Liefert bei jedem :meth:`get_board_data` denselben, vorab erzeugten
    numpy-Array (Form ``(n_rows, n_samples)``) und zaehlt die Aufrufe, damit
    Tests den Ringpuffer-Zugriff verifizieren koennen. ``stop_stream`` und
    ``release_session`` protokollieren nur, dass sie aufgerufen wurden.
    """

    def __init__(self, data: object) -> None:
        self._data = data
        self.get_calls = 0
        self.stopped = 0
        self.released = 0

    def get_board_data(self) -> object:
        self.get_calls += 1
        return self._data

    def stop_stream(self) -> None:
        self.stopped += 1

    def release_session(self) -> None:
        self.released += 1


def _synthetic_eeg(np, n_channels: int, n_samples: int) -> object:
    """
    Erzeugt deterministisches synthetisches EEG (``(n_channels, n_samples)``).

    Args:
        np: Das numpy-Modul (via ``importorskip`` geladen).
        n_channels: Anzahl EEG-Kanaele (Muse: 4).
        n_samples: Anzahl Samples je Kanal.

    Returns:
        Ein ``float``-numpy-Array mit reproduzierbaren Sinus-Signalen (in uV).
    """
    t = np.arange(n_samples, dtype=float) / _SFREQ
    rows = [
        50.0 * np.sin(2.0 * np.pi * (1.0 + ch) * t) + float(ch)
        for ch in range(n_channels)
    ]
    return np.vstack(rows)


def _make_adapter(min_stage_minutes: int, **options: object) -> MuseEEGAdapter:
    """Baut einen Adapter mit Test-Optionen (ohne ``open()``/Hardware)."""
    opts: dict[str, object] = {
        "board": "muse_s",
        "min_stage_minutes": min_stage_minutes,
    }
    opts.update(options)
    return MuseEEGAdapter(opts, timezone="UTC")


def _prime_runtime_state(adapter: MuseEEGAdapter, np, board: _FakeBoard) -> None:
    """
    Setzt den Laufzeit-Zustand, den sonst ``open()`` (mit Hardware) befuellt.

    So bleibt die Kern-Logik von ``poll()`` testbar, ohne echtes BrainFlow.
    """
    adapter._board = board
    adapter._board_id = 0
    adapter._sfreq = _SFREQ
    adapter._eeg_channels = [0, 1, 2, 3]
    adapter._ch_names = ["TP9", "AF7", "AF8", "TP10"]
    adapter._t0 = _T0
    adapter._buffer = np.empty((4, 0), dtype=float)


# ---------------------------------------------------------------------------
# Graceful Degradation: Import + open() ohne brainflow
# ---------------------------------------------------------------------------


def test_import_without_brainflow_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``import adapters.eeg_muse`` crasht nie, auch wenn ``brainflow`` fehlt."""
    # brainflow-Import haart blockieren (None => ``import brainflow`` -> ImportError),
    # dann das Modul frisch laden: der weiche Import muss abgefangen werden.
    monkeypatch.setitem(__import__("sys").modules, "brainflow", None)
    monkeypatch.setitem(__import__("sys").modules, "brainflow.board_shim", None)
    reloaded = importlib.reload(eeg_muse)
    try:
        assert reloaded.BoardShim is None, "Ohne brainflow muss BoardShim None sein."
        assert hasattr(reloaded, "MuseEEGAdapter")
    finally:
        # Modul wieder in den normalen Zustand versetzen (fuer Folgetests).
        monkeypatch.undo()
        importlib.reload(eeg_muse)


@pytest.mark.asyncio
async def test_open_without_brainflow_raises_runtimeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``open()`` ohne ``brainflow`` wirft eine klare :class:`RuntimeError`."""
    adapter = _make_adapter(min_stage_minutes=30)
    monkeypatch.setattr(eeg_muse, "BoardShim", None)

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.open()
    assert "brainflow" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_run_loop_skips_adapter_when_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheitert ``open()``, ueberspringt die Basis-``run``-Schleife den Adapter."""
    adapter = _make_adapter(min_stage_minutes=30)
    monkeypatch.setattr(eeg_muse, "BoardShim", None)

    polled = False

    async def _fail_poll() -> list:
        nonlocal polled
        polled = True
        return []

    monkeypatch.setattr(adapter, "poll", _fail_poll)

    collected: list = []

    async def _sink(reading: object) -> None:
        collected.append(reading)

    # run() darf trotz open()-RuntimeError NICHT crashen und muss ohne
    # jeden poll()-Aufruf sauber zurueckkehren (Graceful Degradation).
    await adapter.run(sink=_sink)
    assert polled is False, "Bei fehlgeschlagenem open() darf nicht gepollt werden."
    assert collected == []


# ---------------------------------------------------------------------------
# Reine Helfer (ohne numpy/brainflow)
# ---------------------------------------------------------------------------


def test_name_property_is_adapter_constant() -> None:
    """Die ``name``-Property entspricht der Registry-Konstante ``eeg_muse``."""
    adapter = _make_adapter(min_stage_minutes=30)
    assert adapter.name == ADAPTER_EEG_MUSE == "eeg_muse"


def test_poll_interval_default_and_override() -> None:
    """Default-Poll-Intervall 1800 s; Option ``poll_interval_s`` ueberschreibt."""
    assert _make_adapter(min_stage_minutes=30).poll_interval_s == 1800
    assert _make_adapter(min_stage_minutes=30, poll_interval_s=600).poll_interval_s == 600


@pytest.mark.parametrize("raw, expected", [
    (None, ""),
    ("", ""),
    ("   ", ""),
    ("${ENV:MUSE_MAC}", ""),
    ("00:11:22:33:44:55", "00:11:22:33:44:55"),
    ("  AA:BB  ", "AA:BB"),
])
def test_clean_mac_normalizes_placeholder(raw: object, expected: str) -> None:
    """Leere Werte und nicht aufgeloeste ``${ENV:...}``-Platzhalter -> ''."""
    assert MuseEEGAdapter._clean_mac(raw) == expected


def test_normalize_stage_maps_unknown_to_light() -> None:
    """Nur gueltige STAGE_*-Werte passieren; Unbekanntes wird ``light``."""
    for stage in (STAGE_WAKE, STAGE_LIGHT, STAGE_DEEP, STAGE_REM):
        assert MuseEEGAdapter._normalize_stage(stage) == stage
    assert MuseEEGAdapter._normalize_stage("N2") == STAGE_LIGHT
    assert MuseEEGAdapter._normalize_stage("") == STAGE_LIGHT


def test_labels_to_segments_merges_and_normalizes() -> None:
    """
    ``_labels_to_segments`` verschmilzt gleiche Labels und normalisiert Unbekanntes.

    Getestet ohne numpy: rein die Zeitstempel-/Merge-Logik ab ``self._t0``.
    """
    adapter = _make_adapter(min_stage_minutes=30)
    adapter._t0 = _T0
    labels = [STAGE_WAKE, STAGE_WAKE, "N2", STAGE_DEEP, STAGE_DEEP, STAGE_REM]

    segments = adapter._labels_to_segments(labels)

    # wake(2) | light(1, aus "N2" normalisiert) | deep(2) | rem(1) = 4 Segmente.
    assert [s.value for s in segments] == [
        STAGE_WAKE,
        STAGE_LIGHT,
        STAGE_DEEP,
        STAGE_REM,
    ]
    # Alle Segmente: metric/source korrekt, tz-aware, end > start.
    for seg in segments:
        assert seg.metric == METRIC_SLEEP_STAGE
        assert seg.source == ADAPTER_EEG_MUSE
        assert seg.start.tzinfo is not None and seg.end is not None
        assert seg.end.tzinfo is not None
        assert seg.end > seg.start
        assert seg.raw["epoch_seconds"] == _EPOCH_SECONDS
        assert seg.raw["board"] == "muse_s"
        assert seg.raw["source"] == "muse_eeg_yasa"

    # Zeitstempel im 30-s-Raster, lueckenlos und aufsteigend ab _T0.
    assert segments[0].start == _T0
    for i, seg in enumerate(segments):
        epochs = seg.raw["epochs"]
        assert (seg.end - seg.start).total_seconds() == epochs * _EPOCH_SECONDS
    for prev, nxt in zip(segments, segments[1:]):
        assert prev.end == nxt.start, "Hypnogramm muss lueckenlos aneinanderschliessen."
    assert segments[0].raw["epochs"] == 2
    assert segments[1].raw["epochs"] == 1
    assert segments[2].raw["epochs"] == 2
    assert segments[3].raw["epochs"] == 1


def test_labels_to_segments_empty_without_labels_or_t0() -> None:
    """Ohne Labels oder ohne ``_t0`` gibt es defensiv keine Segmente."""
    adapter = _make_adapter(min_stage_minutes=30)
    adapter._t0 = _T0
    assert adapter._labels_to_segments([]) == []

    adapter._t0 = None
    assert adapter._labels_to_segments([STAGE_WAKE]) == []


# ---------------------------------------------------------------------------
# Kern-Logik: poll() mit Fake-Board + gemocktem stage_raw_eeg (numpy noetig)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_below_min_stage_minutes_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zu kurzer Puffer (< ``min_stage_minutes``) -> ``[]``, kein Staging."""
    np = pytest.importorskip("numpy")

    # Nur 1 Minute Signal, aber min_stage_minutes=30 -> Gate schliesst.
    n_samples = int(_SFREQ * 60)  # 1 min
    board = _FakeBoard(_synthetic_eeg(np, 4, n_samples))
    adapter = _make_adapter(min_stage_minutes=30)
    _prime_runtime_state(adapter, np, board)

    staged = False

    async def _fake_stage(samples: object, sfreq: float, ch_names: object) -> list:
        nonlocal staged
        staged = True
        return [STAGE_LIGHT]

    monkeypatch.setattr(eeg_muse, "stage_raw_eeg", _fake_stage)

    readings = await adapter.poll()
    assert readings == []
    assert staged is False, "Unter min_stage_minutes darf nicht gestaged werden."
    assert board.get_calls == 1, "Der Ringpuffer wird trotzdem einmal geleert."


@pytest.mark.asyncio
async def test_poll_above_min_stage_minutes_emits_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Genug Signal -> zusammenhaengende ``sleep_stage``-Segmente.

    Prueft, dass die gemockte Label-Sequenz in korrekte, aufsteigende
    30-s-Segmente (ab ``self._t0``) uebersetzt wird, mit Werten in
    ``{wake, light, deep, rem}``.
    """
    np = pytest.importorskip("numpy")

    # 4 Minuten Signal bei min_stage_minutes=1 -> Gate offen.
    n_samples = int(_SFREQ * 60 * 4)
    board = _FakeBoard(_synthetic_eeg(np, 4, n_samples))
    adapter = _make_adapter(min_stage_minutes=1)
    _prime_runtime_state(adapter, np, board)

    labels = [
        STAGE_WAKE,
        STAGE_LIGHT,
        STAGE_LIGHT,
        STAGE_DEEP,
        STAGE_REM,
        STAGE_REM,
        STAGE_WAKE,
    ]
    captured: dict[str, object] = {}

    async def _fake_stage(samples: object, sfreq: float, ch_names: object) -> list:
        captured["sfreq"] = sfreq
        captured["ch_names"] = ch_names
        captured["shape"] = getattr(samples, "shape", None)
        return list(labels)

    monkeypatch.setattr(eeg_muse, "stage_raw_eeg", _fake_stage)

    readings = await adapter.poll()

    # Staging wurde mit dem Gesamtpuffer (4 Kanaele) und korrekter sfreq gerufen.
    assert captured["sfreq"] == _SFREQ
    assert captured["ch_names"] == ["TP9", "AF7", "AF8", "TP10"]
    assert captured["shape"] == (4, n_samples)

    # wake(1) | light(2) | deep(1) | rem(2) | wake(1) = 5 Segmente.
    assert [r.value for r in readings] == [
        STAGE_WAKE,
        STAGE_LIGHT,
        STAGE_DEEP,
        STAGE_REM,
        STAGE_WAKE,
    ]
    valid = {STAGE_WAKE, STAGE_LIGHT, STAGE_DEEP, STAGE_REM}
    for r in readings:
        assert r.value in valid
        assert r.metric == METRIC_SLEEP_STAGE
        assert r.source == ADAPTER_EEG_MUSE
        assert r.end is not None and r.end > r.start
        assert r.start.tzinfo is not None and r.end.tzinfo is not None

    # Zeitstempel: 30-s-Raster ab _T0, lueckenlos und streng aufsteigend.
    assert readings[0].start == _T0
    for prev, nxt in zip(readings, readings[1:]):
        assert prev.end == nxt.start
        assert nxt.start > prev.start
    # Gesamtdauer == Anzahl Labels * 30 s.
    total = (readings[-1].end - readings[0].start).total_seconds()
    assert total == len(labels) * _EPOCH_SECONDS


@pytest.mark.asyncio
async def test_poll_accumulates_buffer_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aufeinanderfolgende Polls haengen neue Samples an den Puffer an."""
    np = pytest.importorskip("numpy")

    chunk = int(_SFREQ * 30)  # 30 s pro Poll
    board = _FakeBoard(_synthetic_eeg(np, 4, chunk))
    adapter = _make_adapter(min_stage_minutes=60)  # Gate bleibt zu
    _prime_runtime_state(adapter, np, board)

    async def _fake_stage(samples: object, sfreq: float, ch_names: object) -> list:
        return [STAGE_LIGHT]

    monkeypatch.setattr(eeg_muse, "stage_raw_eeg", _fake_stage)

    await adapter.poll()
    assert adapter._buffer.shape == (4, chunk)
    await adapter.poll()
    assert adapter._buffer.shape == (4, 2 * chunk)


@pytest.mark.asyncio
async def test_poll_returns_empty_when_staging_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Staging-Fehler wird abgefangen -> ``poll()`` liefert ``[]``."""
    np = pytest.importorskip("numpy")

    n_samples = int(_SFREQ * 60 * 2)
    board = _FakeBoard(_synthetic_eeg(np, 4, n_samples))
    adapter = _make_adapter(min_stage_minutes=1)
    _prime_runtime_state(adapter, np, board)

    async def _boom(samples: object, sfreq: float, ch_names: object) -> list:
        raise RuntimeError("YASA-Modell nicht ladbar")

    monkeypatch.setattr(eeg_muse, "stage_raw_eeg", _boom)

    readings = await adapter.poll()
    assert readings == []


@pytest.mark.asyncio
async def test_poll_without_board_returns_empty() -> None:
    """Ohne offene Session (``_board is None``) liefert ``poll()`` sofort ``[]``."""
    adapter = _make_adapter(min_stage_minutes=1)
    # _board bleibt None (open() wurde nie erfolgreich aufgerufen).
    assert await adapter.poll() == []


# ---------------------------------------------------------------------------
# close(): idempotent und graceful
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_is_idempotent_and_releases_board() -> None:
    """``close()`` stoppt/gibt das Board frei und ist mehrfach aufrufbar."""
    np = pytest.importorskip("numpy")
    board = _FakeBoard(_synthetic_eeg(np, 4, int(_SFREQ)))
    adapter = _make_adapter(min_stage_minutes=1)
    _prime_runtime_state(adapter, np, board)

    await adapter.close()
    assert board.stopped == 1
    assert board.released == 1
    assert adapter._board is None

    # Zweiter Aufruf ist ein sauberer No-op (idempotent, kein Crash).
    await adapter.close()
    assert board.stopped == 1
    assert board.released == 1


@pytest.mark.asyncio
async def test_close_without_board_is_noop() -> None:
    """``close()`` ohne je geoeffnete Session macht nichts (kein Crash)."""
    adapter = _make_adapter(min_stage_minutes=1)
    await adapter.close()  # darf nicht werfen
    assert adapter._board is None


@pytest.mark.asyncio
async def test_close_survives_board_errors(caplog: pytest.LogCaptureFixture) -> None:
    """Wirft das Board beim Aufraeumen, loggt ``close()`` nur und crasht nicht."""
    np = pytest.importorskip("numpy")

    class _AngryBoard(_FakeBoard):
        def stop_stream(self) -> None:
            raise RuntimeError("stream bereits tot")

        def release_session(self) -> None:
            raise RuntimeError("session weg")

    board = _AngryBoard(_synthetic_eeg(np, 4, int(_SFREQ)))
    adapter = _make_adapter(min_stage_minutes=1)
    _prime_runtime_state(adapter, np, board)

    with caplog.at_level(logging.DEBUG):
        await adapter.close()  # darf trotz Board-Fehlern nicht werfen
    assert adapter._board is None
