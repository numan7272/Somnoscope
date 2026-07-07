"""
Gemeinsame Fixtures und Helfer fuer die Somnoscope-Testsuite.

Stellt bereit:
    * Sys-Path-Bootstrap, damit ``core``/``adapters``/... aus dem Projekt-Root
      importierbar sind, egal von wo pytest gestartet wird.
    * Eine Factory zum Schreiben temporaerer ``config.yaml``-Dateien.
    * Eine fertig geladene :class:`core.config_loader.AppConfig`, deren
      ``data_dir`` in ``tmp_path`` zeigt (kein Schreiben ins Repo).
    * Deterministische Simulations-Readings einer kompletten Nacht sowie
      einen daraus gebauten SleepReport.

Alle Fixtures sind synchron gehalten; async-Produktivcode wird in den
Fixtures ueber ``asyncio.run`` ausgefuehrt, damit ``pytest-asyncio`` im
Strict-Mode ohne async-Fixture-Sonderfaelle auskommt.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# -- Sys-Path-Bootstrap: Projekt-Root importierbar machen ---------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.base_wearable import WearableReading  # noqa: E402
from adapters.simulation import SimulationAdapter  # noqa: E402
from core.config_loader import AppConfig, load_config  # noqa: E402

#: Fester Seed fuer reproduzierbare Simulations-Naechte in allen Tests.
SIM_SEED = 42


@pytest.fixture()
def write_config(tmp_path: Path) -> Callable[[str], Path]:
    """
    Factory-Fixture: schreibt YAML-Text als ``config.yaml`` nach ``tmp_path``.

    Returns:
        Eine Funktion ``(yaml_text) -> Pfad der geschriebenen Datei``.

    Seiteneffekte:
        Legt Dateien ausschliesslich unterhalb von ``tmp_path`` an.
    """

    def _write(yaml_text: str) -> Path:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml_text, encoding="utf-8")
        return cfg_path

    return _write


def base_config_yaml(tmp_path: Path) -> str:
    """
    Baut einen minimalen, gueltigen ``config.yaml``-Text (neues Schema).

    Args:
        tmp_path: Temporaeres Verzeichnis; ``data_dir``/``log_dir`` zeigen
            dorthin, damit Tests niemals ins Repo schreiben.

    Returns:
        YAML-Text mit ``system``- und ``wearable``-Block (Multi-Adapter).
    """
    data_dir = (tmp_path / "data").as_posix()
    log_dir = (tmp_path / "logs").as_posix()
    return f"""
system:
  log_level: INFO
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
  timezone: UTC

wearable:
  enabled: true
  adapters:
    - type: simulation
      enabled: true
      mode: replay
      seed: {SIM_SEED}
"""


@pytest.fixture()
def app_config(tmp_path: Path, write_config: Callable[[str], Path]) -> AppConfig:
    """
    Liefert eine geladene :class:`AppConfig` mit ``data_dir`` in ``tmp_path``.

    Returns:
        Vollstaendig validierte, immutable Anwendungskonfiguration.
    """
    cfg_path = write_config(base_config_yaml(tmp_path))
    return load_config(cfg_path)


def make_night_readings(
    seed: int = SIM_SEED, duration_h: float = 8.0
) -> list[WearableReading]:
    """
    Erzeugt deterministische Readings einer kompletten simulierten Nacht.

    Args:
        seed: Zufalls-Seed fuer reproduzierbare Ergebnisse.
        duration_h: Schlafdauer in Stunden.

    Returns:
        Liste von :class:`WearableReading` (Phasen-Segmente + Vitalwerte).
    """
    adapter = SimulationAdapter(
        {"mode": "replay", "seed": seed, "sleep_duration_h": duration_h},
        timezone="UTC",
    )
    return asyncio.run(adapter.poll())


@pytest.fixture()
def night_readings() -> list[WearableReading]:
    """Deterministische Readings einer 8-h-Simulationsnacht (Seed fest)."""
    return make_night_readings()


@pytest.fixture()
def sample_report(night_readings: list[WearableReading]) -> dict:
    """
    Ein realer SleepReport, gebaut ueber :func:`ml_pipeline.build_report`.

    Returns:
        SleepReport-dict (JSON-serialisierbar, ISO-8601-Zeiten).
    """
    from ml_pipeline import build_report

    report = asyncio.run(build_report(night_readings, source="simulation"))
    assert report is not None, "Simulationsnacht muss einen Report ergeben."
    return report
