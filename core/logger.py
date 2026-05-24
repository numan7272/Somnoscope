"""
Zentrales Logging-Setup für Somnoscope.

Dieses Modul kapselt die Initialisierung des Python-``logging``-Subsystems an
**einer einzigen Stelle**. Andere Module rufen einfach ``logging.getLogger(__name__)``
auf und müssen sich um Handler, Formatter und Pfade nicht kümmern.

Designentscheidungen:
    * **Whitebox-Prinzip:** Jeder Verarbeitungsschritt soll nachvollziehbar
      geloggt werden — daher schreiben wir parallel auf Konsole *und* in eine
      rotierende Datei. Die Konsole zeigt menschenlesbare Ausgaben, die
      Logdatei ist für späteres Debugging gedacht.
    * **Keine** ``logging.basicConfig``-Calls: wir setzen Handler explizit, um
      Doppelregistrierung beim mehrfachen Import zu vermeiden.
    * **ISO-8601-Timestamps:** notwendig, damit Logs später mit InfluxDB-Daten
      synchronisiert werden können.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

# Format der Logzeilen. Bewusst ausführlich, damit man auch in einer großen
# Log-Datei schnell sieht, *welches* Modul *was* zu welcher Zeit gemacht hat.
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Rotation-Defaults: 10 MB pro Datei, 5 Backups → max. 50 MB Plattenplatz.
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

# Wir tracken, ob ``setup_logging`` schon einmal aufgerufen wurde, damit
# wiederholte Aufrufe (z.B. in Tests) nicht jedes Mal neue Handler anhängen.
_initialized: bool = False


def setup_logging(level: str, log_dir: Path) -> None:
    """
    Initialisiert das anwendungsweite Logging.

    Richtet zwei Handler ein:
        * einen ``StreamHandler`` für stdout (für interaktive Nutzung),
        * einen ``RotatingFileHandler``, der nach ``log_dir/somnoscope.log`` schreibt.

    Args:
        level: Log-Level als String (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
            ``"ERROR"`` oder ``"CRITICAL"``). Wird via ``logging.getLevelName``
            in den numerischen Level umgesetzt.
        log_dir: Verzeichnis für die Log-Datei. Wird angelegt, falls es noch
            nicht existiert.

    Raises:
        ValueError: Wenn ``level`` kein gültiges Log-Level ist.

    Seiteneffekte:
        * Modifiziert den Root-Logger (Handler werden hinzugefügt).
        * Legt ``log_dir`` an, falls nicht vorhanden.
    """
    global _initialized

    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(
            f"Ungültiges Log-Level: '{level}'. "
            "Erlaubt sind DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "somnoscope.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Bei einem erneuten Aufruf (z.B. in Tests oder nach Config-Reload) zuerst
    # die alten Handler entfernen, damit nichts doppelt geloggt wird.
    if _initialized:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)
    root_logger.addHandler(file_handler)

    _initialized = True

    # Erste Zeile in der frisch aufgesetzten Log-Datei. Macht das Debuggen
    # einfacher, weil sofort klar ist, *welcher* Prozess hier loggt.
    logging.getLogger(__name__).info(
        "Logging initialisiert (level=%s, file=%s)", level.upper(), log_file
    )
