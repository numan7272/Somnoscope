/*
 * Somnoscope · Schlaf-Observatorium — Frontend-Einstieg (ES-Modul).
 *
 * Verantwortlich für die DOM-/Daten-Schicht (Accessibility-Layer):
 *   1. Lädt den jüngsten SleepReport (+ Historie) von der lokalen API.
 *   2. Rendert alle Zahlen als echtes, semantisches DOM: Score, Phasen,
 *      Kennzahlen, Vitalwerte, Klima, Historie. Diese Schicht funktioniert
 *      auch komplett ohne WebGL.
 *   3. Bootet danach die Three.js-Nachtszene (scene.js) als visuelle
 *      Schicht dahinter. Schlägt WebGL fehl, bleibt das Dashboard mit
 *      statischem CSS-Nachthimmel voll benutzbar (body.no-webgl).
 *   4. Stellt den Ansichten-Umschalter „Diese Nacht" / „Verlauf" bereit;
 *      die Verlauf-Ansicht (trends.js) rendert Mehr-Nächte-Trends aus
 *      /api/trends als ruhige SVG-Diagramme — ohne die 3D-Szene.
 *
 * Alles defensiv: fehlende Felder werden zu "–", nie zu Exceptions.
 * Keine externen Requests (Kernprinzip: Edge AI / offline).
 */
"use strict";

import { createTrendsView } from "./trends.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Phasen in Hypnogramm-Reihenfolge (oben = wach), Farben wie in style.css/scene.js. */
const STAGES = [
  { key: "wake",  label: "Wach",        row: 0, color: "#e09a4a", ink: "#ecb277" },
  { key: "rem",   label: "REM",         row: 1, color: "#b26ce0", ink: "#cf9ff0" },
  { key: "light", label: "Leichtschlaf", row: 2, color: "#3fb8ae", ink: "#6fd3ca" },
  { key: "deep",  label: "Tiefschlaf",  row: 3, color: "#5b6ee8", ink: "#9aa8ff" },
];
const STAGE_BY_KEY = Object.fromEntries(STAGES.map((s) => [s.key, s]));

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------------- *
 * Format-Helfer (deutschsprachig, defensiv)
 * ------------------------------------------------------------------------- */

const timeFmt = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dateLongFmt = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "long", year: "numeric" });
const dateShortFmt = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" });

function isNum(x) { return typeof x === "number" && Number.isFinite(x); }

function parseISO(iso) {
  if (typeof iso !== "string") return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtClock(iso) {
  const d = parseISO(iso);
  return d ? timeFmt.format(d) : "–";
}

/** Minuten → "7 h 12 min" bzw. "42 min". */
function fmtMinutes(min) {
  if (!isNum(min)) return "–";
  const total = Math.round(min);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h <= 0) return `${m} min`;
  return `${h} h ${String(m).padStart(2, "0")} min`;
}

function fmtNum(x, digits = 0) {
  return isNum(x) ? x.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "–";
}

/** "YYYY-MM-DD" → "6. Juli 2026" (lokal, ohne TZ-Sprünge). */
function fmtNightDate(dateStr) {
  if (typeof dateStr !== "string") return "";
  const d = parseISO(`${dateStr}T12:00:00`);
  return d ? dateLongFmt.format(d) : dateStr;
}

/* ------------------------------------------------------------------------- *
 * Zustands- und Ansichten-Umschaltung (Diese Nacht / Verlauf)
 * ------------------------------------------------------------------------- */

const el = (id) => document.getElementById(id);

/** Verlauf-Ansicht (trends.js) — lädt lazy beim ersten Umschalten. */
const trendsView = createTrendsView();

let currentView = "night";   // "night" | "trends"
let nightState = "loading";  // Zustand der Nacht-Ansicht bleibt beim Wechsel erhalten

/** Wendet Ansicht + Nacht-Zustand gemeinsam auf das DOM an. */
function applyView() {
  const night = currentView === "night";
  el("state-loading").hidden = !night || nightState !== "loading";
  el("state-empty").hidden = !night || nightState !== "empty";
  el("state-error").hidden = !night || nightState !== "error";
  el("report").hidden = !night || nightState !== "report";
  el("trends").hidden = night;
  el("night-date").hidden = !night;
  el("nav-night").setAttribute("aria-pressed", night ? "true" : "false");
  el("nav-trends").setAttribute("aria-pressed", night ? "false" : "true");
  // In der Verlauf-Ansicht weicht die 3D-Szene einem stillen CSS-Himmel
  // (ihre Anker liegen in der ausgeblendeten Nacht-Ansicht).
  document.body.classList.toggle("view-trends", !night);
  updateExportLinks();
}

