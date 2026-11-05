"use strict";

/* Résultats des modes numériques (FT8, FT4, WSPR) d'une campagne : graphiques 10 à 14 du
   cahier des charges. S'appuie sur results.js (R, ANTS, state, T, chart, baseOpt,
   axisCommon, colorOf, symbolOf, dot, f1, s1, NF0, NF1). Les écarts sont calculés par la
   station sur les pistes validées : antenne − référence de la campagne. */

const DEG = Math.PI / 180;
const EARTH_KM = 6371;
const ZONES = [
  { key: "nvis", label: "NVIS", from: 0, to: 400 },
  { key: "skip", label: "Silence", from: 400, to: 1000 },
  { key: "dx1", label: "1 bond", from: 1000, to: 3800 },
  { key: "dxn", label: "Plusieurs bonds", from: 3800, to: 20100 },
];
const escD = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function distAz(lat1, lon1, lat2, lon2) {
  const p1 = lat1 * DEG, p2 = lat2 * DEG, dl = (lon2 - lon1) * DEG;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  const d = 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(a)));
  const az = (Math.atan2(Math.sin(dl) * Math.cos(p2), Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)) / DEG + 360) % 360;
  return [d, az];
}
const project = (d, az) => [d * Math.sin(az * DEG), d * Math.cos(az * DEG)];

function gapOf(st, k) { const g = st.gap[String(k)] || st.gap[k]; return g && g.median != null ? g : null; }

function renderDigital(t) {
  const D = R.digital, sec = $("sec-digital");
  sec.hidden = !D;
  if (!D) return;
  const ref = ANTS[D.reference], cmpK = state.cmp === D.reference ? ANTS.findIndex((a) => a.k !== D.reference) : state.cmp;
  const cmp = ANTS[cmpK];
  const label = { ft8: "FT8", ft4: "FT4", wspr: "WSPR" }[D.mode] || D.mode;
  $("dig-sub").textContent = `${label} décodé par WSJT-X sur ${NF1.format(D.dial_hz / 1000)} kHz. Pistes validées seulement ; écart ${cmp.id} − ${ref.id} ${D.method === "pivot" ? "par la méthode du pivot (chaque mesure sur une antenne comparée à la référence interpolée entre ses mesures voisines)" : "sur les mêmes transmissions, au même instant"}${state.ref !== D.reference ? ` (référence de la campagne : ${ref.id})` : ""}.`;
  renderDigitalTiles(D, ref, cmp, cmpK, label);
  const st = D.stations.filter((s) => gapOf(s, cmpK));
  renderDigitalMap(t, D, st, cmpK);
  renderDigitalDistance(t, st, cmp, cmpK, ref);
  renderDigitalAngle(t, st, cmp, cmpK, ref);
  renderDigitalPolar(t, st, cmp, cmpK, ref);
  renderDigitalSets(t, D);
  renderDigitalTable(D, cmpK, ref, cmp);
}

function renderDigitalTiles(D, ref, cmp, cmpK, label) {
  const states = D.counts.states || {}, reasons = D.counts.reasons || {};
  const rej = Object.entries(reasons).map(([k, n]) => `${escD(D.counts.reason_labels[k] || k)} : ${n}`).join(" · ");
  const o = D.overall[String(cmpK)] || {};
  $("dig-tiles").innerHTML = `
    <div class="tile"><span class="label">Stations validées</span><span class="value">${NF0.format(D.stations.length)}</span><span class="sub">${NF0.format(states.validated || 0)} piste${(states.validated || 0) > 1 ? "s" : ""} ${label}</span></div>
    <div class="tile"><span class="label">Pistes rejetées</span><span class="value">${NF0.format(states.rejected || 0)}</span><span class="sub">${rej || "aucune"}</span></div>
    <div class="tile"><span class="label">${dot(colorOf(cmp))}Écart ${cmp.id} − ${ref.id}</span><span class="value">${o.median != null ? `${s1(o.median)} <small>dB</small>` : "—"}</span><span class="sub">${o.n ? `${NF0.format(o.n)} mesure${o.n > 1 ? "s" : ""}${o.ci95 != null ? `, IC 95 % ±${f1(o.ci95)} dB` : ""}` : "pas encore de mesure appariée"}</span></div>
    <div class="tile"><span class="label">Méthode</span><span class="value" style="font-size:17px">${D.method === "pivot" ? "Pivot" : "Appariée"}</span><span class="sub">${D.method === "pivot" ? "réception commutée, par créneaux" : "réception simultanée"}</span></div>`;
}

