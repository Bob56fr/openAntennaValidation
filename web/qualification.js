"use strict";

/* Page Qualification : déroulé guidé du banc et rapport (maquette P0 design/qualification.html). */

const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const NF1 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const f1 = (v) => (v == null ? "—" : NF1.format(v).replace("-", "−"));
const f2 = (v) => (v == null ? "—" : NF2.format(v).replace("-", "−"));
const s2 = (v) => (v == null ? "—" : (v > 0.004 ? "+" : "") + f2(v));
const fmtDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const ICON = {
  ok: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--good)"/><path d="M4.6 8.2l2.2 2.2 4.6-4.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  warn: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="var(--warning)"/><path d="M8 6v4" stroke="#0b0b0b" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="12" r="1" fill="#0b0b0b"/></svg>`,
  bad: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--critical)"/><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  unknown: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="var(--muted)" stroke-width="1.5"/><path d="M5 8h6" stroke="var(--muted)" stroke-width="1.8" stroke-linecap="round"/></svg>`,
};
const STATE_WORD = { ok: "Exploitable", warn: "Dégradé", bad: "Inexploitable", unknown: "À mesurer" };

let data = null;
let poll = null;
const charts = {};
const chart = (id) => charts[id] || (charts[id] = echarts.init($(id)));

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) { location.replace("/login"); throw new Error("Non connecté"); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || `Erreur ${res.status}`);
  return json;
}
function showError(msg) { $("error").hidden = !msg; $("error").textContent = msg || ""; }

async function refresh() {
  try { data = await api("/api/qualification"); render(); } catch (e) { showError(e.message); }
  const busy = data && data.bench && data.bench.busy;
  if (busy && !poll) poll = setInterval(refresh, 1000);
  if (!busy && poll) { clearInterval(poll); poll = null; }
}

/* ---------- Rendu ---------- */

function render() {
  const run = data.run, rx = data.receiver;
  const running = run && run.status === "running";
  $("att").value ||= data.settings.attenuator_db;
  $("receiver-warn").hidden = rx.state === "running";
  $("receiver-warn").textContent = rx.state === "absent"
    ? "Aucun SDR branché : la qualification se fait avec le SDR en marche. Les rapports précédents restent consultables."
    : `Récepteur : ${rx.error || rx.state}.`;
  $("btn-new").disabled = rx.state !== "running" || (data.bench && data.bench.busy);
  $("sec-new").hidden = !!running;
  $("sec-steps").hidden = !running;
  $("sec-report").hidden = !run;
  const dev = rx.device;
  $("title").textContent = dev ? `${dev.model}, entrées ${(rx.antennas || []).join(" et ")}${run && run.band ? `, bande ${run.band}` : ""}` : "Qualification du SDR";
  const pill = !run ? ["Aucune qualification", ""] : run.status === "running" ? ["En cours", "warn"] : run.accepted_ms ? ["Profil accepté", "ok"] : ["Terminée, à accepter", "ok"];
  $("run-pill").className = `pill ${pill[1]}`; $("run-word").textContent = pill[0];
  const meta = [];
  if (dev) meta.push(`<span>N° de série <b>${dev.serial}</b></span>`);
  if (run) {
    if (run.gain && run.gain.label) meta.push(`<span>${run.gain.label}</span>`);
    meta.push(`<span>${NF1.format(run.center_hz / 1e6).replace(",", ",")} MHz · ${Math.round(run.sample_rate / 1000)} kS/s</span>`);
    meta.push(`<span>Commutation rapide à <b>${run.switch_rate_hz} Hz</b></span>`);
    meta.push(`<span>Atténuateur ${f1(run.attenuator_db)} dB</span>`);
    meta.push(`<span>${fmtDate.format(run.started_ms)}</span>`);
  }
  $("meta").innerHTML = meta.join("");
  if (running) renderSteps(run);
  if (run) renderReport(run);
}

function stepSummary(key, r) {
  const parts = [];
  if (r.levels) {
    const lv = r.levels, [a, b] = lv.inputs;
    if (key === "floor") parts.push(`plancher ${a} ${f1(lv.floor_dbfs[a])} / ${b} ${f1(lv.floor_dbfs[b])} dBFS par case`);
    else if (key === "isolation") parts.push(`${a} − ${b} : ${f1(lv.total_db[a] - lv.total_db[b])} dB`);
    else { const d = lv.difference_db; parts.push(`${a} − ${b} : ${s2(d.reduce((s, v) => s + v, 0) / d.length)} dB en moyenne`); }
  }
  if (r.switching) {
    const sw = r.switching;
    if (sw.detected) parts.push(`marche ${s2(sw.step_db)} dB, latence p50 ${f2(sw.latency_ms.p50)} ms, établissement ${f2(sw.settle_ms)} ms`);
    else parts.push(`bascule non visible (pas de marche) : ${f2(sw.settle_ms)} ms depuis la commande`);
    if (sw.noisy) parts.push("mesure bruitée : source trop variable");
    if (sw.dropped) parts.push(`${sw.dropped} échantillons perdus`);
  }
  return parts.join(" · ");
}

