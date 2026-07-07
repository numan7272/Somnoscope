/*
 * Somnoscope · Schlaf-Observatorium — Three.js-Nachtszene (ES-Modul).
 *
 * Die Szene ist DATEN-GETRIEBEN, nicht Deko: die Schlafphasen-Sequenz der
 * Nacht wird in eine 1D-Farbtextur gebrannt, die den Himmel entlang der
 * x-Achse (= Zeitachse der Nacht) färbt. Tiefschlaf → langsame Indigo-Wellen,
 * REM → violettes Schimmern, Leichtschlaf → Teal, Wachphasen → Bernstein-
 * Aufhellungen. Dazu:
 *
 *   - Score-Orb (Mond) mit Fresnel-Glow; der Lichtanteil auf der Kugel
 *     entspricht dem Sleep-Score (wie eine Mondphase).
 *   - Hypnogramm als leuchtendes 3D-Band (Terrain-Silhouette) mit sanftem
 *     Undulieren; Hover/Tap zeigt Segment-Details (Callback → Tooltip).
 *   - Vitalwerte (HR/HRV/SpO2) als Linien + Partikel-Ströme entlang der
 *     ECHTEN Kurven.
 *
 * Die 3D-Objekte schweben in Platzhalter-Elementen der Seite
 * ([data-anchor="orb|band|vitals"]); die Kamera folgt dem Scroll.
 *
 * Robustheit: schlägt die Renderer-Erzeugung fehl, gibt createNightScene()
 * null zurück und die DOM-Schicht übernimmt (body.no-webgl). Bei
 * prefers-reduced-motion läuft kein Animations-Loop; gerendert wird nur
 * auf Scroll/Resize (ruhige, statische Ansicht).
 */
import * as THREE from "three";

/* Phasenfarben — identisch zu style.css / app.js. */
const STAGE_COLORS = {
  wake:  new THREE.Color("#e09a4a"),
  light: new THREE.Color("#3fb8ae"),
  deep:  new THREE.Color("#5b6ee8"),
  rem:   new THREE.Color("#b26ce0"),
};
/* "Energie" einer Phase: steuert Aurora-Helligkeit (wach = hell/bernstein). */
const STAGE_ENERGY = { wake: 1.0, rem: 0.60, light: 0.42, deep: 0.26 };
/* Kammhöhe des Hypnogramm-Bands je Phase (0..1). */
const STAGE_LEVEL = { wake: 1.0, rem: 0.68, light: 0.40, deep: 0.10 };

const VITALS = [
  { key: "heart_rate", color: new THREE.Color("#ef8fa3"), offset: 0.62 },
  { key: "hrv",        color: new THREE.Color("#8fe3b0"), offset: 0.0 },
  { key: "spo2",       color: new THREE.Color("#a9c8ff"), offset: -0.62 },
];

const CAM_Z = 12;
const FOV = 50;

/* GLSL-Bausteine: Hash / Value-Noise / fbm (klein, ohne Texturen). */
const GLSL_NOISE = /* glsl */ `
  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
  float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
  }
  float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int k = 0; k < 4; k++) { v += a * vnoise(p); p *= 2.03; a *= 0.5; }
    return v;
  }
`;

/**
 * Erzeugt die komplette Nachtszene auf dem übergebenen Canvas.
 *
 * @param {Object} opts
 * @param {HTMLCanvasElement} opts.canvas   Ziel-Canvas (position:fixed, fullscreen).
 * @param {Object|null} opts.report         SleepReport-Dict oder null (Leerzustand).
 * @param {boolean} opts.reducedMotion      prefers-reduced-motion aktiv?
 * @param {Function} opts.onBandPoint       (segment|null, clientX, clientY) → Tooltip.
 * @param {Function} opts.onFail            Wird bei Kontextverlust gerufen (Fallback).
 * @returns {Object|null} Controller ({dispose}) oder null, wenn WebGL scheitert.
 */