function divColor(t, v, lim = 6) {
  // Diverging: negative (comparée moins bonne) → div-neg, 0 → div-mid, positive → div-pos.
  const x = Math.max(-1, Math.min(1, v / lim));
  const mix = (a, b, w) => { const pa = hexRgb(a), pb = hexRgb(b); return `rgb(${pa.map((c, i) => Math.round(c + (pb[i] - c) * w)).join(",")})`; };
  return x < 0 ? mix(t.divMid, t.divNeg, -x) : mix(t.divMid, t.divPos, x);
}
function hexRgb(c) {
  if (c.startsWith("rgb")) return c.match(/\d+/g).slice(0, 3).map(Number);
  const h = c.replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((x) => x + x).join("") : h, 16);
  return [n >> 16 & 255, n >> 8 & 255, n & 255];
}

function renderDigitalMap(t, D, st, cmpK) {
  const el = $("c-dmap");
  if (!D.home) { chart("c-dmap").clear(); $("d-dmap").textContent = "Indiquez le locator de la station pour situer les stations décodées."; return; }
  const home = D.home;
  const placed = st.map((s) => {
    const lat = s.lat ?? null, lon = s.lon ?? null;
    if (s.distance_km == null || s.azimuth_deg == null) return null;
    return { s, xy: project(s.distance_km, s.azimuth_deg), g: gapOf(s, cmpK) };
  }).filter(Boolean);
  const reach = Math.min(20015, Math.max(1500, ...placed.map((p) => p.s.distance_km)) * 1.12);
  const w = el.clientWidth || 600, h = el.clientHeight || 380, k = Math.max(w, h) / Math.min(w, h);
  const xr = w >= h ? reach * k : reach, yr = w >= h ? reach : reach * k;
  // Land: every outline projected around the station, cut where it leaves the disc.
  const lines = [];
  (window.OAV_WORLD || []).forEach((ring) => {
    let cur = [];
    ring.forEach(([lon, lat]) => {
      const [d, az] = distAz(home.lat, home.lon, lat, lon);
      if (d > reach) { if (cur.length > 1) lines.push(cur); cur = []; return; }
      const p = project(d, az);
      if (cur.length && Math.hypot(p[0] - cur[cur.length - 1][0], p[1] - cur[cur.length - 1][1]) > reach / 3) { if (cur.length > 1) lines.push(cur); cur = []; }
      cur.push(p);
    });
    if (cur.length > 1) lines.push(cur);
  });
  const circles = [400, 1000, 3800, 10000].filter((r) => r < reach);
  const labelled = circles.filter((r) => r >= reach * 0.15);
  const o = baseOpt(t);
  o.grid = { left: 8, right: 8, top: 8, bottom: 8 };
  o.xAxis = { type: "value", min: -xr, max: xr, show: false };
  o.yAxis = { type: "value", min: -yr, max: yr, show: false };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => {
    if (!p.data || !p.data.s) return "";
    const s = p.data.s, g = p.data.g;
    return `<b>${escD(s.call)}</b> ${escD(s.locator || "")}${s.approximate ? " (position du pays)" : ""}<br>${NF0.format(s.distance_km)} km, ${NF0.format(s.azimuth_deg)}°<br>Écart ${s1(g.median)} dB <span style="color:${t.muted}">(${g.n} mesure${g.n > 1 ? "s" : ""})</span>`;
  } };
  o.series = [
    { type: "custom", silent: true, z: 1, data: [0], renderItem: (params, api) => ({ type: "group", children: [
      ...lines.map((pts) => ({ type: "polyline", shape: { points: pts.map((p) => api.coord(p)) }, style: { stroke: t.axis, lineWidth: 1, fill: null } })),
      ...circles.map((r) => { const c = api.coord([0, 0]), e = api.coord([r, 0]); return { type: "circle", shape: { cx: c[0], cy: c[1], r: e[0] - c[0] }, style: { stroke: t.line, lineDash: [4, 4], fill: null } }; }),
      ...labelled.map((r) => { const e = api.coord([r * 0.71, r * 0.71]); return { type: "text", style: { x: e[0] + 3, y: e[1] - 3, text: `${NF0.format(r)} km`, fill: t.muted, fontSize: 10.5 } }; }),
      ...[["N", 0, reach * 0.97], ["E", reach * 0.97, 0], ["S", 0, -reach * 0.97], ["O", -reach * 0.97, 0]].map(([s, x, y]) => { const c = api.coord([x, y]); return { type: "text", style: { x: c[0], y: c[1], text: s, fill: t.muted, fontSize: 11, fontWeight: 600, align: "center", verticalAlign: "middle" } }; }),
    ] }) },
    { type: "scatter", z: 3, data: [{ value: [0, 0], symbol: "pin", symbolSize: 16, itemStyle: { color: t.ink } }], silent: true },
    { type: "scatter", z: 4, data: placed.map((p) => ({ value: p.xy, s: p.s, g: p.g, symbolSize: 8 + Math.min(10, p.g.n), symbol: p.s.approximate ? "emptyCircle" : "circle",
      itemStyle: { color: divColor(t, p.g.median), borderColor: t.surface, borderWidth: 1.5 } })) },
  ];
  chart("c-dmap").setOption(o, true);
  $("d-dmap").textContent = `Centrée sur la station (${home.locator}) : distances et directions vraies depuis la station. Couleur : écart, ${divLegend()} ; taille : nombre de mesures ; cercle creux : position du pays, sans locator.`;
}
const divLegend = () => "rouge quand la comparée est moins bonne, bleu quand elle est meilleure, ±6 dB aux extrêmes";

