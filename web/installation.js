"use strict";

/* Page Installation : catalogue d'antennes et chaînes de réception. */

const $ = (id) => document.getElementById(id);
const NF2 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const db2 = (v) => (v == null ? "—" : `${NF2.format(v).replace("-", "−")} dB`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
let data = null;
let editing = null; // id de l'antenne éditée, null pour une nouvelle
let chain = [];

async function api(path, method = "GET", body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) { location.replace("/login"); throw new Error("Non connecté"); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || `Erreur ${res.status}`);
  return json;
}
function showError(m) { $("error").hidden = !m; $("error").textContent = m || ""; }

function options(sel, entries, empty) {
  sel.innerHTML = (empty ? `<option value="">${empty}</option>` : "") + Object.entries(entries).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
}

function renderList() {
  const ants = data.antennas;
  $("count").textContent = ants.length ? `${ants.length} antenne${ants.length > 1 ? "s" : ""}` : "";
  $("ants").innerHTML = ants.length ? ants.map((a) => {
    const total = a.chain ? a.chain.elements.length : 0;
    return `<article class="ant" data-id="${a.id}" aria-current="${a.id === editing}" tabindex="0">
      <div class="ant-top"><span class="ant-name">${esc(a.name)}</span></div>
      <dl><dt>Type</dt><dd>${esc(a.type_label)}</dd><dt>Hauteur</dt><dd>${a.height_m != null ? `${a.height_m} m` : esc(a.height_note) || "—"}</dd>
      <dt>Polarisation</dt><dd>${esc(data.polarizations[a.polarization] || "—")}</dd><dt>Orientation</dt><dd>${a.azimuth_deg != null ? `${a.azimuth_deg}°` : esc(a.orientation_note) || "—"}</dd></dl>
      <div class="chain">${total ? `Chaîne : ${a.chain.elements.map((e) => esc(e.label)).join(" + ")}` : "Chaîne de réception non décrite"}</div>
    </article>`;
  }).join("") : `<p class="empty">Aucune antenne pour l'instant. Ajoutez celles que vous voulez comparer.</p>`;
}

function fillForm(a) {
  editing = a ? a.id : null;
  $("form-title").textContent = a ? `Modifier : ${a.name}` : "Nouvelle antenne";
  $("f-name").value = a?.name || ""; $("f-type").value = a?.type || "dipole"; $("f-model").value = a?.model || "";
  $("f-pol").value = a?.polarization || ""; $("f-height").value = a?.height_m ?? ""; $("f-height-note").value = a?.height_note || "";
  $("f-az").value = a?.azimuth_deg ?? ""; $("f-orient").value = a?.orientation_note || ""; $("f-bands").value = a?.bands_note || "";
  chain = a?.chain ? a.chain.elements.map((e) => ({ ...e })) : [{ kind: "coax", cable_type: "RG213", length_m: 20 }];
  $("btn-archive").hidden = !a;
  $("saved").hidden = true;
  renderChain();
  renderList();
}