export function createNightScene({ canvas, report, reducedMotion, onBandPoint, onFail }) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
  } catch (err) {
    console.warn("Somnoscope: WebGL-Renderer fehlgeschlagen.", err);
    return null;
  }

  const isMobile =
    window.matchMedia("(max-width: 760px)").matches ||
    window.matchMedia("(pointer: coarse)").matches;
  const maxDPR = isMobile ? 1.5 : 2;

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxDPR));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x070a1c, 1);
  renderer.autoClear = false;

  // Alle Event-Listener hängen an diesem Controller — dispose() ruft ac.abort()
  // und entfernt sie damit in einem Rutsch (vollständiger Lifecycle).
  const ac = new AbortController();
  const signal = ac.signal;

  /* Gemeinsame Uniforms aller Materialien (geteilte Referenzen). */
  const U = {
    uTime:       { value: 0 },
    uEntrance:   { value: reducedMotion ? 1 : 0 },
    uScroll:     { value: 0 },
    uPixelRatio: { value: renderer.getPixelRatio() },
    uAspect:     { value: window.innerWidth / window.innerHeight },
  };

  /* --- Himmel (eigene Ortho-Szene, immer bildschirmfüllend) --------------- */

  const skyScene = new THREE.Scene();
  const skyCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  const stageTex = buildStageTexture(report);
  const skyMat = new THREE.ShaderMaterial({
    depthWrite: false,
    depthTest: false,
    uniforms: {
      uTime: U.uTime, uEntrance: U.uEntrance, uScroll: U.uScroll, uAspect: U.uAspect,
      uStageTex: { value: stageTex },
      uHasData: { value: report && Array.isArray(report.hypnogram) && report.hypnogram.length ? 1 : 0 },
    },
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = vec4(position.xy, 0.999, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec2 vUv;
      uniform float uTime, uEntrance, uScroll, uAspect, uHasData;
      uniform sampler2D uStageTex;
      ${GLSL_NOISE}
      void main() {
        vec2 uv = vUv;
        // Grundhimmel: tiefes Indigo, zum Horizont hin heller
        vec3 cTop = vec3(0.013, 0.017, 0.075);
        vec3 cHor = vec3(0.065, 0.085, 0.230);
        float horizon = pow(1.0 - uv.y, 1.7);
        vec3 col = mix(cTop, cHor, horizon);

        // Die Nacht als Zeitachse: Phase-Farbe + Energie an Position x
        // (3-Tap-Weichzeichner: sanfte Übergänge zwischen den Phasen-Farben)
        float tx = clamp(uv.x, 0.0, 1.0);
        vec4 stage = 0.34 * texture2D(uStageTex, vec2(clamp(tx - 0.035, 0.0, 1.0), 0.5))
                   + 0.32 * texture2D(uStageTex, vec2(tx, 0.5))
                   + 0.34 * texture2D(uStageTex, vec2(clamp(tx + 0.035, 0.0, 1.0), 0.5));

        // Aurora-Vorhang 1 (schneller, fein strukturiert)
        vec2 q = vec2(uv.x * uAspect, uv.y);
        float t = uTime * 0.022;
        float w1 = fbm(q * vec2(1.3, 2.1) + vec2(t * 1.6, -t));
        float yc1 = 0.60 + 0.15 * sin(uv.x * 3.2 + uTime * 0.05) + 0.24 * (w1 - 0.5);
        float band1 = exp(-pow((uv.y - yc1) * 3.2, 2.0));
        float curt = fbm(vec2(uv.x * 6.5 + uTime * 0.035, uv.y * 1.3));
        float aur1 = band1 * (0.30 + 0.70 * curt);
        vec3 tint = mix(vec3(0.16, 0.55, 0.52), vec3(0.44, 0.27, 0.68),
                        fbm(vec2(uv.x * 2.1 - t, 0.31)));
        vec3 aCol = mix(tint, stage.rgb, uHasData * 0.68);
        col += aCol * aur1 * (0.30 + 0.55 * stage.a) * uEntrance;

        // Aurora-Welle 2 (tief, langsam — der "Tiefschlaf-Atem" des Himmels)
        float w2 = fbm(q * 1.05 + vec2(-t * 0.7, t * 0.4));
        float yc2 = 0.28 + 0.09 * sin(uv.x * 2.0 - uTime * 0.028) + 0.14 * (w2 - 0.5);
        float band2 = exp(-pow((uv.y - yc2) * 4.2, 2.0));
        col += stage.rgb * band2 * 0.20 * uEntrance * (0.35 + 0.65 * uHasData);

        // Bernstein-Aufhellung am Horizont bei Wachphasen
        col += stage.rgb * stage.a * stage.a * horizon * 0.12 * uEntrance;

        // Vignette + leichte Abdunklung beim Scrollen (Lesbarkeit des Inhalts)
        float vig = smoothstep(1.35, 0.42, length(uv - vec2(0.5, 0.55)));
        col *= mix(0.72, 1.0, vig);
        col *= 1.0 - 0.28 * clamp(uScroll * 0.4, 0.0, 1.0);

        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  const skyQuad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), skyMat);
  skyQuad.frustumCulled = false;
  skyQuad.renderOrder = 0;
  skyScene.add(skyQuad);

  /* Sterne: screenspace-Punkte mit individuellem Funkeln + Scroll-Parallaxe. */
  const starCount = isMobile ? 320 : 900;
  const stars = buildStars(starCount, U);
  stars.renderOrder = 1;
  skyScene.add(stars);

  /* --- Hauptszene (Orb, Band, Vitalströme; Kamera folgt dem Scroll) ------- */

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(
    FOV, window.innerWidth / window.innerHeight, 0.1, 200);
  camera.position.set(0, 0, CAM_Z);

  const glowTex = makeGlowTexture();
  const groups = {};   // anchor-key → {group, el}
  let bandInfo = null; // {mesh, segments} für Raycasts

  const anchorEl = (key) => document.querySelector(`[data-anchor="${key}"]`);

  if (report) {
    // Score-Orb
    const orbEl = anchorEl("orb");
    if (orbEl) {
      const g = buildOrb(report, U, glowTex);
      groups.orb = { group: g.group, el: orbEl, orb: g };
      scene.add(g.group);
    }
    // Hypnogramm-Band
    const bandEl = anchorEl("band");
    if (bandEl && Array.isArray(report.hypnogram) && report.hypnogram.length) {
      const b = buildBand(report, U);
      if (b) {
        groups.band = { group: b.group, el: bandEl };
        bandInfo = b;
        scene.add(b.group);
        wireBandPointer(bandEl, b);
      }
    }
    // Vital-Ströme
    const vitalsEl = anchorEl("vitals");
    if (vitalsEl) {
      const v = buildVitals(report, U, glowTex, isMobile, reducedMotion);
      if (v) {
        groups.vitals = { group: v.group, el: vitalsEl, vitals: v };
        scene.add(v.group);
      }
    }
  }

  /* --- Layout: DOM-Anker → Welt-Koordinaten ------------------------------- */

  function worldPerPixel() {
    return (2 * CAM_Z * Math.tan(THREE.MathUtils.degToRad(FOV / 2))) / window.innerHeight;
  }

  function layout() {
    const wpp = worldPerPixel();
    const viewW = window.innerWidth * wpp;
    const scrollY = window.scrollY || 0;

    for (const item of Object.values(groups)) {
      const rect = item.el.getBoundingClientRect();
      const centerPx = rect.top + scrollY + rect.height / 2;
      item.group.position.y = -centerPx * wpp;
      item.anchorHeightWorld = rect.height * wpp;
    }
    if (groups.orb) {
      const r = THREE.MathUtils.clamp(viewW * 0.105, 1.05, 1.85);
      groups.orb.group.scale.setScalar(r);
    }
    if (groups.band) {
      const h = THREE.MathUtils.clamp(viewW * 0.16, 1.3, 2.5);
      groups.band.group.scale.set(viewW * 0.88, h, 1);
    }
    if (groups.vitals) {
      const h = THREE.MathUtils.clamp(viewW * 0.075, 0.75, 1.15);
      groups.vitals.group.scale.set(viewW * 0.88, h, 1);
    }
  }

  /* --- Hypnogramm-Hover: Raycast auf das Band ----------------------------- */

  const raycaster = new THREE.Raycaster();
  const pointerNDC = new THREE.Vector2();
  let tooltipTimer = 0;

  function wireBandPointer(elm, band) {
    const handle = (ev) => {
      pointerNDC.set(
        (ev.clientX / window.innerWidth) * 2 - 1,
        -(ev.clientY / window.innerHeight) * 2 + 1);
      raycaster.setFromCamera(pointerNDC, camera);
      const hit = raycaster.intersectObject(band.mesh, false)[0];
      if (hit && hit.uv) {
        const seg = band.segmentAt(hit.uv.x);
        if (seg) {
          onBandPoint?.(seg, ev.clientX, ev.clientY);
          requestRender();
          clearTimeout(tooltipTimer);
          if (ev.pointerType === "touch") {
            tooltipTimer = setTimeout(() => onBandPoint?.(null, 0, 0), 2600);
          }
          return;
        }
      }
      onBandPoint?.(null, 0, 0);
    };
    elm.addEventListener("pointermove", handle, { signal });
    elm.addEventListener("pointerdown", handle, { signal });
    elm.addEventListener("pointerleave", () => onBandPoint?.(null, 0, 0), { signal });
  }

  /* --- Kamera / Scroll / Maus-Sway ---------------------------------------- */

  let camTargetY = 0;
  let camTargetX = 0;

  function updateScrollTargets() {
    const wpp = worldPerPixel();
    const scrollY = window.scrollY || 0;
    camTargetY = -(scrollY + window.innerHeight / 2) * wpp;
    U.uScroll.value = scrollY / Math.max(1, window.innerHeight);
  }

  const canHover = window.matchMedia("(hover: hover)").matches;
  if (canHover && !reducedMotion) {
    window.addEventListener("pointermove", (ev) => {
      camTargetX = ((ev.clientX / window.innerWidth) - 0.5) * 0.45;
    }, { passive: true, signal });
  }

  /* --- Render-Loop --------------------------------------------------------- */

  const clock = new THREE.Clock();
  let rafId = 0;
  let disposed = false;
  let entranceStart = performance.now();

  function renderFrame() {
    renderer.clear();
    renderer.render(skyScene, skyCam);
    renderer.render(scene, camera);
  }

  function animate() {
    if (disposed) return;
    rafId = requestAnimationFrame(animate);
    const dt = Math.min(clock.getDelta(), 0.1);
    U.uTime.value += dt;

    // Entrance-Choreografie: ease-out über ~2.6 s
    if (U.uEntrance.value < 1) {
      const p = Math.min(1, (performance.now() - entranceStart) / 2600);
      U.uEntrance.value = 1 - Math.pow(1 - p, 4);
    }

    updateScrollTargets();
    camera.position.y += (camTargetY - camera.position.y) * Math.min(1, dt * 6);
    camera.position.x += (camTargetX - camera.position.x) * Math.min(1, dt * 3);

    // Ruhige Eigenbewegung des Orbs (Atmen)
    if (groups.orb) {
      groups.orb.orb.mesh.position.y = Math.sin(U.uTime.value * 0.45) * 0.05;
    }
    // Partikel entlang der Vitalkurven treiben lassen
    if (groups.vitals) groups.vitals.vitals.advance(dt);

    renderFrame();
  }

  /* Reduced-Motion: kein Loop — nur auf Anforderung rendern. */
  let renderQueued = false;
  function requestRender() {
    if (!reducedMotion || disposed || renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      updateScrollTargets();
      camera.position.y = camTargetY;
      renderFrame();
    });
  }

  /* --- Resize / Lifecycle --------------------------------------------------- */

  function onResize() {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxDPR));
    renderer.setSize(window.innerWidth, window.innerHeight);
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    U.uPixelRatio.value = renderer.getPixelRatio();
    U.uAspect.value = window.innerWidth / window.innerHeight;
    layout();
    updateScrollTargets();
    if (reducedMotion) {
      camera.position.y = camTargetY;
      renderFrame();
    }
  }
  window.addEventListener("resize", onResize, { signal });

  if (reducedMotion) {
    window.addEventListener("scroll", requestRender, { passive: true, signal });
  } else {
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        cancelAnimationFrame(rafId);
      } else if (!disposed) {
        clock.getDelta();
        animate();
      }
    }, { signal });
  }

  canvas.addEventListener("webglcontextlost", (ev) => {
    ev.preventDefault();
    disposed = true;
    cancelAnimationFrame(rafId);
    console.warn("Somnoscope: WebGL-Kontext verloren — statische Ansicht.");
    onFail?.();
  }, { signal });

  /* Start: Layout nach zwei Frames (Fonts/Umbrüche gesetzt), dann los. */
  requestAnimationFrame(() => requestAnimationFrame(() => {
    layout();
    updateScrollTargets();
    camera.position.y = camTargetY; // ohne Anfahrt von Seitenanfang starten
    if (reducedMotion) {
      renderFrame();
    } else {
      entranceStart = performance.now();
      animate();
    }
  }));
  // Falls spät ladende Inhalte die Anker verschieben:
  window.addEventListener("load", () => { layout(); requestRender(); }, { signal });

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(rafId);
      ac.abort();                       // entfernt ALLE Listener auf einmal
      disposeSceneResources(skyScene);
      disposeSceneResources(scene);
      stageTex.dispose();
      glowTex.dispose();
      renderer.dispose();
    },
  };
}

