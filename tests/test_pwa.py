"""
Tests fuer die PWA-Routes des FastAPI-Dashboards (:mod:`webui.app`).

Abgedeckt (via ``fastapi.testclient.TestClient``):
    * ``GET /sw.js`` -> 200, JavaScript-Content-Type und Header
      ``Service-Worker-Allowed: /`` (weiter SW-Scope trotz /static-Ablage).
    * ``GET /manifest.webmanifest`` -> 200, ``application/manifest+json``,
      Body ist gueltiges JSON mit ``start_url "/"`` und mindestens einem Icon.
    * ``GET /static/icon.svg`` -> 200 (App-Marke, lokal ausgeliefert).
    * Alle im Service Worker praegecachten Shell-Assets sind ueber die App
      erreichbar (sonst schlaegt ``cache.addAll`` beim install-Event fehl).
    * ``index.html`` verlinkt Manifest + theme-color; ``app.js`` registriert
      den SW defensiv; ``sw.js``/Manifest referenzieren keine externen URLs
      (100 % offline, Kernprinzip 1).

Graceful: Fehlt ``fastapi`` (oder der TestClient-Unterbau ``httpx``), wird
die gesamte Datei uebersprungen — die Kern-Suite bleibt lauffaehig.

Der Store wird ueber ``monkeypatch`` von :func:`webui.app._open_store`
neutralisiert (``None``); fuer die PWA-Routes wird kein Store gebraucht und
es wird NIE die echte ``config.yaml`` des Projekts gelesen.
"""

from __future__ import annotations

import json
import logging
import re

import pytest

pytest.importorskip("fastapi", reason="fastapi nicht installiert — PWA-Tests uebersprungen")
pytest.importorskip("httpx", reason="httpx (TestClient-Unterbau) nicht installiert")

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

logger = logging.getLogger(__name__)

# WICHTIG: ``webui/__init__.py`` re-exportiert ``app`` und ueberschattet damit
# das Submodul-Attribut ``webui.app`` (Paket-Attribut zeigt auf die
# FastAPI-Instanz statt aufs Modul). Deshalb das Modul explizit laden.
webui_app = importlib.import_module("webui.app")

if webui_app.app is None:  # pragma: no cover — defensiv
    pytest.skip("webui.app.app ist None — fastapi fehlt.", allow_module_level=True)

