/*
 * Somnoscope · Schlaf-Observatorium — First-Run-Onboarding (ES-Modul).
 *
 * Erstklassiger Empty-State für frische Installationen: liegen noch KEINE
 * Schlafberichte vor, verwandelt dieses Modul die knappe Leeransicht
 * (#state-empty) in ein einladendes Onboarding mit drei Schritten
 * (Datenquelle wählen → Demo-Historie via `python main.py --backfill 30` →
 * Dashboard erkunden) plus Privacy-Hinweis und „Nach Nächten suchen“-Aktion.
 *
 * Integration (durch app.js, nach dem Laden von /api/report/latest+/api/reports):
 *     window.SomnoscopeOnboarding.init(reportsCount)
 *   - reportsCount <= 0 → Onboarding wird in #state-empty montiert; die
 *     Sichtbarkeit steuert weiterhin die bestehende State-Maschine von app.js
 *     (applyView/showState) — beim View-Wechsel oder sobald Daten existieren,
 *     verschwindet das Onboarding automatisch mit der Sektion.
 *   - reportsCount > 0 → sauberer Teardown, die Original-Leeransicht wird
 *     wiederhergestellt (Titel, Botschaft, data-i18n).
 *
 * Accessibility:
 *   - Fokus wandert auf die Onboarding-Überschrift (tabindex="-1"),
 *     aber nur, wenn kein anderes Element fokussiert ist.
 *   - Die Lede trägt aria-live="polite" für den Einblendmoment.
 *   - <ol> liefert die Schritt-Semantik; die römischen Ziffern sind rein
 *     dekorativ (aria-hidden). Voll tastaturbedienbar (nativer Button).
 *   - prefers-reduced-motion: Entrance-Choreografie entfällt (JS + CSS).
 *
 * i18n: alle sichtbaren Strings laufen über t()/MESSAGES aus i18n.js
 * (Keys "onboarding.*"). Fehlen die Keys noch (Integration ausstehend),
 * greift ein identisches lokales Fallback-Wörterbuch — nie der rohe Key.
 * Sprachwechsel: eigener onLangChange-Callback rendert die Texte neu.
 *
 * Kernprinzipien: keine externen Assets, keine Requests, alles defensiv —
 * fehlende DOM-Knoten führen zu einem No-Op, nie zu Exceptions.
 */
"use strict";

import { t, getLang, onLangChange, applyStatic, MESSAGES } from "./i18n.js";

const HOST_ID = "state-empty";
const TITLE_ID = "empty-title";

/** Bewusst unübersetzte, technische Bezeichner (Kommandos/Dateien). */
const CMD_BACKFILL = "python main.py --backfill 30";
/** Docker-Self-Hosting-Variante desselben Kommandos (docker-compose.yml). */
const CMD_BACKFILL_DOCKER = "docker compose run --rm tracker python main.py --backfill 30";
const FILE_CONFIG = "config.yaml";

/**
 * Lokales Fallback-Wörterbuch — identisch zu den "onboarding.*"-Keys, die in
 * i18n.js (MESSAGES.de/.en) integriert werden. Es greift NUR, solange die
 * zentralen Keys fehlen; danach ist i18n.js die einzige Quelle der Wahrheit.
 */
