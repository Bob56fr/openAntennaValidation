"use strict";

/* Tableau de bord : état du récepteur, commandes et spectre en direct. */

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const NF1 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF3 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const f1 = (v) => (v == null || Number.isNaN(v) ? "—" : NF1.format(v).replace("-", "−"));
const mhz = (hz) => NF3.format(hz / 1e6);
const ANTENNA_SLOT = { A: 1, B: 2, C: 3, "": 1 };
const STATE = { stopped: ["Arrêté", ""], starting: ["Démarrage", "warn"], running: ["En marche", "ok"],
  absent: ["SDR non branché", "warn"], error: ["Erreur", "bad"] };

let hw = null;
let bands = [];
const lastByAntenna = {}; // dernier spectre reçu pour chaque entrée
let chart = null;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (res.status === 401) { location.replace("/login"); throw new Error("Non connecté"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erreur ${res.status}`);
  return data;
}

function showError(message, helpUrl, level = "error") {
  const el = $("error");
  el.className = `banner ${level}`;
  el.hidden = !message;
  el.textContent = message || "";
  if (message && helpUrl) {
    el.append(" ");
    const a = document.createElement("a");
    a.href = helpUrl; a.textContent = "Téléchargement"; a.target = "_blank"; a.rel = "noopener";
    el.append(a);
  }
}

/* ---------- État et commandes ---------- */

function renderStatus(report) {
  hw = report;
  const [word, cls] = STATE[report.state] || [report.state, ""];
  $("state-pill").className = `pill ${cls}`;
  $("state-word").textContent = word;
  const dev = report.device;
  const parts = [];
  if (dev) parts.push(`<span><b>${dev.model}</b> n° ${dev.serial}</span>`);
  if (report.library) parts.push(`<span>${report.library}</span>`);
  if (report.sample_rate) parts.push(`<span>${NF1.format(report.sample_rate / 1000)} kS/s · bande utile ${Math.round(report.bandwidth_hz / 1000)} kHz</span>`);
  if (report.identity) parts.push(`<span class="mono" style="font-size:12.5px">${report.identity}</span>`);
  $("meta").innerHTML = parts.join("");
  if (report.state === "absent") {
    showError(`${report.error} Les données de la station restent consultables ; le SDR sera détecté automatiquement dès qu'il sera branché.`, null, "warn");
  } else {
    showError(report.state === "error" ? report.error : "", report.help_url);
  }
  const live = report.state === "running";
  $("spec-empty").hidden = live;
  $("spectrum").hidden = !live;
  $("spec-key").hidden = !live;
  $("tiles").hidden = !live;
  $("identity-changed").hidden = !report.identity_changed;

  // Entrées
  const seg = $("antenna-seg");
  $("ctl-antenna").hidden = !report.antennas.length;
  seg.innerHTML = report.antennas.map((a) => `<button type="button" data-v="${a}" aria-pressed="${a === report.antenna}">${a}</button>`).join("");
  // Gain
  const gain = $("gain");
  if (gain.options.length !== report.gain_steps.length) {
    gain.innerHTML = report.gain_steps.map((label, i) => `<option value="${i}">${label}</option>`).join("");
  }
  if (report.gain_index != null) gain.value = String(report.gain_index);
  // Fréquence
  if (document.activeElement !== $("freq") && report.center_hz) $("freq").value = (report.center_hz / 1000).toFixed(1);
  renderCampaign(report.campaign);
  // Sans récepteur, ou pendant une campagne, les commandes manuelles n'ont rien à piloter.
  const free = live && !report.campaign;
  document.querySelectorAll("#antenna-seg button, #gain, #band, #freq-form button").forEach((el) => { el.disabled = !free; });
  renderBandNote();
}

function renderBandNote() {
  if (!hw) return;
  const center = hw.center_hz, half = (hw.bandwidth_hz || 0) / 2;
  const inside = bands.find((b) => b.low_hz <= center && center <= b.high_hz);
  $("band").value = inside ? inside.name : "";
  let text = `Bande écoutée : ${mhz(center - half)} à ${mhz(center + half)} MHz.`;
  if (inside) {
    const fits = inside.low_hz >= center - half && inside.high_hz <= center + half;
    text += fits ? ` La bande ${inside.name} tient entièrement dans la bande utile.`
      : ` La bande ${inside.name} (${mhz(inside.low_hz)}–${mhz(inside.high_hz)} MHz) dépasse la bande utile : une campagne devra la découper.`;
  }
  $("band-note").textContent = text;
}

