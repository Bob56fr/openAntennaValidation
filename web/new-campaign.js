"use strict";

/* Assistant de nouvelle campagne : réception commutée ou simultanée, fréquences imposées ou
   bande automatique. */

const $ = (id) => document.getElementById(id);
const NF1 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const CLASS = { carrier: "Porteuse / CW", digital: "Numérique étroit", ssb: "SSB (voix)", am_fm: "AM / FM" };
const ICON = {
  ok: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--good)"/><path d="M4.6 8.2l2.2 2.2 4.6-4.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  warn: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="var(--warning)"/><path d="M8 6v4" stroke="#0b0b0b" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="12" r="1" fill="#0b0b0b"/></svg>`,
  bad: `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--critical)"/><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>`,
};
let setup = null;
let freqs = [];
let mode = "fixed";
let reception = "switched";
// Mode simultané : SDR retenus (numéro de série, antenne), étalonnages de l'ensemble, test de charge.
let sim = [];
let simProfiles = null;
let loadTimer = null;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) { location.replace("/login"); throw new Error("Non connecté"); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || `Erreur ${res.status}`);
  return json;
}
function showError(m) { $("error").hidden = !m; $("error").textContent = m || ""; if (m) $("error").scrollIntoView({ block: "center" }); }

const widthOf = (f) => Number(f.width_hz) || setup.class_width_hz[f.class] || 200;
const bytes = (n) => (n > 1e9 ? `${NF1.format(n / 1e9)} Go` : `${NF0.format(n / 1e6)} Mo`);

function renderFreqs() {
  $("freqs").innerHTML = freqs.map((f, i) => `<div class="freq-row" data-i="${i}">
      <input data-k="khz" type="number" step="0.1" min="1000" max="30000" value="${f.khz ?? ""}" aria-label="Fréquence en kHz">
      <select data-k="class" aria-label="Classe">${Object.entries(CLASS).map(([k, v]) => `<option value="${k}"${k === f.class ? " selected" : ""}>${v}</option>`).join("")}</select>
      <input data-k="width_hz" type="number" step="10" min="10" max="20000" placeholder="${setup.class_width_hz[f.class]}" value="${f.width_hz ?? ""}" aria-label="Largeur en Hz">
      <button class="linkbtn" type="button" data-del="${i}">Retirer</button></div>`).join("");
  update();
}

function autoRange() {
  const lo = Number($("fmin").value) * 1000, hi = Number($("fmax").value) * 1000;
  return lo > 0 && hi > lo ? { lo, hi } : null;
}

function selected() { return freqs.filter((f) => Number(f.khz) > 0).map((f) => ({ hz: Number(f.khz) * 1000, w: widthOf(f), class: f.class })); }

const running = () => (setup.receiver.devices || []).filter((d) => d.state === "running");
const used = () => sim.filter((c) => c.use);
// Bande utile : celle du SDR (commuté), la plus étroite des SDR retenus (simultané).
function usableBw() {
  if (reception === "switched") return setup.receiver.bandwidth_hz || 0;
  const bws = used().map((c) => (running().find((d) => d.serial === c.serial) || {}).bandwidth_hz || 0);
  return bws.length ? Math.min(...bws) : 0;
}

function renderSdrs() {
  const opts = (sel) => setup.antennas.map((a) => `<option value="${a.id}"${a.id === sel ? " selected" : ""}>${esc(a.name)} (${esc(a.type_label)})</option>`).join("");
  const aa = $("aa").checked;
  $("sdrs").innerHTML = `<div class="sdr-row head"><span></span><span>SDR</span><span class="in">Entrée</span><span>Antenne</span></div>` +
    sim.map((c, i) => {
      const d = running().find((x) => x.serial === c.serial) || {};
      return `<div class="sdr-row${c.use ? "" : " off"}" data-i="${i}"><input type="checkbox" data-k="use"${c.use ? " checked" : ""} aria-label="Utiliser ce SDR">
        <span>${esc(d.model || "SDR")} <span class="mono">n° ${esc(c.serial)}</span></span><span class="in mono">${esc(d.input || "—")}</span>
        <select data-k="antenna" aria-label="Antenne de ce SDR"${!c.use || (aa && used()[0] !== c) ? " disabled" : ""}>${opts(c.antenna)}</select></div>`;
    }).join("");
  const keep = $("sim-ref").value;
  $("sim-ref").innerHTML = used().map((c, k) => `<option value="${k}">${esc(c.serial)} : ${esc((setup.antennas.find((a) => a.id === c.antenna) || {}).name || "")}</option>`).join("");
  if (keep && Number(keep) < used().length) $("sim-ref").value = keep;
}