/**
 * Gibt Geometrien, Materialien und Texturen einer Szene frei (GPU-Speicher).
 *
 * @param {THREE.Object3D} root  Wurzel der zu räumenden (Sub-)Szene.
 */
function disposeSceneResources(root) {
  root.traverse((obj) => {
    if (obj.geometry) obj.geometry.dispose();
    const mats = Array.isArray(obj.material)
      ? obj.material
      : obj.material ? [obj.material] : [];
    for (const m of mats) {
      if (m.map && typeof m.map.dispose === "function") m.map.dispose();
      m.dispose();
    }
  });
}

/* ==========================================================================
 * Bausteine
 * ========================================================================== */

/**
 * Brennt die Schlafphasen-Sequenz in eine 1D-RGBA-Textur (256 Texel).
 * RGB = Phasenfarbe, A = "Energie" (steuert Aurora-Helligkeit).
 * Ohne Daten: neutrales Teal-Indigo mit ruhiger Energie.
 */
function buildStageTexture(report) {
  const W = 256;
  const data = new Uint8Array(W * 4);
  const hypno = report && Array.isArray(report.hypnogram) ? report.hypnogram : [];

  const segments = [];
  if (hypno.length) {
    const t0 = Date.parse(hypno[0]?.start);
    const t1 = Date.parse(hypno[hypno.length - 1]?.end);
    if (Number.isFinite(t0) && Number.isFinite(t1) && t1 > t0) {
      for (const seg of hypno) {
        const s = Date.parse(seg.start), e = Date.parse(seg.end);
        if (!Number.isFinite(s) || !Number.isFinite(e) || !STAGE_COLORS[seg.stage]) continue;
        segments.push({ a: (s - t0) / (t1 - t0), b: (e - t0) / (t1 - t0), stage: seg.stage });
      }
    }
  }

  const neutral = new THREE.Color("#3f6ea8").lerp(new THREE.Color("#3fb8ae"), 0.35);
  for (let i = 0; i < W; i++) {
    const t = i / (W - 1);
    let color = neutral, energy = 0.35;
    for (const seg of segments) {
      if (t >= seg.a && t <= seg.b) {
        color = STAGE_COLORS[seg.stage];
        energy = STAGE_ENERGY[seg.stage] ?? 0.4;
        break;
      }
    }
    data[i * 4 + 0] = Math.round(color.r * 255);
    data[i * 4 + 1] = Math.round(color.g * 255);
    data[i * 4 + 2] = Math.round(color.b * 255);
    data[i * 4 + 3] = Math.round(energy * 255);
  }

  const tex = new THREE.DataTexture(data, W, 1, THREE.RGBAFormat);
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearFilter;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

/** Screenspace-Sternfeld mit individuellem Funkeln und Scroll-Parallaxe. */
function buildStars(count, U) {
  const pos = new Float32Array(count * 3);
  const phase = new Float32Array(count);
  const size = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    pos[i * 3 + 0] = (Math.random() * 2 - 1) * 1.02;
    pos[i * 3 + 1] = Math.random() * 2 - 1;
    pos[i * 3 + 2] = 0;
    phase[i] = Math.random() * Math.PI * 2;
    size[i] = Math.random() < 0.06 ? 2.6 + Math.random() * 1.4 : 0.7 + Math.random() * 1.5;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
  geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));

  const mat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: U.uTime, uEntrance: U.uEntrance,
      uScroll: U.uScroll, uPixelRatio: U.uPixelRatio,
    },
    vertexShader: /* glsl */ `
      attribute float aPhase;
      attribute float aSize;
      uniform float uTime, uScroll, uPixelRatio;
      varying float vTw;
      void main() {
        vec3 p = position;
        // Parallaxe: der Himmel gleitet langsamer als der Inhalt
        p.y = mod(p.y + uScroll * 0.12 + 1.0, 2.0) - 1.0;
        vTw = 0.5 + 0.5 * sin(uTime * (0.35 + fract(aPhase) * 0.8) + aPhase * 13.0);
        gl_Position = vec4(p.xy, 0.5, 1.0);
        gl_PointSize = aSize * uPixelRatio;
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying float vTw;
      uniform float uEntrance;
      void main() {
        vec2 d = gl_PointCoord - 0.5;
        float a = pow(smoothstep(0.5, 0.0, length(d)), 2.0);
        gl_FragColor = vec4(vec3(0.86, 0.90, 1.0), a * (0.35 + 0.65 * vTw) * uEntrance);
      }
    `,
  });
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false;
  return points;
}