function showState(which) {
  nightState = which;
  applyView();
}

function setView(view) {
  if (view === currentView) return;
  currentView = view;
  applyView();
  window.scrollTo({ top: 0, behavior: "auto" });
  if (view === "trends") trendsView.show();
}

/* ------------------------------------------------------------------------- *
 * Daten-Export (Colophon): CSV/JSON-Downloads, rein lokale /api-URLs
 * ------------------------------------------------------------------------- */

/**
 * Hält die Export-Links im Colophon aktuell. In der Verlauf-Ansicht wird der
 * gewählte Zeitraum (7/14/30 Nächte, aus dem aria-pressed-Zustand des
 * Range-Pickers gelesen) als ?days=… angehängt; in der Nacht-Ansicht bleiben
 * die URLs ohne Parameter (Default = alle Nächte). Defensiv: fehlen die
 * Elemente, passiert schlicht nichts.
 */
function updateExportLinks() {
  const csv = el("export-csv");
  const json = el("export-json");
  if (!csv || !json) return;

  let query = "";
  let scope = "Bis zu 365 Nächte";
  if (currentView === "trends") {
    const pressed = el("range-picker")?.querySelector('button[aria-pressed="true"]');
    const days = Number(pressed?.dataset.days);
    if (Number.isFinite(days) && days > 0) {
      query = `?days=${days}`;
      scope = `Die letzten ${days} Nächte`;
    }
  }
  csv.href = `/api/export/reports.csv${query}`;
  json.href = `/api/export/reports.json${query}`;
  csv.setAttribute("aria-label", `${scope} als CSV-Datei herunterladen`);
  json.setAttribute("aria-label", `${scope} als JSON-Datei herunterladen`);
}

/* ------------------------------------------------------------------------- *
 * Hero: Score + Nacht-Spektrum
 * ------------------------------------------------------------------------- */

function verdictFor(score) {
  if (!isNum(score)) return "";
  if (score >= 85) return "Eine ausgezeichnete Nacht.";
  if (score >= 70) return "Eine erholsame Nacht.";
  if (score >= 55) return "Eine durchwachsene Nacht.";
  if (score >= 40) return "Eine unruhige Nacht.";
  return "Eine schwere Nacht.";
}