function scatterGap(t, st, cmp, cmpK, ref, xOf, xAxis, extra) {
  const col = colorOf(cmp);
  const pts = st.map((s) => ({ s, x: xOf(s), g: gapOf(s, cmpK) })).filter((p) => p.x != null);
  const o = baseOpt(t);
  o.grid = { left: 50, right: 16, top: 16, bottom: 44 };
  o.xAxis = { ...xAxis, ...axisCommon(t) };
  o.yAxis = { type: "value", ...axisCommon(t), axisLabel: { color: t.muted, formatter: (v) => s1(v) }, name: "dB", nameGap: 10 };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => {
    if (!p.data || !p.data.s) return "";
    const { s, g } = p.data;
    return `<b>${escD(s.call)}</b> ${escD(s.locator || s.country || "")}<br>${cmp.id} − ${ref.id} : <b>${s1(g.median)} dB</b>${g.ci95 != null ? ` ±${f1(g.ci95)}` : ""}<br><span style="color:${t.muted}">${g.n} mesure${g.n > 1 ? "s" : ""}, ${NF0.format(s.distance_km ?? 0)} km</span>`;
  } };
  o.series = [
    { type: "custom", z: 2, silent: true, data: pts.filter((p) => p.g.ci95 != null).map((p) => [p.x, p.g.median - p.g.ci95, p.g.median + p.g.ci95]),
      renderItem: (params, api) => { const a = api.coord([api.value(0), api.value(1)]), b = api.coord([api.value(0), api.value(2)]); return { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: col, lineWidth: 1.5, opacity: 0.5 } }; } },
    { type: "scatter", z: 3, symbol: symbolOf(cmp), data: pts.map((p) => ({ value: [p.x, p.g.median], s: p.s, g: p.g, symbolSize: 8 + Math.min(8, p.g.n) })),
      itemStyle: { color: col, borderColor: t.surface, borderWidth: 1.5 },
      markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: t.axis, type: "solid", width: 1 }, data: [{ yAxis: 0 }] },
      ...extra },
  ];
  return o;
}

