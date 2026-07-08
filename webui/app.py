"""
FastAPI-Backend des Somnoscope-Dashboards.

Liefert das statische Frontend (``webui/static/``) aus und exponiert eine
schlanke JSON-API über den Report-Store aus :mod:`database`:

    * ``GET /``                   → ``index.html`` (das Dashboard)
    * ``GET /static/...``         → CSS/JS-Assets
    * ``GET /sw.js``              → Service Worker (PWA, Scope ``/`` via
      ``Service-Worker-Allowed``-Header)
    * ``GET /manifest.webmanifest`` → Web-App-Manifest (installierbare PWA)
    * ``GET /api/report/latest``  → jüngster SleepReport (404 ``no_data`` wenn leer)
    * ``GET /api/reports?limit=N``→ Liste der letzten N Reports (neueste zuerst)
    * ``GET /api/trends?days=N``  → Mehr-Nächte-Trends via
      :func:`analytics.compute_trends` (200 auch bei leerer Historie)
    * ``GET /api/coaching?lang=de|en`` → Coaching-Text des lokalen LLM-Coach
      zur jüngsten Nacht in der gewählten Sprache (Default ``de``, ungültige
      Codes fallen auf ``de`` zurück; 404 ``no_data`` wenn leer; in-memory
      gecacht pro Datum + Sprache)
    * ``GET /api/export/reports.csv?days=N``  → CSV-Download der letzten N
      Nächte via :func:`analytics.export.reports_to_csv` (200 auch ohne Daten)
    * ``GET /api/export/reports.json?days=N`` → JSON-Download der letzten N
      Nächte via :func:`analytics.export.reports_to_json` (200 auch ohne Daten)
    * ``GET /api/status``         → System-Status (App-Version, Zeitzone,
      Adapter, Modul-Flags, Daten-Umfang; 200 auch ohne Config/Store)

Designentscheidungen:
    * **Graceful Degradation:** ``fastapi`` wird in ``try/except`` importiert.
      Fehlt die Abhängigkeit, crasht der Import dieses Moduls *nicht* —
      ``app`` ist dann ``None`` und ein klarer Log-Hinweis erklärt die
      Installation (``pip install fastapi uvicorn``).
    * **Store-Lebenszyklus:** Der Report-Store (:func:`database.create_store`)
      wird genau einmal beim App-Start geöffnet (Lifespan-Handler) und beim
      Shutdown sauber geschlossen — kein Store pro Request.
    * **Nur lokale Daten:** Es finden keinerlei externe Netzwerk-Zugriffe
      statt; alle Daten kommen aus dem lokalen Store (Kernprinzip 1).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from core.constants import SLEEP_STAGES

logger = logging.getLogger(__name__)

#: Absoluter Pfad zum Frontend (index.html, style.css, app.js).
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

#: Obergrenze für den in-memory Coaching-Cache (Key: date|generated_at|lang) —
#: verhindert unbegrenztes Wachstum im Dauerbetrieb (FIFO-Eviction).
_COACHING_CACHE_MAX = 64

#: Vom Coaching-Endpoint unterstützte Sprachen; alles andere fällt auf
#: Deutsch zurück (Graceful Degradation statt 422).
_COACHING_LANGS: frozenset[str] = frozenset({"de", "en"})

#: Default-Sprache des Coaching-Endpoints (rückwärtskompatibel).
_COACHING_DEFAULT_LANG = "de"

#: Anzeigename der Anwendung im Status-Endpoint (``/api/status``).
_APP_NAME = "Somnoscope"

#: Fallback-Zeitzone, wenn keine Config geladen werden konnte.
_DEFAULT_TIMEZONE = "UTC"

#: Kanonische Modul-Namen für den ``modules``-Block von ``/api/status`` —
#: exakt die Namen, die :meth:`core.config_loader.AppConfig.enabled_modules`
#: liefert (stabile Reihenfolge für das Frontend).
_STATUS_MODULES: tuple[str, ...] = (
    "wearable",
    "climate_sensors",
    "database",
    "ml_pipeline",
    "llm_coach",
)

#: Obergrenze der für den ``data``-Block betrachteten Nächte — deckt sich mit
#: dem ``limit``-Maximum der übrigen Report-Endpoints (1 Jahr Historie).
_STATUS_REPORT_LIMIT = 365

# --------------------------------------------------------------------------
# Optionale Abhängigkeit: fastapi (Graceful Degradation, Kernprinzip 2)
# --------------------------------------------------------------------------
try:
    from fastapi import FastAPI, HTTPException, Query, Response
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover — hängt von der Umgebung ab
    _FASTAPI_AVAILABLE = False


def _load_cfg() -> Any | None:
    """
    Lädt die zentrale App-Konfiguration für den App-Lebenszyklus.

    Wird genau einmal im Lifespan-Handler aufgerufen und in
    ``app.state.cfg`` abgelegt — der Coaching-Endpoint braucht die Config
    (``cfg.llm_coach``), ohne sie pro Request neu von Platte zu lesen.

    Hinweis: :func:`_open_store` lädt die Config intern ein zweites Mal.
    Das ist Absicht — dessen argumentloser Vertrag bleibt stabil (u.a. als
    Monkeypatch-Naht der Tests), und ein doppelter YAML-Read beim Start ist
    vernachlässigbar.

    Returns:
        Die geladene ``AppConfig`` oder ``None``, wenn Config-Modul oder
        ``config.yaml`` nicht verfügbar sind (Graceful Degradation).

    Seiteneffekte:
        Liest ``config.yaml`` aus dem Arbeitsverzeichnis.
    """
    try:
        from core.config_loader import load_config
    except ImportError:
        logger.exception(
            "core.config_loader konnte nicht importiert werden — "
            "der LLM-Coach steht im Dashboard nicht zur Verfügung."
        )
        return None

    try:
        return load_config()
    except Exception:  # noqa: BLE001 — Dashboard darf an der Config nicht sterben
        logger.exception(
            "Konfiguration konnte nicht geladen werden — "
            "der LLM-Coach steht im Dashboard nicht zur Verfügung."
        )
        return None


def _open_store() -> Any | None:
    """
    Öffnet den Report-Store über die zentrale Konfiguration.

    Lädt ``config.yaml`` via :func:`core.config_loader.load_config` und
    erzeugt den Store mit :func:`database.create_store`. Schlägt einer der
    Schritte fehl (fehlende Config, fehlende optionale Abhängigkeit), wird
    das geloggt und ``None`` zurückgegeben — die API antwortet dann mit
    „keine Daten" statt zu crashen.

    Returns:
        Die Store-Instanz oder ``None``, wenn kein Store verfügbar ist.

    Seiteneffekte:
        Liest ``config.yaml`` aus dem Arbeitsverzeichnis; öffnet ggf. die
        lokale Datenbank-Datei.
    """
    try:
        from core.config_loader import load_config
        from database import create_store
    except ImportError:
        logger.exception(
            "Store-Module konnten nicht importiert werden — "
            "das Dashboard läuft ohne Daten weiter."
        )
        return None

    try:
        cfg = load_config()
        return create_store(cfg)
    except Exception:  # noqa: BLE001 — Dashboard darf am Store nicht sterben
        logger.exception(
            "Report-Store konnte nicht geöffnet werden — "
            "das Dashboard läuft ohne Daten weiter."
        )
        return None


def _empty_trends() -> dict[str, Any]:
    """
    Baut das leere Trends-Dict im Contract-Format von ``compute_trends``.

    Dient als Fallback, falls das :mod:`analytics`-Modul nicht importierbar
    ist (Graceful Degradation) — der ``/api/trends``-Endpoint antwortet dann
    trotzdem mit ``200`` und einer wohlgeformten, leeren Struktur, statt zu
    crashen.

    Returns:
        Trends-Dict mit ``n_nights = 0``, leeren Serien und ``None``-Werten.
    """
    return {
        "n_nights": 0,
        "range": {"from": None, "to": None},
        "averages": {
            "sleep_score": None,
            "sleep_efficiency_pct": None,
            "total_sleep_min": None,
            "avg_hrv": None,
        },
        "stage_distribution_pct": {stage: 0.0 for stage in SLEEP_STAGES},
        "series": {
            "date": [],
            "sleep_score": [],
            "sleep_efficiency_pct": [],
            "total_sleep_min": [],
            "avg_hrv": [],
            "stages_pct": [],
        },
        "best_night": None,
        "worst_night": None,
        "consistency": {"score_stddev": None},
    }


def _create_app() -> "FastAPI":
    """
    Baut die FastAPI-Anwendung inkl. Routen, Static-Mount und Lifespan.

    Returns:
        Die konfigurierte :class:`fastapi.FastAPI`-Instanz.
    """

    @asynccontextmanager
    async def _lifespan(application: FastAPI):
        """
        Lifespan-Handler: Config + Store einmalig öffnen, bei Shutdown schließen.

        Args:
            application: Die FastAPI-Instanz; Config und Store werden in
                ``application.state.cfg`` bzw. ``application.state.store``
                abgelegt, dazu Cache und Lock für den Coaching-Endpoint
                (``coaching_cache``/``coaching_lock``).
        """
        # _load_cfg()/_open_store() machen blockierendes I/O (Config lesen,
        # SQLite öffnen) — im async Lifespan daher in Threads auslagern
        # (Asyncio-First).
        application.state.cfg = await asyncio.to_thread(_load_cfg)
        store = await asyncio.to_thread(_open_store)
        application.state.store = store
        # In-Memory-Cache für Coaching-Texte (Key: date|generated_at|lang) plus Lock,
        # damit parallele Requests nicht mehrfach das lokale LLM anwerfen.
        application.state.coaching_cache = {}
        application.state.coaching_lock = asyncio.Lock()
        if store is not None:
            logger.info("WebUI: Report-Store geöffnet.")
        try:
            yield
        finally:
            if store is not None:
                try:
                    await store.close()
                    logger.info("WebUI: Report-Store geschlossen.")
                except Exception:  # noqa: BLE001
                    logger.exception("WebUI: Fehler beim Schließen des Stores.")

    application = FastAPI(
        title="Somnoscope Dashboard",
        description=(
            "Lokales Schlaf-Dashboard — visualisiert die zuletzt "
            "erfasste Nacht. 100 % offline."
        ),
        version="1.0.0",
        lifespan=_lifespan,
    )

    application.mount(
        "/static", StaticFiles(directory=str(STATIC_DIR)), name="static"
    )

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """
        Liefert die Dashboard-Startseite (``index.html``).

        Returns:
            :class:`~fastapi.responses.FileResponse` auf das statische
            HTML-Dokument.
        """
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @application.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        """
        Liefert den Service Worker unter Wurzel-Pfad ``/sw.js`` aus (PWA).

        Der SW liegt physisch in ``webui/static/sw.js``, muss aber an der
        Wurzel registriert werden, damit er den gesamten App-Scope ``/``
        kontrollieren darf. Der Header ``Service-Worker-Allowed: /``
        erlaubt dem Browser explizit diesen weiten Scope.

        Returns:
            :class:`~fastapi.responses.FileResponse` mit
            ``application/javascript`` und ``Service-Worker-Allowed: /``.
        """
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @application.get("/manifest.webmanifest", include_in_schema=False)
    async def web_manifest() -> FileResponse:
        """
        Liefert das PWA-Web-App-Manifest (installierbares Dashboard).

        Returns:
            :class:`~fastapi.responses.FileResponse` mit
            ``application/manifest+json``.
        """
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @application.get("/api/report/latest")
    async def latest_report() -> dict[str, Any]:
        """
        Gibt den jüngsten SleepReport als JSON zurück.

        Returns:
            Das SleepReport-Dict der zuletzt erfassten Nacht.

        Raises:
            HTTPException: ``404`` mit ``{"detail": "no_data"}``, wenn noch
                kein Report vorliegt oder kein Store verfügbar ist.
        """
        store = getattr(application.state, "store", None)
        if store is None:
            raise HTTPException(status_code=404, detail="no_data")
        report = await store.latest_report()
        if report is None:
            raise HTTPException(status_code=404, detail="no_data")
        return report

    @application.get("/api/reports")
    async def list_reports(
        limit: int = Query(default=30, ge=1, le=365),
    ) -> list[dict[str, Any]]:
        """
        Listet die letzten Reports (neueste zuerst).

        Args:
            limit: Maximale Anzahl zurückgegebener Reports (1–365, Default 30).

        Returns:
            Liste von SleepReport-Dicts; leere Liste, wenn keine Daten oder
            kein Store vorhanden sind.
        """
        store = getattr(application.state, "store", None)
        if store is None:
            return []
        return await store.list_reports(limit=limit)

    @application.get("/api/trends")
    async def trends(
        days: int = Query(default=30, ge=1, le=365),
    ) -> dict[str, Any]:
        """
        Liefert Mehr-Nächte-Trends für die Verlauf-Ansicht des Dashboards.

        Holt die letzten ``days`` Reports aus dem Store und verdichtet sie
        via :func:`analytics.compute_trends` (Mittelwerte, chronologische
        Serien, Phasenverteilung, beste/schlechteste Nacht, Score-Streuung).
        Antwortet auch bei leerer Historie oder fehlendem Store mit ``200``
        und ``n_nights = 0`` — die Verlauf-Ansicht zeigt dann ihren
        Empty-State (Graceful Degradation).

        Args:
            days: Maximale Anzahl betrachteter Nächte (1–365, Default 30).

        Returns:
            Das JSON-serialisierbare Trends-Dict von
            :func:`analytics.compute_trends` bzw. dessen leere Form, wenn
            das ``analytics``-Modul nicht verfügbar ist.
        """
        store = getattr(application.state, "store", None)
        reports: list[dict[str, Any]] = []
        if store is not None:
            reports = await store.list_reports(limit=days)

        try:
            from analytics import compute_trends
        except ImportError:
            logger.exception(
                "analytics konnte nicht importiert werden — "
                "/api/trends liefert eine leere Trend-Struktur."
            )
            return _empty_trends()

        # compute_trends ist reine CPU-Arbeit (bis zu 365 Nächte) —
        # via Thread auslagern, damit der Event-Loop frei bleibt.
        return await asyncio.to_thread(compute_trends, reports)

    @application.get("/api/coaching")
    async def coaching(lang: str = Query(default=_COACHING_DEFAULT_LANG)) -> dict[str, Any]:
        """
        Liefert den Coaching-Text des lokalen LLM-Coach zur jüngsten Nacht.

        Holt den jüngsten Report plus Historie (30 Nächte) aus dem Store und
        ruft :func:`llm_coach.generate_coaching` (lokales Ollama mit
        regelbasiertem Fallback, wirft nie) in der gewünschten Sprache. Da
        die Generierung bei laufendem Ollama einige Sekunden dauern kann,
        wird das Ergebnis in-memory pro ``report["date"]`` UND Sprache
        gecacht (sonst würden sich DE/EN-Texte mischen); wiederholte Aufrufe
        antworten sofort. Ein ``asyncio.Lock`` verhindert, dass parallele
        Requests dieselbe Nacht mehrfach generieren.

        Args:
            lang: Gewünschte Sprache des Coaching-Texts (``"de"`` oder
                ``"en"``); ungültige Werte fallen still auf ``"de"`` zurück
                (Graceful Degradation statt 422, rückwärtskompatibel).

        Returns:
            ``{"enabled": bool, "text": str, "date": str}`` — ``enabled`` ist
            ``False`` (mit leerem Text), wenn der Coach per Config deaktiviert
            oder das Modul/die Config nicht verfügbar ist.

        Raises:
            HTTPException: ``404`` mit ``{"detail": "no_data"}``, wenn noch
                kein Report vorliegt oder kein Store verfügbar ist.
        """
        # Case-insensitiv normalisieren (analog zu llm_coach.normalize_lang),
        # damit z.B. ?lang=EN nicht faelschlich auf Deutsch faellt.
        lang = lang.strip().lower() if isinstance(lang, str) else _COACHING_DEFAULT_LANG
        if lang not in _COACHING_LANGS:
            logger.debug(
                "/api/coaching: unbekannter lang-Parameter %r — nutze %r.",
                lang,
                _COACHING_DEFAULT_LANG,
            )
            lang = _COACHING_DEFAULT_LANG

        store = getattr(application.state, "store", None)
        if store is None:
            raise HTTPException(status_code=404, detail="no_data")
        report = await store.latest_report()
        if report is None:
            raise HTTPException(status_code=404, detail="no_data")

        date = str(report.get("date") or "")
        # Cache-Key enthält generated_at UND lang: ein neu gebauter Report
        # (gleiches Datum, neue Daten) bekommt frisches Coaching statt
        # veraltetem Text, und DE/EN-Texte mischen sich nicht.
        cache_key = f"{date}|{report.get('generated_at') or ''}|{lang}"
        cache: dict[str, str] = application.state.coaching_cache
        cached = cache.get(cache_key)
        if cached is not None:
            return {"enabled": True, "text": cached, "date": date}

        cfg = getattr(application.state, "cfg", None)
        if cfg is None or not cfg.llm_coach.enabled:
            return {"enabled": False, "text": "", "date": date}

        try:
            from llm_coach import generate_coaching
        except ImportError:
            logger.exception(
                "llm_coach konnte nicht importiert werden — "
                "Coaching wird im Dashboard deaktiviert."
            )
            return {"enabled": False, "text": "", "date": date}

        async with application.state.coaching_lock:
            # Double-Check: ein parallel wartender Request kann den Text
            # inzwischen bereits erzeugt haben.
            text = cache.get(cache_key)
            if text is None:
                history = await store.list_reports(limit=30)
                # generate_coaching ist async, nutzt intern asyncio.to_thread
                # und wirft nie — der Event-Loop bleibt frei.
                text = await generate_coaching(report, history, cfg, lang)
                cache[cache_key] = text
                # Cache begrenzen: ältesten Eintrag (Einfüge-Reihenfolge) werfen.
                if len(cache) > _COACHING_CACHE_MAX:
                    cache.pop(next(iter(cache)))
        return {"enabled": True, "text": text, "date": date}

    async def _export_reports(days: int) -> list[dict[str, Any]]:
        """
        Holt die letzten ``days`` Reports für einen Export aus dem Store.

        Args:
            days: Maximale Anzahl exportierter Nächte.

        Returns:
            Liste von SleepReport-Dicts (neueste zuerst, wie vom Store
            geliefert); leere Liste, wenn kein Store verfügbar ist
            (Graceful Degradation — der Export antwortet dann leer mit 200).
        """
        store = getattr(application.state, "store", None)
        if store is None:
            return []
        return await store.list_reports(limit=days)

    @application.get("/api/export/reports.csv")
    async def export_reports_csv(
        days: int = Query(default=365, ge=1, le=365),
    ) -> Response:
        """
        Liefert die letzten ``days`` Nächte als CSV-Download.

        Body ist :func:`analytics.export.reports_to_csv` über den letzten
        ``days`` Reports (chronologisch aufsteigend, stabile
        Spaltenreihenfolge, eine Zeile je Nacht). Antwortet auch bei leerer
        Historie, fehlendem Store oder nicht importierbarem
        ``analytics.export`` mit ``200`` — dann nur Header-Zeile bzw. leerer
        Body (Graceful Degradation).

        Args:
            days: Maximale Anzahl exportierter Nächte (1–365, Default 365).

        Returns:
            :class:`fastapi.Response` mit ``text/csv; charset=utf-8`` und
            ``Content-Disposition: attachment`` (Dateiname
            ``somnoscope-reports.csv``).
        """
        reports = await _export_reports(days)
        try:
            from analytics.export import reports_to_csv
        except ImportError:
            logger.exception(
                "analytics.export konnte nicht importiert werden — "
                "/api/export/reports.csv liefert einen leeren Export."
            )
            body = ""
        else:
            # Serialisierung ist reine CPU-/String-Arbeit (bis zu 365
            # Nächte) — via Thread auslagern, damit der Event-Loop frei bleibt.
            body = await asyncio.to_thread(reports_to_csv, reports)
        return Response(
            content=body,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="somnoscope-reports.csv"'
            },
        )

    @application.get("/api/export/reports.json")
    async def export_reports_json(
        days: int = Query(default=365, ge=1, le=365),
    ) -> Response:
        """
        Liefert die letzten ``days`` Nächte als JSON-Download (volle Reports).

        Body ist :func:`analytics.export.reports_to_json` über den letzten
        ``days`` Reports: ``{"exported_report_count": int, "reports": [...]}``
        (chronologisch aufsteigend). Antwortet auch bei leerer Historie,
        fehlendem Store oder nicht importierbarem ``analytics.export`` mit
        ``200`` und leerem Export (Graceful Degradation).

        Args:
            days: Maximale Anzahl exportierter Nächte (1–365, Default 365).

        Returns:
            :class:`fastapi.Response` mit ``application/json`` und
            ``Content-Disposition: attachment`` (Dateiname
            ``somnoscope-reports.json``).
        """
        reports = await _export_reports(days)
        try:
            from analytics.export import reports_to_json
        except ImportError:
            logger.exception(
                "analytics.export konnte nicht importiert werden — "
                "/api/export/reports.json liefert einen leeren Export."
            )
            body = '{"exported_report_count": 0, "reports": []}'
        else:
            # Serialisierung via Thread auslagern (Asyncio-First, s.o.).
            body = await asyncio.to_thread(reports_to_json, reports)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="somnoscope-reports.json"'
            },
        )

    @application.get("/api/status")
    async def status() -> dict[str, Any]:
        """
        Liefert den System-Status des Dashboards als JSON.

        Aggregiert App-Metadaten (Name/Version der FastAPI-Instanz), die
        konfigurierte Zeitzone, die Wearable-Adapter (Typ + Aktiv-Flag), die
        Modul-Flags aus :meth:`AppConfig.enabled_modules` sowie den
        Daten-Umfang aus dem Store (Nacht-Anzahl, Datums-Spanne, jüngster
        Score). Antwortet auch ohne Config oder Store mit ``200`` und
        wohldefinierten Defaults (Graceful Degradation): ohne Config leere
        Adapter-Liste, alle Module ``false``, Zeitzone ``"UTC"``; ohne Store
        (oder bei leerem Store) ``night_count = 0`` und ``None``-Felder.

        Returns:
            Status-Dict mit den Blöcken ``app``, ``timezone``, ``adapters``,
            ``modules`` und ``data`` (JSON-serialisierbar).
        """
        cfg = getattr(application.state, "cfg", None)
        store = getattr(application.state, "store", None)

        # -- Config-Teil: Zeitzone, Adapter, Modul-Flags -------------------
        timezone = _DEFAULT_TIMEZONE
        adapters: list[dict[str, Any]] = []
        modules: dict[str, bool] = {name: False for name in _STATUS_MODULES}
        if cfg is not None:
            try:
                timezone = str(cfg.system.timezone)
                adapters = [
                    {"type": str(adapter.type), "enabled": bool(adapter.enabled)}
                    for adapter in cfg.wearable.adapters
                ]
                enabled = set(cfg.enabled_modules())
                modules = {name: name in enabled for name in _STATUS_MODULES}
            except Exception:  # noqa: BLE001 — Status darf an der Config nicht sterben
                logger.exception(
                    "/api/status: Config konnte nicht ausgewertet werden — "
                    "es werden Default-Werte geliefert."
                )

        # -- Daten-Teil: Nacht-Anzahl, Datums-Spanne, jüngster Score -------
        reports: list[dict[str, Any]] = []
        if store is not None:
            try:
                # list_reports ist async (SQLite-I/O läuft store-intern in
                # Threads) — der Event-Loop bleibt frei.
                reports = await store.list_reports(limit=_STATUS_REPORT_LIMIT)
            except Exception:  # noqa: BLE001 — Status darf am Store nicht sterben
                logger.exception(
                    "/api/status: Reports konnten nicht geladen werden — "
                    "der data-Block wird leer geliefert."
                )
                reports = []

        date_from: str | None = None
        date_to: str | None = None
        latest_score: int | None = None
        # Defensiv: fehlende/leere date-Felder überspringen statt zu crashen.
        dates = sorted(str(r.get("date")) for r in reports if r.get("date"))
        if dates:
            date_from, date_to = dates[0], dates[-1]
        if reports:
            # Store-Contract: neueste Nacht zuerst.
            score = reports[0].get("sleep_score")
            if isinstance(score, (int, float)):
                latest_score = int(round(score))

        return {
            "app": {"name": _APP_NAME, "version": application.version},
            "timezone": timezone,
            "adapters": adapters,
            "modules": modules,
            "data": {
                "night_count": len(reports),
                "date_from": date_from,
                "date_to": date_to,
                "latest_score": latest_score,
            },
        }

    return application


#: Die ASGI-App für ``uvicorn webui.app:app``. ``None`` ohne ``fastapi``.
app: "FastAPI | None"

if _FASTAPI_AVAILABLE:
    app = _create_app()
else:
    app = None
    logger.warning(
        "fastapi ist nicht installiert — das Web-Dashboard steht nicht zur "
        "Verfügung. Installation: pip install fastapi uvicorn"
    )
