"use strict";

/* Page de résultats d'une campagne : lit /api/campaigns/{uuid}/results et dessine
   la synthèse et les graphiques validés en P0 (design/resultats.html).
   Les données reçues sont les médianes par fenêtre telles que mesurées au SDR
   (vue Installation) ; la vue Antenne ajoute les pertes de la chaîne du segment. */

/* ---------- Utilitaires ---------- */
const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const NF1 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const MINUS = "−";
const f1 = (v) => (v == null || Number.isNaN(v) ? "—" : NF1.format(Math.abs(v) < 0.05 ? 0 : v).replace("-", MINUS));
const s1 = (v) => (v == null || Number.isNaN(v) ? "—" : (v > 0.049 ? "+" : "") + f1(v));
const hm = (h) => { h = ((h % 24) + 24) % 24; const H = Math.floor(h); const M = Math.round((h - H) * 60); return M === 60 ? `${H + 1} h` : `${H} h ${String(M).padStart(2, "0")}`; };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
function quantile(sorted, p) { if (!sorted.length) return null; const i = (sorted.length - 1) * p; const lo = Math.floor(i), hi = Math.ceil(i); return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo); }
function stats(arr) {
  const a = arr.filter((v) => v != null && !Number.isNaN(v)).sort((x, y) => x - y);
  if (!a.length) return null;
  const mean = a.reduce((s, v) => s + v, 0) / a.length;
  const sd = Math.sqrt(a.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, a.length - 1));
  // Intervalle de confiance : taille effective réduite (mesures corrélées), comme un bootstrap par blocs.
  const ci = 1.96 * sd / Math.sqrt(Math.max(1, a.length / 8));
  return { n: a.length, med: quantile(a, 0.5), p05: quantile(a, 0.05), p10: quantile(a, 0.1), q1: quantile(a, 0.25), q3: quantile(a, 0.75), p90: quantile(a, 0.9), p95: quantile(a, 0.95), ci };
}
function hexA(hex, alpha) {
  const h = hex.replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((x) => x + x).join("") : h, 16);
  return `rgba(${n >> 16 & 255}, ${n >> 8 & 255}, ${n & 255}, ${alpha})`;
}

/* ---------- Soleil et locator ---------- */
function locatorToLatLon(loc) {
  const L = (loc || "JJ00").toUpperCase();
  let lon = (L.charCodeAt(0) - 65) * 20 - 180 + Number(L[2]) * 2;
  let lat = (L.charCodeAt(1) - 65) * 10 - 90 + Number(L[3]);
  if (L.length >= 6) { lon += (L.charCodeAt(4) - 65) * 5 / 60 + 2.5 / 60; lat += (L.charCodeAt(5) - 65) * 2.5 / 60 + 1.25 / 60; }
  else { lon += 1; lat += 0.5; }
  return { lat, lon };
}
function sun(ts, lat, lon) {
  const R = Math.PI / 180, d = ts / 864e5 - 10957.5;
  const g = (357.529 + 0.98560028 * d) * R;
  const q = ((280.459 + 0.98564736 * d) % 360 + 360) % 360;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * R;
  const e = (23.439 - 0.00000036 * d) * R;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)) / R;
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  let eot = q - ra; eot = ((eot + 180) % 360 + 360) % 360 - 180; eot *= 4;
  const utcH = ((ts / 36e5) % 24 + 24) % 24;
  const solar = ((utcH + lon / 15 + eot / 60) % 24 + 24) % 24;
  const el = Math.asin(Math.sin(lat * R) * Math.sin(dec) + Math.cos(lat * R) * Math.cos(dec) * Math.cos((solar - 12) * 15 * R)) / R;
  return { el, solar, eot, dec };
}
const solarClass = (el) => (el > 10 ? "jour" : el < -6 ? "nuit" : "crep");
const CLASS_LABEL = { jour: "Jour", crep: "Crépuscule", nuit: "Nuit" };

/* ---------- Données ---------- */
const CLASSES = ["carrier", "digital", "ssb", "am_fm"];
const CLASS_NAME = { all: "toutes classes", carrier: "porteuses et CW", digital: "signaux numériques", ssb: "SSB", am_fm: "AM et FM" };
const CLASS_SHORT = { carrier: "CW", digital: "Num.", ssb: "SSB", am_fm: "AM/FM" };
const CLASS_TITLE = { carrier: "Porteuses et CW", digital: "Signaux numériques", ssb: "SSB", am_fm: "AM et FM" };
const SYMBOL = ["circle", "rect", "triangle", "diamond", "roundRect", "pin"];
let R, ANTS, WINS, CL, STATION, TZ, fmtDay, fmtDayHour, EOT_MID, DEC_MID, NWIN, STEP, START;
const state = { metric: "rssi", view: "installation", cls: "all", ref: 0, cmp: 1, pairsPeriod: "jour" };

function prepare(results) {
  R = results;
  // The noise point measures the floor all along: kept apart from the signals.
  R.noise = R.signals.filter((s) => s.class === "noise");
  R.signals = R.signals.filter((s) => s.class !== "noise");
  STATION = locatorToLatLon(R.campaign.locator);
  TZ = R.campaign.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  fmtDay = new Intl.DateTimeFormat("fr-FR", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" });
  fmtDayHour = new Intl.DateTimeFormat("fr-FR", { timeZone: TZ, weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const hourFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "numeric", hourCycle: "h23" });
  const dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });
  ({ start_ms: START, step_ms: STEP, count: NWIN } = R.windows);
  ANTS = R.channels.map((c) => ({ ...c, id: c.letter, k: c.idx }));
  const calmAt = (ts) => R.activity.some((a) => a.state === "calm" && a.start_ms <= ts && ts < a.end_ms);
  const segAt = (ts) => { let s = 0; R.segments.forEach((g, i) => { if (ts >= g.start_ms) s = i; }); return s; };
  WINS = [];
  for (let i = 0; i < NWIN; i++) {
    const ts = START + i * STEP, mid = ts + STEP / 2;
    const s = sun(mid, STATION.lat, STATION.lon);
    WINS.push({ ts, el: s.el, solar: s.solar, cls: solarClass(s.el), lh: +hourFmt.format(ts) % 24, day: dayFmt.format(ts), calm: calmAt(mid), seg: segAt(mid) });
  }
  const mid = START + NWIN * STEP / 2;
  ({ eot: EOT_MID, dec: DEC_MID } = sun(mid, STATION.lat, STATION.lon));
  // Moyenne par classe des signaux de cette classe (vue Installation).
  CL = {};
  for (const c of CLASSES) {
    const sigs = R.signals.filter((s) => s.class === c);
    if (!sigs.length) { CL[c] = null; continue; }
    const mean = (key, k, i) => { let sum = 0, n = 0; for (const s of sigs) { const v = s[key][k][i]; if (v != null) { sum += v; n++; } } return n ? sum / n : null; };
    CL[c] = { rssi: ANTS.map((a) => WINS.map((_, i) => mean("rssi", a.k, i))), floor: ANTS.map((a) => WINS.map((_, i) => mean("floor", a.k, i))) };
  }
  document.querySelectorAll('.seg[data-key="cls"] button').forEach((b) => { if (b.dataset.v !== "all") b.disabled = !CL[b.dataset.v]; });
  state.ref = R.campaign.reference_channel || 0;
  state.cmp = state.ref === 0 ? 1 : 0;
}