function renderSteps(run) {
  const bench = data.bench || {};
  const busy = !!bench.busy;
  $("steps").innerHTML = data.steps.map((s, i) => {
    const r = run.steps[s.key];
    const mine = bench.run === run.uuid && bench.step === s.key;
    const isBusy = busy && mine, isErr = !busy && mine && bench.error;
    const cls = isBusy ? "busy" : isErr ? "err" : r ? "done" : "";
    const status = isBusy ? `<div class="res">Mesure en cours…</div>`
      : isErr ? `<div class="res err">${bench.error}</div>`
      : r ? `<div class="res">${stepSummary(s.key, r)}</div>` : "";
    return `<div class="step ${cls}"><span class="num">${i + 1}</span>
      <div><h3>${s.title}${s.optional ? "" : ""}</h3><p>${s.setup}</p>${status}</div>
      <div class="act"><button class="btn ${r ? "ghost" : "primary"}" data-step="${s.key}" type="button" ${busy || data.receiver.state !== "running" ? "disabled" : ""}>${r ? "Refaire" : "Mesurer"}</button></div></div>`;
  }).join("");
  const missing = run.profile.missing;
  $("btn-finish").disabled = busy || missing.length > 0;
  $("finish-note").textContent = missing.length ? `À faire : ${missing.join(", ")}.` : "Toutes les étapes obligatoires sont mesurées.";
}

function renderReport(run) {
  const p = run.profile, t = theme();
  $("verdicts").innerHTML = p.verdicts.map((v) => `<article class="vcard ${v.state === "unknown" ? "" : v.state}">
      <div class="vtop">${ICON[v.state]}${STATE_WORD[v.state]}</div>
      <div class="vname">${v.label}</div>
      <div class="vvalue mono">${v.useful == null ? "—" : `${Math.round(v.useful * 100)} <small>% de temps utile</small>`}</div>
      <div class="vdesc">À ${v.rate_hz} Hz de commutation, cadence nécessaire pour cette classe. Marge en dB : campagne A/A.</div></article>`).join("");
  $("report-warn").hidden = !p.warnings.length;
  $("report-warn").innerHTML = p.warnings.map((w) => `<div>${w}</div>`).join("");
  renderStep(t, p);
  renderHist(t, p);
  renderImbalance(t, p);
  renderFraction(t, p);
  renderProfile(p, run);
  const done = run.status === "done";
  $("accept").hidden = !done;
  if (done) {
    $("btn-accept").disabled = !!run.accepted_ms || !$("ack").checked;
    $("ack").disabled = !!run.accepted_ms;
    $("accept-note").textContent = run.accepted_ms ? `Accepté le ${fmtDate.format(run.accepted_ms)}.` : "";
  }
}

function theme() {
  return { ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), line: css("--line"), axis: css("--axis"), surface: css("--surface"),
    blank: css("--blank"), tol: css("--tol"), zone: css("--zone"), s1: css("--s1"), critical: css("--critical"), font: css("--font-body") };
}
function base(t) {
  return { animation: false, textStyle: { fontFamily: t.font, color: t.ink2, fontSize: 12 }, grid: { left: 58, right: 18, top: 18, bottom: 40 },
    tooltip: { backgroundColor: t.surface, borderColor: t.line, borderWidth: 1, padding: [8, 10], textStyle: { color: t.ink, fontSize: 12.5 } } };
}
const ax = (t) => ({ axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, axisLabel: { color: t.muted, fontSize: 11.5 }, splitLine: { lineStyle: { color: t.line } }, nameTextStyle: { color: t.muted, fontSize: 11.5 } });