function renderDigitalDistance(t, st, cmp, cmpK, ref) {
  const zone = (z, i) => [{ xAxis: Math.max(10, z.from), name: z.label, itemStyle: { color: i % 2 ? t.twilight : "transparent" } }, { xAxis: z.to }];
  const o = scatterGap(t, st, cmp, cmpK, ref, (s) => s.distance_km, { type: "log", min: 10, max: 20100, name: "distance (km)", nameLocation: "middle", nameGap: 26, axisLabel: { color: t.muted, formatter: (v) => NF0.format(v) }, splitLine: { show: false } },
    { markArea: { silent: true, label: { color: t.muted, fontSize: 10.5, position: "insideTop" }, data: ZONES.map(zone) } });
  chart("c-ddist").setOption(o, true);
  $("d-ddist").textContent = `Chaque point est une station, avec son intervalle de confiance à 95 %. Fond : NVIS, zone de silence, DX en un bond, DX en plusieurs bonds (l'onde de sol, sous 30 à 80 km selon la bande, se confond avec le NVIS).`;
}

function renderDigitalAngle(t, st, cmp, cmpK, ref) {
  const o = scatterGap(t, st, cmp, cmpK, ref, (s) => s.takeoff_deg, { type: "value", min: 0, max: 90, interval: 15, name: "angle de départ estimé (°)", nameLocation: "middle", nameGap: 26, axisLabel: { color: t.muted, formatter: (v) => `${v}°` }, splitLine: { show: false } }, {});
  chart("c-dangle").setOption(o, true);
  $("d-dangle").textContent = "Angle d'un seul bond sur la couche F2 (300 km), estimé par la distance : non calculé au-delà de la portée d'un bond. Une estimation, pas une mesure.";
}