const tzOffsetH = (ts) => {
  const p = (tz) => Date.parse(new Date(ts).toLocaleString("en-US", { timeZone: tz }));
  return (p(TZ) - p("UTC")) / 3.6e6;
};
const solarToLocal = (h) => h - STATION.lon / 15 - EOT_MID / 60 + tzOffsetH(START + NWIN * STEP / 2);
const lossOf = (a, i) => R.segments.length ? (R.segments[WINS[i].seg].chains[a.k] || { loss_db: 0 }).loss_db : 0;
// Marge de qualification de la classe ; 0 quand elle est inconnue (pas de campagne A/A) :
// seul l'intervalle de confiance décide alors, et la page le dit.
const marginKnown = () => Object.keys(R.campaign.margins || {}).length > 0;
const margin = () => { const m = R.campaign.margins || {}; return m[state.cls] ?? m.all ?? 0; };
const marginText = () => (marginKnown() ? `marge ±${f1(margin())} dB` : "marge inconnue");

function rawValue(rssi, floor, a, i, metric, view) {
  if (metric === "snr") return rssi == null || floor == null ? null : rssi - floor;
  const off = view === "antenne" ? lossOf(a, i) : 0;
  if (metric === "rssi") return rssi == null ? null : rssi + off;
  return floor == null ? null : floor + off;
}
// "Toutes classes" : moyenne des classes mesurées dans la fenêtre. Un signal intermittent
// (FT8 un créneau sur deux) ne doit pas vider la fenêtre.
function value(a, c, i, metric = state.metric, view = state.view) {
  if (c === "all") {
    let s = 0, n = 0;
    for (const cc of CLASSES) { if (!CL[cc]) continue; const v = value(a, cc, i, metric, view); if (v != null) { s += v; n++; } }
    return n ? s / n : null;
  }
  if (!CL[c]) return null;
  return rawValue(CL[c].rssi[a.k][i], CL[c].floor[a.k][i], a, i, metric, view);
}
// Écart : par classe d'abord (les deux antennes sur les mêmes signaux), puis moyenne.
function delta(a, b, i, metric = state.metric) {
  const classes = state.cls === "all" ? CLASSES : [state.cls];
  let s = 0, n = 0;
  for (const c of classes) {
    const va = value(a, c, i, metric), vb = value(b, c, i, metric);
    if (va != null && vb != null) { s += va - vb; n++; }
  }
  return n ? s / n : null;
}
const better = (v) => (state.metric === "noise" ? -v : v);

/* ---------- Thème ---------- */
const T = () => ({ ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), line: css("--line"), axis: css("--axis"), surface: css("--surface"),
  night: css("--night"), twilight: css("--twilight"), calm: css("--calm"), margin: css("--margin"), neutral: css("--neutral-mark"),
  divNeg: css("--div-neg"), divMid: css("--div-mid"), divPos: css("--div-pos") });
const colorOf = (a) => css(`--s${a.color_slot || a.k + 1}`);
const symbolOf = (a) => SYMBOL[a.k % SYMBOL.length];
const inkOn = (a) => ((a.color_slot || a.k + 1) === 4 ? "#0b0b0b" : "#ffffff");
const dot = (color) => `<span style="display:inline-block;width:9px;height:9px;margin-right:6px;background:${color};border-radius:50%"></span>`;
const unitOf = () => ({ rssi: "dBFS", noise: "dBFS", snr: "dB" })[state.metric];
const metricWord = () => ({ rssi: "RSSI", noise: "bruit", snr: "SNR" })[state.metric];
function baseOpt(t) {
  return { animation: false, textStyle: { fontFamily: css("--font-body"), color: t.ink2, fontSize: 12 }, grid: { left: 52, right: 18, top: 18, bottom: 34 },
    tooltip: { backgroundColor: t.surface, borderColor: t.line, borderWidth: 1, padding: [8, 10], textStyle: { color: t.ink, fontSize: 12.5, fontFamily: css("--font-body") },
      extraCssText: "box-shadow: 0 4px 16px rgba(0,0,0,.12); border-radius: 6px;" } };
}
const axisCommon = (t) => ({ axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, axisLabel: { color: t.muted, fontSize: 11.5 },
  splitLine: { lineStyle: { color: t.line, width: 1 } }, nameTextStyle: { color: t.muted, fontSize: 11.5 } });
// Axe du temps : le jour à minuit, l'heure sinon ; les minutes pour une campagne courte.
const timeLabel = (v) => {
  const d = new Date(v);
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "numeric", minute: "2-digit", hourCycle: "h23" }).formatToParts(d);
  const h = +parts.find((x) => x.type === "hour").value % 24, m = parts.find((x) => x.type === "minute").value;
  if (NWIN * STEP < 2 * 864e5) return `${h} h ${m}`;
  return h === 0 && m === "00" ? fmtDay.format(d) : `${h} h`;
};
const windowText = () => (STEP >= 60000 ? `${Math.round(STEP / 60000)} min` : `${Math.round(STEP / 1000)} s`);

/* ---------- Bandes jour / nuit ---------- */
function nightAreasTime(t) {
  const areas = []; let cur = null;
  WINS.forEach((W) => {
    const c = W.cls === "jour" ? null : W.cls;
    if (c !== (cur && cur.c)) { if (cur) areas.push([{ xAxis: cur.start, itemStyle: { color: cur.c === "nuit" ? t.night : t.twilight } }, { xAxis: W.ts }]); cur = c ? { c, start: W.ts } : null; }
  });
  if (cur) areas.push([{ xAxis: cur.start, itemStyle: { color: cur.c === "nuit" ? t.night : t.twilight } }, { xAxis: START + NWIN * STEP }]);
  return areas;
}
const calmAreas = (t) => R.activity.filter((a) => a.state === "calm").map((a) => [{ xAxis: a.start_ms, itemStyle: { color: t.calm } }, { xAxis: a.end_ms }]);
function elevAtSolar(h) {
  const r = Math.PI / 180, lat = STATION.lat * r;
  return Math.asin(Math.sin(lat) * Math.sin(DEC_MID) + Math.cos(lat) * Math.cos(DEC_MID) * Math.cos((h - 12) * 15 * r)) / r;
}
function nightAreasSolar(t) {
  const areas = []; let cur = null;
  for (let h = 0; h <= 24.0001; h += 0.05) {
    const c = h > 24 ? "end" : solarClass(elevAtSolar(h)) === "jour" ? null : solarClass(elevAtSolar(h));
    if (c !== (cur && cur.c)) { if (cur) areas.push([{ xAxis: cur.start, itemStyle: { color: cur.c === "nuit" ? t.night : t.twilight } }, { xAxis: Math.min(24, h) }]); cur = c && c !== "end" ? { c, start: h } : null; }
  }
  if (cur) areas.push([{ xAxis: cur.start, itemStyle: { color: cur.c === "nuit" ? t.night : t.twilight } }, { xAxis: 24 }]);
  return areas;
}

