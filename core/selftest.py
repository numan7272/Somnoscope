"""
Setup-Selbstdiagnose für Somnoscope (``python main.py --check``).

Prüft die lokale Installation, ohne den Tracker zu starten und ohne Daten
zu verändern:

    * **Config:** Zeitzone, Data-Dir und aktivierte Module aus der geladenen
      :class:`~core.config_loader.AppConfig`.
    * **Optionale Python-Pakete:** Verfügbarkeit via
      :func:`importlib.util.find_spec` (kein Import-Seiteneffekt) — fehlende
      Pakete sind nur eine Warnung (Graceful Degradation, Kernprinzip 2).
    * **Store:** Lesetest gegen die SQLite-DB (``list_reports(1)``) plus
      Schreibbarkeits-Check des ``data_dir`` über eine kurzlebige Temp-Datei.
      Die echte Datenbank wird dabei *nicht* verändert.
    * **Aktive Adapter:** Jeder aktive Wearable-Adapter wird auf seine
      Abhängigkeiten geprüft (``brainflow``/``numpy`` für Muse, das
      ``ghealth``-CLI für Fitbit-Air, keine für die Simulation).
    * **LLM-Coach:** Erreichbarkeit von Ollama mit kurzem Timeout — bei
      Nichterreichbarkeit nur eine Warnung, weil der regelbasierte Fallback
      des Coaches greift.

Design:
    * :func:`run_selftest` wirft **nie** — jeder Check läuft in einem eigenen
      try/except und liefert im Fehlerfall ein ``fail``-Ergebnis mit klarer
      Detail-Meldung.
    * Ausgabe erfolgt über :func:`format_report` als String; das Drucken
      übernimmt ``main.py`` (Konvention: ``logging`` statt ``print`` in
      Modulen).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import shutil
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.constants import (
    ADAPTER_EEG_MUSE,
    ADAPTER_FITBIT_GH_API,
    ADAPTER_SIMULATION,
)

if TYPE_CHECKING:  # nur für Type-Checker, kein Laufzeit-Import-Zyklus
    from core.config_loader import AppConfig

logger = logging.getLogger(__name__)

__all__ = ["CheckResult", "run_selftest", "format_report", "selftest_exit_code"]

# ----------------------------------------------------------------------------
# Status-Werte und Anzeige-Marker
# ----------------------------------------------------------------------------

#: Check erfolgreich — alles einsatzbereit.
STATUS_OK = "ok"
#: Check nicht erfolgreich, aber unkritisch (optionales Feature degradiert).
STATUS_WARN = "warn"
#: Check fehlgeschlagen — die betroffene Funktion wird nicht laufen.
STATUS_FAIL = "fail"

#: Alle gültigen Status-Werte eines :class:`CheckResult`.
VALID_STATUSES: frozenset[str] = frozenset({STATUS_OK, STATUS_WARN, STATUS_FAIL})

#: Text-Marker pro Status für :func:`format_report`.
_MARKERS: dict[str, str] = {
    STATUS_OK: "[OK]",
    STATUS_WARN: "[WARN]",
    STATUS_FAIL: "[FAIL]",
}

#: Optionale Python-Pakete: (Modulname für find_spec, pip-Paketname).
#: Fehlende Pakete sind *warn*, nicht *fail* — Graceful Degradation.
_OPTIONAL_PACKAGES: tuple[tuple[str, str], ...] = (
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("mne", "mne"),
    ("yasa", "yasa"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("paho.mqtt.client", "paho-mqtt"),
    ("influxdb_client", "influxdb-client"),
    ("brainflow", "brainflow"),
)

#: Timeout (Sekunden) für den Ollama-Erreichbarkeits-Check — bewusst kurz,
#: damit ``--check`` nicht hängt, wenn kein Ollama läuft.
_OLLAMA_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class CheckResult:
    """
    Ergebnis eines einzelnen Diagnose-Checks.

    Attributes:
        name: Kurzname des Checks (z.B. ``"Store (Lesetest)"``).
        status: Einer der Werte ``"ok"``, ``"warn"`` oder ``"fail"``.
        detail: Menschlich lesbare Detail-Meldung (was wurde geprüft, was
            ist das Ergebnis, ggf. wie behebt man das Problem).
    """

    name: str
    status: str
    detail: str


# ----------------------------------------------------------------------------
# Öffentliche API
# ----------------------------------------------------------------------------

async def run_selftest(cfg: AppConfig) -> list[CheckResult]:
    """
    Führt alle Setup-Checks aus und sammelt die Ergebnisse.

    Jeder Check-Block läuft in einem eigenen try/except: Ein unerwarteter
    Fehler in einem Check erzeugt ein ``fail``-Ergebnis mit der
    Exception-Meldung, statt die Diagnose abzubrechen. Diese Funktion
    wirft daher nie.

    Args:
        cfg: Die bereits geladene Anwendungskonfiguration.

    Returns:
        Liste aller :class:`CheckResult` in Ausführungsreihenfolge.

    Seiteneffekte:
        Legt ``cfg.system.data_dir`` an (falls nötig), öffnet die SQLite-DB
        lesend und schreibt/löscht eine kurzlebige Temp-Datei im ``data_dir``.
        Es werden keine Reports geschrieben oder verändert.
    """
    checks: tuple[tuple[str, Callable[[], list[CheckResult] | Awaitable[list[CheckResult]]]], ...] = (
        ("Konfiguration", lambda: _check_config(cfg)),
        ("Optionale Pakete", _check_optional_packages),
        ("Store", lambda: _check_store(cfg)),
        ("Aktive Adapter", lambda: _check_adapters(cfg)),
        ("LLM-Coach", lambda: _check_llm_coach(cfg)),
    )

    results: list[CheckResult] = []
    for label, check in checks:
        try:
            outcome = check()
            if isinstance(outcome, Awaitable):
                outcome = await outcome
            results.extend(outcome)
        except Exception as exc:  # noqa: BLE001 — Selbstdiagnose darf nie reissen
            logger.exception("Selftest-Check '%s' ist unerwartet fehlgeschlagen.", label)
            results.append(
                CheckResult(
                    name=label,
                    status=STATUS_FAIL,
                    detail=f"Unerwarteter Fehler: {exc.__class__.__name__}: {exc}",
                )
            )
    return results


def format_report(results: list[CheckResult]) -> str:
    """
    Formatiert die Check-Ergebnisse als mehrzeiligen Text-Report.

    Das Modul druckt selbst nichts (Konvention: ``logging`` statt ``print``) —
    der Aufrufer (``main.py``) gibt den String aus.

    Args:
        results: Die Ergebnisse aus :func:`run_selftest`.

    Returns:
        Mehrzeiliger Report mit ``[OK]``/``[WARN]``/``[FAIL]``-Markern pro
        Check und einer Kurz-Zusammenfassung (Anzahl ok/warn/fail) am Ende.
    """
    counts = Counter(r.status for r in results)
    width = 60
    lines = [
        "Somnoscope — Setup-Selbstdiagnose",
        "=" * width,
    ]
    for result in results:
        marker = _MARKERS.get(result.status, "[????]")
        lines.append(f"{marker:<7} {result.name}: {result.detail}")
    lines.append("-" * width)
    lines.append(
        "Zusammenfassung: "
        f"{counts.get(STATUS_OK, 0)} ok, "
        f"{counts.get(STATUS_WARN, 0)} warn, "
        f"{counts.get(STATUS_FAIL, 0)} fail"
    )
    return "\n".join(lines)


def selftest_exit_code(results: list[CheckResult]) -> int:
    """
    Leitet den Prozess-Exit-Code aus den Check-Ergebnissen ab.

    Args:
        results: Die Ergebnisse aus :func:`run_selftest`.

    Returns:
        ``0``, wenn kein Check ``fail`` meldet (Warnungen sind ok),
        sonst ``1``.
    """
    return 1 if any(r.status == STATUS_FAIL for r in results) else 0


# ----------------------------------------------------------------------------
# Einzelne Checks
# ----------------------------------------------------------------------------

def _check_config(cfg: AppConfig) -> list[CheckResult]:
    """
    Prüft die geladene Konfiguration und listet aktive Module.

    Da ``cfg`` bereits als validiertes :class:`AppConfig` übergeben wird,
    ist das Laden selbst schon gelungen — hier werden die Eckdaten
    (Zeitzone, Data-Dir) genannt und pro aktivem Modul eine Info-Zeile
    erzeugt.

    Args:
        cfg: Die geladene Anwendungskonfiguration.

    Returns:
        Ein ``ok``-Ergebnis für die Config plus eines pro aktivem Modul.
    """
    results = [
        CheckResult(
            name="Config",
            status=STATUS_OK,
            detail=(
                f"geladen — Zeitzone '{cfg.system.timezone}', "
                f"Data-Dir '{cfg.system.data_dir}'"
            ),
        )
    ]
    for module in cfg.enabled_modules():
        results.append(
            CheckResult(
                name=f"Modul '{module}'",
                status=STATUS_OK,
                detail="aktiviert (config.yaml)",
            )
        )
    disabled = cfg.disabled_modules()
    if disabled:
        results.append(
            CheckResult(
                name="Inaktive Module",
                status=STATUS_OK,
                detail=", ".join(disabled) + " (bewusst deaktiviert — kein Problem)",
            )
        )
    return results


def _check_optional_packages() -> list[CheckResult]:
    """
    Prüft die Verfügbarkeit optionaler Python-Pakete.

    Nutzt :func:`importlib.util.find_spec`, damit kein Paket tatsächlich
    importiert wird (keine Import-Seiteneffekte, kein Startzeit-Malus).
    Fehlende Pakete sind nur ``warn`` — sie schalten einzelne Features ab,
    verhindern aber nicht den Betrieb (Graceful Degradation).

    Returns:
        Ein Ergebnis pro Paket aus ``_OPTIONAL_PACKAGES``.
    """
    results: list[CheckResult] = []
    for module_name, pip_name in _OPTIONAL_PACKAGES:
        if _module_available(module_name):
            results.append(
                CheckResult(
                    name=f"Paket '{pip_name}'",
                    status=STATUS_OK,
                    detail="installiert",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"Paket '{pip_name}'",
                    status=STATUS_WARN,
                    detail=(
                        f"nicht installiert (optional) — bei Bedarf: "
                        f"pip install {pip_name}"
                    ),
                )
            )
    return results


async def _check_store(cfg: AppConfig) -> list[CheckResult]:
    """
    Prüft Persistenz-Schicht und Schreibbarkeit des Datenverzeichnisses.

    Ablauf:
        1. ``create_store(cfg)`` + ``await store.list_reports(1)`` als
           reiner Lesetest gegen die SQLite-DB (keine Mutation).
        2. Temp-Datei in ``cfg.system.data_dir`` schreiben und sofort
           löschen, um Schreibrechte zu verifizieren.

    Args:
        cfg: Die geladene Anwendungskonfiguration.

    Returns:
        Zwei Ergebnisse: Store-Lesetest und Data-Dir-Schreibtest;
        Fehler ergeben jeweils ``fail``.

    Seiteneffekte:
        Legt ``data_dir`` (und die DB-Datei, falls noch nicht vorhanden)
        an; die Temp-Datei wird wieder gelöscht.
    """
    results: list[CheckResult] = []
    data_dir = Path(cfg.system.data_dir)

    # 1) Lesetest gegen den Store (lazy Import: fehlendes Modul -> fail statt Crash).
    store: Any = None
    try:
        from database import create_store

        store = create_store(cfg)
        reports = await store.list_reports(1)
        results.append(
            CheckResult(
                name="Store (Lesetest)",
                status=STATUS_OK,
                detail=(
                    f"SQLite-DB unter '{data_dir}' lesbar "
                    f"({len(reports)} Report(s) abgefragt)"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Selftest: Store-Lesetest fehlgeschlagen.")
        results.append(
            CheckResult(
                name="Store (Lesetest)",
                status=STATUS_FAIL,
                detail=f"Lesetest fehlgeschlagen: {exc.__class__.__name__}: {exc}",
            )
        )
    finally:
        if store is not None:
            try:
                await store.close()
            except Exception:  # noqa: BLE001 — close darf die Diagnose nicht reissen
                logger.debug("Selftest: store.close() fehlgeschlagen.", exc_info=True)

    # 2) Schreibtest: kurzlebige Temp-Datei im data_dir (schreiben + löschen).
    probe = data_dir / f".selftest_{uuid.uuid4().hex}.tmp"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("somnoscope selftest", encoding="utf-8")
        results.append(
            CheckResult(
                name="Data-Dir (Schreibtest)",
                status=STATUS_OK,
                detail=f"'{data_dir}' ist beschreibbar",
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Selftest: Data-Dir-Schreibtest fehlgeschlagen.")
        results.append(
            CheckResult(
                name="Data-Dir (Schreibtest)",
                status=STATUS_FAIL,
                detail=(
                    f"'{data_dir}' ist nicht beschreibbar: "
                    f"{exc.__class__.__name__}: {exc}"
                ),
            )
        )
    finally:
        # Aufräumen darf das Ergebnis nicht verfälschen (z.B. AV-Lock beim unlink):
        # der Schreibtest gilt schon als bestanden, sobald write_text durchlief.
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "Selftest: Temp-Datei %s konnte nicht entfernt werden.",
                probe,
                exc_info=True,
            )
    return results


def _check_adapters(cfg: AppConfig) -> list[CheckResult]:
    """
    Prüft die Abhängigkeiten aller *aktiven* Wearable-Adapter.

    Regeln:
        * ``simulation`` — hardware-frei, keine Abhängigkeiten -> immer ``ok``.
        * ``eeg_muse`` — benötigt ``brainflow`` und ``numpy``; fehlt eines,
          ist der aktiv konfigurierte Adapter nicht lauffähig -> ``fail``.
        * ``fitbit_gh_api`` — benötigt das ``ghealth``-CLI (Name/Pfad aus
          ``options['ghealth_bin']``, Default ``"ghealth"``) im PATH ->
          sonst ``fail``.
        * Unbekannte Typen -> ``warn`` (die Factory überspringt sie ohnehin).

    Args:
        cfg: Die geladene Anwendungskonfiguration.

    Returns:
        Ein Ergebnis pro aktivem Adapter; eine Warnung, wenn gar keiner
        aktiv ist.
    """
    active = cfg.wearable.active_adapters() if cfg.wearable.enabled else []
    if not active:
        return [
            CheckResult(
                name="Wearable-Adapter",
                status=STATUS_WARN,
                detail=(
                    "kein aktiver Adapter — ohne Datenquelle entstehen keine "
                    "SleepReports (Tipp: 'simulation' in config.yaml aktivieren)"
                ),
            )
        ]

    results: list[CheckResult] = []
    for entry in active:
        name = f"Adapter '{entry.type}'"
        if entry.type == ADAPTER_SIMULATION:
            results.append(
                CheckResult(
                    name=name,
                    status=STATUS_OK,
                    detail="hardware-frei, keine zusätzlichen Abhängigkeiten",
                )
            )
        elif entry.type == ADAPTER_EEG_MUSE:
            missing = [
                pkg for pkg in ("brainflow", "numpy") if not _module_available(pkg)
            ]
            if missing:
                results.append(
                    CheckResult(
                        name=name,
                        status=STATUS_FAIL,
                        detail=(
                            "aktiv, aber Abhängigkeiten fehlen: "
                            + ", ".join(missing)
                            + f" (pip install {' '.join(missing)})"
                        ),
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name=name,
                        status=STATUS_OK,
                        detail="brainflow und numpy verfügbar",
                    )
                )
        elif entry.type == ADAPTER_FITBIT_GH_API:
            ghealth_bin = str(entry.options.get("ghealth_bin", "ghealth"))
            resolved = shutil.which(ghealth_bin)
            if resolved is None:
                results.append(
                    CheckResult(
                        name=name,
                        status=STATUS_FAIL,
                        detail=(
                            f"aktiv, aber CLI '{ghealth_bin}' nicht im PATH "
                            "gefunden (siehe docs/fitbit_air_setup.md)"
                        ),
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name=name,
                        status=STATUS_OK,
                        detail=f"CLI '{ghealth_bin}' gefunden: {resolved}",
                    )
                )
        else:
            results.append(
                CheckResult(
                    name=name,
                    status=STATUS_WARN,
                    detail=(
                        "unbekannter Adapter-Typ — wird von der Factory "
                        "übersprungen (Tippfehler in config.yaml?)"
                    ),
                )
            )
    return results


async def _check_llm_coach(cfg: AppConfig) -> list[CheckResult]:
    """
    Prüft die Erreichbarkeit von Ollama, falls der LLM-Coach aktiv ist.

    Der Coach hat einen regelbasierten Fallback — ein nicht erreichbares
    Ollama ist deshalb nur ``warn``, nie ``fail``. Der HTTP-GET auf
    ``<ollama_url>/api/tags`` läuft blocking (``urllib``) und wird darum via
    :func:`asyncio.to_thread` mit kurzem Timeout ausgeführt, damit
    ``--check`` nicht hängt.

    Args:
        cfg: Die geladene Anwendungskonfiguration.

    Returns:
        Genau ein Ergebnis, wenn ``llm_coach`` aktiv ist; sonst leere Liste.
    """
    if "llm_coach" not in cfg.enabled_modules():
        return []

    url = cfg.llm_coach.ollama_url.rstrip("/") + "/api/tags"
    try:
        # Harte Obergrenze um den blocking Probe: das urlopen-Timeout deckt die
        # DNS-Auflösung nicht zuverlässig ab, wait_for garantiert, dass --check
        # nie länger als ~Timeout+1s an diesem Check hängt.
        await asyncio.wait_for(
            asyncio.to_thread(_probe_ollama, url),
            timeout=_OLLAMA_TIMEOUT_S + 1.0,
        )
        return [
            CheckResult(
                name="LLM-Coach (Ollama)",
                status=STATUS_OK,
                detail=f"Ollama erreichbar unter {cfg.llm_coach.ollama_url}",
            )
        ]
    except urllib.error.HTTPError as exc:
        # Ein HTTP-Fehlerstatus (z.B. 404) heisst: der Server antwortet.
        return [
            CheckResult(
                name="LLM-Coach (Ollama)",
                status=STATUS_OK,
                detail=(
                    f"Ollama erreichbar unter {cfg.llm_coach.ollama_url} "
                    f"(HTTP {exc.code} auf /api/tags)"
                ),
            )
        ]
    except Exception as exc:  # noqa: BLE001 — URLError, Timeout, ConnRefused, ...
        logger.debug("Selftest: Ollama nicht erreichbar (%s).", exc, exc_info=True)
        return [
            CheckResult(
                name="LLM-Coach (Ollama)",
                status=STATUS_WARN,
                detail=(
                    f"Ollama unter {cfg.llm_coach.ollama_url} nicht erreichbar "
                    f"({exc.__class__.__name__}) — der regelbasierte Fallback "
                    "des Coaches greift"
                ),
            )
        ]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _module_available(module_name: str) -> bool:
    """
    Prüft, ob ein Modul importierbar wäre — ohne es zu importieren.

    Args:
        module_name: Voller Modulname (auch gepunktet, z.B.
            ``"paho.mqtt.client"``).

    Returns:
        ``True``, wenn :func:`importlib.util.find_spec` einen Spec findet.
        Fehler bei der Suche (z.B. fehlendes/kaputtes Eltern-Paket) gelten als
        „nicht verfügbar" — ein Defekt in einem einzelnen optionalen Paket darf
        die gesamte Paket-Prüfung nicht zum Absturz bringen.
    """
    try:
        # Bei gepunkteten Namen zuerst das Top-Level-Paket prüfen: find_spec auf
        # ein Submodul importiert dessen Eltern-Pakete — ein defektes Eltern-
        # Paket könnte dabei eine beliebige Exception werfen.
        top_level = module_name.split(".", 1)[0]
        if importlib.util.find_spec(top_level) is None:
            return False
        return importlib.util.find_spec(module_name) is not None
    except Exception:  # noqa: BLE001 — jeder Fehler = „nicht verfügbar", nie ein Crash
        logger.debug("Selftest: find_spec(%r) fehlgeschlagen.", module_name, exc_info=True)
        return False


def _probe_ollama(url: str) -> None:
    """
    Blockierender Erreichbarkeits-Check (GET) — nur via ``asyncio.to_thread``.

    Args:
        url: Voll qualifizierte URL, z.B. ``http://localhost:11434/api/tags``.

    Raises:
        urllib.error.URLError: Wenn der Server nicht erreichbar ist.
        TimeoutError: Wenn das 2-Sekunden-Timeout überschritten wird.
    """
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=_OLLAMA_TIMEOUT_S):
        pass
