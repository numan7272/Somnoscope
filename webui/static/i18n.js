/*
 * Somnoscope · Schlaf-Observatorium — Zweisprachigkeit DE/EN (ES-Modul).
 *
 * Kleine, abhängigkeitsfreie i18n-Schicht für das Dashboard:
 *   - t(key, params):   Lookup in der aktuellen Sprache, {name}-Platzhalter
 *                        werden aus params ersetzt. Fehlt ein Key, wird der
 *                        Key selbst zurückgegeben (+ console.warn) — nie eine
 *                        Exception (defensiv wie der Rest des Frontends).
 *   - getLang/setLang:  aktuelle Sprache ("de" | "en"); setLang persistiert
 *                        in localStorage ("somnoscope-lang"), setzt
 *                        document.documentElement.lang, wendet die statischen
 *                        Texte neu an (applyStatic) und ruft alle via
 *                        onLangChange registrierten Callbacks auf.
 *   - initLang:         Startsprache aus localStorage, sonst aus
 *                        navigator.language ("de*" → de, sonst en; Default de).
 *   - applyStatic:      übersetzt alle [data-i18n] (textContent) und
 *                        [data-i18n-aria] (aria-label) unterhalb von root.
 *   - getLocale:        Intl-Locale je Sprache ("de-DE" / "en-GB") für
 *                        Datums-/Zeit-/Zahlenformatierung.
 *
 * MESSAGES enthält BEIDE Wörterbücher mit IDENTISCHEN Keys (Key-Parität
 * ist Pflicht — siehe Self-Check). Keine externen Requests, alles lokal.
 *
 * Hinweis: Der Coach-Text aus /api/coaching bleibt vorerst deutsch (der
 * LLM-Prompt ist deutsch) — übersetzt wird nur die statische Coach-UI.
 */
"use strict";

const STORAGE_KEY = "somnoscope-lang";

/** Intl-Locale je Sprache — zentral, damit alle Formatter konsistent sind. */
const LOCALES = { de: "de-DE", en: "en-GB" };

/**
 * Alle sichtbaren UI-Strings des Dashboards, DE und EN mit identischen Keys.
 * Platzhalter im Format {name} werden von t() aus params ersetzt.
 */