/* ---------- Calculs ---------- */
function profile(a, b) {
  const bins = Array.from({ length: 24 }, () => []);
  WINS.forEach((W, i) => { const d = delta(a, b, i); if (d != null) bins[Math.floor(W.solar) % 24].push(d); });
  return bins.map((arr, h) => ({ h, x: h + 0.5, ...(stats(arr) || {}) }));
}
function byClass(a, b, metric = state.metric) {
  const out = {};
  for (const c of ["jour", "crep", "nuit"]) out[c] = stats(WINS.map((W, i) => (W.cls === c ? delta(a, b, i, metric) : null)));
  return out;
}
function crossings(prof) {
  const xs = [];
  for (let i = 0; i < 24; i++) {
    const p = prof[i], q = prof[(i + 1) % 24];
    if (p.med == null || q.med == null) continue;
    if ((p.med > 0) !== (q.med > 0)) { const x = p.x + (0 - p.med) / (q.med - p.med); xs.push(((x % 24) + 24) % 24); }
  }
  return xs;
}

/* ---------- Résumé ---------- */
const ICON = {
  check: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--good)"/><path d="M4.6 8.2l2.2 2.2 4.6-4.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  warn: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="var(--warning)"/><path d="M8 6v4" stroke="#0b0b0b" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="12" r="1" fill="#0b0b0b"/></svg>`,
  dash: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="var(--muted)" stroke-width="1.5"/><path d="M5 8h6" stroke="var(--muted)" stroke-width="1.8" stroke-linecap="round"/></svg>`,
};
function phrase(cmpA, refA, s, m, metric = state.metric) {
  const cmp = `${cmpA.short} (${cmpA.id})`, ref = `${refA.short} (${refA.id})`;
  if (!s) return "pas de mesure";
  if (Math.abs(s.med) <= Math.max(m, s.ci)) return `pas d'écart significatif entre ${cmp} et ${ref} (${s1(s.med)} dB, ${marginText()})`;
  const abs = f1(Math.abs(s.med));
  if (metric === "rssi") return `${cmp} reçoit ${abs} dB de ${s.med > 0 ? "plus" : "moins"} que ${ref}`;
  if (metric === "snr") return `${cmp} a un SNR ${s.med > 0 ? "meilleur" : "moins bon"} de ${abs} dB que ${ref}`;
  return `${cmp} capte ${abs} dB de bruit en ${s.med > 0 ? "plus" : "moins"} que ${ref}`;
}
const significant = (s, m) => s && Math.abs(s.med) > Math.max(m, s.ci);
function renderSummary() {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp], m = margin();
  const bc = byClass(cmp, ref), prof = profile(cmp, ref);
  const period = (label, s) => (s ? `${label}, ${phrase(cmp, ref, s, m)} (±${f1(s.ci)} dB).` : `${label}, pas encore de mesure.`);
  let main = `${period("De jour", bc.jour)} ${period("La nuit", bc.nuit)}`;
  const xs = crossings(prof);
  if (xs.length && significant(bc.jour, m) && significant(bc.nuit, m) && (bc.jour.med > 0) !== (bc.nuit.med > 0)) {
    main += ` L'écart change de signe vers ${xs.map((x) => hm(solarToLocal(x))).join(" et ")}, heure locale.`;
  }
  $("verdict-main").textContent = main;
  let second;
  if (state.metric === "rssi") {
    const sn = byClass(cmp, ref, "snr"), nz = byClass(cmp, ref, "noise");
    if (significant(bc.nuit, m) && bc.nuit.med > 0 && !significant(sn.nuit, m)) second = `Attention : en SNR, l'avantage de nuit disparaît. ${cap(cmp.short)} (${cmp.id}) capte aussi ${f1(nz.nuit.med)} dB de bruit en plus ; le bruit extérieur domine sur les deux antennes.`;
    else if (significant(sn.nuit, m) || significant(sn.jour, m)) second = `En SNR : de jour ${s1(sn.jour?.med)} dB, de nuit ${s1(sn.nuit?.med)} dB (${cmp.id} − ${ref.id}).`;
    else second = "En SNR, aucun écart significatif.";
  } else if (state.metric === "snr") second = "Le SNR ne dépend pas des pertes de la chaîne quand le bruit extérieur domine : c'est la grandeur qui départage les antennes en HF.";
  else second = "Une antenne plus bruyante n'est pas forcément moins bonne : comparer le SNR.";
  $("verdict-second").textContent = second;

  const deltas = WINS.map((W, i) => delta(cmp, ref, i)).filter((v) => v != null);
  const pct = deltas.length ? 100 * deltas.filter((v) => better(v) > 0).length / deltas.length : 0;
  const calmPct = 100 * WINS.filter((W) => W.calm).length / NWIN;
  const chip = (s) => (significant(s, m) ? `<span class="sig yes">${ICON.check} significatif</span>` : `<span class="sig no">${ICON.dash} non significatif</span>`);
  const word = state.metric === "noise" ? "moins bruyante" : "meilleure";
  $("tiles").innerHTML = `
    <div class="tile"><div class="label">Écart de jour</div><div class="value">${s1(bc.jour?.med)} <small>dB</small></div><div class="sub">${cmp.id} − ${ref.id} · IC 95 % ±${f1(bc.jour?.ci)}</div>${chip(bc.jour)}</div>
    <div class="tile"><div class="label">Écart de nuit</div><div class="value">${s1(bc.nuit?.med)} <small>dB</small></div><div class="sub">${cmp.id} − ${ref.id} · IC 95 % ±${f1(bc.nuit?.ci)}</div>${chip(bc.nuit)}</div>
    <div class="tile"><div class="label">${cmp.id} ${word}</div><div class="value">${NF0.format(pct)} <small>% du temps</small></div><div class="sub">Sur ${NF0.format(deltas.length)} fenêtres de ${windowText()}</div></div>
    <div class="tile"><div class="label">Bande calme</div><div class="value">${f1(calmPct)} <small>%</small></div><div class="sub">Bruit seul mesuré pendant ces périodes</div></div>`;
}

/* ---------- Graphiques ---------- */
const charts = {};
const chart = (id) => charts[id] || (charts[id] = echarts.init($(id), null, { renderer: "canvas" }));