function renderDigitalPolar(t, st, cmp, cmpK, ref) {
  const col = colorOf(cmp);
  const pts = st.filter((s) => s.azimuth_deg != null).map((s) => ({ s, g: gapOf(s, cmpK) }));
  const lim = Math.max(6, Math.ceil(Math.max(...pts.map((p) => Math.abs(p.g.median)), 0) + 1));
  // Median by 30° sector, closed around the circle.
  const sectors = [];
  for (let a = 0; a < 360; a += 30) {
    const v = pts.filter((p) => p.s.azimuth_deg >= a && p.s.azimuth_deg < a + 30).map((p) => p.g.median).sort((x, y) => x - y);
    if (v.length) sectors.push([v[Math.floor(v.length / 2)], a + 15]);
  }
  const o = baseOpt(t);
  o.polar = { radius: "78%" };
  o.angleAxis = { type: "value", min: 0, max: 360, interval: 45, startAngle: 90, clockwise: true, axisLine: { lineStyle: { color: t.axis } }, axisLabel: { color: t.muted, formatter: (v) => ({ 0: "N", 90: "E", 180: "S", 270: "O" })[v] ?? `${v}°` }, splitLine: { lineStyle: { color: t.line } } };
  o.radiusAxis = { type: "value", min: -lim, max: lim, interval: Math.ceil(lim / 2), axisLine: { show: false }, axisLabel: { color: t.muted, fontSize: 10.5, formatter: (v) => s1(v) }, splitLine: { lineStyle: { color: t.line, type: "dashed" } } };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (p) => (p.data && p.data.s ? `<b>${escD(p.data.s.call)}</b>, ${NF0.format(p.data.s.azimuth_deg)}°<br>${cmp.id} − ${ref.id} : <b>${s1(p.data.g.median)} dB</b>` : p.seriesName === "secteurs" ? `Secteur ${p.value[1] - 15}°–${p.value[1] + 15}° : médiane ${s1(p.value[0])} dB` : "") };
  const beams = ANTS.filter((a) => a.azimuth != null && a.azimuth !== "").map((a) => ({ value: [lim, Number(a.azimuth)], symbol: "triangle", symbolSize: 12, symbolRotate: -Number(a.azimuth), itemStyle: { color: colorOf(a) }, label: { show: true, formatter: a.id, color: t.ink2, position: "top" } }));
  o.series = [
    { type: "line", name: "zéro", coordinateSystem: "polar", silent: true, symbol: "none", data: Array.from({ length: 73 }, (_, i) => [0, i * 5]), lineStyle: { color: t.axis, width: 1 } },
    { type: "line", name: "secteurs", coordinateSystem: "polar", data: sectors.length > 2 ? [...sectors, sectors[0]] : sectors, symbol: "none", lineStyle: { color: col, width: 2 } },
    { type: "scatter", coordinateSystem: "polar", symbol: symbolOf(cmp), data: pts.map((p) => ({ value: [p.g.median, p.s.azimuth_deg], s: p.s, g: p.g, symbolSize: 7 + Math.min(7, p.g.n) })), itemStyle: { color: col, borderColor: t.surface, borderWidth: 1.5 } },
    { type: "scatter", coordinateSystem: "polar", silent: true, data: beams },
  ];
  chart("c-dpolar").setOption(o, true);
  $("d-dpolar").textContent = `${cmp.id} − ${ref.id} selon la direction de la station ; trait : médiane par secteur de 30° ; cercle plein : écart nul.${beams.length ? " Triangles : orientation des antennes directives." : ""}`;
}

