"""
WebUI-Modul von Somnoscope — lokales Nacht-Dashboard.

Stellt das FastAPI-Backend (:mod:`webui.app`) und die statischen
Frontend-Assets (``webui/static/``) bereit. Das Dashboard visualisiert den
zuletzt gespeicherten :data:`SleepReport` (Sleep-Score, Hypnogramm,
Phasen-Verteilung, Vitaltrends, Klima) — vollständig offline, ohne externe
CDNs oder Fonts (Kernprinzip 1: Edge AI / Privacy First).

Öffentliche API:
    * :data:`webui.app.app` — die ASGI-Anwendung (z.B. für
      ``uvicorn webui.app:app``). ``None``, falls ``fastapi`` nicht
      installiert ist (Graceful Degradation).
"""

from __future__ import annotations

from webui.app import app

__all__ = ["app"]