function renderScore(report) {
  const target = isNum(report.sleep_score) ? Math.round(report.sleep_score) : null;
  const valueEl = el("score-value");
  el("score-verdict").textContent = verdictFor(target);

  if (target === null) { valueEl.textContent = "–"; return; }
  // Ohne Animation: reduzierte Bewegung oder Tab im Hintergrund (kein rAF).
  if (REDUCED_MOTION || document.hidden) { valueEl.textContent = String(target); return; }

  // Sanftes Hochzählen (ease-out), synchron zum Aufleuchten des Orbs.
  const dur = 1500;
  const t0 = performance.now();
  const tick = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    const eased = 1 - Math.pow(1 - p, 4);
    valueEl.textContent = String(Math.round(target * eased));
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/** Baut den Farbstreifen der Nacht (Hypnogramm als CSS-Gradient mit harten Kanten). */
function renderNightStrip(report) {
  const strip = el("night-strip");
  const hypno = Array.isArray(report.hypnogram) ? report.hypnogram : [];
  const t0 = parseISO(report.sleep_onset) ?? parseISO(hypno[0]?.start);
  const t1 = parseISO(report.final_wake) ?? parseISO(hypno[hypno.length - 1]?.end);
  if (!hypno.length || !t0 || !t1 || t1 <= t0) { strip.hidden = true; return; }

  const total = t1 - t0;
  const stops = [];
  for (const seg of hypno) {
    const s = parseISO(seg.start), e = parseISO(seg.end);
    const stage = STAGE_BY_KEY[seg.stage];
    if (!s || !e || !stage) continue;
    const a = Math.max(0, Math.min(100, ((s - t0) / total) * 100));
    const b = Math.max(0, Math.min(100, ((e - t0) / total) * 100));
    if (b <= a) continue;
    stops.push(`${stage.color} ${a.toFixed(2)}% ${b.toFixed(2)}%`);
  }
  if (!stops.length) { strip.hidden = true; return; }

  const bar = el("strip-bar");
  bar.style.background = `linear-gradient(90deg, ${stops.join(", ")})`;
  bar.setAttribute("aria-label",
    `Schlafphasen-Verlauf von ${fmtClock(report.sleep_onset)} bis ${fmtClock(report.final_wake)} Uhr`);
  el("strip-onset").textContent = fmtClock(report.sleep_onset);
  el("strip-wake").textContent = fmtClock(report.final_wake);
}

/* ------------------------------------------------------------------------- *
 * Kapitel I: Hypnogramm (DOM-SVG) + Phasen-Bilanz
 * ------------------------------------------------------------------------- */

function renderHypnogram(report) {
  const svg = el("hypno-svg");
  svg.textContent = "";
  const hypno = Array.isArray(report.hypnogram) ? report.hypnogram : [];
  const t0 = parseISO(hypno[0]?.start);
  const t1 = parseISO(hypno[hypno.length - 1]?.end);
  if (!hypno.length || !t0 || !t1 || t1 <= t0) return;

  const total = t1 - t0;
  const ROW_H = 25;           // viewBox: 1000 × 100, 4 Reihen
  const BAR_H = 8;
  const rowY = (row) => row * ROW_H + (ROW_H - BAR_H) / 2;

  // Zarte Reihen-Führungslinien
  for (let r = 0; r < 4; r++) {
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", "0"); line.setAttribute("x2", "1000");
    const y = rowY(r) + BAR_H / 2;
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", "rgba(158,170,215,0.10)");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(line);
  }

  let prev = null;
  for (const seg of hypno) {
    const s = parseISO(seg.start), e = parseISO(seg.end);
    const stage = STAGE_BY_KEY[seg.stage];
    if (!s || !e || !stage || e <= s) continue;
    const x = ((s - t0) / total) * 1000;
    const w = ((e - s) / total) * 1000;
    const y = rowY(stage.row);

    if (prev && prev.stage.row !== stage.row) {
      // Verbindungslinie zwischen Phasenwechseln (wie beim klassischen Hypnogramm)
      const join = document.createElementNS(SVG_NS, "line");
      join.setAttribute("x1", x); join.setAttribute("x2", x);
      join.setAttribute("y1", rowY(prev.stage.row) + BAR_H / 2);
      join.setAttribute("y2", y + BAR_H / 2);
      join.setAttribute("stroke", "rgba(158,170,215,0.22)");
      join.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(join);
    }

    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", y);
    rect.setAttribute("width", Math.max(w, 1.5)); rect.setAttribute("height", BAR_H);
    rect.setAttribute("fill", stage.color);
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${stage.label}: ${fmtClock(seg.start)} bis ${fmtClock(seg.end)} Uhr`;
    rect.appendChild(title);
    svg.appendChild(rect);
    prev = { stage };
  }

  // Zeit-Ticks unter dem Chart
  const ticks = el("hypno-ticks");
  ticks.textContent = "";
  const hours = total / 3.6e6;
  const stepH = hours > 6.5 ? 2 : 1;
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  if (first < t0) first.setHours(first.getHours() + 1);
  for (let d = new Date(first); d <= t1; d.setHours(d.getHours() + stepH)) {
    const pct = ((d - t0) / total) * 100;
    if (pct < 2 || pct > 98) continue;
    const span = document.createElement("span");
    span.style.left = `${pct.toFixed(2)}%`;
    span.textContent = timeFmt.format(d);
    ticks.appendChild(span);
  }

  el("hypno-caption").textContent =
    `Hypnogramm der Nacht von ${fmtClock(report.sleep_onset)} bis ${fmtClock(report.final_wake)} Uhr. ` +
    `Die Phasen-Bilanz in Minuten steht in der folgenden Liste.`;
}

function renderStageLedger(report) {
  const list = el("stage-ledger");
  list.textContent = "";
  const mins = report.stages_min ?? {};
  const pcts = report.stages_pct ?? {};
  // Reihenfolge der Bilanz: Tief → REM → Leicht → Wach (Wertigkeit der Nacht)
  for (const key of ["deep", "rem", "light", "wake"]) {
    const stage = STAGE_BY_KEY[key];
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "stage-name";
    name.style.color = stage.ink;
    const dot = document.createElement("i");
    dot.style.background = stage.color;
    dot.setAttribute("aria-hidden", "true");
    name.append(dot, stage.label);
    const val = document.createElement("span");
    val.className = "stage-min";
    val.textContent = fmtMinutes(mins[key]);
    const pct = document.createElement("span");
    pct.className = "stage-pct";
    pct.textContent = isNum(pcts[key]) ? `${fmtNum(pcts[key], 0)} % der Nacht` : "–";
    li.append(name, val, pct);
    list.appendChild(li);
  }
}

/* ------------------------------------------------------------------------- *
 * Kapitel II: Kennzahlen-Ledger
 * ------------------------------------------------------------------------- */

function ledgerRow(label, note, value, unit) {
  const row = document.createElement("div");
  const dt = document.createElement("dt");
  dt.append(label);
  if (note) {
    const small = document.createElement("small");
    small.textContent = note;
    dt.appendChild(small);
  }
  const dd = document.createElement("dd");
  dd.append(value);
  if (unit) {
    const small = document.createElement("small");
    small.textContent = unit;
    dd.appendChild(small);
  }
  row.append(dt, dd);
  return row;
}

function renderMetrics(report) {
  const dl = el("metrics-ledger");
  dl.textContent = "";
  dl.append(
    ledgerRow("Gesamtschlaf", "tatsächlich geschlafen", fmtMinutes(report.total_sleep_min)),
    ledgerRow("Zeit im Bett", `${fmtClock(report.sleep_onset)} bis ${fmtClock(report.final_wake)} Uhr`,
      fmtMinutes(report.time_in_bed_min)),
    ledgerRow("Schlafeffizienz", "Schlafanteil der Bettzeit",
      fmtNum(report.sleep_efficiency_pct, 0), "%"),
    ledgerRow("Einschlaflatenz", "bis zur ersten Schlafphase", fmtMinutes(report.sleep_latency_min)),
    ledgerRow("Wach nach dem Einschlafen", "WASO", fmtMinutes(report.waso_min)),
  );
}

/* ------------------------------------------------------------------------- *
 * Kapitel III: Vitalwerte (Werte + DOM-Sparklines)
 * ------------------------------------------------------------------------- */

function seriesValues(series) {
  if (!Array.isArray(series)) return [];
  return series.map((p) => p?.v).filter(isNum);
}

/** Kleine SVG-Sparkline aus einer Werte-Reihe (max. 140 Stützpunkte). */
function sparkline(values, color) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 100 30");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  if (values.length < 2) return svg;

  const step = Math.max(1, Math.floor(values.length / 140));
  const pts = values.filter((_, i) => i % step === 0);
  const min = Math.min(...pts), max = Math.max(...pts);
  const span = max - min || 1;
  const coords = pts.map((v, i) => {
    const x = (i / (pts.length - 1)) * 100;
    const y = 27 - ((v - min) / span) * 24;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });

  const area = document.createElementNS(SVG_NS, "polygon");
  area.setAttribute("points", `0,30 ${coords.join(" ")} 100,30`);
  area.setAttribute("fill", color);
  area.setAttribute("opacity", "0.10");
  const line = document.createElementNS(SVG_NS, "polyline");
  line.setAttribute("points", coords.join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "1.6");
  line.setAttribute("stroke-linejoin", "round");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(area, line);
  return svg;
}

function vitalItem({ label, color, value, unit, sub, series }) {
  const li = document.createElement("li");
  const head = document.createElement("div");
  head.className = "vital-head";

  const name = document.createElement("span");
  name.className = "vital-name";
  const dot = document.createElement("i");
  dot.style.background = color;
  dot.setAttribute("aria-hidden", "true");
  name.append(dot, label);

  const val = document.createElement("span");
  val.className = "vital-value";
  val.append(value);
  const small = document.createElement("small");
  small.textContent = unit;
  val.appendChild(small);

  head.append(name, val);
  if (sub) {
    const subEl = document.createElement("span");
    subEl.className = "vital-sub";
    subEl.textContent = sub;
    head.appendChild(subEl);
  }

  const spark = document.createElement("div");
  spark.className = "vital-spark";
  spark.appendChild(sparkline(seriesValues(series), color));

  li.append(head, spark);
  return li;
}

function renderVitals(report) {
  const list = el("vitals-list");
  list.textContent = "";
  const v = report.vitals ?? {};
  const s = report.series ?? {};

  list.append(
    vitalItem({
      label: "Puls", color: "#ef8fa3",
      value: fmtNum(v.avg_hr, 0), unit: "bpm Ø",
      sub: isNum(v.min_hr) ? `Tiefster Wert der Nacht: ${fmtNum(v.min_hr, 0)} bpm` : "",
      series: s.heart_rate,
    }),
    vitalItem({
      label: "HRV", color: "#8fe3b0",
      value: fmtNum(v.avg_hrv, 0), unit: "ms Ø",
      sub: "Herzratenvariabilität, höher ist erholter",
      series: s.hrv,
    }),
    vitalItem({
      label: "SpO₂", color: "#a9c8ff",
      value: fmtNum(v.avg_spo2, 1), unit: "% Ø",
      sub: "Sauerstoffsättigung im Blut",
      series: s.spo2,
    }),
  );

  // Hauttemperatur nur, wenn die Serie tatsächlich vorliegt.
  const tempVals = seriesValues(s.skin_temp);
  if (tempVals.length) {
    const avg = tempVals.reduce((a, b) => a + b, 0) / tempVals.length;
    list.append(vitalItem({
      label: "Hauttemperatur", color: "#e8c07a",
      value: fmtNum(avg, 1), unit: "°C Ø",
      sub: "",
      series: s.skin_temp,
    }));
  }
}

/* ------------------------------------------------------------------------- *
 * Kapitel IV: Schlafklima (optional)
 * ------------------------------------------------------------------------- */

function renderClimate(report) {
  const c = report.climate ?? {};
  const has = isNum(c.avg_co2) || isNum(c.avg_temp) || isNum(c.avg_humidity);
  const section = el("climate-section");
  section.hidden = !has;
  if (!has) return;

  const dl = el("climate-ledger");
  dl.textContent = "";
  if (isNum(c.avg_co2)) dl.append(ledgerRow("CO₂", "Durchschnitt im Schlafzimmer", fmtNum(c.avg_co2, 0), "ppm"));
  if (isNum(c.avg_temp)) dl.append(ledgerRow("Raumtemperatur", "Durchschnitt", fmtNum(c.avg_temp, 1), "°C"));
  if (isNum(c.avg_humidity)) dl.append(ledgerRow("Luftfeuchte", "Durchschnitt", fmtNum(c.avg_humidity, 0), "%"));
}

/* ------------------------------------------------------------------------- *
 * Kapitel V: Historie (Score-Verlauf der letzten Nächte)
 * ------------------------------------------------------------------------- */

function renderHistory(reports, current) {
  const section = el("history-section");
  const rows = (Array.isArray(reports) ? reports : [])
    .filter((r) => r && typeof r.date === "string" && isNum(r.sleep_score))
    .slice(0, 14)
    .reverse(); // API liefert neueste zuerst → chronologisch drehen
  if (rows.length < 2) { section.hidden = true; return; }
  section.hidden = false;

  const strip = el("history-strip");
  strip.textContent = "";
  strip.setAttribute("aria-label",
    `Schlaf-Score der letzten ${rows.length} Nächte, von ${fmtNightDate(rows[0].date)} bis ${fmtNightDate(rows[rows.length - 1].date)}`);

  for (const r of rows) {
    const bar = document.createElement("div");
    bar.className = "bar";
    const score = Math.max(0, Math.min(100, Math.round(r.sleep_score)));
    bar.style.height = `${Math.max(6, score)}%`;
    bar.title = `${fmtNightDate(r.date)}: Score ${score}`;
    if (current && r.date === current.date) {
      bar.classList.add("is-current");
      const lbl = document.createElement("span");
      lbl.className = "bar-score";
      lbl.textContent = String(score);
      bar.appendChild(lbl);
    }
    strip.appendChild(bar);
  }

  const d0 = parseISO(`${rows[0].date}T12:00:00`);
  const d1 = parseISO(`${rows[rows.length - 1].date}T12:00:00`);
  el("history-from").textContent = d0 ? dateShortFmt.format(d0) : "";
  el("history-to").textContent = d1 ? dateShortFmt.format(d1) : "";
}

/* ------------------------------------------------------------------------- *
 * Kapitel VI: Schlaf-Coach (lokales LLM, asynchron nachgeladen)
 * ------------------------------------------------------------------------- */

/**
 * Rendert mehrzeiligen Coach-Text als Absätze + Aufzählungen (nur DOM-APIs,
 * kein innerHTML). Bullet-Zeilen ("- ", "* ", "• ", "1. ") werden zu <ul>,
 * Leerzeilen trennen Absätze.
 */
function renderCoachText(container, text) {
  container.textContent = "";
  const BULLET = /^\s*(?:[-*•–]|\d+[.)])\s+/;
  let ul = null;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    const p = document.createElement("p");
    p.textContent = para.join(" ");
    container.appendChild(p);
    para = [];
  };

  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { flushPara(); ul = null; continue; }
    if (BULLET.test(line)) {
      flushPara();
      if (!ul) { ul = document.createElement("ul"); container.appendChild(ul); }
      const li = document.createElement("li");
      li.textContent = line.replace(BULLET, "");
      ul.appendChild(li);
    } else {
      ul = null;
      para.push(line);
    }
  }
  flushPara();
}

/**
 * Lädt /api/coaching und füllt die Coach-Karte. Zeigt während der (u.U.
 * mehrere Sekunden dauernden) lokalen Generierung einen Ladezustand; bei
 * Fehler, deaktiviertem Coach oder leerem Text wird das Kapitel wieder
 * ausgeblendet. Läuft bewusst NACH dem Haupt-Rendering, blockiert nichts.
 */
async function loadCoaching() {
  const section = el("coach-section");
  const card = section.querySelector(".coach-card");
  const status = el("coach-status");
  const textEl = el("coach-text");

  // Ladezustand: Kapitel einblenden, Karte als "beschäftigt" markieren.
  section.hidden = false;
  card.setAttribute("aria-busy", "true");
  status.hidden = false;
  textEl.textContent = "";
  renumberChapters();

  let data = null;
  try {
    data = await fetchJSON("/api/coaching");
  } catch (err) {
    console.warn("Somnoscope: Coaching konnte nicht geladen werden.", err);
  }

  const text = typeof data?.text === "string" ? data.text.trim() : "";
  if (!data || data.enabled === false || !text) {
    // Kein Coach verfügbar → Kapitel sauber entfernen statt leer stehen lassen.
    section.hidden = true;
    renumberChapters();
    return;
  }

  status.hidden = true;
  renderCoachText(textEl, text);
  card.setAttribute("aria-busy", "false");
  section.classList.add("is-visible"); // ggf. schon im Viewport → sofort zeigen
}

/* ------------------------------------------------------------------------- *
 * Rahmen: Masthead, Colophon, Kapitelnummern, Scroll-Reveal
 * ------------------------------------------------------------------------- */

function renderFrame(report) {
  el("night-date").textContent = report?.date ? `Nacht vom ${fmtNightDate(report.date)}` : "";

  const parts = [];
  if (report?.source) parts.push(`Quelle: ${report.source}`);
  const gen = parseISO(report?.generated_at);
  if (gen) {
    parts.push(`Auswertung vom ${dateLongFmt.format(gen)}, ${timeFmt.format(gen)} Uhr`);
  }
  el("colophon-meta").textContent = parts.join(" · ");
}

/** Nummeriert die sichtbaren Kapitel der Nacht-Ansicht neu (I, II, …). */
function renumberChapters() {
  const roman = ["I", "II", "III", "IV", "V", "VI"];
  let i = 0;
  for (const no of document.querySelectorAll("#report .chapter:not([hidden]) .chapter-no")) {
    no.textContent = roman[i++] ?? "·";
  }
}

function setupReveal() {
  const chapters = document.querySelectorAll(".chapter");
  if (REDUCED_MOTION || !("IntersectionObserver" in window)) {
    chapters.forEach((c) => c.classList.add("is-visible"));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); }
    }
  }, { rootMargin: "0px 0px -12% 0px" });
  chapters.forEach((c) => io.observe(c));
}

/* ------------------------------------------------------------------------- *
 * Tooltip (Hypnogramm-Hover aus der 3D-Szene)
 * ------------------------------------------------------------------------- */

function makeTooltipHandler() {
  const tip = el("tooltip");
  return (segment, x, y) => {
    if (!segment) { tip.hidden = true; return; }
    const stage = STAGE_BY_KEY[segment.stage];
    tip.textContent = "";
    const head = document.createElement("span");
    head.className = "tt-stage";
    head.style.color = stage?.ink ?? "inherit";
    head.textContent = stage?.label ?? segment.stage;
    const time = document.createElement("span");
    time.className = "tt-time";
    const dur = (parseISO(segment.end) - parseISO(segment.start)) / 6e4;
    time.textContent = `${fmtClock(segment.start)} bis ${fmtClock(segment.end)} Uhr · ${fmtMinutes(dur)}`;
    tip.append(head, time);
    tip.style.left = `${Math.max(90, Math.min(window.innerWidth - 90, x))}px`;
    tip.style.top = `${Math.max(60, y)}px`;
    tip.hidden = false;
  };
}

/* ------------------------------------------------------------------------- *
 * 3D-Szene booten (mit sauberem Fallback)
 * ------------------------------------------------------------------------- */

async function bootScene(report) {
  const canvas = document.getElementById("sky");

  // Früh prüfen, ob WebGL überhaupt verfügbar ist — sonst gar nicht erst laden.
  let glOk = false;
  try {
    const probe = document.createElement("canvas");
    glOk = !!(probe.getContext("webgl2") || probe.getContext("webgl"));
  } catch { glOk = false; }

  if (!glOk) {
    document.body.classList.add("no-webgl");
    console.info("Somnoscope: kein WebGL verfügbar, statische Ansicht aktiv.");
    return;
  }

  try {
    const { createNightScene } = await import("./scene.js");
    const scene = createNightScene({
      canvas,
      report,
      reducedMotion: REDUCED_MOTION,
      onBandPoint: makeTooltipHandler(),
      onFail: () => document.body.classList.add("no-webgl"),
    });
    if (!scene) {
      document.body.classList.add("no-webgl");
    } else {
      canvas.classList.add("is-live");
    }
  } catch (err) {
    // Szene ist reine Kür: Fehler werden geschluckt, DOM-Schicht trägt alles.
    console.warn("Somnoscope: 3D-Szene konnte nicht starten.", err);
    document.body.classList.add("no-webgl");
  }
}

/* ------------------------------------------------------------------------- *
 * Haupt-Ablauf
 * ------------------------------------------------------------------------- */

async function fetchJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status} für ${url}`);
  return res.json();
}

async function main() {
  showState("loading");

  let report = null;
  let history = [];
  try {
    const [latest, list] = await Promise.allSettled([
      fetchJSON("/api/report/latest"),
      fetchJSON("/api/reports?limit=14"),
    ]);
    if (latest.status === "rejected") throw latest.reason;
    report = latest.value;
    history = list.status === "fulfilled" && Array.isArray(list.value) ? list.value : [];
  } catch (err) {
    console.warn("Somnoscope: Laden fehlgeschlagen.", err);
    showState("error");
    bootScene(null);
    return;
  }

  if (!report) {
    showState("empty");
    bootScene(null);
    return;
  }

  try {
    renderFrame(report);
    renderScore(report);
    renderNightStrip(report);
    renderHypnogram(report);
    renderStageLedger(report);
    renderMetrics(report);
    renderVitals(report);
    renderClimate(report);
    renderHistory(history, report);
    renumberChapters();
  } catch (err) {
    console.error("Somnoscope: Rendering-Fehler.", err);
  }

  showState("report");
  setupReveal();
  bootScene(report);
  // Coach-Text bewusst zuletzt und ohne await: die lokale LLM-Generierung
  // darf dauern, das Dashboard steht längst. Fehler behandelt loadCoaching.
  loadCoaching();
}

el("retry-btn").addEventListener("click", () => window.location.reload());
el("nav-night").addEventListener("click", () => setView("night"));
el("nav-trends").addEventListener("click", () => setView("trends"));

// Zeitraum-Wechsel im Verlauf: Export-Links nachziehen. Der Listener ist
// bewusst NACH createTrendsView() registriert — trends.js aktualisiert
// aria-pressed synchron, bevor dieser Handler den Zustand ausliest.
el("range-picker").addEventListener("click", () => updateExportLinks());

updateExportLinks();
main();
