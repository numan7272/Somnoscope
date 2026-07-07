"""
Optionaler Adapter: Fitbit Air über die Google Health API (Feature B).

⚠️  BEWUSSTE AUSNAHME VON KERNPRINZIP 1 ("KEINE Cloud-API-Calls für
    Bio-Signale"). Die BLE-Payload des Fitbit Air ist Ende-zu-Ende
    verschlüsselt und nur über Googles Cloud entschlüsselbar — ein lokaler
    Read (wie beim geplanten ``fitbit_ble``) ist technisch unmöglich. Wer die
    Air-Daten will, akzeptiert damit *einen* Cloud-Hop. Deshalb ist dieser
    Adapter **opt-in** und in ``config.yaml`` standardmässig ``enabled: false``.

Warum kein natives OAuth im Repo?
    Der OAuth-2.0-/PKCE-Flow gegen Google (inkl. 7-Tage-Refresh-Token im
    Test-Modus) ist fehleranfällig und zöge mehrere neue Abhängigkeiten nach
    sich (siehe Konvention „Keine ungefragte Erweiterung der Abhängigkeiten").
    Stattdessen delegieren wir Auth *und* Datenabruf an das offizielle,
    quelloffene CLI **ghealth** (``github.com/Google-Health-API/google-health-cli``),
    das man einmalig mit ``ghealth setup`` konfiguriert. Dieser Adapter ruft es
    als Subprozess auf und übersetzt das JSON in :class:`WearableReading`.
    → Null neue Python-Pakete; Tokens liegen ausserhalb des Repos
    (``~/.config/ghealth/``).

Der Adapter ist ein reiner Pull-Adapter: :meth:`poll` fragt die konfigurierten
Datentypen ab; die Basis-``run``-Schleife ruft ``poll`` im Intervall auf.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from core.constants import (
    ADAPTER_FITBIT_GH_API,
    GH_DATATYPE_TO_METRIC,
    METRIC_HEART_RATE,
    METRIC_HRV,
    METRIC_SKIN_TEMP,
    METRIC_SLEEP_STAGE,
    METRIC_SPO2,
    STAGE_DEEP,
    STAGE_LIGHT,
    STAGE_REM,
    STAGE_WAKE,
)

from .base_wearable import WearableAdapter, WearableReading

#: Standard-Datentypen, falls in der config.yaml nichts anderes steht.
_DEFAULT_METRICS: tuple[str, ...] = (
    "sleep",
    "heart-rate",
    "heart-rate-variability",
    "oxygen-saturation",
)

#: Einheit je vereinheitlichter Metrik (für Grafana/InfluxDB).
_METRIC_UNITS: dict[str, str | None] = {
    METRIC_SLEEP_STAGE: None,  # kategorisch (Phasenname)
    METRIC_HEART_RATE: "bpm",
    METRIC_HRV: "ms",
    METRIC_SPO2: "%",
    METRIC_SKIN_TEMP: "°C",
}

#: Mögliche Feldnamen für Start-/Endzeit in einem Health-API-DataPoint.
#: Defensiv gehalten, weil das Schema der Google Health API jung ist und wir
#: das Original-Payload ohnehin in ``raw`` behalten.
_START_KEYS = ("startTime", "start_time", "start", "time", "timestamp")
_END_KEYS = ("endTime", "end_time", "end")

#: Normalisierung proprietärer Schlafphasen-Namen (Fitbit/Google) auf das
#: 4-Phasen-Modell des SleepReport-Contracts ({wake, light, deep, rem}).
#: Unbekannte Werte -> None, damit sie sauber verworfen statt contract-widrig
#: emittiert werden.
_STAGE_NAME_MAP: dict[str, str] = {
    "wake": STAGE_WAKE,
    "awake": STAGE_WAKE,
    "wakeup": STAGE_WAKE,
    "restless": STAGE_WAKE,
    "asleep": STAGE_LIGHT,
    "light": STAGE_LIGHT,
    "core": STAGE_LIGHT,
    "n1": STAGE_LIGHT,
    "n2": STAGE_LIGHT,
    "deep": STAGE_DEEP,
    "n3": STAGE_DEEP,
    "sws": STAGE_DEEP,
    "rem": STAGE_REM,
}


class FitbitGoogleHealthAdapter(WearableAdapter):
    """
    Zieht Schlaf-/Vitaldaten des Fitbit Air über das ``ghealth``-CLI.

    Relevante Options-Felder aus ``config.yaml`` (``wearable.adapters[]``):
        * ``ghealth_bin`` (str): Pfad/Name des CLI. Default ``"ghealth"``.
        * ``metrics`` (list[str]): Health-API-Datentypen (kebab-case), z.B.
          ``["sleep", "heart-rate"]``. Default siehe :data:`_DEFAULT_METRICS`.
        * ``poll_interval_s`` (int): Abfrage-Intervall. Default 1800.
        * ``lookback_days`` (int): Wie weit zurück je Poll abgefragt wird, damit
          spät synchronisierte Nächte nicht verloren gehen. Default 2.
        * ``user`` (str): Health-API-User-ID. Default ``"me"``.
    """

    def __init__(self, options: Mapping[str, Any], timezone: str = "UTC") -> None:
        super().__init__(options, timezone)
        self._bin: str = str(self._options.get("ghealth_bin", "ghealth"))
        metrics = self._options.get("metrics") or _DEFAULT_METRICS
        # Nur bekannte Datentypen zulassen — Tippfehler früh sichtbar machen.
        self._metrics: tuple[str, ...] = tuple(
            m for m in metrics if m in GH_DATATYPE_TO_METRIC
        )
        self._lookback_days: int = int(self._options.get("lookback_days", 2))
        self._user: str = str(self._options.get("user", "me"))

    @property
    def name(self) -> str:
        return ADAPTER_FITBIT_GH_API

    # -- Lebenszyklus --------------------------------------------------------

    async def open(self) -> None:
        """
        Prüft, ob das ``ghealth``-CLI verfügbar ist.

        Raises:
            RuntimeError: Wenn das Binary nicht im PATH liegt oder keine
                gültigen Datentypen konfiguriert sind. Die Basis-``run``-Schleife
                überspringt den Adapter dann sauber (Graceful Degradation).
        """
        if shutil.which(self._bin) is None:
            raise RuntimeError(
                f"ghealth-CLI '{self._bin}' nicht gefunden. "
                "Installieren und einmalig 'ghealth setup' ausführen — "
                "siehe docs/fitbit_air_setup.md."
            )
        if not self._metrics:
            raise RuntimeError(
                "Keine gültigen Datentypen in 'metrics' konfiguriert. "
                f"Erlaubt: {sorted(GH_DATATYPE_TO_METRIC)}"
            )
        self._log.warning(
            "[%s] AKTIV — dieser Adapter ruft die Google-Cloud-API auf "
            "(bewusste Ausnahme von Kernprinzip 1). Datentypen: %s",
            self.name,
            ", ".join(self._metrics),
        )

    # -- Pull ----------------------------------------------------------------

    async def poll(self) -> list[WearableReading]:
        """
        Fragt alle konfigurierten Datentypen einmal ab und übersetzt das
        Ergebnis in :class:`WearableReading`.

        Deduplizierung über wiederholte Polls (der ``lookback_days``-Fenster
        überlappt bewusst) ist Aufgabe der späteren DB-/ML-Stufe.
        """
        since = self._since_date()
        readings: list[WearableReading] = []
        for datatype in self._metrics:
            try:
                data_points = await self._fetch(datatype, since)
            except Exception:  # noqa: BLE001 — ein Datentyp darf die anderen nicht killen
                self._log.exception(
                    "[%s] Abruf von '%s' fehlgeschlagen — übersprungen.",
                    self.name,
                    datatype,
                )
                continue
            metric = GH_DATATYPE_TO_METRIC[datatype]
            translated = self._translate(datatype, metric, data_points)
            self._log.debug(
                "[%s] %s: %d DataPoint(s) -> %d Reading(s).",
                self.name,
                datatype,
                len(data_points),
                len(translated),
            )
            readings.extend(translated)
        return readings

    # -- Interna -------------------------------------------------------------

    def _since_date(self) -> str:
        """Berechnet die untere Zeitgrenze (``YYYY-MM-DD``) für den Abruf."""
        tz = self._resolve_tz()
        start = datetime.now(tz) - timedelta(days=self._lookback_days)
        return start.date().isoformat()

    def _resolve_tz(self) -> timezone:
        """
        Löst die konfigurierte Zeitzone auf. Fällt auf UTC zurück, falls
        ``zoneinfo`` (z.B. mangels ``tzdata`` unter Windows) die Zone nicht
        kennt — für eine reine Datumsgrenze ist das unkritisch.
        """
        try:
            from zoneinfo import ZoneInfo  # lokal, um Import-Kosten zu sparen

            return ZoneInfo(self._timezone)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            self._log.debug(
                "[%s] Zeitzone '%s' nicht auflösbar, nutze UTC.",
                self.name,
                self._timezone,
            )
            return timezone.utc

    async def _fetch(self, datatype: str, since: str) -> list[dict[str, Any]]:
        """
        Ruft ``ghealth data <datatype> list --from <since> --json`` auf und gibt
        die DataPoint-Liste zurück.
        """
        args = ["data", datatype, "list", "--from", since, "--json"]
        if datatype == "sleep":
            args.append("--detail")  # Stage-by-Stage (Wach/Leicht/Tief/REM)
        stdout = await self._run_ghealth(args)
        return self._parse_json(stdout)

    async def _run_ghealth(self, args: list[str]) -> str:
        """
        Führt das ``ghealth``-CLI als Subprozess aus und liefert stdout.

        In eine eigene Methode ausgelagert, damit Tests sie mocken können, ohne
        ein echtes CLI oder Netzwerk zu brauchen.

        Raises:
            RuntimeError: Bei Exit-Code != 0.
        """
        proc = await asyncio.create_subprocess_exec(
            self._bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"ghealth {' '.join(args)} -> Exit {proc.returncode}: "
                f"{stderr_b.decode('utf-8', 'replace').strip()}"
            )
        return stdout_b.decode("utf-8", "replace")

    @staticmethod
    def _parse_json(stdout: str) -> list[dict[str, Any]]:
        """
        Parst die ghealth-Ausgabe defensiv in eine Liste von DataPoints.

        Akzeptiert drei Formen: ein ``{"dataPoints": [...]}``-Objekt, eine nackte
        JSON-Liste oder JSON-Lines (ein Objekt pro Zeile).
        """
        stdout = stdout.strip()
        if not stdout:
            return []
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            # Fallback: JSON-Lines
            points: list[dict[str, Any]] = []
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    points.append(obj)
            return points

        if isinstance(parsed, dict):
            data_points = parsed.get("dataPoints", parsed.get("data", []))
            return [p for p in data_points if isinstance(p, dict)]
        if isinstance(parsed, list):
            return [p for p in parsed if isinstance(p, dict)]
        return []

    def _translate(
        self, datatype: str, metric: str, data_points: Iterable[dict[str, Any]]
    ) -> list[WearableReading]:
        """
        Übersetzt Health-API-DataPoints in vereinheitlichte Readings.

        Bewusst tolerant: Zeit- und Wertfelder werden über mehrere mögliche
        Schlüssel gesucht, und das Original bleibt in ``raw`` erhalten. So
        funktioniert der Adapter auch, wenn Google einzelne Feldnamen anpasst —
        notfalls justiert man die ``_*_KEYS``-Listen statt der Logik.
        """
        unit = _METRIC_UNITS.get(metric)
        out: list[WearableReading] = []
        for dp in data_points:
            start = _parse_dt(_first(dp, _START_KEYS))
            if start is None:
                self._log.debug(
                    "[%s] DataPoint ohne erkennbaren Startzeitpunkt übersprungen: %s",
                    self.name,
                    dp,
                )
                continue
            end = _parse_dt(_first(dp, _END_KEYS))
            value = self._extract_value(datatype, dp)
            # Contract: Schlafphasen brauchen Start UND Ende UND eine bekannte
            # Phase. Unbrauchbare Segmente verwerfen statt contract-widrig senden.
            if datatype == "sleep" and (end is None or value is None):
                self._log.debug(
                    "[%s] Schlafphasen-DataPoint verworfen (Ende/Phase fehlt): %s",
                    self.name,
                    dp,
                )
                continue
            out.append(
                WearableReading(
                    source=self.name,
                    metric=metric,
                    start=start,
                    end=end,
                    value=value,
                    unit=unit,
                    raw=dp,
                )
            )
        return out

    @staticmethod
    def _extract_value(datatype: str, dp: dict[str, Any]) -> float | str | None:
        """
        Zieht den eigentlichen Messwert aus einem DataPoint.

        Für ``sleep`` ist das der kategorische Phasenname (z.B. ``"deep"``),
        sonst ein numerischer Skalar. Sucht defensiv in verschachtelten
        ``value``/typspezifischen Objekten.
        """
        if datatype == "sleep":
            for key in ("stage", "sleepStage", "level", "type"):
                val = _deep_get(dp, key)
                if isinstance(val, str):
                    # Proprietären Namen auf {wake,light,deep,rem} normalisieren;
                    # Unbekanntes -> None (wird in _translate verworfen).
                    return _STAGE_NAME_MAP.get(val.strip().lower())
            return None

        # Numerische Metriken: erst flache, dann verschachtelte Kandidaten.
        for key in ("value", "bpm", "beatsPerMinute", "percent", "millis", "celsius"):
            val = _deep_get(dp, key)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, dict):
                for inner in ("value", "quantity", "doubleValue", "fpVal"):
                    if isinstance(val.get(inner), (int, float)):
                        return float(val[inner])
        return None


# ----------------------------------------------------------------------------
# Modul-lokale Helfer (frei von Adapter-Zustand -> leicht testbar)
# ----------------------------------------------------------------------------

def _first(dp: Mapping[str, Any], keys: Iterable[str]) -> Any:
    """Gibt den ersten in ``dp`` vorhandenen Wert aus ``keys`` zurück (sonst None)."""
    for key in keys:
        if key in dp and dp[key] is not None:
            return dp[key]
    return None


def _deep_get(dp: Mapping[str, Any], key: str) -> Any:
    """Sucht ``key`` flach und eine Ebene tief in ``dp`` (für value-Wrapper)."""
    if key in dp:
        return dp[key]
    for val in dp.values():
        if isinstance(val, Mapping) and key in val:
            return val[key]
    return None


def _parse_dt(raw: Any) -> datetime | None:
    """
    Parst einen Zeitwert zu einem tz-aware :class:`datetime`.

    Akzeptiert ISO-8601-Strings (inkl. ``Z``) sowie Unix-Timestamps (Sekunden
    oder Millisekunden). Naive Zeiten werden als UTC interpretiert.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        secs = raw / 1000.0 if raw > 1e12 else float(raw)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None
