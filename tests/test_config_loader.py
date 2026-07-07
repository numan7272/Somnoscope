"""
Tests fuer :mod:`core.config_loader`.

Abgedeckt:
    * Neues Multi-Adapter-Schema (``wearable.adapters`` als Liste).
    * Backward-Compat: altes ``device_type``-Einzelgeraet-Schema wird in
      genau einen Adapter ueberfuehrt.
    * ``${ENV:...}``-Substitution (gesetzte und fehlende Variablen).
    * Fehlerfaelle (fehlende Datei, kaputtes YAML, fehlende Pflichtfelder,
      Datenbank aktiviert ohne Token).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core.config_loader import ConfigError, load_config
from tests.conftest import base_config_yaml


# ----------------------------------------------------------------------------
# Neues Schema: wearable.adapters[]
# ----------------------------------------------------------------------------

def test_new_schema_multi_adapter(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """Mehrere Adapter-Eintraege werden mit type/enabled/options gemappt."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  log_level: DEBUG
  data_dir: "{data_dir}"
  timezone: Europe/Berlin

wearable:
  enabled: true
  adapters:
    - type: simulation
      enabled: true
      mode: replay
      seed: 7
    - type: fitbit_gh_api
      enabled: false
      ghealth_bin: ghealth
      poll_interval_s: 900
"""
    )
    cfg = load_config(cfg_path)

    assert cfg.system.log_level == "DEBUG"
    assert cfg.system.timezone == "Europe/Berlin"
    assert cfg.wearable.enabled is True
    assert len(cfg.wearable.adapters) == 2

    sim, fitbit = cfg.wearable.adapters
    assert sim.type == "simulation"
    assert sim.enabled is True
    # type/enabled duerfen NICHT in den options landen, der Rest schon.
    assert sim.options == {"mode": "replay", "seed": 7}

    assert fitbit.type == "fitbit_gh_api"
    assert fitbit.enabled is False
    assert fitbit.options["poll_interval_s"] == 900

    # Nur der aktivierte Adapter zaehlt als aktiv.
    active = cfg.wearable.active_adapters()
    assert [a.type for a in active] == ["simulation"]


def test_new_schema_base_fixture(tmp_path: Path, write_config: Callable[[str], Path]) -> None:
    """Die geteilte Basis-Config der Testsuite laedt sauber durch."""
    cfg = load_config(write_config(base_config_yaml(tmp_path)))
    assert cfg.wearable.active_adapters()[0].type == "simulation"
    assert cfg.database.enabled is False
    assert cfg.ml_pipeline.enabled is False


def test_new_schema_adapter_without_type_fails(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """Ein Adapter-Eintrag ohne ``type`` ist ein klarer ConfigError."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters:
    - enabled: true
      mode: replay
"""
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_new_schema_adapters_must_be_list(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """``wearable.adapters`` als Mapping statt Liste wird abgelehnt."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters:
    type: simulation
"""
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


# ----------------------------------------------------------------------------
# Backward-Compat: altes device_type-Schema
# ----------------------------------------------------------------------------

def test_legacy_device_type_schema(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """Altes ``device_type``-Schema ergibt genau einen Adapter inkl. Options."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  device_type: fitbit_ble
  mac_address: "AA:BB:CC:DD:EE:FF"
  poll_interval_s: 300
"""
    )
    cfg = load_config(cfg_path)

    assert len(cfg.wearable.adapters) == 1
    adapter = cfg.wearable.adapters[0]
    assert adapter.type == "fitbit_ble"
    # Der Legacy-Adapter erbt den enabled-Status des wearable-Blocks.
    assert adapter.enabled is True
    # Restfelder wandern in options; Schema-Schluessel bleiben draussen.
    assert adapter.options == {
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "poll_interval_s": 300,
    }


def test_legacy_schema_disabled_wearable(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """``wearable.enabled=false`` schlaegt auf den Legacy-Adapter durch."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: false
  device_type: fitbit_ble
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.wearable.enabled is False
    assert cfg.wearable.active_adapters() == []


# ----------------------------------------------------------------------------
# ${ENV:...}-Substitution
# ----------------------------------------------------------------------------

def test_env_substitution_set(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gesetzte ``${ENV:NAME}``-Platzhalter werden durch den Env-Wert ersetzt."""
    monkeypatch.setenv("SOMNO_TEST_MQTT_USER", "geheimer_user")
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters: []
climate_sensors:
  enabled: false
  username: "${{ENV:SOMNO_TEST_MQTT_USER}}"
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.climate_sensors.username == "geheimer_user"


def test_env_substitution_missing_becomes_none(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fehlende Env-Variable wird zu ``None`` aufgeloest (kein Crash)."""
    monkeypatch.delenv("SOMNO_TEST_DOES_NOT_EXIST", raising=False)
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters: []
climate_sensors:
  enabled: false
  password: "${{ENV:SOMNO_TEST_DOES_NOT_EXIST}}"
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.climate_sensors.password is None


def test_env_substitution_in_adapter_options(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Substitution wirkt rekursiv, auch tief in Adapter-Options."""
    monkeypatch.setenv("SOMNO_TEST_GH_BIN", "/opt/bin/ghealth")
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters:
    - type: fitbit_gh_api
      enabled: true
      ghealth_bin: "${{ENV:SOMNO_TEST_GH_BIN}}"
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.wearable.adapters[0].options["ghealth_bin"] == "/opt/bin/ghealth"


# ----------------------------------------------------------------------------
# Fehlerfaelle
# ----------------------------------------------------------------------------

def test_missing_file_raises(tmp_path: Path) -> None:
    """Nicht vorhandene Datei -> ConfigError mit klarer Meldung."""
    with pytest.raises(ConfigError):
        load_config(tmp_path / "gibt_es_nicht.yaml")


def test_broken_yaml_raises(write_config: Callable[[str], Path]) -> None:
    """Kaputtes YAML -> ConfigError (kein roher yaml.YAMLError)."""
    cfg_path = write_config("system: [unclosed")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_non_mapping_toplevel_raises(write_config: Callable[[str], Path]) -> None:
    """Top-Level muss ein Mapping sein."""
    cfg_path = write_config("- nur\n- eine\n- liste\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_missing_wearable_block_raises(
    tmp_path: Path, write_config: Callable[[str], Path]
) -> None:
    """Fehlender ``wearable``-Block ist ein Pflichtfeld-Fehler."""
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
"""
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_database_enabled_without_token_raises(
    tmp_path: Path,
    write_config: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``database.enabled=true`` ohne gesetzten Token-Env -> ConfigError."""
    monkeypatch.delenv("SOMNO_TEST_INFLUX_TOKEN", raising=False)
    data_dir = (tmp_path / "data").as_posix()
    cfg_path = write_config(
        f"""
system:
  data_dir: "{data_dir}"
wearable:
  enabled: true
  adapters: []
database:
  enabled: true
  token_env_var: SOMNO_TEST_INFLUX_TOKEN
"""
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)
