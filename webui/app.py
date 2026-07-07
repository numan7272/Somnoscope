"""
FastAPI-Backend des Somnoscope-Dashboards.

Liefert das statische Frontend (``webui/static/``) aus und exponiert eine
schlanke JSON-API über den Report-Store aus :mod:`database`:

    * ``GET /``                   → ``index.html`` (das Dashboard)
    * ``GET /static/...``         → CSS/JS-Assets
    * ``GET /api/report/latest``  → jüngster SleepReport (404 ``no_data`` wenn leer)
    * ``GET /api/reports?limit=N``→ Liste der letzten N Reports (neueste zuerst)
    * ``GET /api/trends?days=N``  → Mehr-Nächte-Trends via
      :func:`analytics.compute_trends` (200 auch bei leerer Historie)
    * ``GET /api/coaching``       → Coaching-Text des lokalen LLM-Coach zur
      jüngsten Nacht (404 ``no_data`` wenn leer; in-memory gecacht pro Datum)

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

#: Obergrenze für den in-memory Coaching-Cache (Key: date|generated_at) —
#: verhindert unbegrenztes Wachstum im Dauerbetrieb (FIFO-Eviction).
_COACHING_CACHE_MAX = 64

# --------------------------------------------------------------------------
# Optionale Abhängigkeit: fastapi (Graceful Degradation, Kernprinzip 2)
# --------------------------------------------------------------------------
try:
    from fastapi import FastAPI, HTTPException, Query
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
        # In-Memory-Cache für Coaching-Texte (Key: report["date"]) plus Lock,
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
    async def coaching() -> dict[str, Any]:
        """
        Liefert den Coaching-Text des lokalen LLM-Coach zur jüngsten Nacht.

        Holt den jüngsten Report plus Historie (30 Nächte) aus dem Store und
        ruft :func:`llm_coach.generate_coaching` (lokales Ollama mit
        regelbasiertem Fallback, wirft nie). Da die Generierung bei laufendem
        Ollama einige Sekunden dauern kann, wird das Ergebnis in-memory pro
        ``report["date"]`` gecacht; wiederholte Aufrufe antworten sofort.
        Ein ``asyncio.Lock`` verhindert, dass parallele Requests dieselbe
        Nacht mehrfach generieren.

        Returns:
            ``{"enabled": bool, "text": str, "date": str}`` — ``enabled`` ist
            ``False`` (mit leerem Text), wenn der Coach per Config deaktiviert
            oder das Modul/die Config nicht verfügbar ist.

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

        date = str(report.get("date") or "")
        # Cache-Key enthält generated_at: ein neu gebauter Report (gleiches
        # Datum, neue Daten) bekommt frisches Coaching statt veraltetem Text.
        cache_key = f"{date}|{report.get('generated_at') or ''}"
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
                text = await generate_coaching(report, history, cfg)
                cache[cache_key] = text
                # Cache begrenzen: ältesten Eintrag (Einfüge-Reihenfolge) werfen.
                if len(cache) > _COACHING_CACHE_MAX:
                    cache.pop(next(iter(cache)))
        return {"enabled": True, "text": text, "date": date}

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