function renderProfile(t) {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp], m = margin();
  const others = ANTS.filter((a, k) => k !== state.ref);
  const series = [{ type: "line", data: [], silent: true, markArea: { silent: true, data: nightAreasSolar(t) } }];
  others.forEach((a) => {
    const p = profile(a, ref), focus = a === cmp, col = colorOf(a), full = focus || ANTS.length === 2;
    if (focus) {
      series.push({ type: "custom", name: "_band", silent: true, z: 2, tooltip: { show: false }, data: [[12, 0]], encode: { x: 0, y: 1 },
        renderItem: (params, api) => {
          const pts = [];
          p.forEach((d) => { if (d.p90 != null) pts.push(api.coord([d.x, d.p90])); });
          for (let j = p.length - 1; j >= 0; j--) if (p[j].p10 != null) pts.push(api.coord([p[j].x, p[j].p10]));
          return { type: "polygon", shape: { points: pts }, style: { fill: hexA(col, 0.16) } };
        } });
    }
    series.push({
      type: "line", name: `${a.id} · ${a.short}`, data: p.map((d) => [d.x, d.med, d.p10, d.p90, d.n]), symbol: symbolOf(a), symbolSize: focus ? 7 : 6,
      lineStyle: { width: 2, color: col, opacity: full ? 1 : 0.55 }, itemStyle: { color: col, borderColor: t.surface, borderWidth: 1.5, opacity: full ? 1 : 0.55 },
      emphasis: { focus: "series", lineStyle: { width: 2.5, opacity: 1 }, itemStyle: { opacity: 1 } }, z: focus ? 5 : 3,
      markArea: focus ? { silent: true, itemStyle: { color: t.margin }, data: [[{ yAxis: -m }, { yAxis: m }]] } : undefined,
      markLine: focus ? { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: t.axis, type: "solid", width: 1 }, data: [{ yAxis: 0 }] } : undefined,
    });
  });
  const o = baseOpt(t);
  o.grid = { left: 50, right: 18, top: 20, bottom: 42 };
  o.xAxis = { type: "value", min: 0, max: 24, interval: 3, name: "heure solaire", nameLocation: "middle", nameGap: 26, ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => `${v} h` }, splitLine: { show: false } };
  o.yAxis = { type: "value", ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => s1(v) }, name: "dB", nameGap: 10 };
  o.tooltip = { ...o.tooltip, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.axis } },
    formatter: (ps) => {
      const rows = ps.filter((p) => p.seriesType === "line" && p.value && p.value[1] != null);
      if (!rows.length) return "";
      const h = Math.floor(rows[0].value[0]);
      let s = `<b>${h} h – ${h + 1} h solaire</b><br><span style="color:${t.muted}">≈ ${hm(solarToLocal(h))} – ${hm(solarToLocal(h + 1))} locale</span><br>`;
      rows.forEach((p) => { s += `${dot(p.color)}${p.seriesName.split(" · ")[0]} − ${ref.id} : <b>${s1(p.value[1])} dB</b> <span style="color:${t.muted}">(p10 ${s1(p.value[2])}, p90 ${s1(p.value[3])})</span><br>`; });
      return s;
    } };
  o.series = series;
  chart("c-profile").setOption(o, true);
  $("key-profile").innerHTML = others.map((a) => `<span>${dot(colorOf(a))}${a.id} − ${ref.id}${ANTS.length > 2 ? "" : ` (${a.short} − ${ref.short})`}</span>`).join("")
    + `<span><i class="sw" style="background:${t.night}"></i>Nuit</span><span><i class="sw" style="background:${t.twilight}"></i>Crépuscule</span>`
    + (marginKnown() ? `<span><i class="sw" style="background:${t.margin}"></i>Marge de qualification ±${f1(m)} dB</span>` : `<span>Marge de qualification inconnue</span>`)
    + `<span><i class="sw" style="background:${hexA(colorOf(cmp), 0.16)}"></i>p10–p90 (${cmp.id})</span>`;
  $("t-profile").textContent = `Écart de ${metricWord()} sur 24 heures`;
  $("d-profile").textContent = ANTS.length > 2
    ? `Chaque antenne moins la référence ${ref.id} (${ref.short}), ${CLASS_NAME[state.cls]}. Survolez une courbe pour la faire ressortir ; ${cmp.id} est mise en avant avec sa dispersion.`
    : `${cmp.id} (${cmp.short}) moins ${ref.id} (${ref.short}), ${CLASS_NAME[state.cls]}. Midi solaire ≈ ${hm(solarToLocal(12))} à l'heure locale.`;
  const p = profile(cmp, ref);
  $("profile-table").innerHTML = `<table><thead><tr><th>Heure solaire</th><th>Heure locale</th><th class="num">Médiane ${cmp.id} − ${ref.id}</th><th class="num">p10</th><th class="num">p90</th><th class="num">Fenêtres</th></tr></thead><tbody>${
    p.map((d) => `<tr><td>${d.h} h – ${d.h + 1} h</td><td>${hm(solarToLocal(d.h))}</td><td class="num">${s1(d.med)}</td><td class="num">${s1(d.p10)}</td><td class="num">${s1(d.p90)}</td><td class="num">${d.n ?? 0}</td></tr>`).join("")}</tbody></table>`;
}

function renderRank(t) {
  $("card-rank").hidden = ANTS.length <= 2;
  if (ANTS.length <= 2) return;
  const ref = ANTS[state.ref];
  const profs = ANTS.map((a) => (a === ref ? null : profile(a, ref)));
  const data = [];
  for (let h = 0; h < 24; h++) {
    let best = ref, bv = 0;
    ANTS.forEach((a, k) => { if (a === ref) return; const v = profs[k][h].med; if (v != null && better(v) > better(bv)) { best = a; bv = v; } });
    data.push({ value: 1, best, bv, itemStyle: { color: colorOf(best), borderRadius: 3 }, label: { color: inkOn(best) } });
  }
  const o = baseOpt(t);
  o.grid = { left: 8, right: 8, top: 6, bottom: 26 };
  o.xAxis = { type: "category", data: Array.from({ length: 24 }, (_, h) => h), ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.muted, interval: 2, formatter: (v) => `${v} h` }, splitLine: { show: false } };
  o.yAxis = { type: "value", show: false, max: 1 };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => { const d = data[p.dataIndex]; return `<b>${p.dataIndex} h solaire</b><br>${dot(colorOf(d.best))}${d.best.id} · ${d.best.short}${d.best === ref ? " (référence)" : ` : ${s1(d.bv)} dB par rapport à ${ref.id}`}`; } };
  o.series = [{ type: "bar", data, barCategoryGap: "6%", label: { show: true, position: "inside", formatter: (p) => data[p.dataIndex].best.id, fontFamily: css("--font-cond"), fontWeight: 600, fontSize: 13 } }];
  chart("c-rank").setOption(o, true);
  $("d-rank").textContent = `Antenne au ${state.metric === "noise" ? "bruit le plus faible" : `${metricWord()} médian le plus élevé`} pour chaque heure solaire, ${CLASS_NAME[state.cls]}.`;
}