function renderStep(t, p) {
  const sw = p.switching;
  if (!sw) { chart("c-step").clear(); $("d-step").textContent = "Mesurée aux étapes « Splitter symétrique » et « Atténuateur sur B »."; $("k-step").innerHTML = ""; return; }
  const e = sw.envelope, pts = (k) => e.t_ms.map((x, i) => [x, e[k][i]]);
  const tol = sw.settle_tolerance_db ?? 0.2;
  const o = base(t);
  o.xAxis = { type: "value", min: e.t_ms[0], max: e.t_ms[e.t_ms.length - 1], ...ax(t), name: sw.aligned_on === "effect" ? "temps depuis l'effet (ms)" : "temps depuis la commande (ms)", nameLocation: "middle", nameGap: 26, splitLine: { show: false } };
  o.yAxis = { type: "value", ...ax(t), axisLabel: { color: t.muted, formatter: (v) => `${s2(v).replace(/,00$/, "")} dB` } };
  o.tooltip = { ...o.tooltip, trigger: "axis", formatter: (ps) => `<b>${f2(ps[0].value[0])} ms</b><br>` + ps.filter((x) => x.seriesName).map((x) => `${x.seriesName} : ${s2(x.value[1])} dB`).join("<br>") };
  o.series = [
    { type: "line", data: [], silent: true, markArea: { silent: true, data: [[{ xAxis: 0, itemStyle: { color: t.blank } }, { xAxis: sw.settle_ms }], [{ yAxis: -tol, itemStyle: { color: t.tol } }, { yAxis: tol }]] } },
    { type: "line", name: "p95", data: pts("p95_db"), symbol: "none", lineStyle: { width: 1, color: t.muted, type: "dashed" } },
    { type: "line", name: "p5", data: pts("p05_db"), symbol: "none", lineStyle: { width: 1, color: t.muted, type: "dashed" } },
    { type: "line", name: "Moyenne", data: pts("mean_db"), symbol: "none", lineStyle: { width: 2, color: t.ink },
      markLine: { silent: true, symbol: "none", data: [{ xAxis: 0, lineStyle: { color: t.ink2, type: "dashed" }, label: { formatter: sw.aligned_on === "effect" ? "effet" : "commande", color: t.ink2, position: "insideEndTop", fontSize: 11 } }] } },
  ];
  chart("c-step").setOption(o, true);
  $("t-step").textContent = sw.source === "attenuator" ? "Réponse à une bascule vers B (atténuateur)" : "Réponse à une bascule (splitter symétrique)";
  $("d-step").textContent = `Moyenne de ${sw.switches} bascules, relative au niveau final${sw.aligned_on === "effect" ? ", alignées sur l'instant réel de la bascule" : ", alignées sur la commande (la bascule n'est pas visible sans marche de niveau)"}. Parasite ${f1(sw.glitch_db)} dB, retour à ±${f2(tol)} dB en ${f2(sw.settle_ms)} ms.`;
  $("k-step").innerHTML = `<span><i class="ln" style="border-color:${t.ink}"></i>Moyenne</span><span><i class="ln" style="border-color:${t.muted};border-top-style:dashed"></i>p5 et p95 des bascules</span><span><i class="sw" style="background:${t.blank}"></i>Établissement</span><span><i class="sw" style="background:${t.tol}"></i>Tolérance</span>`;
}

function renderHist(t, p) {
  const sw = p.switching;
  if (!sw || !sw.latency_ms) { chart("c-hist").clear(); $("d-hist").textContent = "Mesurée à l'étape « Atténuateur sur B » : il faut une marche de niveau pour voir l'instant réel de la bascule."; return; }
  const h = sw.histogram, lat = sw.latency_ms;
  const o = base(t);
  o.xAxis = { type: "category", data: h.edges_ms.map((v) => f1(v)), ...ax(t), name: "latence (ms)", nameLocation: "middle", nameGap: 26, splitLine: { show: false }, axisLabel: { color: t.muted, interval: Math.max(0, Math.floor(h.edges_ms.length / 8)) } };
  o.yAxis = { type: "value", ...ax(t), name: "bascules", nameGap: 10 };
  o.tooltip = { ...o.tooltip, trigger: "item", formatter: (x) => `<b>${x.name} à ${f1(+x.name.replace(",", ".") + 0.1)} ms</b><br>${x.value} bascules` };
  const near = (v) => h.edges_ms.reduce((best, e, i) => (Math.abs(e - v) < Math.abs(h.edges_ms[best] - v) ? i : best), 0);
  o.series = [{ type: "bar", data: h.counts, barCategoryGap: "12%", itemStyle: { color: t.muted, borderRadius: [3, 3, 0, 0] },
    markLine: { silent: true, symbol: "none", data: [
      { xAxis: near(lat.p50), lineStyle: { color: t.ink }, label: { formatter: `p50 ${f2(lat.p50)} ms`, color: t.ink2, fontSize: 11, position: "insideEndTop" } },
      { xAxis: near(lat.p99), lineStyle: { color: t.critical }, label: { formatter: `p99 ${f2(lat.p99)} ms`, color: t.ink2, fontSize: 11, position: "insideEndTop" } }] } }];
  chart("c-hist").setOption(o, true);
  $("d-hist").textContent = `${sw.detections} bascules repérées dans le flux sur ${sw.switches}. Gigue ${f2(lat.jitter)} ms. L'API applique la bascule de façon asynchrone : l'instant réel est lu dans le flux grâce à la marche de niveau.`;
}

