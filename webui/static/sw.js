/*
 * Somnoscope · Service Worker — Offline-Faehigkeit der App-Shell.
 *
 * Strategie (100 % offline, keine externen URLs):
 *   - install:  App-Shell (HTML, CSS, JS, Three.js-Vendor, Manifest, Icon)
 *               vollstaendig praecachen.
 *   - activate: alte, versionierte Caches aufraeumen.
 *   - fetch:    Shell-Assets & Navigationen → CACHE-FIRST (Netz-Fallback);
 *               /api/* → NETWORK-FIRST ohne Cache-Fallback: Gesundheitsdaten
 *               werden bewusst NICHT gecacht, offline schlaegt die API sauber
 *               fehl und die Empty/Error-States des Dashboards greifen.
 *
 * Selbstgenuegsam: keine importScripts, keine CDNs, keine Fonts.
 */
"use strict";

/** Versionierter Cache-Name — bei Shell-Aenderungen hochzaehlen.
 *  Zusaetzlich revalidiert shellCacheFirst im Hintergrund (stale-while-
 *  revalidate), sodass Aenderungen Bestandsclients auch ohne Bump erreichen. */
const CACHE_NAME = "somnoscope-shell-v2";

/** Die App-Shell: alles, was das Dashboard-Geruest offline braucht. */
const SHELL_ASSETS = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/scene.js",
  "/static/trends.js",
  "/static/vendor/three.module.min.js",
  "/manifest.webmanifest",
  "/static/icon.svg",
];

/* --------------------------------------------------------------------------
 * install: App-Shell praecachen, dann sofort aktiv werden.
 * -------------------------------------------------------------------------- */
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

/* --------------------------------------------------------------------------
 * activate: veraltete Cache-Versionen entfernen, Kontrolle uebernehmen.
 * -------------------------------------------------------------------------- */
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("somnoscope-") && key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

/* --------------------------------------------------------------------------
 * fetch: Routing der Strategien.
 * -------------------------------------------------------------------------- */

/**
 * NETWORK-FIRST fuer /api/*: immer frische Daten versuchen; offline gibt es
 * eine saubere 503-Antwort statt veralteter Gesundheitsdaten aus dem Cache.
 */
async function apiNetworkFirst(request) {
  try {
    return await fetch(request);
  } catch (err) {
    return new Response(
      JSON.stringify({ detail: "offline" }),
      {
        status: 503,
        statusText: "Service Unavailable",
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}

/**
 * CACHE-FIRST fuer Shell-Assets: sofort aus dem Cache, sonst Netz (und das
 * Ergebnis fuer das naechste Mal ablegen). Navigations-Requests fallen
 * offline auf die gecachte index.html ("/") zurueck.
 */
async function shellCacheFirst(request, isNavigation) {
  const cache = await caches.open(CACHE_NAME);
  const key = isNavigation ? "/" : request;
  const cached = await cache.match(key);
  if (cached) {
    // Stale-while-revalidate: gecachte Shell sofort ausliefern, im Hintergrund
    // aktualisieren — so erreichen Frontend-Aenderungen Bestandsclients beim
    // naechsten Laden auch OHNE Cache-Namen-Bump (best effort, nie werfend).
    revalidateShell(cache, request, key);
    return cached;
  }
  try {
    const response = await fetch(request);
    // Nur vollwertige Antworten cachen (kein 404/500 einfrieren).
    if (response.ok) cache.put(key, response.clone());
    return response;
  } catch (err) {
    // Offline und nicht im Cache: bei Navigationen zur Not die Shell.
    const fallback = await cache.match("/");
    if (isNavigation && fallback) return fallback;
    throw err;
  }
}

/** Aktualisiert einen Shell-Eintrag im Hintergrund; Fehler (offline) egal. */
function revalidateShell(cache, request, key) {
  fetch(request)
    .then((response) => {
      if (response && response.ok) cache.put(key, response.clone());
    })
    .catch(() => { /* offline: gecachte Version bleibt gueltig */ });
}

/**
 * Navigationen: NUR "/" wird cache-first aus der App-Shell bedient. Andere
 * Navigations-Pfade (z.B. FastAPIs /docs, /redoc, /openapi.json) laufen
 * NETWORK-FIRST und fallen offline auf die gecachte Shell zurueck. So verdeckt
 * der SW online keine echten Routen und der "/"-Cache-Eintrag kann nicht durch
 * eine Fremd-Navigation vergiftet werden.
 */
async function navigationHandler(request) {
  const url = new URL(request.url);
  if (url.pathname === "/") return shellCacheFirst(request, true);
  try {
    return await fetch(request);
  } catch (err) {
    const cache = await caches.open(CACHE_NAME);
    const shell = await cache.match("/");
    if (shell) return shell;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Fremde Origins gehen den SW nichts an (es gibt ohnehin keine — offline).
  if (url.origin !== self.location.origin) return;

  // API: NETWORK-FIRST, nie aus dem Cache.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(apiNetworkFirst(request));
    return;
  }

  // Navigationen (Adresszeile, Reload, Install-Start): nur "/" aus der Shell,
  // fremde Pfade network-first (siehe navigationHandler).
  if (request.mode === "navigate") {
    event.respondWith(navigationHandler(request));
    return;
  }

  // Shell-Assets (Pfadvergleich ohne Query) → CACHE-FIRST.
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith(shellCacheFirst(request, false));
  }
  // Alles andere (z.B. unbekannte statische Pfade) laeuft normal ins Netz.
});