#: Muster fuer externe Referenzen — die PWA muss 100 % offline sein.
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """
    TestClient ohne Report-Store (PWA-Routes brauchen keine Daten).

    ``_open_store`` wird auf ``None`` gepatcht, damit der Lifespan-Handler
    weder ``config.yaml`` liest noch eine Datenbank oeffnet.

    Yields:
        Ein betretener :class:`TestClient` (Lifespan aktiv).
    """
    monkeypatch.setattr(webui_app, "_open_store", lambda: None)
    with TestClient(webui_app.app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# GET /sw.js — Service Worker an der Wurzel mit weitem Scope
# ---------------------------------------------------------------------------


def test_sw_js_served_with_scope_header(client: TestClient) -> None:
    """``GET /sw.js`` liefert 200, JavaScript und Service-Worker-Allowed: /."""
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    # Der Header erlaubt dem an /sw.js registrierten SW den Scope "/".
    assert resp.headers["service-worker-allowed"] == "/"


def test_sw_js_body_is_the_service_worker(client: TestClient) -> None:
    """Der Body ist der echte SW: versionierter Cache + Event-Handler."""
    body = client.get("/sw.js").text
    # Versionierter Cache-Name (z.B. somnoscope-shell-v1).
    assert re.search(r"somnoscope-shell-v\d+", body)
    for event in ("install", "activate", "fetch"):
        assert f'addEventListener("{event}"' in body, (
            f"sw.js muss einen {event}-Handler registrieren."
        )
    # Selbstgenuegsam: kein importScripts-AUFRUF (das Wort darf in
    # Kommentaren vorkommen), keine externen URLs.
    assert "importScripts(" not in body
    assert not _EXTERNAL_URL.search(body)


def test_sw_precached_shell_assets_are_reachable(client: TestClient) -> None:
    """
    Jedes im SW gelistete Shell-Asset antwortet mit 200.

    ``cache.addAll`` beim install-Event schlaegt komplett fehl, sobald auch
    nur EIN Asset nicht ausgeliefert wird — die Liste im sw.js muss also
    dauerhaft mit den echten Routen uebereinstimmen.
    """
    body = client.get("/sw.js").text
    match = re.search(r"SHELL_ASSETS\s*=\s*\[(.*?)\]", body, re.DOTALL)
    assert match, "sw.js muss eine SHELL_ASSETS-Liste enthalten."
    assets = re.findall(r'"([^"]+)"', match.group(1))
    assert assets, "SHELL_ASSETS darf nicht leer sein."
    assert "/" in assets, "Die Startseite gehoert in die App-Shell."
    for asset in assets:
        assert asset.startswith("/"), f"Nur lokale Pfade erlaubt: {asset!r}"
        resp = client.get(asset)
        logger.debug("Shell-Asset %s -> %s", asset, resp.status_code)
        assert resp.status_code == 200, f"Shell-Asset nicht erreichbar: {asset!r}"


# ---------------------------------------------------------------------------
# GET /manifest.webmanifest — installierbare PWA
# ---------------------------------------------------------------------------


def test_manifest_served_as_manifest_json(client: TestClient) -> None:
    """``GET /manifest.webmanifest`` liefert 200 mit korrektem Media-Type."""
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert "application/manifest+json" in resp.headers["content-type"]


def test_manifest_is_valid_pwa_manifest(client: TestClient) -> None:
    """Body ist gueltiges JSON mit den Pflichtfeldern einer installierbaren PWA."""
    resp = client.get("/manifest.webmanifest")
    manifest = json.loads(resp.text)  # wirft bei ungueltigem JSON -> Test rot

    assert manifest["name"] == "Somnoscope"
    assert manifest["short_name"] == "Somnoscope"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["lang"] == "de"
    # Farbwelt des Dashboards (--bg) — Splash + Statusleiste bleiben dunkel.
    assert manifest["background_color"] == "#070a1c"
    assert manifest["theme_color"] == "#070a1c"

    icons = manifest["icons"]
    assert isinstance(icons, list) and len(icons) >= 1
    icon = icons[0]
    assert icon["type"] == "image/svg+xml"
    assert icon["sizes"] == "any"
    assert "maskable" in icon["purpose"]
    # Icon lokal und ueber die App erreichbar (kein externes Asset).
    assert icon["src"].startswith("/")
    assert client.get(icon["src"]).status_code == 200


def test_manifest_has_no_external_urls(client: TestClient) -> None:
    """Das Manifest referenziert keinerlei externe URLs (100 % offline)."""
    assert not _EXTERNAL_URL.search(client.get("/manifest.webmanifest").text)


# ---------------------------------------------------------------------------
# Statische PWA-Assets & Verdrahtung in der Shell
# ---------------------------------------------------------------------------


def test_icon_svg_served(client: TestClient) -> None:
    """``GET /static/icon.svg`` liefert die App-Marke als SVG (200)."""
    resp = client.get("/static/icon.svg")
    assert resp.status_code == 200
    assert "<svg" in resp.text
    # Eigenstaendige Marke ohne externe Referenzen. Die xmlns-Deklaration
    # (http://www.w3.org/...) ist ein reiner XML-Namespace-Bezeichner, kein
    # Netzwerk-Zugriff — sie wird vor der Pruefung ausgeblendet.
    without_ns = re.sub(r'xmlns(?::\w+)?="[^"]*"', "", resp.text)
    assert not _EXTERNAL_URL.search(without_ns)


def test_index_html_wires_up_the_pwa(client: TestClient) -> None:
    """``index.html`` verlinkt Manifest, theme-color und apple-touch-icon."""
    html = client.get("/").text
    assert 'rel="manifest"' in html
    assert 'href="/manifest.webmanifest"' in html
    assert '<meta name="theme-color" content="#070a1c">' in html
    assert 'rel="apple-touch-icon"' in html


def test_app_js_registers_service_worker_defensively(client: TestClient) -> None:
    """``app.js`` registriert /sw.js nur, wenn der Browser SW unterstuetzt."""
    body = client.get("/static/app.js").text
    assert '"serviceWorker" in navigator' in body, (
        "Registrierung muss Feature-Detection nutzen (kein Crash ohne SW-Support)."
    )
    assert 'register("/sw.js"' in body