function renderBox(t) {
  const ref = ANTS[state.ref], others = ANTS.filter((a, k) => k !== state.ref);
  const o = baseOpt(t);
  o.grid = { left: 50, right: 14, top: 18, bottom: 30 };
  o.xAxis = { type: "category", data: ["Jour", "Crépuscule", "Nuit"], ...axisCommon(t), axisLabel: { color: t.ink2, fontSize: 12 }, splitLine: { show: false } };
  o.yAxis = { type: "value", ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => s1(v) } };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => { const [, lo, q1, med, q3, hi] = p.value; return `<b>${p.seriesName} − ${ref.id}, ${p.name.toLowerCase()}</b><br>médiane ${s1(med)} dB<br>quartiles ${s1(q1)} à ${s1(q3)} dB<br><span style="color:${t.muted}">p5 ${s1(lo)} · p95 ${s1(hi)}</span>`; } };
  o.series = others.map((a, n) => {
    const bc = byClass(a, ref), col = colorOf(a);
    return { type: "boxplot", name: a.id, boxWidth: [6, others.length > 2 ? 14 : 30], itemStyle: { color: hexA(col, 0.14), borderColor: col, borderWidth: 1.6 },
      data: ["jour", "crep", "nuit"].map((c) => (bc[c] ? [bc[c].p05, bc[c].q1, bc[c].med, bc[c].q3, bc[c].p95] : [])),
      markLine: n === 0 ? { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: t.axis, type: "solid", width: 1 }, data: [{ yAxis: 0 }] } : undefined };
  });
  chart("c-box").setOption(o, true);
  $("d-box").textContent = `${others.length > 1 ? "Chaque antenne" : others[0].id} moins ${ref.id}. Boîte : quartiles ; moustaches : p5 à p95.`;
}

const divColors = (t) => [t.divNeg, hexA(t.divNeg, 0.55), t.divMid, hexA(t.divPos, 0.55), t.divPos];

function renderHeat(t) {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp];
  const days = [...new Set(WINS.map((W) => W.day))];
  const cells = {};
  WINS.forEach((W, i) => { const k = `${W.day}|${W.lh}`; (cells[k] = cells[k] || []).push(delta(cmp, ref, i)); });
  const data = [];
  days.forEach((d, y) => { for (let h = 0; h < 24; h++) { const s = stats(cells[`${d}|${h}`] || []); data.push([h, y, s ? +s.med.toFixed(2) : "-"]); } });
  const labels = days.map((d) => fmtDay.format(new Date(`${d}T12:00:00Z`)));
  const inv = state.metric === "noise", lim = inv ? 4 : 6;
  const o = baseOpt(t);
  o.grid = { left: 84, right: 12, top: 8, bottom: 62 };
  o.xAxis = { type: "category", data: Array.from({ length: 24 }, (_, h) => h), ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.muted, interval: 2, formatter: (v) => `${v} h` }, splitLine: { show: false } };
  o.yAxis = { type: "category", data: labels, inverse: true, ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.ink2 }, splitLine: { show: false } };
  o.visualMap = { min: -lim, max: lim, calculable: false, orient: "horizontal", left: "center", bottom: 4, itemWidth: 12, itemHeight: 160,
    text: inv ? [`${ref.id} plus bruyante`, `${cmp.id} plus bruyante`] : [`${cmp.id} meilleure`, `${ref.id} meilleure`],
    textStyle: { color: t.ink2, fontSize: 11.5 }, inRange: { color: inv ? divColors(t).reverse() : divColors(t) }, outOfRange: { color: t.line } };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => `<b>${labels[p.value[1]]}, ${p.value[0]} h – ${p.value[0] + 1} h</b><br>${cmp.id} − ${ref.id} : <b>${p.value[2] === "-" ? "pas de mesure" : `${s1(p.value[2])} dB`}</b>` };
  o.series = [{ type: "heatmap", data, itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 2 }, emphasis: { itemStyle: { borderColor: t.ink, borderWidth: 1 } } }];
  chart("c-heat").setOption(o, true);
  $("d-heat").textContent = `${cmp.id} − ${ref.id} par heure locale. La régularité d'un jour à l'autre montre un effet de propagation, pas un hasard.`;
}

function renderPairs(t) {
  $("sec-pairs").hidden = ANTS.length <= 2;
  if (ANTS.length <= 2) return;
  const period = state.pairsPeriod, m = margin(), npairs = ANTS.length * (ANTS.length - 1) / 2;
  const data = [], raw = [];
  ANTS.forEach((r, y) => ANTS.forEach((c, x) => {
    if (x === y) { data.push({ value: [x, y, "-"], itemStyle: { color: t.line } }); return; }
    raw.push({ x, y, s: stats(WINS.map((W, i) => (W.cls === period ? delta(r, c, i) : null))) });
  }));
  raw.sort((p, q) => Math.abs(q.s.med) / q.s.ci - Math.abs(p.s.med) / p.s.ci);
  raw.forEach((p, rank) => {
    const holm = Math.sqrt(1 + Math.log(npairs - Math.floor(rank / 2)));
    const sig = Math.abs(p.s.med) > Math.max(m, p.s.ci * holm);
    data.push({ value: [p.x, p.y, +p.s.med.toFixed(2)], sig, itemStyle: sig ? undefined : { color: t.divMid }, label: { color: t.ink } });
  });
  const inv = state.metric === "noise", lim = inv ? 8 : 10;
  const o = baseOpt(t);
  o.grid = { left: 120, right: 14, top: 8, bottom: 64 };
  o.xAxis = { type: "category", data: ANTS.map((a) => a.id), ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.ink2, fontWeight: 600 }, splitLine: { show: false } };
  o.yAxis = { type: "category", data: ANTS.map((a) => `${a.id} · ${a.short}`), inverse: true, ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.ink2 }, splitLine: { show: false } };
  o.visualMap = { min: -lim, max: lim, calculable: false, orient: "horizontal", left: "center", bottom: 4, itemWidth: 12, itemHeight: 160,
    text: inv ? ["colonne plus bruyante", "ligne plus bruyante"] : ["ligne meilleure", "colonne meilleure"], textStyle: { color: t.ink2, fontSize: 11.5 },
    inRange: { color: inv ? divColors(t).reverse() : divColors(t) }, seriesIndex: 0 };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => { const [x, y, v] = p.value; if (v === "-") return ""; return `<b>${ANTS[y].id} − ${ANTS[x].id}, ${CLASS_LABEL[period].toLowerCase()}</b><br>${s1(v)} dB<br><span style="color:${t.muted}">${p.data.sig ? "significatif après correction de Holm" : "non significatif"}</span>`; } };
  o.series = [{ type: "heatmap", data, label: { show: true, formatter: (p) => (p.value[2] === "-" ? "" : s1(p.value[2])), fontFamily: css("--font-mono"), fontSize: 12 }, itemStyle: { borderColor: t.surface, borderWidth: 3, borderRadius: 3 } }];
  chart("c-pairs").setOption(o, true);
  $("d-pairs").textContent = `${cap(metricWord())}, ${CLASS_NAME[state.cls]}, ${CLASS_LABEL[period].toLowerCase()}. Lire : la ligne reçoit X dB de plus que la colonne.`;
}