/** Weiche radiale Glow-Textur (Canvas), für Orb-Halo und Vital-Partikel. */
function makeGlowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0.0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(255,255,255,0.55)");
  g.addColorStop(0.6, "rgba(255,255,255,0.12)");
  g.addColorStop(1.0, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * Score-Orb: Kugel mit Fresnel-Rand und Mondphasen-Füllung nach Score,
 * dahinter ein additiver Halo-Sprite.
 */
function buildOrb(report, U, glowTex) {
  const score = typeof report.sleep_score === "number"
    ? THREE.MathUtils.clamp(report.sleep_score / 100, 0, 1)
    : 0.6;

  const group = new THREE.Group();
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: U.uTime, uEntrance: U.uEntrance,
      uScore: { value: score },
    },
    vertexShader: /* glsl */ `
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec3 vN;
      varying vec3 vV;
      uniform float uTime, uScore, uEntrance;
      ${GLSL_NOISE}
      void main() {
        vec3 n = normalize(vN);
        vec3 v = normalize(vV);
        float fres = pow(1.0 - max(dot(n, v), 0.0), 2.3);

        // Mondphase: der beleuchtete Anteil entspricht dem Score
        vec3 lightDir = normalize(vec3(-0.55, 0.30, 0.78));
        float thr = mix(0.95, -1.05, uScore * uEntrance);
        float lit = smoothstep(thr - 0.28, thr + 0.28, dot(n, lightDir));

        // Mond-Oberfläche: gefleckt (fbm über die Normale, nahtfrei)
        float m = fbm(vec2(n.x * 2.6 + n.z * 1.7, n.y * 2.6 - n.z * 1.3)
                      + vec2(uTime * 0.008, 0.0));
        vec3 surfLit = mix(vec3(0.82, 0.80, 0.74), vec3(0.99, 0.97, 0.90), m);
        vec3 surfDark = vec3(0.070, 0.085, 0.220) * (0.75 + 0.5 * m);
        vec3 col = mix(surfDark, surfLit, lit);

        // Aurora-Rim: Teal ↔ Violett, atmet langsam
        vec3 rim = mix(vec3(0.32, 0.76, 0.72), vec3(0.60, 0.42, 0.86),
                       0.5 + 0.5 * sin(uTime * 0.14));
        col += rim * fres * (0.35 + 0.65 * uScore) * uEntrance;

        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 48), mat);
  group.add(mesh);

  const halo = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTex,
    color: new THREE.Color("#8ea0ff").lerp(new THREE.Color("#efe9da"), score * 0.6),
    transparent: true,
    opacity: 0.14 + 0.3 * score,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  }));
  halo.scale.setScalar(4.6);
  halo.position.z = -0.5;
  group.add(halo);

  return { group, mesh, halo };
}

/** Phase an normalisierter Zeit t (0..1) — vorbereitete Segmentliste. */
function normalizedSegments(report) {
  const hypno = Array.isArray(report.hypnogram) ? report.hypnogram : [];
  if (!hypno.length) return null;
  const t0 = Date.parse(hypno[0]?.start);
  const t1 = Date.parse(hypno[hypno.length - 1]?.end);
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) return null;
  const list = [];
  for (const seg of hypno) {
    const s = Date.parse(seg.start), e = Date.parse(seg.end);
    // Ein einziger flacher Filter: ungueltige Zeiten ODER unbekannte Phase raus.
    if (!Number.isFinite(s) || !Number.isFinite(e) || !(seg.stage in STAGE_LEVEL)) continue;
    list.push({ a: (s - t0) / (t1 - t0), b: (e - t0) / (t1 - t0), seg });
  }
  return list.length ? list : null;
}

/**
 * Hypnogramm als leuchtendes Terrain-Band: Kammhöhe = Phase, Farbe = Phase,
 * nach unten auslaufend (additiv). Undulieren im Vertex-Shader.
 */
function buildBand(report, U) {
  const segs = normalizedSegments(report);
  if (!segs) return null;

  const N = 320;
  const stageAt = (t) => {
    for (const s of segs) if (t >= s.a && t <= s.b) return s.seg.stage;
    return segs[t < 0.5 ? 0 : segs.length - 1].seg.stage;
  };

  // Level-Kurve abtasten und glätten (sanftes Terrain statt harter Stufen)
  const raw = new Float32Array(N);
  const stages = new Array(N);
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    stages[i] = stageAt(t);
    raw[i] = STAGE_LEVEL[stages[i]] ?? 0.4;
  }
  const level = new Float32Array(N);
  const R = 5;
  for (let i = 0; i < N; i++) {
    let sum = 0, cnt = 0;
    for (let k = -R; k <= R; k++) {
      const j = i + k;
      if (j >= 0 && j < N) { sum += raw[j]; cnt++; }
    }
    level[i] = sum / cnt;
  }

  // Geometrie: Streifen aus 2×N Vertices (unten transparent, oben Kamm)
  const positions = new Float32Array(N * 2 * 3);
  const colors = new Float32Array(N * 2 * 3);
  const uvs = new Float32Array(N * 2 * 2);
  const indices = [];
  const dimmed = new THREE.Color();

  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    const x = t - 0.5;
    const yTop = 0.12 + level[i] * 0.88;   // 0.12..1.0
    const yBot = -0.28;
    const c = STAGE_COLORS[stages[i]] ?? STAGE_COLORS.light;
    dimmed.copy(c).lerp(new THREE.Color("#0d1230"), 0.15);

    // Vertex 2i = unten, 2i+1 = oben
    positions.set([x, yBot, 0], (i * 2) * 3);
    positions.set([x, yTop, 0], (i * 2 + 1) * 3);
    colors.set([dimmed.r, dimmed.g, dimmed.b], (i * 2) * 3);
    colors.set([dimmed.r, dimmed.g, dimmed.b], (i * 2 + 1) * 3);
    uvs.set([t, 0], (i * 2) * 2);
    uvs.set([t, 1], (i * 2 + 1) * 2);

    if (i < N - 1) {
      const a = i * 2, b = i * 2 + 1, cIdx = i * 2 + 2, d = i * 2 + 3;
      indices.push(a, cIdx, b, b, cIdx, d);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
  geo.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(indices);

  const mat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    uniforms: { uTime: U.uTime, uEntrance: U.uEntrance },
    vertexShader: /* glsl */ `
      attribute vec3 aColor;
      varying vec3 vC;
      varying vec2 vUv;
      uniform float uTime, uEntrance;
      void main() {
        vC = aColor;
        vUv = uv;
        vec3 p = position;
        // sanftes Undulieren, oben stärker als unten
        p.y += uv.y * (0.035 * sin(uv.x * 21.0 + uTime * 0.55)
                     + 0.025 * sin(uv.x * 8.0 - uTime * 0.33));
        // Entrance: das Band wächst aus der Mitte
        p.x *= mix(0.65, 1.0, uEntrance);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec3 vC;
      varying vec2 vUv;
      uniform float uEntrance;
      void main() {
        float body = pow(vUv.y, 1.8) * 0.42;
        float crest = smoothstep(0.90, 1.0, vUv.y) * 0.85;
        float a = (body + crest) * uEntrance;
        vec3 col = vC * (0.5 + 0.75 * vUv.y);
        gl_FragColor = vec4(col, a);
      }
    `,
  });

  const mesh = new THREE.Mesh(geo, mat);
  const group = new THREE.Group();
  group.add(mesh);

  /** Segment-Lookup für Tooltips: normalisierte Zeit → Original-Segment. */
  const segmentAt = (t) => {
    for (const s of segs) if (t >= s.a && t <= s.b) return s.seg;
    return null;
  };

  return { group, mesh, segmentAt };
}

/**
 * Vitalwerte als Linien + treibende Partikel-Ströme entlang der echten Kurven.
 * HR (rosé) oben, HRV (mint) mittig, SpO2 (eisblau) unten.
 */
function buildVitals(report, U, glowTex, isMobile, reducedMotion) {
  const series = report.series ?? {};
  const group = new THREE.Group();
  const streams = [];

  for (const spec of VITALS) {
    const points = Array.isArray(series[spec.key]) ? series[spec.key] : [];
    const clean = points
      .map((p) => ({ t: Date.parse(p?.t), v: p?.v }))
      .filter((p) => Number.isFinite(p.t) && typeof p.v === "number" && Number.isFinite(p.v));
    if (clean.length < 4) continue;

    const t0 = clean[0].t, t1 = clean[clean.length - 1].t;
    let min = Infinity, max = -Infinity;
    for (const p of clean) { if (p.v < min) min = p.v; if (p.v > max) max = p.v; }
    const span = max - min || 1;

    // Dichte Polyline (unit-Koordinaten) als Lookup für Linie und Partikel
    const M = Math.min(240, Math.max(48, clean.length));
    const poly = new Float32Array(M * 3);
    for (let i = 0; i < M; i++) {
      const tt = t0 + ((t1 - t0) * i) / (M - 1);
      // linear interpolieren
      let j = 1;
      while (j < clean.length - 1 && clean[j].t < tt) j++;
      const p0 = clean[j - 1], p1 = clean[j];
      const f = p1.t === p0.t ? 0 : (tt - p0.t) / (p1.t - p0.t);
      const v = p0.v + (p1.v - p0.v) * f;
      poly[i * 3 + 0] = i / (M - 1) - 0.5;
      poly[i * 3 + 1] = spec.offset + (((v - min) / span) - 0.5) * 0.42;
      poly[i * 3 + 2] = 0;
    }

    // 1) stille Grundlinie
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.BufferAttribute(poly.slice(), 3));
    const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({
      color: spec.color, transparent: true, opacity: 0.30,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    group.add(line);

    // 2) Partikel-Strom entlang der Kurve
    const count = isMobile ? 46 : 100;
    const pPos = new Float32Array(count * 3);
    const params = new Float32Array(count);
    const speeds = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      params[i] = reducedMotion ? i / count : Math.random();
      speeds[i] = 0.012 + Math.random() * 0.02;
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pPos, 3));
    const pMat = new THREE.PointsMaterial({
      map: glowTex, color: spec.color,
      size: 0.055, sizeAttenuation: true,
      transparent: true, opacity: 0.9,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    const cloud = new THREE.Points(pGeo, pMat);
    cloud.frustumCulled = false;
    group.add(cloud);

    streams.push({ poly, M, pPos, params, speeds, attr: pGeo.getAttribute("position") });
  }

  if (!streams.length) return null;

  /** Setzt Partikel auf ihre Kurvenposition (param 0..1 → Polyline-Lerp). */
  const place = (stream) => {
    const { poly, M, pPos, params } = stream;
    for (let i = 0; i < params.length; i++) {
      const f = params[i] * (M - 1);
      const j = Math.min(M - 2, Math.floor(f));
      const frac = f - j;
      pPos[i * 3 + 0] = poly[j * 3] + (poly[(j + 1) * 3] - poly[j * 3]) * frac;
      pPos[i * 3 + 1] = poly[j * 3 + 1] + (poly[(j + 1) * 3 + 1] - poly[j * 3 + 1]) * frac;
      pPos[i * 3 + 2] = 0;
    }
    stream.attr.needsUpdate = true;
  };
  streams.forEach(place);

  return {
    group,
    /** Bewegt alle Ströme um dt Sekunden weiter (im Loop aufgerufen). */
    advance(dt) {
      for (const s of streams) {
        for (let i = 0; i < s.params.length; i++) {
          s.params[i] = (s.params[i] + s.speeds[i] * dt) % 1;
        }
        place(s);
      }
    },
  };
}