async function loadProfiles() {
  simProfiles = null;
  const serials = used().map((c) => c.serial);
  if (reception === "simultaneous" && serials.length >= 2) {
    try { simProfiles = await api(`/api/campaign/profiles?serials=${encodeURIComponent(serials.join(","))}`); } catch (e) { showError(e.message); }
  }
  update();
}

function renderLoad(t) {
  const serials = used().map((c) => c.serial);
  const valid = t && t.state === "done" && Date.now() - t.ended_ms < 3600_000 && serials.every((s) => t.serials.includes(s));
  $("load-accept-row").hidden = !(valid && t.verdict === "limit");
  if (t && t.state === "running") {
    $("load-state").innerHTML = `Test en cours sur ${t.serials.length} SDR (${NF0.format(t.seconds)} s)…`;
    $("load-go").disabled = true;
    return;
  }
  $("load-go").disabled = serials.length < 2;
  if (!valid) {
    $("load-state").innerHTML = `${ICON.warn} Test de charge à faire avant le lancement : chaque SDR mesure ${setup.max_signals} signaux pendant ${NF0.format(setup.load_test_s)} s, et le programme vérifie que l'ordinateur suit.`;
    return;
  }
  const icon = { ok: ICON.ok, limit: ICON.warn, refuse: ICON.bad }[t.verdict];
  const detail = Object.entries(t.loads).map(([s, v]) => `${esc(s)} ${v == null ? "—" : `${Math.round(v * 100)} %`}`).join(", ");
  $("load-state").innerHTML = `${icon} ${esc(t.message)} <span style="font-family:var(--font-mono);font-size:12.5px">(${detail} ; ${t.cores} cœurs)</span>`;
}

async function pollLoad() {
  clearTimeout(loadTimer);
  try {
    const { load_test: t } = await api("/api/campaign/loadtest");
    setup.receiver.load_test = t;
    renderLoad(t);
    if (t && t.state === "running") loadTimer = setTimeout(pollLoad, 1000);
  } catch (e) { showError(e.message); }
}