function renderDigitalSets(t, D) {
  const n = ANTS.length, sets = D.decoded_sets || {};
  const box = $("c-dsets");
  const count = (key) => sets[key] || 0;
  if (n <= 3) {
    chart("c-dsets").clear();
    const W = 420, H = 260, r = 82;
    const centres = n === 2 ? [[160, 130], [260, 130]] : [[170, 105], [250, 105], [210, 175]];
    const subsets = [];
    for (let m = 1; m < 1 << n; m++) subsets.push([...Array(n).keys()].filter((k) => m >> k & 1));
    const pos = (ks) => { if (ks.length === n) return centres.reduce((a, c) => [a[0] + c[0] / n, a[1] + c[1] / n], [0, 0]);
      const inC = centres.filter((_, k) => ks.includes(k)), out = centres.filter((_, k) => !ks.includes(k));
      const m = inC.reduce((a, c) => [a[0] + c[0] / inC.length, a[1] + c[1] / inC.length], [0, 0]);
      const o = out.reduce((a, c) => [a[0] + c[0] / out.length, a[1] + c[1] / out.length], [0, 0]);
      const dx = m[0] - o[0], dy = m[1] - o[1], L = Math.hypot(dx, dy) || 1, push = ks.length === 1 ? 0.55 * r : 0.18 * r;
      return [m[0] + dx / L * push, m[1] + dy / L * push]; };
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" role="img" aria-label="Stations décodées par antenne">
      ${centres.map((c, k) => `<circle cx="${c[0]}" cy="${c[1]}" r="${r}" fill="${hexA(colorOf(ANTS[k]), 0.18)}" stroke="${colorOf(ANTS[k])}" stroke-width="2"/>`).join("")}
      ${centres.map((c, k) => `<text x="${c[0] + (k === 0 ? -r : k === 1 ? r : 0) * 0.75}" y="${k === 2 ? c[1] + r + 16 : c[1] - r - 6}" text-anchor="middle" font-size="13" font-weight="600" fill="${t.ink2}">${ANTS[k].id} · ${escD(ANTS[k].short)}</text>`).join("")}
      ${subsets.map((ks) => { const p = pos(ks); return `<text x="${p[0]}" y="${p[1] + 5}" text-anchor="middle" font-size="16" font-weight="600" fill="${t.ink}">${count(ks.join(","))}</text>`; }).join("")}
    </svg>`;
  } else {
    box.innerHTML = "";
    const combos = Object.entries(sets).sort((a, b) => b[1] - a[1]).slice(0, 20);
    const o = baseOpt(t);
    o.grid = [{ left: 60, right: 12, top: 10, height: "52%" }, { left: 60, right: 12, top: "64%", bottom: 10 }];
    o.xAxis = [{ type: "category", gridIndex: 0, data: combos.map(([k]) => k), show: false }, { type: "category", gridIndex: 1, data: combos.map(([k]) => k), show: false }];
    o.yAxis = [{ type: "value", gridIndex: 0, ...axisCommon(t), name: "stations" }, { type: "category", gridIndex: 1, data: ANTS.map((a) => a.id), inverse: true, ...axisCommon(t), axisLine: { show: false } }];
    o.series = [
      { type: "bar", xAxisIndex: 0, yAxisIndex: 0, data: combos.map(([, v]) => v), itemStyle: { color: t.ink2, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 18 },
      { type: "scatter", xAxisIndex: 1, yAxisIndex: 1, symbolSize: 9, data: combos.flatMap(([k]) => ANTS.map((a) => ({ value: [k, a.id], itemStyle: { color: k.split(",").includes(String(a.k)) ? colorOf(a) : t.line } }))) },
    ];
    chart("c-dsets").setOption(o, true);
  }
  const excl = ANTS.map((a) => `${a.id} seule : ${NF0.format((D.exclusive || {})[a.k] || (D.exclusive || {})[String(a.k)] || 0)}`).join(" · ");
  $("d-dsets").textContent = `Stations validées décodées par chaque antenne et par chaque combinaison (${n <= 3 ? "diagramme de Venn" : "UpSet : barres = nombre de stations, points = antennes qui les ont décodées"}). ${excl}.`;
}

function renderDigitalTable(D, cmpK, ref, cmp) {
  const rows = D.stations.map((s) => {
    const g = gapOf(s, cmpK);
    const by = s.decoded_by.map((k) => ANTS[k] ? ANTS[k].id : k).join(", ");
    return `<tr><td class="mono">${escD(s.call)}</td><td class="mono">${escD(s.locator || "—")}</td><td>${escD(s.country || "")}</td>
      <td class="num">${s.distance_km != null ? NF0.format(s.distance_km) + (s.approximate ? " ≈" : "") : "—"}</td><td class="num">${s.azimuth_deg != null ? NF0.format(s.azimuth_deg) + "°" : "—"}</td>
      <td>${escD(D.class_labels[s.class] || "")}</td><td class="num">${s.takeoff_deg != null ? NF0.format(s.takeoff_deg) + "°" : "—"}</td>
      <td class="num">${NF0.format(s.slots)}</td><td class="num">${g ? `${s1(g.median)}${g.ci95 != null ? ` ±${f1(g.ci95)}` : ""}` : "—"}</td><td>${by}</td></tr>`;
  }).join("");
  $("dig-table").innerHTML = `<thead><tr><th>Indicatif</th><th>Locator</th><th>Pays</th><th class="num">km</th><th class="num">Azimut</th><th>Classe</th><th class="num">Angle</th><th class="num">Créneaux</th><th class="num">${cmp.id} − ${ref.id} (dB)</th><th>Décodée par</th></tr></thead><tbody>${rows || `<tr><td colspan="10">Aucune piste validée pour l'instant.</td></tr>`}</tbody>`;
}
