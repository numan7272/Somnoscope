"""
Tests fuer die Setup-Selbstdiagnose (``core/selftest.py``).

Abgedeckt:
    * ``run_selftest`` liefert nur gueltige Status-Werte und wirft nie —
      auch nicht, wenn ein Check intern crasht (kaputter Store).
    * ``format_report`` enthaelt die Marker [OK]/[WARN]/[FAIL] und eine
      Zusammenfassung mit den Zaehlern.
    * ``selftest_exit_code``: 0 ohne fail, 1 mit fail.
    * Adapter-Abhaengigkeits-Checks: simulation immer ok; aktiver
      ``eeg_muse`` ohne brainflow -> fail; aktiver ``fitbit_gh_api`` ohne
      ghealth-CLI -> fail (beides via monkeypatch, keine echten Deps noetig).
    * Store-Lesetest gegen eine echte (leere) SQLite-DB in ``tmp_path``.
    * LLM-Coach-Check: nicht erreichbares Ollama -> warn (nie fail),
      deaktiviertes Modul -> kein Check.

Es wird nie ins Repo geschrieben (``data_dir`` zeigt immer nach
``tmp_path``) und es findet kein echter Netzwerkzugriff statt
(``_probe_ollama`` wird gemockt).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import core.selftest as selftest
from core.config_loader import AppConfig, load_config
from core.selftest import (
    CheckResult,
    format_report,
    run_selftest,
    selftest_exit_code,
)


# -----------------------------------------------------------------------------
# Helfer: Config-Varianten
# -----------------------------------------------------------------------------

def _config_yaml(
    tmp_path: Path,
    adapter_type: str = "simulation",
    llm_enabled: bool = False,
) -> str:
    """
    Baut einen minimalen ``config.yaml``-Text mit waehlbarem Adapter.

    Args:
        tmp_path: Temporaeres Verzeichnis fuer ``data_dir``/``log_dir``.
        adapter_type: Typ des einzigen (aktiven) Wearable-Adapters.
        llm_enabled: Ob der LLM-Coach-Block aktiviert wird.

    Returns:
        YAML-Text fuer :func:`core.config_loader.load_config`.
    """
    data_dir = (tmp_path / "data").as_posix()
    log_dir = (tmp_path / "logs").as_posix()
    llm_block = ""
    if llm_enabled:
        llm_block = """
llm_coach:
  enabled: true
  backend: ollama
  ollama_url: "http://localhost:11434"
  model: "llama3"
  context_window_days: 7
"""
    return f"""
system:
  log_level: INFO
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
  timezone: UTC

