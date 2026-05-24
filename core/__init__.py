"""
Somnoscope Core-Paket.

Bündelt die Querschnittsmodule, die von allen anderen Subpaketen benötigt werden:

* :mod:`core.config_loader` — liest und validiert die zentrale ``config.yaml``.
* :mod:`core.logger`        — initialisiert das anwendungsweite Logging.

Beispiel:
    >>> from core.config_loader import load_config
    >>> from core.logger import setup_logging
    >>> cfg = load_config("config.yaml")
    >>> setup_logging(cfg.system.log_level, cfg.system.log_dir)
"""

from core.config_loader import (
    AppConfig,
    ClimateSensorsConfig,
    ConfigError,
    DatabaseConfig,
    LLMCoachConfig,
    MLPipelineConfig,
    SystemConfig,
    WearableConfig,
    load_config,
)
from core.logger import setup_logging

__all__ = [
    "AppConfig",
    "ClimateSensorsConfig",
    "ConfigError",
    "DatabaseConfig",
    "LLMCoachConfig",
    "MLPipelineConfig",
    "SystemConfig",
    "WearableConfig",
    "load_config",
    "setup_logging",
]
