"""
Config-Loader für Somnoscope.

Liest die zentrale ``config.yaml`` ein, validiert die Struktur und liefert ein
typsicheres, eingefrorenes :class:`AppConfig`-Objekt zurück.

Designentscheidungen:
    * **Dataclasses statt nackter Dicts:** Der Rest der Anwendung soll
      Auto-Vervollständigung und Type-Checking bekommen, statt mit
      ``cfg["wearable"]["mac_address"]`` durch verschachtelte Dicts zu hangeln.
    * **Frozen Dataclasses:** Config ist immutable — wer Konfiguration ändern
      will, soll das beim Reload tun, nicht zur Laufzeit patchen.
    * **ENV-Variable-Substitution:** Strings im Format ``${ENV:VARNAME}``
      werden beim Laden aus dem Environment aufgelöst. So bleiben Tokens
      ausserhalb des Repos.
    * **Strikte Validierung:** Fehler werden früh und mit klarer Meldung
      als :class:`ConfigError` geworfen — nicht erst tief in der ML-Pipeline.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Regex für ENV-Variable-Substitution im YAML: "${ENV:VARNAME}"
_ENV_PATTERN = re.compile(r"\$\{ENV:([A-Z0-9_]+)\}")

# Gültige Log-Levels — gespiegelt aus ``logging``, hier nochmal explizit, damit
# wir Tippfehler in der YAML schon beim Laden abfangen.
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Wird geworfen, wenn die ``config.yaml`` fehlt, kaputt oder unvollständig ist."""


# ----------------------------------------------------------------------------
# Dataclasses pro Modul
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemConfig:
    """System-weite Einstellungen (Logging, Pfade, Zeitzone)."""

    log_level: str
    data_dir: Path
    log_dir: Path
    timezone: str


@dataclass(frozen=True)
class WearableConfig:
    """Konfiguration des BLE-Wearables. Pflicht-Modul."""

    enabled: bool
    device_type: str
    mac_address: str
    reconnect_interval_s: int
    scan_timeout_s: int


@dataclass(frozen=True)
class ClimateSensorsConfig:
    """MQTT-basierte Klimasensoren (optional)."""

    enabled: bool
    broker_host: str
    broker_port: int
    topics: tuple[str, ...]
    qos: int
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class DatabaseConfig:
    """InfluxDB-Konfiguration (optional)."""

    enabled: bool
    url: str
    org: str
    bucket: str
    token_env_var: str

    @property
    def token(self) -> str | None:
        """Liest den Token erst bei Bedarf aus der Umgebung — niemals aus dem YAML."""
        return os.environ.get(self.token_env_var)


@dataclass(frozen=True)
class MLPipelineConfig:
    """Machine-Learning-Pipeline (Preprocessing + Scoring)."""

    enabled: bool
    preprocessing: dict[str, Any] = field(default_factory=dict)
    scorer: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCoachConfig:
    """Lokaler LLM-Coach (optional, Phase 6)."""

    enabled: bool
    backend: str
    ollama_url: str
    model: str
    context_window_days: int


@dataclass(frozen=True)
class AppConfig:
    """
    Top-Level-Container aller Module.

    Wird von :func:`load_config` zurückgegeben und im Rest der Anwendung
    nur lesend verwendet.
    """

    system: SystemConfig
    wearable: WearableConfig
    climate_sensors: ClimateSensorsConfig
    database: DatabaseConfig
    ml_pipeline: MLPipelineConfig
    llm_coach: LLMCoachConfig

    def enabled_modules(self) -> list[str]:
        """
        Gibt die Namen aller aktiv geschalteten optionalen Module zurück.

        Das Wearable ist immer dabei (Pflicht), sofern es nicht explizit
        deaktiviert wurde.
        """
        active: list[str] = []
        if self.wearable.enabled:
            active.append("wearable")
        if self.climate_sensors.enabled:
            active.append("climate_sensors")
        if self.database.enabled:
            active.append("database")
        if self.ml_pipeline.enabled:
            active.append("ml_pipeline")
        if self.llm_coach.enabled:
            active.append("llm_coach")
        return active

    def disabled_modules(self) -> list[str]:
        """Gegenstück zu :meth:`enabled_modules` — listet inaktive Module."""
        all_modules = {
            "wearable": self.wearable.enabled,
            "climate_sensors": self.climate_sensors.enabled,
            "database": self.database.enabled,
            "ml_pipeline": self.ml_pipeline.enabled,
            "llm_coach": self.llm_coach.enabled,
        }
        return [name for name, enabled in all_modules.items() if not enabled]


# ----------------------------------------------------------------------------
# Öffentlicher Einstiegspunkt
# ----------------------------------------------------------------------------

def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """
    Lädt und validiert die Somnoscope-Konfiguration.

    Ablauf:
        1. YAML-Datei lesen.
        2. ``${ENV:VARNAME}``-Platzhalter aus dem Environment auflösen.
        3. Jeden Modul-Block in seine Dataclass mappen.
        4. Plausibilität prüfen (z.B. Wearable muss konfiguriert sein).

    Args:
        path: Pfad zur YAML-Datei. Default ist ``config.yaml`` im
            aktuellen Arbeitsverzeichnis.

    Returns:
        Ein vollständig validiertes, immutable :class:`AppConfig`.

    Raises:
        ConfigError: Wenn die Datei fehlt, kein gültiges YAML ist oder
            erforderliche Felder fehlen.
    """
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise ConfigError(
            f"Config-Datei nicht gefunden: {cfg_path.resolve()}. "
            "Lege eine config.yaml im Projekt-Root an (siehe README)."
        )

    try:
        with cfg_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML konnte nicht geparst werden: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(
            "Top-Level der config.yaml muss ein Mapping sein, "
            f"gefunden: {type(raw).__name__}"
        )

    raw = _resolve_env_vars(raw)

    try:
        system = _build_system(raw["system"])
        wearable = _build_wearable(raw["wearable"])
        climate = _build_climate(raw.get("climate_sensors", {"enabled": False}))
        database = _build_database(raw.get("database", {"enabled": False}))
        ml = _build_ml(raw.get("ml_pipeline", {"enabled": False}))
        llm = _build_llm(raw.get("llm_coach", {"enabled": False}))
    except KeyError as exc:
        raise ConfigError(f"Pflichtfeld fehlt in config.yaml: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Ungültiger Wert in config.yaml: {exc}") from exc

    config = AppConfig(
        system=system,
        wearable=wearable,
        climate_sensors=climate,
        database=database,
        ml_pipeline=ml,
        llm_coach=llm,
    )

    _validate_semantics(config)
    logger.debug("Config geladen aus %s", cfg_path.resolve())
    return config


