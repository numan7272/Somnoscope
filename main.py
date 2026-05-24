"""
Somnoscope — Einstiegspunkt.

Verantwortlichkeiten dieses Skripts:
    1. ``config.yaml`` über den :func:`core.config_loader.load_config` einlesen.
    2. Anwendungsweites Logging über :func:`core.logger.setup_logging` initialisieren.
    3. Den asynchronen Haupt-Loop starten, der nach und nach die aktivierten
       Module hochfährt (Wearable, MQTT, ML, DB, LLM-Coach).

Phase 1 implementiert davon nur den Bootstrap — die Module geben aktuell nur
ihren Status aus und werden in späteren Phasen mit echten Tasks ersetzt.

Ausführung::

    python main.py

Optional kann ein abweichender Config-Pfad übergeben werden::

    python main.py path/to/other-config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from core.config_loader import AppConfig, ConfigError, load_config
from core.logger import setup_logging

logger = logging.getLogger("somnoscope.main")

# Exit-Codes, damit Wrapper-Scripts / systemd genau wissen, *warum* es schiefging.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 130


# ----------------------------------------------------------------------------
# Modul-Stubs für Phase 1
# ----------------------------------------------------------------------------
# Jeder dieser Coroutines steht für ein späteres Modul. Aktuell loggen sie nur,
# dass sie gestartet würden, und beenden sich sofort wieder. In Phase 2+ werden
# sie durch echte Long-Running-Tasks (BLE-Scan, MQTT-Loop, ...) ersetzt.

async def _start_wearable_module(cfg: AppConfig) -> None:
    """Platzhalter — wird in Phase 2 durch den BLE-Adapter ersetzt."""
    logger.info(
        "[wearable] Aktiv: device_type=%s, mac=%s (Adapter folgt in Phase 2)",
        cfg.wearable.device_type,
        cfg.wearable.mac_address or "<nicht gesetzt>",
    )


async def _start_climate_module(cfg: AppConfig) -> None:
    """Platzhalter — wird in Phase 3 durch den MQTT-Subscriber ersetzt."""
    logger.info(
        "[climate_sensors] Aktiv: broker=%s:%d, %d Topic(s) abonniert",
        cfg.climate_sensors.broker_host,
        cfg.climate_sensors.broker_port,
        len(cfg.climate_sensors.topics),
    )


async def _start_ml_module(cfg: AppConfig) -> None:
    """Platzhalter — wird in Phase 4 durch Preprocessor + YASA-Scorer ersetzt."""
    logger.info(
        "[ml_pipeline] Aktiv: scorer=%s",
        cfg.ml_pipeline.scorer.get("backend", "<unbekannt>"),
    )


async def _start_database_module(cfg: AppConfig) -> None:
    """Platzhalter — wird in Phase 5 durch den InfluxDB-Writer ersetzt."""
    logger.info(
        "[database] Aktiv: url=%s, bucket=%s",
        cfg.database.url,
        cfg.database.bucket,
    )


async def _start_llm_coach_module(cfg: AppConfig) -> None:
    """Platzhalter — wird in Phase 6 durch den RAG-Prompt-Builder ersetzt."""
    logger.info(
        "[llm_coach] Aktiv: backend=%s, model=%s",
        cfg.llm_coach.backend,
        cfg.llm_coach.model or "<nicht gesetzt>",
    )


# Mapping: Modul-Name (wie in config.yaml) -> Startfunktion
_MODULE_STARTERS = {
    "wearable": _start_wearable_module,
    "climate_sensors": _start_climate_module,
    "ml_pipeline": _start_ml_module,
    "database": _start_database_module,
    "llm_coach": _start_llm_coach_module,
}


# ----------------------------------------------------------------------------
# Async-Bootstrap
# ----------------------------------------------------------------------------

async def main(cfg: AppConfig) -> None:
    """
    Hauptschleife der Anwendung.

    Startet für jedes aktivierte Modul eine asynchrone Task. Inaktive Module
    werden zur Transparenz im Log aufgeführt, aber nicht angelegt
    (Graceful Degradation).

    Args:
        cfg: Die geladene und validierte Anwendungs-Konfiguration.
    """
    enabled = cfg.enabled_modules()
    disabled = cfg.disabled_modules()

    logger.info("=" * 64)
    logger.info("Somnoscope — Bootstrap (Phase 1)")
    logger.info("=" * 64)
    logger.info("Aktive Module   : %s", ", ".join(enabled) if enabled else "<keine>")
    logger.info(
        "Inaktive Module : %s", ", ".join(disabled) if disabled else "<keine>"
    )
    logger.info("Zeitzone        : %s", cfg.system.timezone)
    logger.info("Daten-Verz.     : %s", cfg.system.data_dir.resolve())
    logger.info("=" * 64)

    # Tasks für aktive Module sammeln und parallel starten.
    tasks: list[asyncio.Task[None]] = []
    for name in enabled:
        starter = _MODULE_STARTERS.get(name)
        if starter is None:
            logger.warning(
                "Kein Starter für Modul '%s' registriert — wird übersprungen.", name
            )
            continue
        tasks.append(asyncio.create_task(starter(cfg), name=f"start:{name}"))

    if not tasks:
        logger.warning(
            "Keine Module aktiviert. Anwendung beendet sich. "
            "Aktivieren Sie mindestens 'wearable' in config.yaml."
        )
        return

    # Auf alle Starter warten. ``return_exceptions=True`` sorgt dafür, dass
    # ein fehlschlagendes Modul die anderen nicht mitreisst — passend zum
    # Graceful-Degradation-Prinzip.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.exception(
                "Modul-Start '%s' ist fehlgeschlagen: %s", task.get_name(), result
            )

    logger.info("Bootstrap abgeschlossen — Long-Running-Tasks folgen ab Phase 2.")


# ----------------------------------------------------------------------------
# CLI-Einstieg
# ----------------------------------------------------------------------------

def _resolve_config_path(argv: list[str]) -> Path:
    """Wertet das erste CLI-Argument als Config-Pfad aus (Default: ``config.yaml``)."""
    if len(argv) > 1:
        return Path(argv[1])
    return Path("config.yaml")


def _run() -> int:
    """
    Synchrone Wrapper-Funktion, die :func:`main` über ``asyncio.run`` aufruft.

    Returns:
        Den Exit-Code, den der Prozess an die Shell zurückgibt.
    """
    cfg_path = _resolve_config_path(sys.argv)

    try:
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        # Logging ist hier noch nicht initialisiert — wir benutzen ``print``
        # bewusst, weil sonst niemand den Fehler sehen würde.
        print(f"[FATAL] Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    setup_logging(cfg.system.log_level, cfg.system.log_dir)

    try:
        asyncio.run(main(cfg))
    except KeyboardInterrupt:
        logger.info("Abbruch durch Benutzer (SIGINT).")
        return EXIT_INTERRUPTED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(_run())