function renderImbalance(t, p) {
  const im = p.imbalance;
  if (!im) { chart("c-imb").clear(); $("d-imb").textContent = "Mesuré aux étapes « Splitter symétrique » et « Câbles inversés »."; $("k-imb").innerHTML = ""; return; }
  const f = im.freqs_hz.map((x) => x / 1e6);
  const o = base(t);
  o.xAxis = { type: "value", min: f[0], max: f[f.length - 1], ...ax(t), name: "MHz", nameLocation: "middle", nameGap: 26, splitLine: { show: false }, axisLabel: { color: t.muted, formatter: (v) => NF2.format(v) } };
  o.yAxis = { type: "value", scale: true, ...ax(t), axisLabel: { color: t.muted, formatter: (v) => `${s2(v)} dB` } };
  o.tooltip = { ...o.tooltip, trigger: "axis", formatter: (ps) => `<b>${NF2.format(ps[0].value[0])} MHz</b><br>` + ps.map((x) => `${x.seriesName} : ${s2(x.value[1])} dB`).join("<br>") };
  const line = (name, arr, color, type, w) => ({ type: "line", name, data: f.map((x, i) => [x, arr[i]]), symbol: "none", lineStyle: { width: w, color, type } });
  o.series = [line("Câbles normaux", im.normal_db, t.muted, "solid", 1.5), line("Câbles inversés", im.swapped_db, t.muted, "dashed", 1.5), line("Correction retenue", im.port_db, t.s1, "solid", 2.2)];
  chart("c-imb").setOption(o, true);
  $("d-imb").textContent = `Mesuré câbles normaux puis inversés : la moyenne retire le déséquilibre du splitter (${s2(im.splitter_db)} dB). Correction de ${s2(im.port_min_db)} à ${s2(im.port_max_db)} dB sur la bande.`;
  $("k-imb").innerHTML = `<span><i class="ln" style="border-color:${t.muted}"></i>Câbles normaux</span><span><i class="ln" style="border-color:${t.muted};border-top-style:dashed"></i>Câbles inversés</span><span><i class="ln" style="border-color:${t.s1}"></i>Correction retenue</span>`;
}

function renderFraction(t, p) {
  if (p.blanking_ms == null) { chart("c-frac").clear(); $("d-frac").textContent = "Calculé dès que la neutralisation après bascule est connue."; return; }
  const b = p.blanking_ms, rate = data.run.switch_rate_hz;
  const pts = []; for (let r = 1; r <= 100.01; r *= 1.05) pts.push([r, Math.max(0, 100 * (1 - b / (1000 / (2 * r))))]);
  const at = Math.max(0, 100 * (1 - b / (1000 / (2 * rate))));
  const o = base(t);
  o.xAxis = { type: "log", min: 1, max: 100, logBase: 10, ...ax(t), name: "cadence de commutation", nameLocation: "middle", nameGap: 26, splitLine: { show: false }, axisLabel: { color: t.muted, formatter: (v) => `${v} Hz` } };
  o.yAxis = { type: "value", min: 0, max: 100, ...ax(t), axisLabel: { color: t.muted, formatter: (v) => `${v} %` } };
  o.tooltip = { ...o.tooltip, trigger: "axis", formatter: (ps) => `<b>${f1(ps[0].value[0])} Hz</b> (${f1(1000 / (2 * ps[0].value[0]))} ms par entrée)<br>temps utile ${Math.round(ps[0].value[1])} %` };
  const ssb = data.settings.class_rate_hz.ssb || 20;
  o.series = [{ type: "line", data: pts, symbol: "none", lineStyle: { width: 2, color: t.ink },
    markArea: { silent: true, data: [[{ xAxis: ssb, itemStyle: { color: t.zone }, label: { show: true, formatter: "cadence utile pour la SSB", color: t.ink2, fontSize: 11, position: "insideTop" } }, { xAxis: 100 }]] },
    markPoint: { symbol: "circle", symbolSize: 10, itemStyle: { color: t.ink, borderColor: t.surface, borderWidth: 2 }, label: { show: true, position: "left", formatter: `réglage : ${rate} Hz, ${Math.round(at)} %`, color: t.ink, fontSize: 11.5, distance: 8 }, data: [{ coord: [rate, at] }] } }];
  chart("c-frac").setOption(o, true);
  $("d-frac").textContent = `Chaque bascule neutralise ${f2(b)} ms (latence p99 + établissement + marge). À ${rate} Hz, chaque entrée est écoutée ${f1(1000 / (2 * rate))} ms, dont ${f1(Math.max(0, 1000 / (2 * rate) - b))} ms utiles.`;
}