function segmentLines(t) {
  return R.segments.slice(1).map((s) => ({ xAxis: s.start_ms, lineStyle: { color: t.ink2, type: "dashed", width: 1 },
    label: { show: true, formatter: `Segment ${s.idx + 1}`, position: "insideEndTop", color: t.ink2, fontSize: 11.5 } }));
}

function renderDeltaTS(t) {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp], m = margin(), col = colorOf(cmp);
  const med = WINS.map((W, i) => [W.ts, delta(cmp, ref, i)]);
  const o = baseOpt(t);
  o.grid = { left: 50, right: 18, top: 24, bottom: 34 };
  o.xAxis = { type: "time", min: START, max: START + NWIN * STEP, ...axisCommon(t), splitLine: { show: false }, axisLabel: { color: t.muted, hideOverlap: true, formatter: timeLabel } };
  o.yAxis = { type: "value", ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => s1(v) }, name: "dB", nameGap: 10 };
  o.tooltip = { ...o.tooltip, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.axis } }, formatter: (ps) => {
    const p = ps.find((x) => x.seriesName === "med"); if (!p) return "";
    const W = WINS[p.dataIndex];
    return `<b>${fmtDayHour.format(W.ts)}</b> · ${CLASS_LABEL[W.cls].toLowerCase()}${W.calm ? " · bande calme" : ""}<br>${dot(col)}${cmp.id} − ${ref.id} : <b>${p.value[1] == null ? "—" : `${s1(p.value[1])} dB`}</b><br><span style="color:${t.muted}">segment ${W.seg + 1}</span>`; } };
  o.series = [
    { type: "line", data: [], silent: true, markArea: { silent: true, data: [...nightAreasTime(t), ...calmAreas(t)] } },
    { type: "line", name: "med", data: med, symbol: "none", lineStyle: { width: 1.6, color: col }, itemStyle: { color: col },
      markArea: { silent: true, itemStyle: { color: t.margin }, data: [[{ yAxis: -m }, { yAxis: m }]] },
      markLine: { silent: true, symbol: "none", data: [{ yAxis: 0, lineStyle: { color: t.axis, type: "solid", width: 1 }, label: { show: false } }, ...segmentLines(t)] } },
  ];
  chart("c-dts").setOption(o, true);
  $("t-dts").textContent = `Écart de ${metricWord()} mesuré`;
  const segNote = R.segments.slice(1).map((s) => s.reason).filter(Boolean).join(" ; ");
  $("d-dts").textContent = `${cmp.id} − ${ref.id} par fenêtre de ${windowText()}${marginKnown() ? ", avec la marge de qualification" : ""}.${segNote && state.view === "installation" && state.metric !== "snr" ? ` ${segNote} : en vue Installation la courbe se décale, la vue Antenne le compense.` : ""}`;
}

function renderLevels(t) {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp];
  // Une moyenne par heure sur une longue campagne, moins sur une courte (au moins ~60 points).
  const per = Math.max(1, Math.min(Math.round(3_600_000 / STEP), Math.floor(NWIN / 60)));
  const perText = per * STEP >= 3_600_000 ? "Moyenne horaire" : per === 1 ? `Médiane par fenêtre de ${windowText()}` : `Moyenne par ${Math.round(per * STEP / 60000)} min`;
  const series = [{ type: "line", data: [], silent: true, markArea: { silent: true, data: [...nightAreasTime(t), ...calmAreas(t)] } }];
  const sel = {};
  ANTS.forEach((a) => {
    const pts = [];
    for (let i = 0; i < NWIN; i += per) {
      const vs = []; for (let j = 0; j < per && i + j < NWIN; j++) { const v = value(a, state.cls, i + j); if (v != null) vs.push(v); }
      pts.push([WINS[i].ts + (per * STEP) / 2, vs.length ? vs.reduce((s, v) => s + v, 0) / vs.length : null]);
    }
    const name = `${a.id} · ${a.short}`;
    sel[name] = ANTS.length <= 2 || a === ref || a === cmp;
    series.push({ type: "line", name, data: pts, symbol: symbolOf(a), symbolSize: 5, showSymbol: false, lineStyle: { width: 1.8, color: colorOf(a) }, itemStyle: { color: colorOf(a) }, emphasis: { focus: "series" } });
  });
  const o = baseOpt(t);
  o.grid = { left: 56, right: 18, top: 40, bottom: 34 };
  o.legend = { top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: t.ink2, fontSize: 12 }, selected: sel, inactiveColor: t.axis, data: ANTS.map((a) => `${a.id} · ${a.short}`) };
  o.xAxis = { type: "time", min: START, max: START + NWIN * STEP, ...axisCommon(t), splitLine: { show: false }, axisLabel: { color: t.muted, hideOverlap: true, formatter: timeLabel } };
  o.yAxis = { type: "value", scale: true, ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => f1(v).replace(",0", "") }, name: unitOf(), nameGap: 10 };
  o.tooltip = { ...o.tooltip, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.axis } }, formatter: (ps) => {
    const rows = ps.filter((p) => p.seriesIndex > 0); if (!rows.length) return "";
    let s = `<b>${fmtDayHour.format(rows[0].value[0])}</b> (${perText.toLowerCase()})<br>`;
    rows.forEach((p) => { s += `${dot(p.color)}${p.seriesName} : <b>${p.value[1] == null ? "bande calme" : `${f1(p.value[1])} ${unitOf()}`}</b><br>`; });
    return s; } };
  o.series = series;
  chart("c-lvl").setOption(o, true);
  $("t-lvl").textContent = state.metric === "noise" ? "Plancher de bruit" : state.metric === "snr" ? "SNR" : "Niveaux reçus";
  $("d-lvl").textContent = `${perText}, ${CLASS_NAME[state.cls]}, vue ${state.view === "antenne" ? "Antenne (au pied d'antenne)" : "Installation (au SDR)"}.${ANTS.length > 2 ? " Cliquez sur la légende pour afficher d'autres antennes." : ""}`;
}

