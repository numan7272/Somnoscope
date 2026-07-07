"""
Adapter-Registry und Factory.

Übersetzt die in ``config.yaml`` unter ``wearable.adapters[]`` konfigurierten
Adapter-Typen in konkrete :class:`~adapters.base_wearable.WearableAdapter`-
Instanzen. Nur *aktive* (``enabled: true``) und *unterstützte* Adapter werden
instanziiert — alles andere wird mit klarer Log-Meldung übersprungen
(Graceful Degradation, Kernprinzip 2).

Der Import der konkreten Adapter-Klasse geschieht **lazy** (erst bei Bedarf),
damit ein Adapter mit noch fehlender optionaler Abhängigkeit (z.B. ``brainflow``
für Muse) nicht den Start des ganzen Systems verhindert.
"""

from __future__ import annotations

import importlib
import logging

from core.config_loader import AppConfig
from core.constants import (
    ADAPTER_EEG_MUSE,
    ADAPTER_FITBIT_BLE,
    ADAPTER_FITBIT_GH_API,
    ADAPTER_SIMULATION,
)

from .base_wearable import WearableAdapter, WearableReading

logger = logging.getLogger("somnoscope.adapters")

__all__ = ["WearableAdapter", "WearableReading", "create_adapters"]

#: Registry: Adapter-Typ -> (Modulname, Klassenname). Lazy aufgelöst.
_REGISTRY: dict[str, tuple[str, str]] = {
    ADAPTER_FITBIT_GH_API: ("adapters.fitbit_gh_api", "FitbitGoogleHealthAdapter"),
    ADAPTER_EEG_MUSE: ("adapters.eeg_muse", "MuseEEGAdapter"),
    # Simulation: Hardware-freie Testquelle, damit das System end-to-end läuft.
    ADAPTER_SIMULATION: ("adapters.simulation", "SimulationAdapter"),
}

#: Adapter-Typen, die einen SleepReport erzeugen (Schlafphasen liefern). Sind
#: mehrere gleichzeitig aktiv, teilen sie sich den Report-Schlüssel 'date'.
_REPORT_PRODUCING: frozenset[str] = frozenset(
    {ADAPTER_SIMULATION, ADAPTER_FITBIT_GH_API, ADAPTER_EEG_MUSE}
)

#: Typen, die es (noch) nicht als lokalen Adapter geben kann — mit Begründung.
_UNSUPPORTED: dict[str, str] = {
    ADAPTER_FITBIT_BLE: (
        "Die BLE-Payload des Fitbit Air ist Ende-zu-Ende verschlüsselt und nur "
        "über Googles Cloud entschlüsselbar — ein lokaler BLE-Read ist unmöglich. "
        "Nutze stattdessen den optionalen 'fitbit_gh_api'-Adapter (Cloud-API)."
    ),
}


def create_adapters(cfg: AppConfig) -> list[WearableAdapter]:
    """
    Instanziiert alle aktiven, unterstützten Wearable-Adapter aus der Config.

    Args:
        cfg: Die geladene Anwendungs-Konfiguration.

    Returns:
        Liste startklarer Adapter-Instanzen. Kann leer sein, wenn kein Adapter
        aktiv/unterstützt/importierbar ist — der Aufrufer behandelt das als
        „nichts zu tun".
    """
    adapters: list[WearableAdapter] = []
    for entry in cfg.wearable.active_adapters():
        atype = entry.type

        if atype in _UNSUPPORTED:
            logger.error(
                "[factory] Adapter-Typ '%s' wird nicht unterstützt: %s",
                atype,
                _UNSUPPORTED[atype],
            )
            continue

        target = _REGISTRY.get(atype)
        if target is None:
            logger.warning(
                "[factory] Unbekannter Adapter-Typ '%s' — übersprungen. Bekannt: %s",
                atype,
                ", ".join(sorted(_REGISTRY)),
            )
            continue

        module_name, class_name = target
        try:
            module = importlib.import_module(module_name)
            adapter_cls = getattr(module, class_name)
        except Exception:  # noqa: BLE001 — fehlende opt. Dependency darf nicht reissen
            logger.exception(
                "[factory] Adapter '%s' konnte nicht geladen werden "
                "(fehlt eine optionale Abhängigkeit?) — übersprungen.",
                atype,
            )
            continue

        try:
            adapters.append(adapter_cls(entry.options, cfg.system.timezone))
        except Exception:  # noqa: BLE001
            logger.exception(
                "[factory] Adapter '%s' konnte nicht instanziiert werden — "
                "übersprungen.",
                atype,
            )
            continue

        logger.debug("[factory] Adapter '%s' bereit.", atype)

    # Warnen, wenn mehrere report-liefernde Adapter aktiv sind: sie schreiben
    # denselben date-Schlüssel und überschreiben sich pro Nacht gegenseitig.
    report_sources = [a for a in adapters if a.name in _REPORT_PRODUCING]
    if len(report_sources) > 1:
        logger.warning(
            "[factory] Mehrere report-liefernde Adapter aktiv (%s). Sie teilen "
            "sich den Report-Schlüssel 'date' und überschreiben sich pro Nacht "
            "gegenseitig — für den Report-Kanal aktuell nur EINEN Adapter aktiv "
            "lassen (weitere sind für InfluxDB/Vergleich ok).",
            ", ".join(a.name for a in report_sources),
        )

    return adapters