const FALLBACK = {
  de: {
    "onboarding.title": "Willkommen im Observatorium",
    "onboarding.lede": "Das Observatorium ist bereit. Drei Schritte, und deine erste Nacht leuchtet am Himmel.",
    "onboarding.stepsAria": "Erste Schritte: drei Etappen bis zum ersten Schlafbericht",
    "onboarding.step1Title": "Datenquelle wählen",
    "onboarding.step1a": "Der Simulations-Adapter ist ab Werk aktiv, ganz ohne Hardware. Fitbit (Cloud, opt-in) oder Muse-EEG aktivierst du optional in ",
    "onboarding.step1b": ".",
    "onboarding.step2Title": "Demo-Historie erzeugen",
    "onboarding.step2a": "Starte ",
    "onboarding.step2b": " im Projektordner: 30 synthetische Nächte zum Erkunden.",
    "onboarding.step2Docker": "Im Docker-Setup: ",
    "onboarding.step3Title": "Dashboard erkunden",
    "onboarding.step3Text": "Danach neu laden. Score-Orb, Hypnogramm, Verlauf und System-Ansicht warten schon.",
    "onboarding.refresh": "Nach Nächten suchen",
    "onboarding.refreshAria": "Seite neu laden und nach neuen Schlafberichten suchen",
    "onboarding.privacy": "Alle Daten bleiben auf diesem Gerät. 100 % lokal, keine Cloud.",
  },
  en: {
    "onboarding.title": "Welcome to the observatory",
    "onboarding.lede": "The observatory is ready. Three steps, and your first night will light up the sky.",
    "onboarding.stepsAria": "Getting started: three steps to your first sleep report",
    "onboarding.step1Title": "Choose a data source",
    "onboarding.step1a": "The simulation adapter is on by default, no hardware needed. Optionally enable Fitbit (cloud, opt-in) or Muse EEG in ",
    "onboarding.step1b": ".",
    "onboarding.step2Title": "Create a demo history",
    "onboarding.step2a": "Run ",
    "onboarding.step2b": " in the project folder: 30 synthetic nights to explore.",
    "onboarding.step2Docker": "In the Docker setup: ",
    "onboarding.step3Title": "Explore the dashboard",
    "onboarding.step3Text": "Then reload. The score orb, hypnogram, trends and system view are waiting.",
    "onboarding.refresh": "Check for nights",
    "onboarding.refreshAria": "Reload the page and look for new sleep reports",
    "onboarding.privacy": "All data stays on this device. 100% local, no cloud.",
  },
};

/**
 * Übersetzt einen Onboarding-Key: bevorzugt das zentrale MESSAGES-Wörterbuch
 * (via t(), damit künftige Platzhalter-Logik greift), sonst das lokale
 * Fallback. Gibt notfalls den Key zurück — nie eine Exception.
 */
function msg(key) {
  const lang = getLang() === "en" ? "en" : "de";
  try {
    if (typeof MESSAGES?.[lang]?.[key] === "string") return t(key);
  } catch {
    /* defensiv: MESSAGES unerwartet geformt → Fallback */
  }
  const dict = FALLBACK[lang] ?? FALLBACK.de;
  return typeof dict[key] === "string" ? dict[key] : key;
}

/* ------------------------------------------------------------------------- *
 * Übersetzungs-Bindings: Knoten ↔ Key, Re-Render beim Sprachwechsel
 * ------------------------------------------------------------------------- */

/** @type {{node: Element, key: string, attr: string | null}[]} */
const bindings = [];

function applyBinding(b) {
  const value = msg(b.key);
  if (b.attr) b.node.setAttribute(b.attr, value);
  else b.node.textContent = value;
}

/** Registriert einen Knoten für einen i18n-Key und wendet ihn sofort an. */
function bind(node, key, attr = null) {
  const b = { node, key, attr };
  bindings.push(b);
  applyBinding(b);
  return node;
}

function retranslate() {
  for (const b of bindings) {
    try {
      applyBinding(b);
    } catch {
      /* einzelner Knoten defekt → Rest weiter übersetzen */
    }
  }
}

/* ------------------------------------------------------------------------- *
 * Mount / Unmount in #state-empty (Sichtbarkeit steuert app.js)
 * ------------------------------------------------------------------------- */

let mounted = false;
let hostRef = null;
let addedNodes = [];
let hiddenMsg = null;    // die ausgeblendete Original-Botschaft (.state-msg)
let savedTitle = null;   // Original-Zustand der Überschrift für den Teardown
let enterTimer = 0;

function prefersReducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Füllt einen Absatz aus Teilen: { key } (übersetzbarer Span) oder { code }. */
function fillParts(p, parts) {
  for (const part of parts) {
    if (part.code) {
      const code = document.createElement("code");
      code.textContent = part.code;
      p.appendChild(code);
    } else {
      p.appendChild(bind(document.createElement("span"), part.key));
    }
  }
}