function renderSignals(t) {
  const ref = ANTS[state.ref], cmp = ANTS[state.cmp], m = margin();
  const sigs = R.signals.filter((s) => state.cls === "all" || s.class === state.cls);
  const rows = sigs.map((s) => {
    const d = (cls) => stats(WINS.map((W, i) => {
      if (W.cls !== cls) return null;
      const a = rawValue(s.rssi[cmp.k][i], s.floor[cmp.k][i], cmp, i, state.metric, state.view);
      const b = rawValue(s.rssi[ref.k][i], s.floor[ref.k][i], ref, i, state.metric, state.view);
      return a == null || b == null ? null : a - b;
    }));
    const dj = d("jour"), dn = d("nuit");
    return { name: `${(s.freq_hz / 1e6).toFixed(4).replace(".", ",")} · ${CLASS_SHORT[s.class] || s.class}`, dj: dj?.med ?? null, dn: dn?.med ?? null };
  });
  const o = baseOpt(t);
  o.grid = { left: 124, right: 18, top: 28, bottom: 40 };
  o.legend = { top: 0, right: 0, textStyle: { color: t.ink2, fontSize: 12 }, data: ["Jour", "Nuit"], itemWidth: 10, itemHeight: 10 };
  o.yAxis = { type: "category", data: rows.map((r) => r.name), inverse: true, ...axisCommon(t), axisLine: { show: false }, axisLabel: { color: t.ink2, fontFamily: css("--font-mono"), fontSize: 11.5 }, splitLine: { show: false } };
  o.xAxis = { type: "value", ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => s1(v) }, name: `${cmp.id} − ${ref.id} (dB)`, nameLocation: "middle", nameGap: 24 };
  const tip = (r) => `<b>${r.name} MHz</b><br>Jour : ${s1(r.dj)} dB<br>Nuit : ${s1(r.dn)} dB`;
  o.tooltip = { ...o.tooltip, trigger: "item" };
  o.series = [
    { type: "custom", name: "liaison", silent: true, data: rows.map((r, i) => [i, r.dj, r.dn]), encode: { y: 0, x: [1, 2] },
      renderItem: (params, api) => { const a = api.coord([api.value(1), api.value(0)]), b = api.coord([api.value(2), api.value(0)]); return { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: t.axis, lineWidth: 2 } }; },
      markArea: { silent: true, itemStyle: { color: t.margin }, data: [[{ xAxis: -m }, { xAxis: m }]] },
      markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: t.axis, type: "solid", width: 1 }, data: [{ xAxis: 0 }] } },
    { type: "scatter", name: "Jour", data: rows.map((r, i) => [r.dj, i]), symbolSize: 11, itemStyle: { color: t.ink, borderColor: t.surface, borderWidth: 2 }, z: 4, tooltip: { formatter: (p) => tip(rows[p.value[1]]) } },
    { type: "scatter", name: "Nuit", data: rows.map((r, i) => [r.dn, i]), symbolSize: 11, itemStyle: { color: t.surface, borderColor: t.ink, borderWidth: 2 }, z: 4, tooltip: { formatter: (p) => tip(rows[p.value[1]]) } },
  ];
  chart("c-sig").setOption(o, true);
  $("t-sig").textContent = `Écart de ${metricWord()} par signal`;
}

function renderActivity(t) {
  const ref = ANTS[state.ref];
  const per = Array.from({ length: 24 }, () => []);
  WINS.forEach((W, i) => per[W.lh].push(R.signals.filter((s) => s.rssi[ref.k][i] != null).length));
  const data = per.map((a) => (a.length ? +(a.reduce((s, v) => s + v, 0) / a.length).toFixed(1) : 0));
  const o = baseOpt(t);
  o.grid = { left: 40, right: 12, top: 18, bottom: 30 };
  o.xAxis = { type: "category", data: Array.from({ length: 24 }, (_, h) => h), ...axisCommon(t), axisLabel: { color: t.muted, interval: 2, formatter: (v) => `${v} h` }, splitLine: { show: false } };
  o.yAxis = { type: "value", minInterval: 1, ...axisCommon(t), axisLabel: { color: t.muted } };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => `<b>${p.name} h – ${+p.name + 1} h locale</b><br>${NF1.format(p.value)} signaux mesurés en moyenne` };
  o.series = [{ type: "bar", data, barMaxWidth: 16, itemStyle: { color: t.neutral, borderRadius: [4, 4, 0, 0] } }];
  chart("c-act").setOption(o, true);
}

