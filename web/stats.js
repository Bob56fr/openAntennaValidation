/* Statistiques des pages de résultats (page locale et plugin WordPress).

   Les écarts entre antennes sont des médianes de fenêtres successives : deux fenêtres voisines
   se ressemblent (propagation, évanouissements lents), et un intervalle de confiance calculé
   comme si elles étaient indépendantes serait bien trop étroit. On rééchantillonne donc par
   blocs de fenêtres consécutives (bootstrap par blocs mobiles), la longueur des blocs suivant
   l'autocorrélation de la série. Les tirages sont reproductibles : mêmes données, même
   intervalle, à chaque affichage. */

(function (root) {
  "use strict";

  // Générateur pseudo-aléatoire reproductible (mulberry32).
  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function quantileSorted(s, p) {
    if (!s.length) return null;
    const i = (s.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return s[lo] + (s[hi] - s[lo]) * (i - lo);
  }

  function median(values) {
    return quantileSorted(Float64Array.from(values).sort(), 0.5);
  }

  /* Longueur de bloc : premier décalage où l'autocorrélation passe sous 0,2 (les fenêtres
     y deviennent à peu près indépendantes), au moins n^(1/3), au plus n/4. */
  function blockLength(x) {
    const n = x.length;
    if (n < 8) return 1;
    let mean = 0;
    for (const v of x) mean += v;
    mean /= n;
    let v0 = 0;
    for (const v of x) v0 += (v - mean) ** 2;
    const maxLag = Math.floor(n / 4);
    let lag = maxLag;
    if (v0 > 0) {
      for (let k = 1; k <= maxLag; k++) {
        let c = 0;
        for (let i = 0; i + k < n; i++) c += (x[i] - mean) * (x[i + k] - mean);
        if (c / v0 < 0.2) { lag = k; break; }
      }
    } else lag = 1;
    return Math.max(1, Math.min(maxLag, Math.max(lag, Math.round(Math.cbrt(n)))));
  }

  const cache = new Map();

  /* Médiane d'une série ordonnée dans le temps, avec son intervalle de confiance (bootstrap
     par blocs mobiles) et la p-valeur bilatérale de « médiane nulle ». Retourne null sans
     valeur. */
  function bootstrap(series, options) {
    const opts = Object.assign({ reps: 500, level: 0.95 }, options || {});
    const x = series.filter((v) => v != null && !Number.isNaN(v));
    const n = x.length;
    if (!n) return null;
    let sum = 0, sum2 = 0;
    for (const v of x) { sum += v; sum2 += v * v; }
    const key = `${n}|${sum}|${sum2}|${x[0]}|${x[n - 1]}|${opts.reps}|${opts.level}`;
    if (cache.has(key)) return cache.get(key);
    const med = median(x);
    const b = blockLength(x);
    const draw = rng(n * 2654435761 + Math.round(sum * 1000));
    const blocks = Math.ceil(n / b);
    const buf = new Float64Array(n);
    const meds = new Float64Array(opts.reps);
    let below = 0, above = 0;
    for (let k = 0; k < opts.reps; k++) {
      let j = 0;
      for (let q = 0; q < blocks; q++) {
        const start = Math.floor(draw() * (n - b + 1));
        for (let i = 0; i < b && j < n; i++) buf[j++] = x[start + i];
      }
      buf.sort();
      const m = n % 2 ? buf[(n - 1) / 2] : (buf[n / 2 - 1] + buf[n / 2]) / 2;
      meds[k] = m;
      if (m <= 0) below++;
      if (m >= 0) above++;
    }
    meds.sort();
    const alpha = 1 - opts.level;
    const lo = quantileSorted(meds, alpha / 2), hi = quantileSorted(meds, 1 - alpha / 2);
    const result = {
      n, med, block: b, lo, hi,
      ci: Math.max(med - lo, hi - med),  // demi-largeur affichée (« ±x dB »)
      p: Math.min(1, (2 * Math.min(below, above)) / opts.reps),
    };
    if (cache.size > 2000) cache.clear();
    cache.set(key, result);
    return result;
  }

  /* Correction de Holm : avec 45 paires, quelques écarts paraîtraient significatifs par hasard.
     Retourne, pour chaque p-valeur, si l'hypothèse « pas d'écart » est rejetée au niveau alpha. */
  function holm(pvalues, alpha) {
    const a = alpha == null ? 0.05 : alpha;
    const m = pvalues.length;
    const order = pvalues.map((p, i) => [p, i]).sort((u, v) => u[0] - v[0]);
    const out = pvalues.map(() => false);
    for (let k = 0; k < m; k++) {
      if (order[k][0] <= a / (m - k)) out[order[k][1]] = true;
      else break;
    }
    return out;
  }

  root.OAVStats = { bootstrap, holm, blockLength, median, rng };
  if (typeof module !== "undefined" && module.exports) module.exports = root.OAVStats;
})(typeof globalThis !== "undefined" ? globalThis : this);
