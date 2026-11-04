/* État des stations : dernier envoi, campagne en cours, état du récepteur. */
(function () {
  "use strict";
  const C = window.OAV_STATIONS || {};
  const box = document.getElementById("oav-stations");
  if (!box) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const STATE = { running: ["En marche", "ok"], absent: ["SDR absent", "warn"], error: ["En erreur", "bad"], starting: ["Démarrage", "warn"], stopped: ["Arrêtée", ""] };
  const ago = (ms) => {
    const m = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (m < 2) return "à l'instant";
    if (m < 60) return `il y a ${m} min`;
    const h = Math.round(m / 60);
    return h < 48 ? `il y a ${h} h` : `il y a ${Math.round(h / 24)} jours`;
  };
  fetch(C.api + "stations").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Erreur ${r.status}`)))).then((data) => {
    const rows = data.stations || [];
    if (!rows.length) { box.innerHTML = '<p class="desc">Aucune station n\'a encore envoyé de données.</p>'; return; }
    box.innerHTML = `<div class="card tbl-wrap"><table><thead><tr><th>Station</th><th>Opérateur</th><th>État</th><th>Dernier envoi</th><th>Campagne en cours</th></tr></thead><tbody>${rows.map((s) => {
      // Sans nouvelles depuis plus d'une heure : l'état transmis n'est plus sûr.
      const stale = Date.now() - s.last_seen_ms > 3600e3;
      const [word, cls] = stale ? ["Sans nouvelles", "warn"] : (STATE[s.state] || [s.state || "—", ""]);
      return `<tr><td>${esc(s.name)}${s.sdr ? `<br><span class="desc">${esc(s.sdr)}</span>` : ""}</td><td>${esc(s.operator)} ${esc(s.locator)}</td>
        <td><span class="pill ${cls}"><span class="dot"></span>${esc(word)}</span></td><td>${ago(s.last_seen_ms)}</td>
        <td>${s.campaign ? esc(s.campaign.title) : "—"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }).catch((e) => { box.innerHTML = `<p class="desc">État indisponible : ${esc(e.message)}</p>`; });
})();