function update() {
  const rx = setup.receiver;
  const bw = usableBw();
  const simul = reception === "simultaneous";
  document.querySelectorAll("#reception button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === reception)));
  $("eyebrow").textContent = simul ? "Campagne · réception simultanée" : "Campagne · réception commutée";
  $("sw-ants").hidden = simul; $("sim-ants").hidden = !simul;
  $("rate-ctl").hidden = simul; $("load-block").hidden = !simul;
  $("aa-label").textContent = simul
    ? "Campagne A/A : la même antenne sur tous les SDR, par un splitter. L'écart vrai est nul : la campagne étalonne l'ensemble (correction de gain de chaque SDR et marges par classe de signal)."
    : "Campagne A/A : la même antenne sur les deux entrées, par un splitter. L'écart vrai est nul ; ce qui reste mesure l'erreur de la méthode et donne les marges par classe de signal au profil de qualification.";
  if (simul) renderLoad(rx.load_test);
  const list = selected();
  const cov = $("coverage");
  $("auto-fields").hidden = mode !== "auto";
  $("fixed-fields").hidden = mode !== "fixed";
  document.querySelectorAll("#mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === mode)));
  if (mode === "auto") {
    const r = autoRange();
    if (r && bw) {
      const center = (r.lo + r.hi) / 2, fits = r.hi - r.lo + 10_000 <= bw;
      const pos = (hz) => `${((hz - (center - bw / 2)) / bw) * 100}%`;
      cov.innerHTML = `<i style="left:${pos(r.lo)};width:${((r.hi - r.lo) / bw) * 100}%;opacity:.35"></i><span class="dc" style="left:50%"></span>`;
      $("freq-note").textContent = fits
        ? `Bande de ${NF1.format((r.hi - r.lo) / 1000)} kHz. Le programme relève la bande pendant ${NF0.format(setup.survey_s)} s, choisit jusqu'à ${$("nmax").value || setup.max_signals} signaux (enveloppe constante d'abord, puis stabilité, puis SNR) et un point de bruit, puis réévalue la bande toutes les ${NF0.format(setup.reevaluate_s / 60)} min.`
        : `La bande fait ${NF1.format((r.hi - r.lo) / 1000)} kHz : elle doit tenir dans ${NF0.format((bw - 10_000) / 1000)} kHz (bande utile moins le voisinage du bruit). Faites plusieurs campagnes.`;
      $("freq-note").style.color = fits ? "" : "var(--crit-ink)";
      renderQual(center);
    } else { cov.innerHTML = ""; $("freq-note").textContent = "Indiquez Fmin et Fmax."; renderQual(null); }
  } else if (list.length && bw) {
    const low = Math.min(...list.map((f) => f.hz - f.w / 2)), high = Math.max(...list.map((f) => f.hz + f.w / 2));
    const center = (low + high) / 2, span = high - low + 10_000;
    const pos = (hz) => `${((hz - (center - bw / 2)) / bw) * 100}%`;
    cov.innerHTML = list.map((f) => `<i style="left:${pos(f.hz - f.w / 2)};width:${(Math.max(f.w, bw / 400) / bw) * 100}%"></i>`).join("") + `<span class="dc" style="left:50%"></span>`;
    const fits = span <= bw;
    $("freq-note").textContent = fits
      ? `${list.length} fréquence${list.length > 1 ? "s" : ""} sur ${NF1.format((high - low) / 1000)} kHz, dans la bande utile de ${NF0.format(bw / 1000)} kHz. Le SDR sera accordé pour éloigner toutes les fréquences de son pic central.`
      : `Les fréquences couvrent ${NF1.format((high - low) / 1000)} kHz : il faut qu'elles tiennent dans ${NF0.format((bw - 10_000) / 1000)} kHz (bande utile moins le voisinage du bruit). Faites plusieurs campagnes.`;
    $("freq-note").style.color = fits ? "" : "var(--crit-ink)";
    renderQual(center);
  } else {
    cov.innerHTML = ""; $("freq-note").textContent = "Ajoutez au moins une fréquence."; renderQual(null);
  }
  // Plan : fenêtre, période, volume.
  const dur = Number($("dur").value) * Number($("unit").value);
  const win = (setup.windows.find(([h]) => dur / 3600 <= h) || setup.windows[setup.windows.length - 1])[1];
  const period = Math.min(setup.period_s, win / 3);
  const n = mode === "auto" ? (Number($("nmax").value) || setup.max_signals) + 1 : Math.max(1, list.length);
  const nch = simul ? Math.max(2, used().length) : 2;
  const volume = (dur / period) * n * nch * 90 + (dur / win) * n * (nch + 1) * 260;
  const need = Math.max(volume * 1.2, volume + 2 ** 30);
  const rate = Number($("rate").value) || setup.rate_hz;
  $("plan").innerHTML = `<dt>Fenêtre d'agrégation</dt><dd>${win >= 60 ? `${win / 60} min` : `${win} s`}</dd>
    <dt>Une mesure par signal et par ${simul ? "SDR" : "entrée"} toutes les</dt><dd>${NF1.format(period).replace(",0", "")} s</dd>
    ${simul ? `<dt>Réception</dt><dd>${nch} SDR en parallèle, sans commutation</dd>` : `<dt>Durée sur chaque entrée</dt><dd>${NF1.format(1000 / (2 * rate))} ms à ${rate} Hz</dd>`}
    <dt>Volume estimé</dt><dd>${bytes(volume)} (${bytes(setup.disk_free_bytes)} libres${setup.disk_free_bytes < need ? ` : <b style="color:var(--crit-ink)">espace insuffisant</b>` : ""})</dd>`;
  renderPublish(dur, win, n, nch);
  const aa = $("aa").checked;
  $("ant-b").disabled = aa;
  if (aa) $("ant-b").value = $("ant-a").value;
  $("ant-note").textContent = !aa ? "" : simul
    ? "Branchez la même antenne sur tous les SDR par un splitter et des câbles identiques."
    : "Branchez la même antenne sur A et B par un splitter et deux câbles identiques.";
}

// Résolution publiée sur le site : la station élargit la fenêtre si la campagne dépasse la
// taille de paquet du site (la base locale garde sa fenêtre).
let publishTimer = null;
function renderPublish(dur, win, n, nch) {
  const wp = setup.wordpress || {};
  $("publish-block").hidden = !wp.enabled;
  if (!wp.enabled) return;
  clearTimeout(publishTimer);
  publishTimer = setTimeout(async () => {
    try {
      const e = await api(`/api/wordpress/estimate?duration_s=${dur}&n_signals=${n}&n_channels=${nch}&window_s=${win}`);
      if (e.error) { $("publish-note").textContent = `Site injoignable pour l'instant (${e.error}) : la campagne sera envoyée quand il répondra.`; return; }
      const w = (ms) => (ms >= 60000 ? `${ms / 60000} min` : `${ms / 1000} s`);
      $("publish-note").textContent = `${e.window_ms > e.local_window_ms ? `Publiée par fenêtres de ${w(e.window_ms)} (${w(e.local_window_ms)} en local) pour tenir dans la taille autorisée par le site` : `Publiée par fenêtres de ${w(e.window_ms)}, comme en local`} : environ ${bytes(e.bytes)}${e.limit_bytes ? ` sur ${bytes(e.limit_bytes)} autorisés` : ""}.${e.full ? " Le stockage du site est plein : les envois attendront." : ""}`;
    } catch (err) { $("publish-note").textContent = err.message; }
  }, 300);
}

function renderQual(center) {
  const band = center ? (setup.bands.find((b) => b.low_hz <= center && center <= b.high_hz) || {}).name : null;
  const simul = reception === "simultaneous";
  const p = band ? (simul ? (simProfiles || {})[band] : setup.profiles[band]) : null;
  $("accept-row").hidden = !p;
  if (!center) { $("qual").innerHTML = ""; return; }
  if (simul) {
    if (!p) {
      $("qual").innerHTML = `<p class="note">${ICON.warn} Aucun étalonnage de cet ensemble de SDR${band ? ` sur ${band}` : ""}, dans cet ordre. ${$("aa").checked ? "Cette campagne A/A le produira." : "Les écarts de gain entre SDR ne seront pas corrigés et la campagne sera marquée « non qualifiée » : faites d'abord une campagne A/A (même antenne sur tous les SDR)."}</p>`;
      return;
    }
    const verd = p.verdicts.map((v) => `<span>${ICON[v.state] || ""}${esc(v.label)}${v.margin_db != null ? ` : ±${NF1.format(v.margin_db)} dB` : ""}</span>`).join("");
    $("qual").innerHTML = `<p class="note">Étalonnage A/A de ces SDR sur ${esc(band)} : correction de gain de chaque SDR et marges par classe.</p><div class="verd">${verd}</div>`;
    return;
  }
  if (!p) {
    $("qual").innerHTML = `<p class="note">${ICON.warn} Aucun profil de qualification pour ce SDR${band ? ` sur ${band}` : ""}. La campagne utilisera une neutralisation prudente de ${NF1.format(setup.default_blanking_ms)} ms après chaque bascule, et sera marquée « non qualifiée » : aucun écart ne sera affirmé sans marge connue. <a href="/qualification">Qualifier le SDR</a>.</p>`;
    return;
  }
  const verd = p.verdicts.map((v) => `<span>${ICON[v.state] || ""}${esc(v.label)}${v.margin_db != null ? ` : ±${NF1.format(v.margin_db)} dB` : ""}</span>`).join("");
  $("qual").innerHTML = `<p class="note">Profil de qualification sur ${esc(band)} : neutralisation ${NF1.format(p.blanking_ms)} ms, temps utile ${Math.round(p.useful * 100)} % à ${p.switch_rate_hz} Hz${p.accepted ? "" : " — <b>pas encore accepté</b> (onglet Qualification)"}.</p>
    <div class="verd">${verd}</div>
    ${p.margins ? "" : `<p class="note">Marges en dB inconnues : faites une campagne A/A pour les mesurer.</p>`}`;
}

$("freqs").addEventListener("input", (e) => {
  const row = e.target.closest(".freq-row"); if (!row) return;
  freqs[+row.dataset.i][e.target.dataset.k] = e.target.value;
  if (e.target.dataset.k === "class") renderFreqs(); else update();
});
$("freqs").addEventListener("click", (e) => { const b = e.target.closest("[data-del]"); if (b) { freqs.splice(+b.dataset.del, 1); renderFreqs(); } });
$("add-freq").addEventListener("click", () => { const last = freqs[freqs.length - 1]; freqs.push({ khz: last ? (Number(last.khz) + 5).toFixed(1) : "", class: "carrier" }); renderFreqs(); });
$("band").addEventListener("change", () => {
  const b = setup.bands.find((x) => x.name === $("band").value);
  if (!b) return;
  fillRange(b);
  if (!selected().length) { freqs = [{ khz: ((b.low_hz + b.high_hz) / 2 / 1000).toFixed(1), class: "carrier" }]; renderFreqs(); }
  update();
});
// Bande proposée : la bande entière si elle tient dans la bande utile, sinon autour du centre écouté.
function fillRange(b) {
  const bw = (setup.receiver.bandwidth_hz || 0) - 10_000;
  let lo = b.low_hz, hi = b.high_hz;
  if (bw > 0 && hi - lo > bw) {
    const c = Math.min(Math.max(setup.receiver.center_hz, lo + bw / 2), hi - bw / 2);
    lo = c - bw / 2; hi = c + bw / 2;
  }
  $("fmin").value = (lo / 1000).toFixed(1); $("fmax").value = (hi / 1000).toFixed(1);
}
$("mode").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { mode = b.dataset.v; update(); } });
$("reception").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  reception = b.dataset.v;
  checkBlockers(); renderSdrs(); loadProfiles();
});
$("sdrs").addEventListener("input", (e) => {
  const row = e.target.closest(".sdr-row"); if (!row || row.dataset.i === undefined) return;
  const c = sim[+row.dataset.i];
  if (e.target.dataset.k === "use") c.use = e.target.checked; else c.antenna = Number(e.target.value);
  if ($("aa").checked && used().length) used().forEach((x) => { x.antenna = used()[0].antenna; });
  renderSdrs(); loadProfiles();
});
$("aa").addEventListener("input", () => {
  if ($("aa").checked && used().length) used().forEach((x) => { x.antenna = used()[0].antenna; });
  renderSdrs(); update();
});
$("load-go").addEventListener("click", async () => {
  showError("");
  try {
    const t = await api("/api/campaign/loadtest", { serials: used().map((c) => c.serial) });
    setup.receiver.load_test = t; renderLoad(t);
    loadTimer = setTimeout(pollLoad, 1000);
  } catch (e) { showError(e.message); }
});
["fmin", "fmax", "nmax"].forEach((id) => $(id).addEventListener("input", update));
["dur", "unit", "rate", "aa", "ant-a"].forEach((id) => $(id).addEventListener("input", update));

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const aa = $("aa").checked;
  const r = autoRange();
  const body = {
    title: $("title").value, mode,
    low_hz: mode === "auto" && r ? r.lo : null, high_hz: mode === "auto" && r ? r.hi : null,
    max_signals: Number($("nmax").value) || null,
    frequencies: mode === "auto" ? [] : selected().map((f, i) => ({ freq_hz: f.hz, class: f.class, width_hz: Number(freqs.filter((x) => Number(x.khz) > 0)[i].width_hz) || null })),
    reception,
    channels: reception === "simultaneous"
      ? used().map((c) => ({ antenna_id: c.antenna, serial: c.serial }))
      : [{ antenna_id: Number($("ant-a").value) }, { antenna_id: Number(aa ? $("ant-a").value : $("ant-b").value) }],
    reference: Number(reception === "simultaneous" ? $("sim-ref").value : $("ref").value),
    duration_s: Number($("dur").value) * Number($("unit").value),
    rate_hz: reception === "simultaneous" ? null : Number($("rate").value) || null, aa,
    accept_limits: $("accept").checked, accept_load: $("load-accept").checked,
    visibility: setup.wordpress && setup.wordpress.enabled && $("publish").checked ? "public" : "private",
  };
  try {
    $("go").disabled = true;
    const r = await api("/api/campaign", body);
    location.href = `/campaign?id=${encodeURIComponent(r.uuid)}`;
  } catch (err) { showError(err.message); $("go").disabled = false; }
});