export const MESSAGES = {
  de: {
    "app.title": "Somnoscope · Schlaf-Observatorium",
    "skip": "Zum Inhalt springen",
    "masthead.role": "Schlaf · Observatorium",

    "nav.aria": "Ansicht wechseln",
    "nav.night": "Diese Nacht",
    "nav.trends": "Verlauf",
    "nav.system": "System",

    "lang.aria": "Sprache wählen",
    "lang.de": "Deutsch",
    "lang.en": "Englisch",

    "common.retry": "Erneut versuchen",
    "common.errorTitle": "Verbindung unterbrochen",
    "common.fromToClock": "{from} bis {to} Uhr",
    "common.fromToPlain": "{from} bis {to}",
    "common.avg": "Ø",
    "common.pct": "{pct} %",

    "night.loading": "Die Nacht wird geladen …",
    "night.emptyTitle": "Noch keine Schlafdaten",
    "night.emptyMsg1": "Das Observatorium ist bereit, aber der Himmel ist noch leer.",
    "night.emptyMsg2a": "Starte ",
    "night.emptyMsg2b": ", um eine Nacht zu erfassen.",
    "night.errorMsg": "Die Schlafdaten konnten nicht geladen werden.",

    "score.label": "Schlaf-Score",
    "verdict.excellent": "Eine ausgezeichnete Nacht.",
    "verdict.good": "Eine erholsame Nacht.",
    "verdict.mixed": "Eine durchwachsene Nacht.",
    "verdict.restless": "Eine unruhige Nacht.",
    "verdict.hard": "Eine schwere Nacht.",
    "strip.caption": "Verlauf der Nacht",
    "strip.aria": "Schlafphasen-Verlauf von {from} bis {to} Uhr",

    "chapter.night": "Die Nacht im Verlauf",
    "chapter.metrics": "Kennzahlen",
    "chapter.vitals": "Vitalwerte",
    "chapter.climate": "Schlafklima",
    "chapter.history": "Letzte Nächte",
    "chapter.coach": "Schlaf-Coach",

    "stage.wake": "Wach",
    "stage.rem": "REM",
    "stage.light": "Leichtschlaf",
    "stage.deep": "Tiefschlaf",
    "hypno.rowWake": "Wach",
    "hypno.rowRem": "REM",
    "hypno.rowLight": "Leicht",
    "hypno.rowDeep": "Tief",
    "hypno.segTitle": "{stage}: {from} bis {to} Uhr",
    "hypno.caption": "Hypnogramm der Nacht von {from} bis {to} Uhr. Die Phasen-Bilanz in Minuten steht in der folgenden Liste.",
    "stages.pctOfNight": "{pct} % der Nacht",

    "metrics.totalSleep": "Gesamtschlaf",
    "metrics.totalSleepNote": "tatsächlich geschlafen",
    "metrics.timeInBed": "Zeit im Bett",
    "metrics.efficiency": "Schlafeffizienz",
    "metrics.efficiencyNote": "Schlafanteil der Bettzeit",
    "metrics.latency": "Einschlaflatenz",
    "metrics.latencyNote": "bis zur ersten Schlafphase",
    "metrics.waso": "Wach nach dem Einschlafen",
    "metrics.wasoNote": "WASO",

    "vitals.hr": "Puls",
    "vitals.hrUnit": "bpm Ø",
    "vitals.hrSub": "Tiefster Wert der Nacht: {v} bpm",
    "vitals.hrv": "HRV",
    "vitals.hrvUnit": "ms Ø",
    "vitals.hrvSub": "Herzratenvariabilität, höher ist erholter",
    "vitals.spo2": "SpO₂",
    "vitals.spo2Unit": "% Ø",
    "vitals.spo2Sub": "Sauerstoffsättigung im Blut",
    "vitals.temp": "Hauttemperatur",
    "vitals.tempUnit": "°C Ø",

    "climate.co2": "CO₂",
    "climate.co2Note": "Durchschnitt im Schlafzimmer",
    "climate.temp": "Raumtemperatur",
    "climate.humidity": "Luftfeuchte",
    "climate.avgNote": "Durchschnitt",

    "history.aria": "Schlaf-Score der letzten {n} Nächte, von {from} bis {to}",
    "history.barTitle": "{date}: Score {score}",

    "coach.cardAria": "Coaching-Empfehlung des lokalen Schlaf-Coach",
    "coach.status": "Analysiere deine Nacht …",
    "coach.note": "Vom lokalen Modell verfasst — deine Daten verlassen dieses Gerät nicht.",

    "frame.nightOf": "Nacht vom {date}",
    "frame.source": "Quelle: {source}",
    "frame.generated": "Auswertung vom {date}, {time} Uhr",

    "export.title": "Daten exportieren",
    "export.hint": "Deine Nächte als CSV/JSON — 100 % lokal.",
    "export.scopeAll": "Bis zu 365 Nächte",
    "export.scopeDays": "Die letzten {days} Nächte",
    "export.ariaCsv": "{scope} als CSV-Datei herunterladen",
    "export.ariaJson": "{scope} als JSON-Datei herunterladen",
    "colophon.claim": "100 % lokal ausgewertet · keine Cloud",

    "trends.title": "Verlauf",
    "trends.rangeAria": "Zeitraum wählen",
    "trends.nights7": "7 Nächte",
    "trends.nights14": "14 Nächte",
    "trends.nights30": "30 Nächte",
    "trends.loading": "Der Verlauf wird geladen …",
    "trends.emptyTitle": "Noch zu wenig Historie",
    "trends.emptyMsg": "Noch zu wenig Historie — erfasse mehr Nächte.",
    "trends.errorMsg": "Der Verlauf konnte nicht geladen werden.",
    "trends.chAvg": "Im Durchschnitt",
    "trends.chScore": "Score im Verlauf",
    "trends.chStages": "Phasen über die Nächte",
    "trends.chVitals": "Effizienz & HRV",
    "trends.chBalance": "Bilanz",
    "trends.labelEff": "Schlafeffizienz",
    "trends.labelHrv": "HRV",
    "trends.tooFew": "Für diesen Zeitraum liegen zu wenige Werte vor.",
    "trends.statScore": "Ø Schlaf-Score",
    "trends.statScoreNote": "von 100 Punkten",
    "trends.statEff": "Ø Effizienz",
    "trends.statDur": "Ø Schlafdauer",
    "trends.statDurNote": "pro Nacht",
    "trends.statHrv": "Ø HRV",
    "trends.statHrvNote": "Herzratenvariabilität",
    "trends.legendPct": "Ø {pct} %",
    "trends.best": "Beste Nacht",
    "trends.bestNote": "höchster Score im Zeitraum",
    "trends.worst": "Schwächste Nacht",
    "trends.worstNote": "niedrigster Score im Zeitraum",
    "trends.consistency": "Konsistenz",
    "trends.consistencyNote": "Streuung des Scores — kleiner ist gleichmäßiger",
    "trends.points": "Punkte",
    "trends.nightOne": "Nacht",
    "trends.nightsMany": "Nächte",
    "trends.capScore": "Schlaf-Score über {range}: zwischen {min} und {max} Punkten, zuletzt {last}.",
    "trends.capScoreEmpty": "Keine Score-Werte im Zeitraum.",
    "trends.capStages": "Anteil der Schlafphasen je Nacht in Prozent; die Durchschnittswerte stehen in der Legende darunter.",
    "trends.capEff": "Schlafeffizienz je Nacht in Prozent, Durchschnitt {avg} %.",
    "trends.capHrv": "Herzratenvariabilität je Nacht in Millisekunden, Durchschnitt {avg} ms.",
    "trends.ariaScore": "Liniendiagramm: Schlaf-Score über {range}",
    "trends.ariaStages": "Gestapeltes Balkendiagramm: Schlafphasen je Nacht über {range}",
    "trends.ariaEff": "Liniendiagramm: Schlafeffizienz über {range}",
    "trends.ariaHrv": "Liniendiagramm: HRV über {range}",

    "system.title": "System",
    "system.loading": "Der Systemstatus wird geladen …",
    "system.errorMsg": "Der Systemstatus konnte nicht geladen werden.",
    "system.chModules": "Module",
    "system.chAdapters": "Wearable-Adapter",
    "system.chData": "Daten-Bilanz",
    "system.modulesAria": "Status der Somnoscope-Module",
    "system.adaptersAria": "Konfigurierte Wearable-Adapter",
    "system.dataAria": "Bilanz der erfassten Schlafdaten",
    "system.noAdapters": "Keine Adapter konfiguriert — siehe ",
    "system.meta": "{name} · Version {version} · Zeitzone {tz}",
    "system.active": "aktiv",
    "system.inactive": "inaktiv",
    "system.on": "an",
    "system.off": "aus",
    "system.mod.wearable": "Wearable",
    "system.mod.wearableNote": "Schlafdaten-Quelle (BLE/Adapter)",
    "system.mod.climate": "Klimasensorik",
    "system.mod.climateNote": "CO₂, Temperatur, Luftfeuchte via MQTT",
    "system.mod.database": "Datenbank",
    "system.mod.databaseNote": "lokale Persistenz der Nächte",
    "system.mod.ml": "ML-Pipeline",
    "system.mod.mlNote": "Scoring und Phasen-Analyse",
    "system.mod.coach": "Schlaf-Coach",
    "system.mod.coachNote": "lokales Sprachmodell",
    "system.nightsRecorded": "Erfasste Nächte",
    "system.nightsRecordedNote": "im lokalen Archiv",
    "system.range": "Zeitraum",
    "system.noNights": "noch keine Nächte erfasst",
    "system.lastScore": "Letzter Score",
    "system.lastScoreNote": "jüngste ausgewertete Nacht",
    "system.of100": "von 100",

    // First-Run-Onboarding (Empty-State) — siehe onboarding.js / onboarding.css.
    "onboarding.title": "Willkommen im Observatorium",
    "onboarding.lede": "Das Observatorium ist bereit. Drei Schritte, und deine erste Nacht leuchtet am Himmel.",
    "onboarding.stepsAria": "Erste Schritte: drei Etappen bis zum ersten Schlafbericht",
    "onboarding.step1Title": "Datenquelle wählen",
    "onboarding.step1a": "Der Simulations-Adapter ist ab Werk aktiv, ganz ohne Hardware. Fitbit (Cloud, opt-in) oder Muse-EEG aktivierst du optional in ",
    "onboarding.step1b": ".",
    "onboarding.step2Title": "Demo-Historie erzeugen",
    "onboarding.step2a": "Starte ",
    "onboarding.step2b": " im Projektordner: 30 synthetische Nächte zum Erkunden.",
    "onboarding.step3Title": "Dashboard erkunden",
    "onboarding.step3Text": "Danach neu laden. Score-Orb, Hypnogramm, Verlauf und System-Ansicht warten schon.",
    "onboarding.refresh": "Nach Nächten suchen",
    "onboarding.refreshAria": "Seite neu laden und nach neuen Schlafberichten suchen",
    "onboarding.privacy": "Alle Daten bleiben auf diesem Gerät. 100 % lokal, keine Cloud.",
  },

  en: {
    "app.title": "Somnoscope · Sleep Observatory",
    "skip": "Skip to content",
    "masthead.role": "Sleep · Observatory",

    "nav.aria": "Switch view",
    "nav.night": "Tonight",
    "nav.trends": "Trends",
    "nav.system": "System",

    "lang.aria": "Choose language",
    "lang.de": "German",
    "lang.en": "English",

    "common.retry": "Try again",
    "common.errorTitle": "Connection lost",
    "common.fromToClock": "{from} to {to}",
    "common.fromToPlain": "{from} to {to}",
    "common.avg": "avg",
    "common.pct": "{pct}%",

    "night.loading": "Loading the night …",
    "night.emptyTitle": "No sleep data yet",
    "night.emptyMsg1": "The observatory is ready, but the sky is still empty.",
    "night.emptyMsg2a": "Run ",
    "night.emptyMsg2b": " to record a night.",
    "night.errorMsg": "The sleep data could not be loaded.",

    "score.label": "Sleep score",
    "verdict.excellent": "An excellent night.",
    "verdict.good": "A restful night.",
    "verdict.mixed": "A mixed night.",
    "verdict.restless": "A restless night.",
    "verdict.hard": "A rough night.",
    "strip.caption": "Course of the night",
    "strip.aria": "Sleep-stage timeline from {from} to {to}",

    "chapter.night": "The night as it unfolded",
    "chapter.metrics": "Key metrics",
    "chapter.vitals": "Vitals",
    "chapter.climate": "Sleep climate",
    "chapter.history": "Recent nights",
    "chapter.coach": "Sleep coach",

    "stage.wake": "Awake",
    "stage.rem": "REM",
    "stage.light": "Light",
    "stage.deep": "Deep",
    "hypno.rowWake": "Awake",
    "hypno.rowRem": "REM",
    "hypno.rowLight": "Light",
    "hypno.rowDeep": "Deep",
    "hypno.segTitle": "{stage}: {from} to {to}",
    "hypno.caption": "Hypnogram of the night from {from} to {to}. The stage totals in minutes are listed below.",
    "stages.pctOfNight": "{pct}% of the night",

    "metrics.totalSleep": "Total sleep",
    "metrics.totalSleepNote": "time actually asleep",
    "metrics.timeInBed": "Time in bed",
    "metrics.efficiency": "Sleep efficiency",
    "metrics.efficiencyNote": "share of time in bed spent asleep",
    "metrics.latency": "Sleep latency",
    "metrics.latencyNote": "until the first sleep stage",
    "metrics.waso": "Wake after sleep onset",
    "metrics.wasoNote": "WASO",

    "vitals.hr": "Heart rate",
    "vitals.hrUnit": "bpm avg",
    "vitals.hrSub": "Lowest of the night: {v} bpm",
    "vitals.hrv": "HRV",
    "vitals.hrvUnit": "ms avg",
    "vitals.hrvSub": "Heart-rate variability — higher means better recovery",
    "vitals.spo2": "SpO₂",
    "vitals.spo2Unit": "% avg",
    "vitals.spo2Sub": "Blood oxygen saturation",
    "vitals.temp": "Skin temperature",
    "vitals.tempUnit": "°C avg",

    "climate.co2": "CO₂",
    "climate.co2Note": "bedroom average",
    "climate.temp": "Room temperature",
    "climate.humidity": "Humidity",
    "climate.avgNote": "average",

    "history.aria": "Sleep score of the last {n} nights, from {from} to {to}",
    "history.barTitle": "{date}: score {score}",

    "coach.cardAria": "Coaching recommendation from the local sleep coach",
    "coach.status": "Analyzing your night …",
    "coach.note": "Written by the local model — your data never leaves this device.",

    "frame.nightOf": "Night of {date}",
    "frame.source": "Source: {source}",
    "frame.generated": "Analyzed on {date}, {time}",

    "export.title": "Export data",
    "export.hint": "Your nights as CSV/JSON — 100% local.",
    "export.scopeAll": "Up to 365 nights",
    "export.scopeDays": "The last {days} nights",
    "export.ariaCsv": "{scope} — download as CSV file",
    "export.ariaJson": "{scope} — download as JSON file",
    "colophon.claim": "Analyzed 100% locally · no cloud",

    "trends.title": "Trends",
    "trends.rangeAria": "Choose time range",
    "trends.nights7": "7 nights",
    "trends.nights14": "14 nights",
    "trends.nights30": "30 nights",
    "trends.loading": "Loading trends …",
    "trends.emptyTitle": "Not enough history yet",
    "trends.emptyMsg": "Not enough history yet — record more nights.",
    "trends.errorMsg": "The trends could not be loaded.",
    "trends.chAvg": "On average",
    "trends.chScore": "Score over time",
    "trends.chStages": "Stages across the nights",
    "trends.chVitals": "Efficiency & HRV",
    "trends.chBalance": "Summary",
    "trends.labelEff": "Sleep efficiency",
    "trends.labelHrv": "HRV",
    "trends.tooFew": "Not enough values for this range.",
    "trends.statScore": "Avg sleep score",
    "trends.statScoreNote": "out of 100 points",
    "trends.statEff": "Avg efficiency",
    "trends.statDur": "Avg sleep duration",
    "trends.statDurNote": "per night",
    "trends.statHrv": "Avg HRV",
    "trends.statHrvNote": "heart-rate variability",
    "trends.legendPct": "avg {pct}%",
    "trends.best": "Best night",
    "trends.bestNote": "highest score in the range",
    "trends.worst": "Weakest night",
    "trends.worstNote": "lowest score in the range",
    "trends.consistency": "Consistency",
    "trends.consistencyNote": "score spread — smaller is steadier",
    "trends.points": "points",
    "trends.nightOne": "night",
    "trends.nightsMany": "nights",
    "trends.capScore": "Sleep score across {range}: between {min} and {max} points, most recently {last}.",
    "trends.capScoreEmpty": "No score values in this range.",
    "trends.capStages": "Share of sleep stages per night in percent; the averages are shown in the legend below.",
    "trends.capEff": "Sleep efficiency per night in percent, average {avg}%.",
    "trends.capHrv": "Heart-rate variability per night in milliseconds, average {avg} ms.",
    "trends.ariaScore": "Line chart: sleep score across {range}",
    "trends.ariaStages": "Stacked bar chart: sleep stages per night across {range}",
    "trends.ariaEff": "Line chart: sleep efficiency across {range}",
    "trends.ariaHrv": "Line chart: HRV across {range}",

    "system.title": "System",
    "system.loading": "Loading system status …",
    "system.errorMsg": "The system status could not be loaded.",
    "system.chModules": "Modules",
    "system.chAdapters": "Wearable adapters",
    "system.chData": "Data summary",
    "system.modulesAria": "Status of the Somnoscope modules",
    "system.adaptersAria": "Configured wearable adapters",
    "system.dataAria": "Summary of the recorded sleep data",
    "system.noAdapters": "No adapters configured — see ",
    "system.meta": "{name} · Version {version} · Time zone {tz}",
    "system.active": "active",
    "system.inactive": "inactive",
    "system.on": "on",
    "system.off": "off",
    "system.mod.wearable": "Wearable",
    "system.mod.wearableNote": "sleep-data source (BLE/adapters)",
    "system.mod.climate": "Climate sensors",
    "system.mod.climateNote": "CO₂, temperature, humidity via MQTT",
    "system.mod.database": "Database",
    "system.mod.databaseNote": "local persistence of your nights",
    "system.mod.ml": "ML pipeline",
    "system.mod.mlNote": "scoring and stage analysis",
    "system.mod.coach": "Sleep coach",
    "system.mod.coachNote": "local language model",
    "system.nightsRecorded": "Nights recorded",
    "system.nightsRecordedNote": "in the local archive",
    "system.range": "Date range",
    "system.noNights": "no nights recorded yet",
    "system.lastScore": "Latest score",
    "system.lastScoreNote": "most recent analyzed night",
    "system.of100": "of 100",

    // First-Run onboarding (empty state) — see onboarding.js / onboarding.css.
    "onboarding.title": "Welcome to the observatory",
    "onboarding.lede": "The observatory is ready. Three steps, and your first night will light up the sky.",
    "onboarding.stepsAria": "Getting started: three steps to your first sleep report",
    "onboarding.step1Title": "Choose a data source",
    "onboarding.step1a": "The simulation adapter is on by default, no hardware needed. Optionally enable Fitbit (cloud, opt-in) or Muse EEG in ",
    "onboarding.step1b": ".",
    "onboarding.step2Title": "Create a demo history",
    "onboarding.step2a": "Run ",
    "onboarding.step2b": " in the project folder: 30 synthetic nights to explore.",
    "onboarding.step3Title": "Explore the dashboard",
    "onboarding.step3Text": "Then reload. The score orb, hypnogram, trends and system view are waiting.",
    "onboarding.refresh": "Check for nights",
    "onboarding.refreshAria": "Reload the page and look for new sleep reports",
    "onboarding.privacy": "All data stays on this device. 100% local, no cloud.",
  },
};

