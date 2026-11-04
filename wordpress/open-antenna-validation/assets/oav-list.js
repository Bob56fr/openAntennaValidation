/* Liste publique des campagnes, avec filtres : opérateur, bande, paire de types d'antennes
   (non ordonnée), mode de réception, SDR, période, campagnes qualifiées seulement. */
(function () {
  "use strict";
  const C = window.OAV_LIST || {};
  const box = document.getElementById("oav-list");
  if (!box) return;
  const fmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  const STATE = { done: "Terminée", running: "En cours", interrupted: "Interrompue", abandoned: "Abandonnée", preparation: "En préparation" };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const typeLabel = (code) => (C.types || {})[code] || code;
  const link = (uuid) => { const u = new URL(C.page || location.href, location.href); u.searchParams.set("campagne", uuid); return u.toString(); };
  const uniq = (a) => [...new Set(a.filter(Boolean))].sort((x, y) => x.localeCompare(y, "fr"));
  const state = { operator: "", band: "", t1: "", t2: "", reception: "", sdr: "", from: "", to: "", qualified: false };
  let items = [];

  function options(id, label, values, labelOf) {
    return `<div class="ctl"><label class="eyebrow" for="oav-${id}">${label}</label><select id="oav-${id}" data-k="${id}"><option value="">Tous</option>${values.map((v) => `<option value="${esc(v)}">${esc(labelOf ? labelOf(v) : v)}</option>`).join("")}</select></div>`;
  }

  function filtersHtml() {
    const types = uniq(items.flatMap((c) => c.types || []));
    return `<div class="oav-filters">
      ${options("operator", "Opérateur", uniq(items.map((c) => c.operator)))}
      ${options("band", "Bande", uniq(items.map((c) => c.band)))}
      ${options("t1", "Antenne", types, typeLabel)}
      ${options("t2", "contre", types, typeLabel)}
      ${options("reception", "Réception", ["switched", "simultaneous"], (v) => (v === "switched" ? "Commutée" : "Simultanée"))}
      ${options("sdr", "SDR", uniq(items.map((c) => c.sdr)))}
      <div class="ctl"><label class="eyebrow" for="oav-from">Du</label><input type="date" id="oav-from" data-k="from"></div>
      <div class="ctl"><label class="eyebrow" for="oav-to">Au</label><input type="date" id="oav-to" data-k="to"></div>
      <label class="oav-check"><input type="checkbox" data-k="qualified"> Qualifiées seulement</label>
    </div>
    <p class="desc" id="oav-count"></p>
    <div class="clist" id="oav-items"></div>`;
  }

  function matches(c) {
    if (state.operator && c.operator !== state.operator) return false;
    if (state.band && c.band !== state.band) return false;
    // Paire non ordonnée : la campagne contient les deux types choisis, dans n'importe quel ordre.
    const t = c.types || [];
    if (state.t1 && !t.includes(state.t1)) return false;
    if (state.t2 && !t.includes(state.t2)) return false;
    if (state.reception && c.reception_mode !== state.reception) return false;
    if (state.sdr && c.sdr !== state.sdr) return false;
    if (state.qualified && !c.qualified) return false;
    const end = c.end_ms || Date.now();
    if (state.from && end < Date.parse(state.from)) return false;
    if (state.to && (c.start_ms || 0) > Date.parse(state.to) + 864e5) return false;
    return true;
  }

  function render() {
    const shown = items.filter(matches);
    const demoOnly = items.length && items.every((c) => c.demo);
    document.getElementById("oav-count").textContent = (shown.length ? `${shown.length} campagne${shown.length > 1 ? "s" : ""}` : "Aucune campagne ne correspond aux filtres.")
      + (demoOnly ? " Campagnes de démonstration, données simulées : elles disparaîtront dès la première vraie campagne publiée." : "");
    document.getElementById("oav-items").innerHTML = shown.map((c) => {
      const days = c.end_ms && c.start_ms ? Math.round((c.end_ms - c.start_ms) / 864e5) : null;
      const types = (c.type_labels || (c.types || []).map(typeLabel)).join(", ");
      return `<a class="camp" href="${esc(link(c.uuid))}">
        <div><div class="b">${esc(c.band)}</div><div class="f">${(c.low_hz / 1e6).toFixed(3).replace(".", ",")}–${(c.high_hz / 1e6).toFixed(3).replace(".", ",")}</div></div>
        <div style="display:grid;gap:4px;min-width:0">
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><h3>${esc(c.title)}</h3>${c.demo ? '<span class="tag demo">Démonstration</span>' : ""}${c.aa ? '<span class="tag">A/A</span>' : ""}<span class="tag">${STATE[c.state] || esc(c.state)}</span>${c.qualified ? '<span class="tag">Qualifiée</span>' : ""}</div>
          <div class="meta"><span>${esc(c.operator)}</span><span>${c.n_channels} antennes${types ? ` : ${esc(types)}` : ""}</span><span>${c.start_ms ? fmt.format(c.start_ms) : "—"}${days ? ` · ${days} jour${days > 1 ? "s" : ""}` : ""}</span><span>${c.reception_mode === "switched" ? "Commutée" : "Simultanée"} · ${esc(c.sdr)}</span></div>
          ${c.comment ? `<div class="meta">${esc(c.comment)}</div>` : ""}
        </div>
        <span class="linkbtn">Voir les résultats</span>
      </a>`;
    }).join("");
  }

  fetch(C.api + "campaigns").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Erreur ${r.status}`)))).then((data) => {
    items = data.campaigns || [];
    C.types = Object.assign({}, data.types || {}, C.types || {});
    if (!items.length) { box.innerHTML = '<p class="desc">Aucune campagne publiée pour le moment.</p>'; return; }
    box.innerHTML = filtersHtml();
    box.addEventListener("input", (e) => {
      const k = e.target.dataset.k; if (!k) return;
      state[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      render();
    });
    render();
  }).catch((e) => { box.innerHTML = `<p class="desc">Liste indisponible : ${esc(e.message)}</p>`; });
})();