const fmtWhen = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function duration(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${String(m % 60).padStart(2, "0")}` : `${Math.floor(h / 24)} j ${h % 24} h`;
}
function renderCampaign(c) {
  const has = !!c;
  ["camp-pill", "camp-results", "camp-stop", "camp-bar"].forEach((id) => { $(id).hidden = !has; });
  $("camp-new").hidden = has;
  if (!has) {
    $("camp-eyebrow").textContent = "Campagne";
    $("camp-title").textContent = "Aucune campagne en cours";
    $("camp-meta").innerHTML = `<a class="linkbtn" href="/campaigns">Voir les campagnes enregistrées</a>`;
    $("camp-confirm").hidden = true;
    return;
  }
  const now = c.last_ms || Date.now();
  const total = c.planned_end_ms - c.start_ms;
  const done = Math.min(1, Math.max(0, (now - c.start_ms) / total));
  const state = c.state === "interrupted" ? ["Interrompue", "bad"] : c.calm ? ["Bande calme", ""] : ["Bande active", "ok"];
  $("camp-eyebrow").textContent = `Campagne en cours${c.aa ? " · A/A" : ""} · mode commuté`;
  $("camp-title").textContent = c.title;
  $("camp-pill").className = `pill ${state[1]}`; $("camp-word").textContent = state[0];
  $("camp-results").href = `/campaign?id=${encodeURIComponent(c.uuid)}`;
  $("camp-progress").style.width = `${Math.round(done * 100)}%`;
  $("camp-meta").innerHTML = `<span>${c.signals} fréquence${c.signals > 1 ? "s" : ""} · commutation ${c.rate_hz} Hz</span>
    <span>${Math.round(done * 100)} % · ${duration(now - c.start_ms)} écoulées, reste ${duration(c.planned_end_ms - now)}</span>
    <span>Fin prévue ${fmtWhen.format(c.planned_end_ms)}</span>`;
}
$("camp-stop").addEventListener("click", () => { $("camp-confirm").hidden = false; });
$("camp-stop-no").addEventListener("click", () => { $("camp-confirm").hidden = true; });
$("camp-stop-yes").addEventListener("click", async () => {
  try { await api("/api/campaign/stop", {}); $("camp-confirm").hidden = true; renderStatus(await api("/api/hardware")); } catch (err) { showError(err.message); }
});

$("antenna-seg").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try { renderStatus(await api("/api/hardware/antenna", { antenna: b.dataset.v })); } catch (err) { showError(err.message); }
});
$("gain").addEventListener("change", async (e) => {
  try { renderStatus(await api("/api/hardware/gain", { index: Number(e.target.value) })); } catch (err) { showError(err.message); }
});
$("freq-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const khz = Number($("freq").value);
  try { renderStatus(await api("/api/receiver/frequency", { center_hz: khz * 1000 })); } catch (err) { showError(err.message); }
});
$("band").addEventListener("change", async (e) => {
  const band = bands.find((b) => b.name === e.target.value);
  if (!band) return;
  try { renderStatus(await api("/api/receiver/frequency", { center_hz: (band.low_hz + band.high_hz) / 2 })); } catch (err) { showError(err.message); }
});
$("logout").addEventListener("click", async () => { await fetch("/api/logout", { method: "POST" }); location.replace("/login"); });

/* ---------- Spectre ---------- */

function theme() {
  return { ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), line: css("--line"), axis: css("--axis"),
    surface: css("--surface"), zone: css("--zone"), font: css("--font-body") };
}
const colorOf = (antenna) => css(`--s${ANTENNA_SLOT[antenna] || 1}`);
const points = (s) => s.bins_dbfs.map((v, i) => [(s.f_start_hz + (i + 0.5) * s.f_step_hz) / 1e6, v]);

function renderSpectrum() {
  const current = hw && lastByAntenna[hw.antenna ?? ""];
  if (!current) return;
  const t = theme();
  if (!chart) chart = echarts.init($("spectrum"));
  const low = current.f_start_hz / 1e6, high = (current.f_start_hz + current.bins_dbfs.length * current.f_step_hz) / 1e6;
  const usableLow = (current.center_hz - current.bandwidth_hz / 2) / 1e6, usableHigh = (current.center_hz + current.bandwidth_hz / 2) / 1e6;
  const others = Object.entries(lastByAntenna).filter(([a, s]) => a !== current.antenna && s.center_hz === current.center_hz);
  const series = others.map(([a, s]) => ({
    type: "line", name: `Entrée ${a} (dernière mesure)`, data: points(s), symbol: "none", silent: true,
    lineStyle: { width: 1.2, color: colorOf(a), type: "dashed", opacity: 0.7 }, itemStyle: { color: colorOf(a) },
  }));
  series.push({
    type: "line", name: current.antenna ? `Entrée ${current.antenna}` : "Signal", data: points(current), symbol: "none",
    lineStyle: { width: 1.4, color: colorOf(current.antenna) }, itemStyle: { color: colorOf(current.antenna) },
    markArea: { silent: true, itemStyle: { color: t.zone }, data: [[{ xAxis: low }, { xAxis: usableLow }], [{ xAxis: usableHigh }, { xAxis: high }]] },
    markLine: { silent: true, symbol: "none", label: { show: true, position: "insideStartTop", formatter: `plancher ${f1(current.floor_dbfs)} dBFS`, color: t.ink2, fontSize: 11 },
      lineStyle: { color: t.axis, type: "solid", width: 1 }, data: [{ yAxis: current.floor_dbfs }] },
  });
  chart.setOption({
    animation: false,
    textStyle: { fontFamily: t.font, color: t.ink2, fontSize: 12 },
    grid: { left: 70, right: 16, top: 16, bottom: 34 },
    tooltip: { trigger: "axis", backgroundColor: t.surface, borderColor: t.line, textStyle: { color: t.ink, fontSize: 12.5 },
      formatter: (ps) => `<b>${NF3.format(ps[0].value[0])} MHz</b><br>` + ps.map((p) => `${p.seriesName} : ${f1(p.value[1])} dBFS`).join("<br>") },
    xAxis: { type: "value", min: low, max: high, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false },
      axisLabel: { color: t.muted, formatter: (v) => NF3.format(v) }, splitLine: { show: false } },
    yAxis: { type: "value", min: (v) => Math.floor((v.min - 5) / 10) * 10, max: (v) => Math.ceil((v.max + 5) / 10) * 10,
      axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, splitLine: { lineStyle: { color: t.line } },
      axisLabel: { color: t.muted, formatter: (v) => `${String(v).replace("-", "−")} dBFS` } },
    series,
  }, true);
  const keyParts = [`<span><i class="ln" style="border-color:${colorOf(current.antenna)}"></i>${current.antenna ? `Entrée ${current.antenna}, en direct` : "En direct"}</span>`];
  others.forEach(([a, s]) => keyParts.push(`<span><i class="ln" style="border-color:${colorOf(a)};border-top-style:dashed"></i>Entrée ${a}, il y a ${Math.round((Date.now() - s.ts_ms) / 1000)} s</span>`));
  keyParts.push(`<span><i class="sw" style="background:${t.zone}"></i>Hors bande utile</span>`);
  $("spec-key").innerHTML = keyParts.join("");
  $("spec-sub").textContent = `Puissance par case FFT (${Math.round(current.f_step_hz)} Hz par point affiché), moyenne sur ${current.frames} trames.`;
  renderTiles(current);
}

function renderTiles(s) {
  const a = lastByAntenna.A, b = lastByAntenna.B;
  const diff = a && b && a.center_hz === b.center_hz ? a.floor_dbfs - b.floor_dbfs : null;
  const tiles = [
    ["Plancher de bruit", `${f1(s.floor_dbfs)} <small>dBFS / case</small>`, "Médiane hors signaux"],
    ["Écrêtage", `${NF1.format(s.clip_ratio * 100)} <small>%</small>`, s.clip_alert ? "Au-delà du seuil : baisser le gain" : "Sous le seuil d'alerte"],
    ["Échantillons perdus", `${s.dropped.toLocaleString("fr-FR")}`, s.overload ? "Surcharge signalée par le SDR" : "Pas de surcharge signalée"],
    ["Plancher A − B", diff == null ? "—" : `${diff > 0 ? "+" : ""}${f1(diff)} <small>dB</small>`, diff == null ? "Basculer sur l'autre entrée pour comparer" : "D'après la dernière mesure de chaque entrée"],
  ];
  $("tiles").innerHTML = tiles.map(([l, v, sub]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");
}

/* ---------- WebSockets avec reconnexion ---------- */

function connect(path, onMessage) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}${path}`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => setTimeout(() => connect(path, onMessage), 2000);
}

async function start() {
  const status = await api("/api/status");
  const st = status.station;
  $("station-name").textContent = st.callsign ? `${st.name} · ${st.callsign}` : st.name;
  $("footer").textContent = `openAntennaValidation ${status.version}, logiciel libre (GPLv3).`;
  bands = await api("/api/bands");
  $("band").innerHTML = `<option value="">Hors plan de bandes</option>` + bands.map((b) => `<option>${b.name}</option>`).join("");
  renderStatus(status.hardware);
  connect("/ws/status", renderStatus);
  connect("/ws/spectrum", (s) => { lastByAntenna[s.antenna ?? ""] = s; if (hw) { hw.center_hz = s.center_hz; } renderSpectrum(); });
}

new ResizeObserver(() => chart && chart.resize()).observe($("spectrum"));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderSpectrum);
new MutationObserver(renderSpectrum).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
start().catch((err) => showError(err.message));