/**
 * Ein Schritt der Anleitung: römische Ziffer (dekorativ) + Titel + Text.
 * parts: Liste aus { key } (übersetzbarer Span) oder { code } (Monospace).
 * altParts (optional): dezente Zusatzzeile, z.B. die Docker-Variante
 * eines Kommandos (.onboarding-step-alt).
 */
function makeStep(roman, titleKey, parts, altParts = null) {
  const li = document.createElement("li");
  li.className = "onboarding-step";

  const no = document.createElement("span");
  no.className = "onboarding-step-no";
  no.setAttribute("aria-hidden", "true");
  no.textContent = roman;

  const body = document.createElement("div");
  body.className = "onboarding-step-body";

  const title = document.createElement("h2");
  title.className = "onboarding-step-title";
  bind(title, titleKey);

  const text = document.createElement("p");
  text.className = "onboarding-step-text";
  fillParts(text, parts);

  body.append(title, text);
  if (Array.isArray(altParts) && altParts.length) {
    const alt = document.createElement("p");
    alt.className = "onboarding-step-alt";
    fillParts(alt, altParts);
    body.appendChild(alt);
  }
  li.append(no, body);
  return li;
}

/**
 * Montiert das Onboarding in #state-empty: übernimmt die vorhandene
 * Überschrift (h1#empty-title, damit aria-labelledby der Sektion intakt
 * bleibt), blendet die knappe Standard-Botschaft aus und hängt Lede,
 * Schritte-Panel, Privacy-Hinweis und Aktions-Knopf an. Idempotent.
 */
function mount() {
  if (mounted) return true;
  const host = document.getElementById(HOST_ID);
  const title = document.getElementById(TITLE_ID);
  if (!host || !title) return false;
  hostRef = host;

  host.classList.add("has-onboarding");

  // Überschrift übernehmen: eigener Text, per Skript fokussierbar.
  savedTitle = { i18n: title.getAttribute("data-i18n"), text: title.textContent };
  title.removeAttribute("data-i18n"); // applyStatic() darf uns nicht überschreiben
  title.classList.add("onboarding-title");
  title.setAttribute("tabindex", "-1");
  bind(title, "onboarding.title");

  // Die Standard-Botschaft („…Starte python main.py…“) weicht dem Onboarding.
  hiddenMsg = host.querySelector(".state-msg");
  if (hiddenMsg) hiddenMsg.hidden = true;

  // Lede: kündigt den Einblendmoment auch für Screenreader an.
  const lede = document.createElement("p");
  lede.className = "onboarding-lede";
  lede.setAttribute("aria-live", "polite");
  bind(lede, "onboarding.lede");

  // Panel in der Glas-Sprache der Coach-Karte, mit Phasen-Spektrum am Kopf.
  const panel = document.createElement("div");
  panel.className = "onboarding-panel";

  const spectrum = document.createElement("span");
  spectrum.className = "onboarding-spectrum";
  spectrum.setAttribute("aria-hidden", "true");

  const steps = document.createElement("ol");
  steps.className = "onboarding-steps";
  bind(steps, "onboarding.stepsAria", "aria-label");
  steps.append(
    makeStep("I", "onboarding.step1Title", [
      { key: "onboarding.step1a" }, { code: FILE_CONFIG }, { key: "onboarding.step1b" },
    ]),
    makeStep("II", "onboarding.step2Title", [
      { key: "onboarding.step2a" }, { code: CMD_BACKFILL }, { key: "onboarding.step2b" },
    ], [
      // Docker-Self-Hosting: dort läuft der Backfill im tracker-Container.
      { key: "onboarding.step2Docker" }, { code: CMD_BACKFILL_DOCKER },
    ]),
    makeStep("III", "onboarding.step3Title", [
      { key: "onboarding.step3Text" },
    ]),
  );

  const privacy = document.createElement("p");
  privacy.className = "onboarding-privacy";
  bind(privacy, "onboarding.privacy");

  panel.append(spectrum, steps, privacy);

  // Aktion: nach dem Backfill im Terminal einfach neu suchen (Reload) —
  // gleiche Semantik wie der Retry-Knopf des Fehlerzustands.
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "retry onboarding-refresh";
  bind(refresh, "onboarding.refresh");
  bind(refresh, "onboarding.refreshAria", "aria-label");
  refresh.addEventListener("click", () => window.location.reload());

  addedNodes = [lede, panel, refresh];
  host.append(...addedNodes);

  // Entrance-Choreografie nur beim ersten Einblenden — und nie bei
  // reduzierter Bewegung. Nach Ablauf wird die Klasse entfernt, damit
  // View-Wechsel (Nacht ↔ Verlauf) die Animation nicht erneut abspielen.
  if (!prefersReducedMotion()) {
    host.classList.add("onboarding-enter");
    enterTimer = window.setTimeout(() => {
      host.classList.remove("onboarding-enter");
      enterTimer = 0;
    }, 1800);
  }

  mounted = true;
  return true;
}

