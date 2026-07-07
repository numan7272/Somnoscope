/*
 * Somnoscope · Schlaf-Observatorium — Verlauf-Ansicht (ES-Modul).
 *
 * Rendert die Trend-Ansicht über mehrere Nächte aus GET /api/trends:
 *   1. Kennzahlen-Reihe (Ø Score, Ø Effizienz, Ø Schlafdauer, Ø HRV),
 *   2. Score-Trend als SVG-Liniendiagramm (Achsen, Ticks, Datums-Labels,
 *      letzter Punkt betont),
 *   3. Phasen-Verteilung über die Nächte als gestapelte SVG-Balken,
 *   4. Effizienz- und HRV-Trendlinien,
 *   5. Bilanz: beste/schwächste Nacht + Konsistenz (Score-Streuung).
 *
 * Alles reines Inline-SVG in Vanilla JS — keine externen Libraries, keine
 * externen Requests (Kernprinzip: Edge AI / offline). Die Diagramme werden
 * in Pixelbreite des Containers gerendert (scharfe Beschriftung) und bei
 * Resize neu aufgebaut. Alle Zahlen stehen zusätzlich als echter Text im
 * DOM (Kennzahlen, Legende, Bilanz, versteckte figcaptions) — die Charts
 * selbst tragen aria-Labels.
 *
 * Defensiv: fehlende Werte werden zu "–" bzw. Lücken in den Linien,
 * nie zu Exceptions. Bei weniger als zwei Nächten erscheint der
 * Empty-State ("Noch zu wenig Historie …").
 *
 * Zweisprachigkeit (DE/EN): alle sichtbaren Strings laufen über t() aus
 * i18n.js, die Intl-Formatter über getLocale(). Beim Sprachwechsel wird
 * die Ansicht aus dem zwischengespeicherten Trend-Payload neu gezeichnet
 * (onLangChange) — ohne erneutes Fetch.
 */
"use strict";

import { t, getLang, getLocale, onLangChange } from "./i18n.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Phasen in Stapel-Reihenfolge (unten = Tiefschlaf), Farben wie style.css.
 *  Labels sind i18n-Keys und werden erst beim Rendern über t() aufgelöst. */
const STACK_STAGES = [
  { key: "deep",  labelKey: "stage.deep",  color: "#5b6ee8", ink: "#9aa8ff" },
  { key: "light", labelKey: "stage.light", color: "#3fb8ae", ink: "#6fd3ca" },
  { key: "rem",   labelKey: "stage.rem",   color: "#b26ce0", ink: "#cf9ff0" },
  { key: "wake",  labelKey: "stage.wake",  color: "#e09a4a", ink: "#ecb277" },
];

/** Linienfarben: Score = Mondlicht, Effizienz = Teal, HRV = Vital-Grün. */
const COLOR_SCORE = "#efe9da";
const COLOR_EFF = "#3fb8ae";
const COLOR_HRV = "#8fe3b0";

/* ------------------------------------------------------------------------- *
 * Format-Helfer (sprachabhängig via getLocale(), defensiv)
 * ------------------------------------------------------------------------- */

/** Intl-Formatter werden lazy je Sprache gebaut (Sprachwechsel = Neuaufbau). */
let fmtLang = "";
let dateLongFmt = null;
let dateShortFmt = null;

function ensureFormatters() {
  if (fmtLang === getLang() && dateLongFmt) return;
  fmtLang = getLang();
  const locale = getLocale();
  dateLongFmt = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric" });
  dateShortFmt = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit" });
}

function isNum(x) { return typeof x === "number" && Number.isFinite(x); }