# ----------------------------------------------------------------------------
# Builder pro Modul-Block
# ----------------------------------------------------------------------------

def _build_system(block: dict[str, Any]) -> SystemConfig:
    """Baut die :class:`SystemConfig` und prüft das Log-Level."""
    log_level = str(block.get("log_level", "INFO")).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"system.log_level '{log_level}' ist ungültig. "
            f"Erlaubt: {sorted(_VALID_LOG_LEVELS)}"
        )
    return SystemConfig(
        log_level=log_level,
        data_dir=Path(block.get("data_dir", "./data")),
        log_dir=Path(block.get("log_dir", "./logs")),
        timezone=str(block.get("timezone", "UTC")),
    )


def _build_wearable(block: dict[str, Any]) -> WearableConfig:
    """Pflicht-Modul. ``enabled`` und ``device_type`` sind erforderlich."""
    return WearableConfig(
        enabled=bool(block["enabled"]),
        device_type=str(block["device_type"]),
        mac_address=str(block.get("mac_address", "")),
        reconnect_interval_s=int(block.get("reconnect_interval_s", 30)),
        scan_timeout_s=int(block.get("scan_timeout_s", 15)),
    )


def _build_climate(block: dict[str, Any]) -> ClimateSensorsConfig:
    """Optionales Modul — wenn ``enabled=false``, reichen Defaults."""
    topics_raw = block.get("topics", []) or []
    if not isinstance(topics_raw, list):
        raise ValueError("climate_sensors.topics muss eine Liste sein.")
    return ClimateSensorsConfig(
        enabled=bool(block.get("enabled", False)),
        broker_host=str(block.get("broker_host", "localhost")),
        broker_port=int(block.get("broker_port", 1883)),
        topics=tuple(str(t) for t in topics_raw),
        qos=int(block.get("qos", 1)),
        username=block.get("username"),
        password=block.get("password"),
    )


def _build_database(block: dict[str, Any]) -> DatabaseConfig:
    """InfluxDB-Block. Der Token wird *nicht* hier geladen — siehe property."""
    return DatabaseConfig(
        enabled=bool(block.get("enabled", False)),
        url=str(block.get("url", "http://localhost:8086")),
        org=str(block.get("org", "")),
        bucket=str(block.get("bucket", "")),
        token_env_var=str(block.get("token_env_var", "INFLUXDB_TOKEN")),
    )


def _build_ml(block: dict[str, Any]) -> MLPipelineConfig:
    """ML-Pipeline. ``preprocessing`` und ``scorer`` werden als Dicts durchgereicht."""
    return MLPipelineConfig(
        enabled=bool(block.get("enabled", False)),
        preprocessing=dict(block.get("preprocessing", {})),
        scorer=dict(block.get("scorer", {})),
    )


def _build_llm(block: dict[str, Any]) -> LLMCoachConfig:
    """LLM-Coach. Defaults gelten, solange das Modul deaktiviert ist."""
    return LLMCoachConfig(
        enabled=bool(block.get("enabled", False)),
        backend=str(block.get("backend", "ollama")),
        ollama_url=str(block.get("ollama_url", "http://localhost:11434")),
        model=str(block.get("model", "")),
        context_window_days=int(block.get("context_window_days", 7)),
    )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _resolve_env_vars(node: Any) -> Any:
    """
    Ersetzt rekursiv alle ``${ENV:NAME}``-Platzhalter durch ``os.environ['NAME']``.

    Ist die Variable nicht gesetzt, wird ``None`` eingesetzt — das jeweilige
    Modul muss dann selbst entscheiden, ob das ein Fehler ist (z.B. die
    Datenbank ohne Token sollte nicht starten).
    """
    if isinstance(node, dict):
        return {k: _resolve_env_vars(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_env_vars(v) for v in node]
    if isinstance(node, str):
        match = _ENV_PATTERN.fullmatch(node.strip())
        if match:
            return os.environ.get(match.group(1))
    return node


def _validate_semantics(cfg: AppConfig) -> None:
    """
    Prüft Plausibilität *über* einzelne Module hinaus.

    Aktuell:
        * Wenn ``database.enabled=True``, muss der Token tatsächlich verfügbar sein.
        * Wenn ``wearable.enabled=False``, ergibt das System keinen Sinn —
          wir warnen statt zu blockieren, damit man trotzdem mit ``--dry-run``
          experimentieren kann.
    """
    if cfg.database.enabled and not cfg.database.token:
        raise ConfigError(
            f"database.enabled=true, aber die Umgebungsvariable "
            f"'{cfg.database.token_env_var}' ist leer oder nicht gesetzt."
        )
    if not cfg.wearable.enabled:
        logger.warning(
            "wearable.enabled=false — ohne Wearable liefert das System keine "
            "Schlafdaten. Aktivieren Sie das Modul in config.yaml."
        )