/* ---------- Blocs fixes ---------- */
const STATE_WORD = { done: "Campagne terminée", running: "Campagne en cours", interrupted: "Campagne interrompue", abandoned: "Campagne abandonnée", preparation: "Campagne en préparation" };
const MODE_WORD = { simultaneous: "mode simultané", switched: "mode commuté" };
function renderStatic() {
  const c = R.campaign;
  $("demo-banner").hidden = !c.demo;
  document.title = `${c.title} · openAntennaValidation`;
  $("head-eyebrow").textContent = `${STATE_WORD[c.state] || c.state} · ${MODE_WORD[c.reception_mode] || c.reception_mode} · ${c.measure_mode === "auto" ? "bande automatique" : "fréquences imposées"}`;
  $("head-title").innerHTML = `${c.title} <span class="freq">${(c.low_hz / 1e6).toFixed(3).replace(".", ",")}–${(c.high_hz / 1e6).toFixed(3).replace(".", ",")} MHz</span>`;
  const endMs = c.end_ms || c.planned_end_ms;
  const days = Math.round((endMs - c.start_ms) / 864e5 * 10) / 10;
  const m = c.margins || {};
  $("head-meta").innerHTML = `<span>Opérateur <b>${c.operator}</b>${c.locator ? ` · ${c.locator}` : ""}</span>
    <span><b>${fmtDay.format(c.start_ms)} → ${fmtDay.format(endMs)}</b>${c.end_ms ? "" : " (prévu)"} · ${days >= 1 ? `${NF1.format(days).replace(",0", "")} jours` : `${Math.round((endMs - c.start_ms) / 60000)} min`}</span>
    <span>${c.sdr}</span><span>${c.band} · ${R.signals.length} signaux</span>
    ${c.aa ? `<span class="qual-chip">Campagne A/A</span>` : ""}
    ${c.qualification ? `<span class="qual-chip">${ICON.check} Qualifiée${marginKnown() ? `, marge ±${f1(m.all)} dB` : ""}</span>` : `<span class="qual-chip">${ICON.dash} Non qualifiée</span>`}`;
  $("time-sub").textContent = `Heure locale de la station (${TZ}). Nuits ombrées, bande calme en gris.`;
  $("inst-sub").textContent = `Pertes de la chaîne sur ${c.band}. La vue « Antenne » les ajoute aux niveaux mesurés.`;
  const chainText = (ch) => `${ch.elements.map((e) => `${e.label} (${f1(e.db)} dB)`).join(" + ")} · <b>${f1(ch.loss_db)} dB</b>`;
  $("ants").innerHTML = ANTS.map((a, k) => {
    const first = R.segments[0]?.chains[k];
    const changes = R.segments.slice(1).filter((s) => s.chains[k] && first && s.chains[k].loss_db !== first.loss_db);
    return `<article class="ant">
      <div class="ant-top"><span class="ant-badge" style="background:${colorOf(a)};color:${inkOn(a)}">${a.id}</span><span class="ant-name">${a.name}</span>${k === state.ref ? `<span class="ant-ref">Référence</span>` : ""}</div>
      <dl><dt>Type</dt><dd>${a.type}</dd><dt>Hauteur</dt><dd>${a.height || "—"}</dd><dt>Polarisation</dt><dd>${a.polarization || "—"}</dd><dt>Orientation</dt><dd>${a.orientation || "—"}</dd></dl>
      ${first ? `<div class="chain">Chaîne : ${chainText(first)}${changes.map((s) => `<br>Segment ${s.idx + 1} : ${chainText(s.chains[k])}`).join("")}</div>` : ""}
    </article>`;
  }).join("");
  $("coupling-note").textContent = `${c.spacing_note || ""} Des antennes proches s'influencent : les résultats décrivent chaque antenne dans cette installation, pas l'antenne seule.`.trim();
  const q = c.qualification;
  if (q && q.kind === "bench") {
    const fmtDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
    $("d-qual").textContent = `Banc de commutation${q.date_ms ? ` du ${fmtDate.format(q.date_ms)}` : ""} : neutralisation ${f1(q.blanking_ms)} ms après chaque bascule, temps utile ${Math.round((q.useful || 0) * 100)} % à la cadence de la campagne.${marginKnown() ? " Marges mesurées par une campagne A/A." : " Marges en dB inconnues tant qu'aucune campagne A/A n'a été faite."}`;
    const icon = { ok: ICON.check, warn: ICON.warn, bad: ICON.warn };
    $("qual-list").innerHTML = (q.verdicts || []).map((v) => {
      const mg = (c.margins || {})[v.class];
      return `<li>${icon[v.state] || ICON.dash}<span>${CLASS_TITLE[v.class] || v.class}${v.useful != null ? ` : temps utile ${Math.round(v.useful * 100)} %` : ""}</span><span class="m">${mg != null ? `±${f1(mg)} dB` : "—"}</span></li>`;
    }).join("");
  } else if (q && q.kind === "aa") {
    // Mode simultané : l'étalonnage de l'ensemble de SDR par une campagne A/A.
    const fmtDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
    const al = q.alignment_ms, lt = c.load_test;
    $("d-qual").textContent = `Étalonnage A/A${q.date_ms ? ` du ${fmtDate.format(q.date_ms)}` : ""} : une antenne sur tous les SDR via un splitter ; l'écart de gain de chaque SDR est corrigé, fréquence par fréquence.${al ? ` Écart temporel entre SDR : ${f1(al.p50)} ms en médiane, ${f1(al.p99)} ms au 99e centile.` : ""}${lt && lt.station_load != null ? ` Test de charge avant lancement : ${Math.round(lt.station_load * 100)} % de la machine.` : ""}`;
    const icon = { ok: ICON.check, warn: ICON.warn, bad: ICON.warn };
    $("qual-list").innerHTML = (q.verdicts || []).map((v) => {
      const mg = (c.margins || {})[v.class];
      return `<li>${icon[v.state] || ICON.dash}<span>${CLASS_TITLE[v.class] || v.class}${v.state === "warn" ? " : SDR mal alignés pour la voix" : ""}</span><span class="m">${mg != null ? `±${f1(mg)} dB` : "—"}</span></li>`;
    }).join("");
  } else if (q) {
    $("d-qual").textContent = `Campagne A/A du ${q.date}, ${q.duration}, une antenne sur toutes les voies via un splitter. Biais résiduel ${q.bias}, alignement temporel ${NF1.format(q.alignment_ms)} ms.`;
    $("qual-list").innerHTML = `
      <li>${ICON.check}<span>Porteuses et CW</span><span class="m">±${f1(m.carrier)} dB</span></li>
      <li>${ICON.check}<span>Signaux numériques</span><span class="m">±${f1(m.digital)} dB</span></li>
      <li>${ICON.warn}<span>SSB : désalignement de ${NF1.format(q.alignment_ms)} ms entre voies, marge élargie</span><span class="m">±${f1(m.ssb)} dB</span></li>
      <li>${q.load_cpu_pct > 70 ? ICON.warn : ICON.check}<span>Test de charge : ${q.load_cpu_pct} % du processeur, aucune perte d'échantillons</span><span class="m">${q.load_cpu_pct > 70 ? "limite" : "OK"}</span></li>`;
  } else {
    $("d-qual").textContent = "Campagne lancée sans qualification : aucun écart n'est affirmé sans marge connue.";
    $("qual-list").innerHTML = "";
  }
}

function fillSelects() {
  const opts = (skip, sel) => ANTS.map((a, k) => (k === skip ? "" : `<option value="${k}"${k === sel ? " selected" : ""}>${a.id} · ${a.short}</option>`)).join("");
  $("sel-ref").innerHTML = opts(-1, state.ref);
  $("sel-cmp").innerHTML = opts(state.ref, state.cmp);
}
function syncButtons() {
  document.querySelectorAll(".seg[data-key]").forEach((g) => g.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(String(state[g.dataset.key]) === b.dataset.v))));
  $("view-hint").textContent = state.metric === "snr"
    ? "Le SNR est le même dans les deux vues : les pertes de la chaîne baissent autant le signal que le bruit extérieur."
    : state.view === "antenne" ? "Vue Antenne : niveaux ramenés au pied d'antenne, pertes de la chaîne ajoutées." : "Vue Installation : niveaux tels que reçus par le SDR.";
}

function renderAll() {
  if (!R) return;
  const t = T();
  syncButtons(); fillSelects(); renderStatic(); renderSummary();
  renderProfile(t); renderRank(t); renderBox(t); renderHeat(t); renderPairs(t); renderDeltaTS(t); renderLevels(t); renderSignals(t); renderActivity(t);
}

/* ---------- Événements ---------- */
document.querySelectorAll(".seg[data-key]").forEach((g) => g.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.disabled) return;
  state[g.dataset.key] = b.dataset.v; renderAll();
}));
$("sel-ref").addEventListener("change", (e) => { state.ref = +e.target.value; if (state.cmp === state.ref) state.cmp = state.ref === 0 ? 1 : 0; renderAll(); });
$("sel-cmp").addEventListener("change", (e) => { state.cmp = +e.target.value; renderAll(); });
$("btn-table").addEventListener("click", (e) => {
  const tb = $("profile-table"); tb.hidden = !tb.hidden;
  e.target.setAttribute("aria-expanded", String(!tb.hidden)); e.target.textContent = tb.hidden ? "Afficher le tableau" : "Masquer le tableau";
});
const ro = new ResizeObserver(() => Object.values(charts).forEach((c) => c.resize()));
document.querySelectorAll(".chart").forEach((el) => ro.observe(el));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
new MutationObserver(renderAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

async function start() {
  const uuid = new URLSearchParams(location.search).get("id");
  if (!uuid) { location.replace("/campaigns"); return; }
  const res = await fetch(`/api/campaigns/${encodeURIComponent(uuid)}/results`);
  if (res.status === 401) { location.replace("/login"); return; }
  if (!res.ok) { const e = $("error"); e.hidden = false; e.textContent = res.status === 404 ? "Campagne introuvable." : `Erreur ${res.status}.`; return; }
  prepare(await res.json());
  renderAll();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(renderAll);
}
start();