function renderChain() {
  $("chain-sub").textContent = `Pertes calculées à ${NF2.format(data.center_hz / 1e6)} MHz (fréquence écoutée).`;
  $("chain").innerHTML = chain.map((e, i) => `<div class="chain-row" data-i="${i}">
      <select data-k="kind" aria-label="Maillon">${Object.entries(data.element_kinds).map(([k, v]) => `<option value="${k}"${k === e.kind ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>
      <select data-k="cable_type" aria-label="Câble" ${e.kind !== "coax" ? "disabled" : ""}><option value="">—</option>${Object.entries(data.cables).map(([k, v]) => `<option value="${k}"${k === e.cable_type ? " selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
      <input data-k="length_m" type="number" step="0.1" min="0" aria-label="Longueur" value="${e.length_m ?? ""}" ${e.kind !== "coax" ? "disabled" : ""}>
      <input data-k="db" type="number" step="0.1" aria-label="Perte saisie" placeholder="${e.kind === "coax" ? "calculée" : "à saisir"}" value="${e.db ?? ""}">
      <span class="loss" data-loss="${i}">—</span>
      <button class="linkbtn" type="button" data-del="${i}" aria-label="Retirer ce maillon">Retirer</button>
    </div>`).join("");
  computeLoss();
}

let lossTimer = null;
function computeLoss() {
  clearTimeout(lossTimer);
  lossTimer = setTimeout(async () => {
    if (!chain.length) { $("total").textContent = db2(0); return; }
    try {
      const r = await api("/api/installation/chain-loss", "POST", { elements: clean(chain), freq_hz: data.center_hz });
      r.elements_db.forEach((v, i) => { const el = document.querySelector(`[data-loss="${i}"]`); if (el) el.textContent = db2(v); });
      $("total").textContent = db2(r.total_db);
      showError("");
    } catch (e) { $("total").textContent = "—"; document.querySelectorAll("[data-loss]").forEach((el) => { el.textContent = "—"; }); }
  }, 250);
}

function clean(elements) {
  return elements.map((e) => {
    const out = { kind: e.kind, label: e.label || "" };
    if (e.kind === "coax" && e.cable_type) { out.cable_type = e.cable_type; out.length_m = e.length_m === "" || e.length_m == null ? null : Number(e.length_m); }
    if (e.db !== "" && e.db != null) out.db = Number(e.db);
    return out;
  });
}

$("chain").addEventListener("input", (ev) => {
  const row = ev.target.closest(".chain-row"); if (!row) return;
  const i = +row.dataset.i, k = ev.target.dataset.k;
  chain[i][k] = ev.target.value;
  if (k === "kind" || k === "cable_type" || k === "length_m") chain[i].label = "";
  if (k === "kind") { if (ev.target.value !== "coax") { delete chain[i].cable_type; delete chain[i].length_m; } renderChain(); return; }
  computeLoss();
});
$("chain").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-del]"); if (!b) return;
  chain.splice(+b.dataset.del, 1); renderChain();
});
$("btn-el").addEventListener("click", () => { chain.push({ kind: "balun", db: "" }); renderChain(); });
$("btn-add").addEventListener("click", () => fillForm(null));
$("ants").addEventListener("click", (ev) => {
  const card = ev.target.closest(".ant"); if (!card) return;
  fillForm(data.antennas.find((a) => a.id === +card.dataset.id));
});
$("ants").addEventListener("keydown", (ev) => { if (ev.key === "Enter") ev.target.closest(".ant")?.click(); });

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const num = (v) => (v === "" ? null : Number(v));
  const body = { name: $("f-name").value, type: $("f-type").value, model: $("f-model").value, polarization: $("f-pol").value,
    height_m: num($("f-height").value), height_note: $("f-height-note").value, azimuth_deg: num($("f-az").value),
    orientation_note: $("f-orient").value, bands_note: $("f-bands").value };
  try {
    data = editing ? await api(`/api/installation/antennas/${editing}`, "PUT", body) : await api("/api/installation/antennas", "POST", body);
    const id = editing ?? data.created_id;
    data = await api(`/api/installation/antennas/${id}/chain`, "POST", { elements: clean(chain) });
    fillForm(data.antennas.find((a) => a.id === id) || null);
    $("saved").hidden = false;
    showError("");
  } catch (e) { showError(e.message); }
});
$("btn-archive").addEventListener("click", async () => {
  if (editing == null) return;
  try { data = await api(`/api/installation/antennas/${editing}`, "DELETE"); fillForm(null); } catch (e) { showError(e.message); }
});

(async function start() {
  try {
    data = await api("/api/installation");
    options($("f-type"), data.antenna_types);
    options($("f-pol"), data.polarizations, "Non précisée");
    $("cables-warn").hidden = data.cables_verified;
    fillForm(null);
  } catch (e) { showError(e.message); }
})();