wearable:
  enabled: true
  adapters:
    - type: {adapter_type}
      enabled: true
{llm_block}
"""


def _load(tmp_path: Path, write_config: Callable[[str], Path], **kwargs) -> AppConfig:
    """Schreibt die YAML-Variante nach ``tmp_path`` und laedt sie als AppConfig."""
    return load_config(write_config(_config_yaml(tmp_path, **kwargs)))


def _by_name(results: list[CheckResult], fragment: str) -> list[CheckResult]:
    """Filtert Ergebnisse, deren ``name`` das Fragment enthaelt."""
    return [r for r in results if fragment in r.name]


# -----------------------------------------------------------------------------
# run_selftest: Grundverhalten
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_selftest_liefert_gueltige_checkresults(app_config: AppConfig):
    """Alle Ergebnisse sind CheckResults mit gueltigem Status und Detail-Text."""
    results = await run_selftest(app_config)

    assert results, "Selftest muss mindestens einen Check liefern."
    for result in results:
        assert isinstance(result, CheckResult)
        assert result.status in selftest.VALID_STATUSES
        assert result.name
        assert result.detail


@pytest.mark.asyncio
async def test_run_selftest_config_und_module(app_config: AppConfig):
    """Config-Check nennt Zeitzone/Data-Dir; aktives Wearable-Modul wird gelistet."""
    results = await run_selftest(app_config)

    config_checks = _by_name(results, "Config")
    assert config_checks and config_checks[0].status == "ok"
    assert "UTC" in config_checks[0].detail
    assert str(app_config.system.data_dir) in config_checks[0].detail

    module_checks = _by_name(results, "Modul 'wearable'")
    assert module_checks and module_checks[0].status == "ok"


@pytest.mark.asyncio
async def test_run_selftest_store_lesetest_ok(app_config: AppConfig):
    """Store-Lesetest und Data-Dir-Schreibtest laufen gegen tmp_path durch."""
    results = await run_selftest(app_config)

    store_checks = _by_name(results, "Store (Lesetest)")
    assert store_checks and store_checks[0].status == "ok"

    write_checks = _by_name(results, "Data-Dir (Schreibtest)")
    assert write_checks and write_checks[0].status == "ok"

    # Der Lesetest darf keine Temp-Datei zuruecklassen.
    leftovers = list(Path(app_config.system.data_dir).glob(".selftest_*.tmp"))
    assert leftovers == []


@pytest.mark.asyncio
async def test_run_selftest_simulation_adapter_ok(app_config: AppConfig):
    """Der Simulation-Adapter ist hardware-frei und immer ok."""
    results = await run_selftest(app_config)

    adapter_checks = _by_name(results, "Adapter 'simulation'")
    assert adapter_checks and adapter_checks[0].status == "ok"


@pytest.mark.asyncio
async def test_run_selftest_wirft_nie_bei_kaputtem_store(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
):
    """Ein crashender Store fuehrt zu einem fail-Ergebnis, nicht zu einer Exception."""

    def _boom(cfg: AppConfig):
        raise RuntimeError("DB explodiert")

    import database

    monkeypatch.setattr(database, "create_store", _boom)

    results = await run_selftest(app_config)  # darf nicht werfen

    store_checks = _by_name(results, "Store (Lesetest)")
    assert store_checks and store_checks[0].status == "fail"
    assert "DB explodiert" in store_checks[0].detail
    assert selftest_exit_code(results) == 1


# -----------------------------------------------------------------------------
# Adapter-Abhaengigkeiten
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eeg_muse_ohne_brainflow_ist_fail(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
):
    """Aktiver eeg_muse-Adapter ohne brainflow -> fail + Exit-Code 1."""
    cfg = _load(tmp_path, write_config, adapter_type="eeg_muse")
    monkeypatch.setattr(selftest, "_module_available", lambda name: False)

    results = await run_selftest(cfg)

    muse_checks = _by_name(results, "Adapter 'eeg_muse'")
    assert muse_checks and muse_checks[0].status == "fail"
    assert "brainflow" in muse_checks[0].detail
    assert selftest_exit_code(results) == 1


@pytest.mark.asyncio
async def test_eeg_muse_mit_deps_ist_ok(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
):
    """Aktiver eeg_muse-Adapter mit brainflow+numpy -> ok."""
    cfg = _load(tmp_path, write_config, adapter_type="eeg_muse")
    monkeypatch.setattr(selftest, "_module_available", lambda name: True)

    results = await run_selftest(cfg)

    muse_checks = _by_name(results, "Adapter 'eeg_muse'")
    assert muse_checks and muse_checks[0].status == "ok"


@pytest.mark.asyncio
async def test_fitbit_ohne_ghealth_cli_ist_fail(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
):
    """Aktiver fitbit_gh_api-Adapter ohne ghealth-CLI im PATH -> fail."""
    cfg = _load(tmp_path, write_config, adapter_type="fitbit_gh_api")
    monkeypatch.setattr(selftest.shutil, "which", lambda name: None)

    results = await run_selftest(cfg)

    fitbit_checks = _by_name(results, "Adapter 'fitbit_gh_api'")
    assert fitbit_checks and fitbit_checks[0].status == "fail"
    assert "ghealth" in fitbit_checks[0].detail
    assert selftest_exit_code(results) == 1


# -----------------------------------------------------------------------------
# LLM-Coach (Ollama)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_coach_nicht_erreichbar_ist_nur_warn(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
):
    """Nicht erreichbares Ollama degradiert zu warn (Fallback greift), nie fail."""
    cfg = _load(tmp_path, write_config, llm_enabled=True)

    def _unreachable(url: str) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(selftest, "_probe_ollama", _unreachable)

    results = await run_selftest(cfg)

    llm_checks = _by_name(results, "LLM-Coach")
    assert llm_checks and llm_checks[0].status == "warn"
    assert "Fallback" in llm_checks[0].detail
    # Ein nicht laufendes Ollama darf den Exit-Code nicht kippen.
    assert selftest_exit_code(results) == 0


@pytest.mark.asyncio
async def test_llm_coach_deaktiviert_wird_nicht_geprueft(app_config: AppConfig):
    """Ohne aktives llm_coach-Modul gibt es keinen Ollama-Check."""
    results = await run_selftest(app_config)
    assert _by_name(results, "LLM-Coach") == []


# -----------------------------------------------------------------------------
# format_report / selftest_exit_code
# -----------------------------------------------------------------------------

def test_format_report_enthaelt_marker_und_zusammenfassung():
    """Report enthaelt [OK]/[WARN]/[FAIL] pro Zeile und die Zaehler am Ende."""
    results = [
        CheckResult(name="Alpha", status="ok", detail="alles gut"),
        CheckResult(name="Beta", status="warn", detail="optional fehlt"),
        CheckResult(name="Gamma", status="fail", detail="kaputt"),
        CheckResult(name="Delta", status="ok", detail="auch gut"),
    ]

    report = format_report(results)

    assert "[OK]" in report
    assert "[WARN]" in report
    assert "[FAIL]" in report
    assert "Alpha" in report and "kaputt" in report
    assert "2 ok" in report
    assert "1 warn" in report
    assert "1 fail" in report
    assert len(report.splitlines()) >= len(results) + 2  # Kopf + Zusammenfassung


def test_selftest_exit_code_ohne_fail_ist_null():
    """Nur ok/warn -> Exit-Code 0."""
    results = [
        CheckResult(name="A", status="ok", detail="-"),
        CheckResult(name="B", status="warn", detail="-"),
    ]
    assert selftest_exit_code(results) == 0


def test_selftest_exit_code_mit_fail_ist_eins():
    """Ein einziges fail reicht fuer Exit-Code 1."""
    results = [
        CheckResult(name="A", status="ok", detail="-"),
        CheckResult(name="B", status="fail", detail="-"),
    ]
    assert selftest_exit_code(results) == 1


def test_selftest_exit_code_leere_liste_ist_null():
    """Keine Ergebnisse -> nichts fehlgeschlagen -> 0."""
    assert selftest_exit_code([]) == 0