(async function start() {
  try {
    setup = await api("/api/campaign/setup");
    const rx = setup.receiver;
    $("meta").innerHTML = rx.device ? `<span><b>${esc(rx.device.model)}</b> n° ${esc(rx.device.serial)}</span><span>Entrées ${(rx.antennas || []).join(", ")}</span><span>Bande utile ${NF0.format((rx.bandwidth_hz || 0) / 1000)} kHz</span>` : "";
    const many = running().length >= 2;
    $("reception").querySelector('[data-v="simultaneous"]').disabled = !many;
    $("rx-note").textContent = many
      ? `${running().length} SDR en marche. En réception simultanée, chaque antenne a son SDR : pas de commutation, toutes les classes de signal sont mesurables, mais les SDR doivent être étalonnés ensemble (campagne A/A) et l'ordinateur doit suivre (test de charge).`
      : "Un seul SDR en marche : la réception simultanée demande un SDR par antenne (liste « others » de la section [hardware] de la configuration).";
    if (many && ((rx.antennas || []).length < 2 || new URLSearchParams(location.search).get("reception") === "simultaneous")) reception = "simultaneous";
    sim = running().map((d, i) => ({ serial: d.serial, use: true, antenna: (setup.antennas[i] || setup.antennas[0] || {}).id }));
    checkBlockers();
    const opts = setup.antennas.map((a) => `<option value="${a.id}">${esc(a.name)} (${esc(a.type_label)})</option>`).join("");
    $("ant-a").innerHTML = opts; $("ant-b").innerHTML = opts;
    if (setup.antennas.length > 1) $("ant-b").selectedIndex = 1;
    $("rate").value = setup.rate_hz;
    $("band").innerHTML = `<option value="">Choisir…</option>` + setup.bands.map((b) => `<option>${esc(b.name)}</option>`).join("");
    const center = rx.center_hz;
    const band = setup.bands.find((b) => b.low_hz <= center && center <= b.high_hz);
    if (band) { $("band").value = band.name; fillRange(band); }
    $("nmax").value = setup.max_signals;
    freqs = [{ khz: (center / 1000).toFixed(1), class: "carrier" }];
    renderSdrs();
    renderFreqs();
    loadProfiles();
  } catch (e) { showError(e.message); }
})();

function checkBlockers() {
  const rx = setup.receiver;
  const blockers = [];
  if (setup.campaign) blockers.push(`Une campagne est déjà en cours : <a href="/campaign?id=${setup.campaign.uuid}">${esc(setup.campaign.title)}</a>.`);
  if (reception === "switched") {
    if (rx.state !== "running") blockers.push("Le récepteur n'est pas en marche : branchez le SDR pour lancer une campagne.");
    if ((rx.antennas || []).length < 2 && rx.state === "running") blockers.push("Ce SDR n'a qu'une entrée : le mode commuté demande un SDR à plusieurs entrées.");
  } else if (running().length < 2) blockers.push("La réception simultanée demande au moins deux SDR en marche.");
  if (setup.antennas.length < 1) blockers.push(`Votre catalogue d'antennes est vide : <a href="/installation">décrivez vos antennes</a> d'abord.`);
  $("blocker").hidden = !blockers.length; $("blocker").innerHTML = blockers.join("<br>");
  $("go").disabled = blockers.length > 0;
}