/** Aktuelle Sprache — Default deutsch, bis initLang()/setLang() entscheiden. */
let currentLang = "de";

/** Registrierte Sprachwechsel-Callbacks (z.B. Re-Render der aktiven Ansicht). */
const listeners = [];

/**
 * Übersetzt einen Key in der aktuellen Sprache. {name}-Platzhalter werden
 * aus params ersetzt; unbekannte Platzhalter bleiben stehen. Fehlt der Key,
 * wird der Key selbst zurückgegeben und eine Warnung geloggt.
 */
export function t(key, params) {
  const dict = MESSAGES[currentLang] ?? MESSAGES.de;
  let msg = dict[key];
  if (typeof msg !== "string") {
    console.warn(`Somnoscope i18n: fehlender Schlüssel "${key}" (${currentLang}).`);
    return key;
  }
  if (params && typeof params === "object") {
    msg = msg.replace(/\{(\w+)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
  }
  return msg;
}

/** Aktuelle Sprache: "de" | "en". */
export function getLang() {
  return currentLang;
}

/** Intl-Locale der aktuellen Sprache ("de-DE" / "en-GB"). */
export function getLocale() {
  return LOCALES[currentLang] ?? LOCALES.de;
}

/**
 * Wendet die statischen Übersetzungen auf den DOM-Teilbaum an:
 * [data-i18n] → textContent, [data-i18n-aria] → aria-label.
 * Der im HTML stehende deutsche Text bleibt als Fallback (ohne JS).
 */
export function applyStatic(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of root.querySelectorAll("[data-i18n-aria]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAria));
  }
}

/** Registriert einen Callback, der bei jedem setLang() aufgerufen wird. */
export function onLangChange(cb) {
  if (typeof cb === "function") listeners.push(cb);
}

/**
 * Wechselt die Sprache: persistiert in localStorage, setzt <html lang>,
 * übersetzt alle statischen Texte neu und benachrichtigt alle Callbacks
 * (die dynamischen Ansichten zeichnen sich daraus selbst neu).
 */
export function setLang(lang) {
  const next = lang === "en" ? "en" : "de";
  currentLang = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* Privater Modus o.Ä. — Sprache gilt dann nur für diese Sitzung. */
  }
  document.documentElement.lang = next;
  applyStatic();
  for (const cb of listeners) {
    try {
      cb(next);
    } catch (err) {
      console.error("Somnoscope i18n: Fehler in Sprachwechsel-Callback.", err);
    }
  }
}

/**
 * Ermittelt die Startsprache: localStorage ("somnoscope-lang"), sonst
 * navigator.language ("de*" → de, sonst en), Default de. Setzt <html lang>
 * und wendet die statischen Texte an — ohne zu persistieren und ohne die
 * Callbacks aufzurufen (beim Start ist noch nichts gerendert).
 */
export function initLang() {
  let saved = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    saved = null;
  }
  if (saved === "de" || saved === "en") {
    currentLang = saved;
  } else {
    const nav = typeof navigator !== "undefined" && typeof navigator.language === "string"
      ? navigator.language.toLowerCase()
      : "de";
    currentLang = nav.startsWith("de") ? "de" : "en";
  }
  document.documentElement.lang = currentLang;
  applyStatic();
}
