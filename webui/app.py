"""
FastAPI-Backend des Somnoscope-Dashboards.

Liefert das statische Frontend (``webui/static/``) aus und exponiert eine
schlanke JSON-API über den Report-Store aus :mod:`database`:

    * ``GET /``                   → ``index.html`` (das Dashboard)
    * ``GET /static/...``         → CSS/JS-Assets
    * ``GET /api/report/latest``  → jüngster SleepReport (404 ``no_data`` wenn leer)
    * ``GET /api/reports?limit=N``→ Liste der letzten N Reports (neueste zuerst)

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

logger = logging.getLogger(__name__)

#: Absoluter Pfad zum Frontend (index.html, style.css, app.js).
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

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


def _create_app() -> "FastAPI":
    """
    Baut die FastAPI-Anwendung inkl. Routen, Static-Mount und Lifespan.

    Returns:
        Die konfigurierte :class:`fastapi.FastAPI`-Instanz.
    """

    @asynccontextmanager
    async def _lifespan(application: FastAPI):
        """
        Lifespan-Handler: Store einmalig öffnen, bei Shutdown schließen.

        Args:
            application: Die FastAPI-Instanz; der Store wird in
                ``application.state.store`` abgelegt.
        """
        # _open_store() macht blockierendes I/O (Config lesen, SQLite öffnen) —
        # im async Lifespan daher in einen Thread auslagern (Asyncio-First).
        store = await asyncio.to_thread(_open_store)
        application.state.store = store
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
