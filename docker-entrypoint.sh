#!/bin/sh
# ============================================================================
# Somnoscope — Container-Entrypoint
# ============================================================================
# Ein Image, mehrere Rollen: Dasselbe Image dient als Web-Dashboard UND als
# Tracker-Daemon. Welches Subkommando läuft, entscheidet das erste Argument
# (in docker-compose.yml via `command:` gesetzt, Default ist "web").
#
#   web            -> uvicorn webui.app:app  (Dashboard auf 0.0.0.0:8000)
#   tracker [...]  -> python main.py [...]   (z.B. `tracker --once` oder
#                                             `tracker --backfill 14`)
#   <alles andere> -> wird 1:1 ausgeführt (z.B. `python main.py --once`,
#                     `pytest` oder eine Shell für Debugging)
#
# `exec` ersetzt die Shell durch den Zielprozess: Der bekommt PID 1 und damit
# SIGTERM von `docker stop` direkt — sauberes Herunterfahren ohne Zombie-Shell.
# ============================================================================
set -e

case "$1" in
    web)
        # Dashboard: 0.0.0.0 ist im Container nötig, damit das gemappte
        # Port-Forwarding (8000:8000) den Prozess überhaupt erreicht.
        # Von außen bleibt es lokal — Somnoscope publisht den Port nur auf
        # dem Host, es gibt keine Cloud-Komponente.
        exec uvicorn webui.app:app --host 0.0.0.0 --port 8000
        ;;
    tracker)
        # Tracker-Daemon; weitere Argumente (z.B. --once, --backfill N)
        # werden an main.py durchgereicht.
        shift
        exec python main.py "$@"
        ;;
    *)
        # Fallback: unbekannte Kommandos unverändert ausführen.
        exec "$@"
        ;;
esac