function renderProfile(p, run) {
  const rows = [];
  const sw = p.switching;
  if (sw && sw.latency_ms) rows.push(["Latence commande → effet", `p50 ${f2(sw.latency_ms.p50)} ms · p99 ${f2(sw.latency_ms.p99)} ms`, `${sw.detections} bascules repérées dans le flux`]);
  if (sw) rows.push(["Temps d'établissement", `${f2(sw.settle_ms)} ms`, `Retour à ±${f2(sw.settle_tolerance_db)} dB du niveau final${sw.source === "symmetric" ? ", depuis la commande (majorant)" : ""}`]);
  if (sw) rows.push(["Parasite à la bascule", `${f1(sw.glitch_db)} dB`, "Écart maximal au niveau final après la bascule"]);
  if (p.blanking_ms != null) rows.push(["Neutralisation après bascule", `${f2(p.blanking_ms)} ms`, "Latence p99 + établissement + marge"]);
  if (p.useful_at_rate != null) rows.push(["Temps utile à la cadence par défaut", `${Math.round(p.useful_at_rate * 100)} %`, `${run.switch_rate_hz} Hz de commutation`]);
  if (p.imbalance) rows.push(["Correction A − B", `${s2(p.imbalance.port_min_db)} à ${s2(p.imbalance.port_max_db)} dB`, "Appliquée au calcul, la mesure brute reste inchangée"]);
  if (p.attenuator_check_db != null) rows.push(["Justesse de la marche mesurée", `${s2(p.attenuator_check_db)} dB`, `Écart à l'atténuateur déclaré (${f1(run.attenuator_db)} dB)`]);
  if (p.own_floor_dbfs) rows.push(["Plancher propre du SDR", Object.entries(p.own_floor_dbfs).map(([k, v]) => `${k} ${f1(v)}`).join(" · ") + " dBFS", "Par case FFT, entrées sur charge 50 Ω"]);
  if (p.isolation) rows.push(["Isolation entre entrées", `${p.isolation.floor_limited ? "≥ " : ""}${f1(p.isolation.db)} dB`, p.isolation.floor_limited ? "Limitée par le plancher du SDR" : "Source sur A, charge sur B"]);
  if (sw && sw.dropped) rows.push(["Échantillons perdus pendant le banc", String(sw.dropped), "Résultat à refaire si la valeur n'est pas nulle"]);
  $("profile").innerHTML = `<thead><tr><th>Paramètre</th><th>Valeur</th><th>Origine</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td style="color:var(--ink-2);white-space:normal">${r[2]}</td></tr>`).join("") || `<tr><td colspan="3" style="color:var(--muted)">Les résultats apparaîtront au fil des étapes.</td></tr>`}</tbody>`;
}

/* ---------- Actions ---------- */

$("btn-new").addEventListener("click", async () => {
  try { showError(""); await api("/api/qualification/runs", { attenuator_db: Number($("att").value) || undefined }); await refresh(); } catch (e) { showError(e.message); }
});
$("steps").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-step]"); if (!b) return;
  try { showError(""); await api(`/api/qualification/runs/${data.run.uuid}/steps/${b.dataset.step}`, {}); await refresh(); } catch (err) { showError(err.message); }
});
$("btn-finish").addEventListener("click", async () => {
  try { showError(""); await api(`/api/qualification/runs/${data.run.uuid}/finish`, {}); await refresh(); } catch (e) { showError(e.message); }
});
$("ack").addEventListener("change", () => { $("btn-accept").disabled = !$("ack").checked; });
$("btn-accept").addEventListener("click", async () => {
  try { showError(""); await api(`/api/qualification/runs/${data.run.uuid}/accept`, {}); await refresh(); } catch (e) { showError(e.message); }
});
new ResizeObserver(() => Object.values(charts).forEach((c) => c.resize())).observe(document.body);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => data && render());
new MutationObserver(() => data && render()).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
refresh();
