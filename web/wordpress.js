"use strict";

/* Réglage de la publication sur le site WordPress (page Installation). */
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const mb = (b) => `${(b / 1048576).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Mo`;
  async function call(path, method = "GET", body) {
    const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401) { location.replace("/login"); throw new Error("Non connecté"); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Erreur ${res.status}`);
    return json;
  }
  function show(r) {
    $("wp-enabled").checked = r.enabled;
    if (document.activeElement !== $("wp-url")) $("wp-url").value = r.url || "";
    $("wp-token").value = "";
    $("wp-token").placeholder = r.token_set ? "jeton enregistré (laisser vide pour le garder)" : "créé dans l'administration du site, menu Antennes HF › Stations";
    const parts = [];
    if (!r.enabled) parts.push("Publication désactivée.");
    else if (r.error) parts.push(`Dernière tentative en échec : ${r.error}`);
    else if (r.last_success_ms) parts.push(`Dernier envoi accepté : ${fmt.format(r.last_success_ms)}.`);
    else parts.push("En attente du premier envoi.");
    if (r.limits) parts.push(`Station « ${r.limits.station} » de ${r.limits.operator} ; taille maximale d'une campagne : ${r.limits.package_max_bytes ? mb(r.limits.package_max_bytes) : "sans limite"}.`);
    $("wp-state").textContent = parts.join(" ");
  }
  $("wp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      show(await call("/api/wordpress", "PUT", { enabled: $("wp-enabled").checked, url: $("wp-url").value, token: $("wp-token").value }));
    } catch (err) { $("wp-state").textContent = err.message; }
  });
  $("wp-test").addEventListener("click", async () => {
    $("wp-state").textContent = "Test en cours…";
    try {
      const { limits } = await call("/api/wordpress/test", "POST");
      $("wp-state").textContent = `Liaison correcte : station « ${limits.station} » de ${limits.operator} ; stockage du site ${mb(limits.used_bytes)} sur ${mb(limits.quota_bytes)}.`;
    } catch (err) { $("wp-state").textContent = err.message; }
  });
  call("/api/wordpress").then(show).catch((err) => { $("wp-state").textContent = err.message; });
})();
