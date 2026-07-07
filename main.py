"""
Somnoscope — Einstiegspunkt.

Verantwortlichkeiten dieses Skripts:
    1. ``config.yaml`` über :func:`core.config_loader.load_config` einlesen.
    2. Anwendungsweites Logging über :func:`core.logger.setup_logging` initialisieren.
    3. Die aktiven Wearable-Adapter hochfahren und ihre Daten über die
       :class:`core.pipeline.SleepPipeline` zu SleepReports verdichten, die im
       lokalen Store landen (und damit im Dashboard sichtbar werden).

Ausführung::

    python main.py                 # Dauerbetrieb (Tracker-Daemon)
    python main.py --once          # genau ein Poll-Zyklus (z.B. „letzte Nacht
                                   # verarbeiten"), dann beenden — ideal für Cron/CI
    python main.py path/to/other-config.yaml

Das Dashboard wird separat gestartet::

    uvicorn webui.app:app          # http://127.0.0.1:8000
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
# Async-Bootstrap
# ----------------------------------------------------------------------------

async def main(cfg: AppConfig, *, once: bool = False) -> None:
    """
    Hauptschleife der Anwendung.

    Baut Store + Pipeline, instanziiert die aktiven Wearable-Adapter und lässt
    sie laufen. Im ``once``-Modus wird pro Adapter genau ein Poll-Zyklus
    verarbeitet und dann beendet; sonst laufen die Adapter im Dauerbetrieb.

    Args:
        cfg: Die geladene und validierte Anwendungs-Konfiguration.
        once: Wenn ``True``, nur ein einzelner Poll-Zyklus (dann Rückkehr).

    Seiteneffekte:
        Öffnet den lokalen SleepReport-Store und schreibt Reports hinein.
    """
    # Lokale Imports halten den reinen Config-/Logging-Pfad importschlank und
    # vermeiden, dass ein Fehler in einem optionalen Modul den Start blockiert.
    from adapters import create_adapters
    from core.pipeline import SleepPipeline
    from database import create_store

    enabled = cfg.enabled_modules()
    disabled = cfg.disabled_modules()

    logger.info("=" * 64)
    logger.info("Somnoscope — %s", "Einzeldurchlauf" if once else "Dauerbetrieb")
    logger.info("=" * 64)
    logger.info("Aktive Module   : %s", ", ".join(enabled) if enabled else "<keine>")
    logger.info("Inaktive Module : %s", ", ".join(disabled) if disabled else "<keine>")
    logger.info("Zeitzone        : %s", cfg.system.timezone)
    logger.info("Daten-Verz.     : %s", cfg.system.data_dir.resolve())
    logger.info("=" * 64)

    if not cfg.wearable.enabled:
        logger.warning(
            "wearable.enabled=false — ohne Wearable liefert das System keine "
            "Schlafdaten. Aktivieren Sie das Modul in config.yaml."
        )
        return

    adapters = create_adapters(cfg)
    if not adapters:
        logger.warning(
            "[wearable] Kein lauffähiger Adapter aktiv — es werden keine "
            "Schlafdaten erfasst. Siehe config.yaml (wearable.adapters)."
        )
        return

    store = create_store(cfg)

    # Optionale Klima-Sensorfusion (ESP32 via MQTT): der Puffer geht an die
    # Pipeline, der Subscriber läuft im Dauerbetrieb als eigene Task.
    climate_buffer = None
    subscriber = None
    if cfg.climate_sensors.enabled:
        try:
            from iot import ClimateBuffer, MqttSubscriber

            climate_buffer = ClimateBuffer()
            subscriber = MqttSubscriber(cfg.climate_sensors, climate_buffer)
        except Exception:  # noqa: BLE001 — Klima ist optional, darf den Start nicht verhindern
            logger.exception(
                "[climate_sensors] Initialisierung fehlgeschlagen — ohne Klima weiter."
            )
            climate_buffer = None
            subscriber = None

    pipeline = SleepPipeline(cfg, store, climate_buffer=climate_buffer)
    logger.info(
        "[pipeline] %d Adapter aktiv (%s) — Persistenz: SQLite (lokal), Klima: %s",
        len(adapters),
        ", ".join(a.name for a in adapters),
        "aktiv" if subscriber is not None else "aus",
    )

    try:
        if once:
            await asyncio.gather(*(pipeline.run_once(a) for a in adapters))
            logger.info("[pipeline] Einzeldurchlauf abgeschlossen.")
        else:
            # Dauerbetrieb: läuft bis Strg-C. return_exceptions=True, damit eine
            # unerwartet sterbende Task die anderen nicht mitreisst (Graceful
            # Degradation) und der Store erst nach allen schliesst.
            runners = [pipeline.run(a) for a in adapters]
            labels = [a.name for a in adapters]
            if subscriber is not None:
                logger.info(
                    "[climate_sensors] MQTT-Subscriber wird gestartet (broker=%s:%d).",
                    cfg.climate_sensors.broker_host,
                    cfg.climate_sensors.broker_port,
                )
                runners.append(subscriber.run())
                labels.append("mqtt_subscriber")

            results = await asyncio.gather(*runners, return_exceptions=True)
            for label, res in zip(labels, results):
                if isinstance(res, Exception) and not isinstance(
                    res, asyncio.CancelledError
                ):
                    logger.error(
                        "[pipeline] Task '%s' unerwartet beendet.",
                        label,
                        exc_info=res,
                    )
    finally:
        if subscriber is not None:
            await subscriber.close()
        await pipeline.aclose()
        await store.close()


# ----------------------------------------------------------------------------
# CLI-Einstieg
# ----------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> tuple[Path, bool]:
    """
    Wertet die CLI-Argumente aus.

    Unterstützt das Flag ``--once`` (einmaliger Poll) und einen optionalen
    Config-Pfad (erstes Nicht-Flag-Argument).

    Args:
        argv: ``sys.argv`` (inkl. Programmname an Index 0).

    Returns:
        Tupel aus Config-Pfad (Default ``config.yaml``) und ``once``-Flag.
    """
    once = False
    config_path = Path("config.yaml")
    for arg in argv[1:]:
        if arg in ("--once", "-1"):
            once = True
        elif not arg.startswith("-"):
            config_path = Path(arg)
    return config_path, once


def _run() -> int:
    """
    Synchrone Wrapper-Funktion, die :func:`main` über ``asyncio.run`` aufruft.

    Returns:
        Den Exit-Code, den der Prozess an die Shell zurückgibt.
    """
    cfg_path, once = _parse_args(sys.argv)

    try:
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        # Logging ist hier noch nicht initialisiert — wir benutzen ``print``
        # bewusst, weil sonst niemand den Fehler sehen würde.
        print(f"[FATAL] Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    setup_logging(cfg.system.log_level, cfg.system.log_dir)

    try:
        asyncio.run(main(cfg, once=once))
    except KeyboardInterrupt:
        logger.info("Abbruch durch Benutzer (SIGINT).")
        return EXIT_INTERRUPTED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(_run())
