# Mitmachen bei Somnoscope

Danke, dass du Somnoscope verbessern möchtest. Damit das Repo langfristig sauber bleibt
(und damit andere deine Fixes / Features auch tatsächlich nachvollziehen können), halten
wir uns an ein paar feste Regeln.

## Issue zuerst

**Jede Code-Änderung beginnt mit einem Issue** — auch ein vermeintlich winziger Bug.
Das hat zwei Gründe:

1. Die Issue-Historie ist die einzige Stelle, an der wir später nachlesen können,
   *warum* eine Sache so geworden ist, wie sie ist.
2. Doppelarbeit lässt sich vermeiden, wenn ein Vorhaben sichtbar ist.

Issue-Templates findest du unter [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/):

- **Bug Report:** für reproduzierbare Fehler
- **Feature Request:** für neue Funktionen / Module
- Für offene Fragen bitte die GitHub-Discussions nutzen, nicht Issues

## Branch-Namen

Format: `<typ>/issue-<nr>-<kurztitel>`

| Typ      | Wann                                              |
|----------|---------------------------------------------------|
| `fix`    | Bug-Fix                                           |
| `feat`   | Neues Feature / Modul                             |
| `refac`  | Refactoring ohne Verhaltensänderung               |
| `docs`   | Nur Dokumentation                                 |
| `chore`  | Wartung (Dependencies, CI, Skripte)               |

Beispiele:

```
fix/issue-12-mqtt-reconnect-loop
feat/issue-23-fitbit-ble-adapter
docs/issue-7-architektur-diagramm
```

## Pull Requests

- **Ein PR pro Issue.** Wenn dein PR mehrere Issues schließt, liegt das vermutlich
  daran, dass die Issues zu klein geschnitten waren — kein Drama, einfach erwähnen.
- **PR-Beschreibung folgt dem [Template](.github/pull_request_template.md):**
  was geändert wurde, warum, wie getestet.
- **`Closes #<nr>`** im PR-Body, damit GitHub den Issue beim Merge automatisch
  verlinkt — aber **nicht automatisch schließt**, solange wir das nicht explizit
  bestätigt haben (siehe nächster Punkt).

## Issue erst beim Merge schließen — nie beim PR-Open

Wenn der PR offen ist, ist der Bug *noch nicht* gelöst. Wir schließen Issues
manuell mit einem **Kommentar, der die Root Cause + die gewählte Lösung
zusammenfasst**. Das ist das wichtigste Artefakt für die Zukunft.

Schlechtes Beispiel:

> Fixed in #42. 👍

Gutes Beispiel:

> **Root Cause:** Der MQTT-Client hat auf `on_disconnect` keinen Backoff angewendet
> und damit den Broker mit Reconnect-Versuchen geflutet.
>
> **Lösung:** Exponential Backoff in `iot/mqtt_subscriber.py` eingeführt, Cap bei
> 60 Sekunden. Erweitert um Logging der Reconnect-Versuche, damit Phase-5-Debug
> einfacher wird.
>
> **Verifiziert mit:** lokalem Mosquitto + erzwungenem Disconnect via
> `mosquitto_pub -t '#' -m 'kill'`. Logfile zeigt jetzt sauberen Backoff.

## Code-Konventionen

Detailliert in [CLAUDE.md](CLAUDE.md). Kurzfassung:

- **Python 3.10+**, `asyncio` für alle I/O-Pfade
- **Type-Hints** konsequent (`str | None` statt `Optional[str]`)
- **Docstrings auf Deutsch**, Google-Style, mit Zweck / Args / Returns / Seiteneffekten
- **Logging statt print** (Ausnahme: CLI-Bootstrap in `main.py`)
- **Keine** Cloud-API-Calls für Gesundheitsdaten

## Tests

Phase 1 hat noch keine Tests — ab Phase 2 erwarten wir `pytest`-Tests für jeden
neuen Parser/Adapter. Pfad: `tests/` (wird angelegt, wenn der erste Test entsteht).

## Setup vor dem ersten Commit

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
python main.py                     # Sollte ohne Fehler durchlaufen
```

## Fragen?

Issue oder Discussion öffnen — wir antworten so schnell wie möglich.