function parseDay(dateStr) {
  if (typeof dateStr !== "string") return null;
  const d = new Date(`${dateStr}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDayLong(dateStr) {
  const d = parseDay(dateStr);
  ensureFormatters();
  return d ? dateLongFmt.format(d) : (dateStr ?? "–");
}

function fmtDayShort(dateStr) {
  const d = parseDay(dateStr);
  ensureFormatters();
  return d ? dateShortFmt.format(d) : "";
}

function fmtNum(x, digits = 0) {
  return isNum(x)
    ? x.toLocaleString(getLocale(), { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "–";
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

const el = (id) => document.getElementById(id);

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

/* ------------------------------------------------------------------------- *
 * Achsen-Helfer
 * ------------------------------------------------------------------------- */

/** "Schöne" Y-Ticks für ein Intervall (1/2/2.5/5 × 10^k Schrittweite). */
function niceTicks(lo, hi, target = 4) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  let step = mag;
  for (const mult of [1, 2, 2.5, 5, 10]) {
    if (raw <= mult * mag) { step = mult * mag; break; }
  }
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    ticks.push(Math.round(v * 1000) / 1000);
  }
  return ticks;
}

/** Gleichmäßig verteilte Index-Auswahl (immer erster + letzter dabei). */
function pickIndices(n, maxCount) {
  if (n <= 0) return [];
  const count = Math.max(2, Math.min(n, maxCount));
  if (n === 1) return [0];
  const picked = new Set();
  for (let k = 0; k < count; k++) {
    picked.add(Math.round((k / (count - 1)) * (n - 1)));
  }
  return [...picked].sort((a, b) => a - b);
}

/* ------------------------------------------------------------------------- *
 * Liniendiagramm (Score / Effizienz / HRV)
 * ------------------------------------------------------------------------- */

/**
 * Rendert ein SVG-Liniendiagramm in den Container.
 * dates/values sind gleich lang; null-Werte erzeugen Lücken in der Linie.
 * Der letzte vorhandene Punkt wird betont (Ring + Wertlabel).
 */
function renderLineChart(mount, { dates, values, color, height, unit = "", decimals = 0, clampMin = null, clampMax = null, area = false, glow = false }) {
  mount.textContent = "";
  const n = Math.min(dates.length, values.length);
  const points = [];
  for (let i = 0; i < n; i++) {
    if (isNum(values[i])) points.push({ i, v: values[i] });
  }
  if (points.length < 2) {
    const hint = document.createElement("p");
    hint.className = "tchart-hint";
    hint.textContent = t("trends.tooFew");
    mount.appendChild(hint);
    return;
  }

  const w = Math.max(280, Math.round(mount.clientWidth || 640));
  const h = height;
  const m = { t: 18, r: 20, b: 30, l: 46 };
  const iw = w - m.l - m.r;
  const ih = h - m.t - m.b;

  let lo = Math.min(...points.map((p) => p.v));
  let hi = Math.max(...points.map((p) => p.v));
  const pad = Math.max((hi - lo) * 0.18, 2);
  lo -= pad; hi += pad;
  if (clampMin !== null) lo = Math.max(clampMin, lo);
  if (clampMax !== null) hi = Math.min(clampMax, hi);
  if (hi - lo < 1) { lo -= 1; hi += 1; }

  const x = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true", focusable: "false" });

  // Y-Raster + Beschriftung
  for (const tick of niceTicks(lo, hi, 4)) {
    const ty = y(tick);
    svg.appendChild(svgEl("line", { x1: m.l, x2: w - m.r, y1: ty, y2: ty, class: "tc-grid" }));
    const label = svgEl("text", { x: m.l - 9, y: ty + 3.5, "text-anchor": "end" });
    label.textContent = fmtNum(tick, tick % 1 ? 1 : 0);
    svg.appendChild(label);
  }
  // Grundlinie
  svg.appendChild(svgEl("line", { x1: m.l, x2: w - m.r, y1: h - m.b, y2: h - m.b, class: "tc-axis" }));

  // X-Ticks: Datums-Labels, gleichmäßig ausgedünnt
  for (const i of pickIndices(n, Math.floor(iw / 74))) {
    const tx = x(i);
    svg.appendChild(svgEl("line", { x1: tx, x2: tx, y1: h - m.b, y2: h - m.b + 5, class: "tc-axis" }));
    const anchor = i === 0 ? "start" : i === n - 1 ? "end" : "middle";
    const label = svgEl("text", { x: tx, y: h - m.b + 18, "text-anchor": anchor });
    label.textContent = fmtDayShort(dates[i]);
    svg.appendChild(label);
  }

  // Linien-Segmente (Lücken bei null-Werten)
  const segments = [];
  let current = [];
  for (let i = 0; i < n; i++) {
    if (isNum(values[i])) {
      current.push({ i, v: values[i] });
    } else if (current.length) {
      segments.push(current); current = [];
    }
  }
  if (current.length) segments.push(current);

  for (const seg of segments) {
    const coords = seg.map((p) => `${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`);
    if (seg.length === 1) {
      svg.appendChild(svgEl("circle", { cx: x(seg[0].i), cy: y(seg[0].v), r: 2.5, fill: color }));
      continue;
    }
    if (area) {
      const base = (h - m.b).toFixed(1);
      const poly = svgEl("polygon", {
        points: `${x(seg[0].i).toFixed(1)},${base} ${coords.join(" ")} ${x(seg[seg.length - 1].i).toFixed(1)},${base}`,
        fill: color, opacity: "0.07",
      });
      svg.appendChild(poly);
    }
    if (glow) {
      svg.appendChild(svgEl("polyline", {
        points: coords.join(" "), fill: "none", stroke: color,
        "stroke-width": 5, opacity: "0.16", "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
    }
    svg.appendChild(svgEl("polyline", {
      points: coords.join(" "), fill: "none", stroke: color,
      "stroke-width": 1.8, "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
  }

  // Letzten Punkt betonen: Ring + Wert
  const last = points[points.length - 1];
  const lx = x(last.i), ly = y(last.v);
  svg.appendChild(svgEl("circle", { cx: lx, cy: ly, r: 7, fill: "none", stroke: color, opacity: "0.45" }));
  svg.appendChild(svgEl("circle", { cx: lx, cy: ly, r: 3, fill: color }));
  const lastLabel = svgEl("text", {
    x: Math.min(lx, w - m.r - 4), y: Math.max(13, ly - 13),
    "text-anchor": lx > w - m.r - 40 ? "end" : "middle",
    class: "tc-last-label",
  });
  lastLabel.textContent = `${fmtNum(last.v, decimals)}${unit}`;
  svg.appendChild(lastLabel);

  mount.appendChild(svg);
}

/* ------------------------------------------------------------------------- *
 * Gestapelte Phasen-Balken (eine Säule je Nacht)
 * ------------------------------------------------------------------------- */

function renderStagesChart(mount, dates, stacks) {
  mount.textContent = "";
  const n = Math.min(dates.length, stacks.length);
  if (n < 2) {
    const hint = document.createElement("p");
    hint.className = "tchart-hint";
    hint.textContent = t("trends.tooFew");
    mount.appendChild(hint);
    return;
  }

  const w = Math.max(280, Math.round(mount.clientWidth || 640));
  const h = 250;
  const m = { t: 14, r: 20, b: 30, l: 46 };
  const iw = w - m.l - m.r;
  const ih = h - m.t - m.b;
  const y = (pct) => m.t + ih - (Math.max(0, Math.min(100, pct)) / 100) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true", focusable: "false" });

  // Y-Raster in Prozent
  const yTicks = w < 430 ? [0, 50, 100] : [0, 25, 50, 75, 100];
  for (const tick of yTicks) {
    const ty = y(tick);
    svg.appendChild(svgEl("line", { x1: m.l, x2: w - m.r, y1: ty, y2: ty, class: tick === 0 ? "tc-axis" : "tc-grid" }));
    const label = svgEl("text", { x: m.l - 9, y: ty + 3.5, "text-anchor": "end" });
    label.textContent = `${tick}`;
    svg.appendChild(label);
  }

  const slot = iw / n;
  const bw = Math.min(26, Math.max(3, slot * 0.62));
  for (let i = 0; i < n; i++) {
    const stages = stacks[i] && typeof stacks[i] === "object" ? stacks[i] : {};
    const cx = m.l + slot * (i + 0.5);
    let cursor = h - m.b;
    const titleParts = [];
    for (const stage of STACK_STAGES) {
      const pct = isNum(stages[stage.key]) ? Math.max(0, stages[stage.key]) : 0;
      titleParts.push(`${t(stage.labelKey)} ${t("common.pct", { pct: fmtNum(pct, 0) })}`);
      const hgt = (Math.min(100, pct) / 100) * ih;
      if (hgt <= 0) continue;
      const rect = svgEl("rect", {
        x: (cx - bw / 2).toFixed(1), y: (cursor - hgt).toFixed(1),
        width: bw.toFixed(1), height: hgt.toFixed(1),
        fill: stage.color,
      });
      svg.appendChild(rect);
      cursor -= hgt;
    }
    // Tooltip je Säule (nativer SVG-title, rein ergänzend)
    const overlay = svgEl("rect", {
      x: (cx - slot / 2).toFixed(1), y: m.t, width: slot.toFixed(1), height: ih,
      fill: "transparent",
    });
    const title = svgEl("title");
    title.textContent = `${fmtDayLong(dates[i])}: ${titleParts.join(", ")}`;
    overlay.appendChild(title);
    svg.appendChild(overlay);
  }

  // X-Ticks
  for (const i of pickIndices(n, Math.floor(iw / 74))) {
    const tx = m.l + slot * (i + 0.5);
    const anchor = i === 0 ? "start" : i === n - 1 ? "end" : "middle";
    const label = svgEl("text", { x: tx, y: h - m.b + 18, "text-anchor": anchor });
    label.textContent = fmtDayShort(dates[i]);
    svg.appendChild(label);
  }

  mount.appendChild(svg);
}

/* ------------------------------------------------------------------------- *
 * Kennzahlen, Legende, Bilanz (sichtbare Zahlen im DOM)
 * ------------------------------------------------------------------------- */

function statItem(label, value, unit, note) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.className = "trend-stat-label";
  name.textContent = label;
  const val = document.createElement("span");
  val.className = "trend-stat-value";
  val.append(value);
  if (unit) {
    const small = document.createElement("small");
    small.textContent = unit;
    val.appendChild(small);
  }
  li.append(name, val);
  if (note) {
    const sub = document.createElement("span");
    sub.className = "trend-stat-note";
    sub.textContent = note;
    li.appendChild(sub);
  }
  return li;
}

function renderStats(averages) {
  const list = el("trend-stats");
  list.textContent = "";
  const avg = averages && typeof averages === "object" ? averages : {};
  list.append(
    statItem(t("trends.statScore"), fmtNum(avg.sleep_score, 0), "", t("trends.statScoreNote")),
    statItem(t("trends.statEff"), fmtNum(avg.sleep_efficiency_pct, 0), "%", t("metrics.efficiencyNote")),
    statItem(t("trends.statDur"), fmtMinutes(avg.total_sleep_min), "", t("trends.statDurNote")),
    statItem(t("trends.statHrv"), fmtNum(avg.avg_hrv, 0), "ms", t("trends.statHrvNote")),
  );
}

function renderLegend(distribution) {
  const list = el("stages-legend");
  list.textContent = "";
  const dist = distribution && typeof distribution === "object" ? distribution : {};
  for (const stage of STACK_STAGES) {
    const li = document.createElement("li");
    li.style.color = stage.ink;
    const dot = document.createElement("i");
    dot.style.background = stage.color;
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.textContent = t(stage.labelKey);
    const pct = document.createElement("span");
    pct.className = "legend-pct";
    pct.textContent = isNum(dist[stage.key]) ? t("trends.legendPct", { pct: fmtNum(dist[stage.key], 0) }) : "–";
    li.append(dot, name, pct);
    list.appendChild(li);
  }
}

function ledgerRow(label, note, value, unit, dateStr) {
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
  if (dateStr) {
    const date = document.createElement("span");
    date.className = "ledger-date";
    date.textContent = fmtDayLong(dateStr);
    dd.appendChild(date);
  }
  row.append(dt, dd);
  return row;
}

function renderLedger(data) {
  const dl = el("trend-ledger");
  dl.textContent = "";
  const best = data.best_night;
  const worst = data.worst_night;
  const stddev = data.consistency && isNum(data.consistency.score_stddev)
    ? data.consistency.score_stddev
    : null;

  dl.append(
    ledgerRow(
      t("trends.best"), t("trends.bestNote"),
      best && isNum(best.sleep_score) ? fmtNum(best.sleep_score, 0) : "–",
      best ? t("trends.points") : "", best?.date,
    ),
    ledgerRow(
      t("trends.worst"), t("trends.worstNote"),
      worst && isNum(worst.sleep_score) ? fmtNum(worst.sleep_score, 0) : "–",
      worst ? t("trends.points") : "", worst?.date,
    ),
    ledgerRow(
      t("trends.consistency"), t("trends.consistencyNote"),
      stddev !== null ? `± ${fmtNum(stddev, 1)}` : "–",
      stddev !== null ? t("trends.points") : "",
    ),
  );
}

/* ------------------------------------------------------------------------- *
 * Zusammenbau der Ansicht
 * ------------------------------------------------------------------------- */

function seriesOf(data) {
  const s = data && typeof data.series === "object" && data.series ? data.series : {};
  const dates = Array.isArray(s.date) ? s.date : [];
  const pick = (key) => (Array.isArray(s[key]) ? s[key] : []);
  return {
    dates,
    score: pick("sleep_score"),
    eff: pick("sleep_efficiency_pct"),
    hrv: pick("avg_hrv"),
    stages: pick("stages_pct"),
  };
}

function renderCharts(data) {
  const s = seriesOf(data);

  renderLineChart(el("chart-score"), {
    dates: s.dates, values: s.score, color: COLOR_SCORE,
    height: 260, decimals: 0, clampMin: 0, clampMax: 100, area: true, glow: true,
  });
  renderStagesChart(el("chart-stages"), s.dates, s.stages);
  renderLineChart(el("chart-eff"), {
    dates: s.dates, values: s.eff, color: COLOR_EFF,
    height: 180, unit: " %", decimals: 0, clampMin: 0, clampMax: 100,
  });
  renderLineChart(el("chart-hrv"), {
    dates: s.dates, values: s.hrv, color: COLOR_HRV,
    height: 180, unit: " ms", decimals: 0, clampMin: 0,
  });
}

function describeRange(data) {
  const from = data.range?.from, to = data.range?.to;
  const n = data.n_nights;
  const nights = `${fmtNum(n, 0)} ${n === 1 ? t("trends.nightOne") : t("trends.nightsMany")}`;
  if (from && to) {
    return `${nights} · ${t("common.fromToPlain", { from: fmtDayLong(from), to: fmtDayLong(to) })}`;
  }
  return nights;
}

function setCaptions(data) {
  const s = seriesOf(data);
  const scoreVals = s.score.filter(isNum);
  const range = describeRange(data);
  el("chart-score-caption").textContent = scoreVals.length
    ? t("trends.capScore", {
        range,
        min: fmtNum(Math.min(...scoreVals), 0),
        max: fmtNum(Math.max(...scoreVals), 0),
        last: fmtNum(scoreVals[scoreVals.length - 1], 0),
      })
    : t("trends.capScoreEmpty");
  el("chart-stages-caption").textContent = t("trends.capStages");
  el("chart-eff-caption").textContent =
    t("trends.capEff", { avg: fmtNum(data.averages?.sleep_efficiency_pct, 0) });
  el("chart-hrv-caption").textContent =
    t("trends.capHrv", { avg: fmtNum(data.averages?.avg_hrv, 0) });

  el("chart-score").setAttribute("aria-label", t("trends.ariaScore", { range }));
  el("chart-stages").setAttribute("aria-label", t("trends.ariaStages", { range }));
  el("chart-eff").setAttribute("aria-label", t("trends.ariaEff", { range }));
  el("chart-hrv").setAttribute("aria-label", t("trends.ariaHrv", { range }));

  el("avg-eff").textContent = "";
  el("avg-eff").append(`${t("common.avg")} ${fmtNum(data.averages?.sleep_efficiency_pct, 0)}`);
  const effUnit = document.createElement("small"); effUnit.textContent = "%";
  el("avg-eff").appendChild(effUnit);
  el("avg-hrv").textContent = "";
  el("avg-hrv").append(`${t("common.avg")} ${fmtNum(data.averages?.avg_hrv, 0)}`);
  const hrvUnit = document.createElement("small"); hrvUnit.textContent = "ms";
  el("avg-hrv").appendChild(hrvUnit);
}

async function fetchJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} für ${url}`);
  return res.json();
}

/**
 * Erzeugt die Verlauf-Ansicht (lazy: lädt erst beim ersten Anzeigen).
 * Rückgabe: { show() } — idempotent, wird vom Ansichten-Umschalter in
 * app.js beim Wechsel auf „Verlauf" aufgerufen.
 */
export function createTrendsView() {
  let currentDays = 30;
  let loadedDays = null;
  let loading = false;
  let lastData = null;
  const cache = new Map();

  function setState(which) {
    el("trends-loading").hidden = which !== "loading";
    el("trends-empty").hidden = which !== "empty";
    el("trends-error").hidden = which !== "error";
    el("trends-body").hidden = which !== "body";
  }

  function setPickerPressed() {
    for (const btn of el("range-picker").querySelectorAll("button")) {
      btn.setAttribute("aria-pressed", Number(btn.dataset.days) === currentDays ? "true" : "false");
      btn.disabled = loading;
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    setPickerPressed();
    setState("loading");
    try {
      let data = cache.get(currentDays);
      if (!data) {
        data = await fetchJSON(`/api/trends?days=${currentDays}`);
        cache.set(currentDays, data);
      }
      lastData = data;
      loadedDays = currentDays;
      // Range-Zeile nur zeigen, wenn es auch Charts gibt (>= 2 Nächte) — sonst
      // stünde sie widersprüchlich über dem Empty-State.
      el("trends-range").textContent =
        data && isNum(data.n_nights) && data.n_nights >= 2 ? describeRange(data) : "";

      if (!data || !isNum(data.n_nights) || data.n_nights < 2) {
        setState("empty");
        return;
      }
      renderStats(data.averages);
      renderLegend(data.stage_distribution_pct);
      renderLedger(data);
      setState("body"); // erst sichtbar machen, dann Charts in Pixelbreite messen
      paintCharts(data);
      setCaptions(data);
    } catch (err) {
      console.warn("Somnoscope: Verlauf konnte nicht geladen werden.", err);
      lastData = null;
      setState("error");
    } finally {
      loading = false;
      setPickerPressed();
    }
  }

  el("range-picker").addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-days]");
    if (!btn || loading) return;
    const days = Number(btn.dataset.days);
    if (!Number.isFinite(days) || days === currentDays) return;
    currentDays = days;
    load();
  });

  el("trends-retry").addEventListener("click", () => {
    cache.delete(currentDays);
    loadedDays = null;
    load();
  });

  // Charts bei Breitenänderung neu rendern: sie sind in Pixelbreite
  // gezeichnet, damit die Beschriftung scharf bleibt. Doppelt abgesichert
  // (ResizeObserver + window-resize) und beim Anzeigen erneut geprüft.
  let resizeTimer = 0;
  let renderedWidth = 0;

  function paintCharts(data) {
    renderedWidth = el("chart-score").clientWidth;
    renderCharts(data);
  }

  const rerenderOnResize = () => {
    if (!lastData || el("trends").hidden || el("trends-body").hidden) return;
    const width = el("chart-score").clientWidth;
    if (!width || width === renderedWidth) return;
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => paintCharts(lastData), 160);
  };
  if ("ResizeObserver" in window) {
    new ResizeObserver(rerenderOnResize).observe(el("trends"));
  }
  window.addEventListener("resize", rerenderOnResize);

  // Sprachwechsel: Ansicht aus dem zwischengespeicherten Payload neu zeichnen
  // (kein Neu-Fetch). Laden-/Leer-/Fehlerzustände sind statisch und werden
  // bereits von applyStatic() in i18n.js übersetzt.
  onLangChange(() => {
    if (!lastData || !isNum(lastData.n_nights) || lastData.n_nights < 2) return;
    try {
      el("trends-range").textContent = describeRange(lastData);
      renderStats(lastData.averages);
      renderLegend(lastData.stage_distribution_pct);
      renderLedger(lastData);
      paintCharts(lastData);
      setCaptions(lastData);
    } catch (err) {
      console.error("Somnoscope: Verlauf-Rendering-Fehler beim Sprachwechsel.", err);
    }
  });

  return {
    /** Zeigt die Ansicht an; lädt lazy bzw. passt die Charts der Breite an. */
    show() {
      if (loadedDays !== currentDays) {
        load();
        return;
      }
      // Breite kann sich geändert haben, während die Ansicht verborgen war.
      rerenderOnResize();
    },
  };
}