/** Baut das Onboarding zurück und stellt die Original-Leeransicht wieder her. */
function unmount() {
  if (!mounted) return;

  for (const node of addedNodes) {
    try { node.remove(); } catch { /* Knoten ggf. schon entfernt */ }
  }
  addedNodes = [];

  const title = document.getElementById(TITLE_ID);
  if (title) {
    title.classList.remove("onboarding-title");
    title.removeAttribute("tabindex");
    if (savedTitle?.i18n) title.setAttribute("data-i18n", savedTitle.i18n);
    title.textContent = savedTitle?.text ?? "";
  }
  if (hiddenMsg) {
    hiddenMsg.hidden = false;
    hiddenMsg = null;
  }
  if (hostRef) {
    hostRef.classList.remove("has-onboarding", "onboarding-enter");
    // Wiederhergestellte data-i18n-Texte in der aktuellen Sprache auffrischen.
    try { applyStatic(hostRef); } catch { /* Fallback-Text bleibt stehen */ }
  }
  if (enterTimer) {
    clearTimeout(enterTimer);
    enterTimer = 0;
  }

  bindings.length = 0;
  savedTitle = null;
  mounted = false;
}

/**
 * Fokus-Management: Nach dem Einblenden wandert der Fokus auf die
 * Onboarding-Überschrift — aber nur, wenn die Sektion sichtbar ist und
 * nicht bereits etwas anderes fokussiert wurde (z.B. per Tastatur).
 * Bewusst setTimeout statt requestAnimationFrame: app.js blendet
 * #state-empty erst NACH init() via showState("empty") ein, und rAF
 * feuert in Hintergrund-Tabs nicht.
 */
function focusTitle() {
  window.setTimeout(() => {
    const title = document.getElementById(TITLE_ID);
    if (!mounted || !title || !hostRef || hostRef.hidden) return;
    const active = document.activeElement;
    if (active && active !== document.body && active !== document.documentElement) return;
    try {
      title.focus({ preventScroll: false });
    } catch {
      try { title.focus(); } catch { /* kein Fokus möglich → still bleiben */ }
    }
  }, 60);
}

/* ------------------------------------------------------------------------- *
 * Öffentliche API
 * ------------------------------------------------------------------------- */

/**
 * Einstiegspunkt für app.js — nach dem Laden von /api/reports aufrufen.
 *
 * @param {number} reportsCount Anzahl vorhandener Schlafberichte.
 * @returns {boolean} true, wenn das Onboarding jetzt sichtbar montiert ist.
 */
function init(reportsCount) {
  const count = Number(reportsCount);
  const isEmpty = Number.isFinite(count) ? count <= 0 : !reportsCount;
  if (isEmpty) {
    const ok = mount();
    if (ok) focusTitle();
    return ok;
  }
  unmount();
  return false;
}

// Sprachwechsel: nur die eigenen Bindings neu anwenden (applyStatic von
// i18n.js fasst unsere Knoten nicht an — sie tragen kein data-i18n).
onLangChange(() => {
  if (mounted) retranslate();
});

window.SomnoscopeOnboarding = Object.freeze({
  init,
  show: () => init(0),
  hide: () => init(1),
});
