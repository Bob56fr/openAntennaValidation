// Exercise web/stats.js; prints JSON read by tests/test_stats_js.py.
"use strict";
const path = require("path");
const S = require(path.join(__dirname, "..", "..", "web", "stats.js"));

const r = S.rng(42);
const gauss = () => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());

// Independent windows, no offset.
const iid = Array.from({ length: 400 }, () => gauss());
// Correlated windows (slow fading, AR(1) 0.9), true offset +0.3 dB.
const ar = [];
let e = 0;
for (let i = 0; i < 400; i++) { e = 0.9 * e + Math.sqrt(1 - 0.81) * gauss(); ar.push(0.3 + e); }
const naive = (x) => {
  const m = x.reduce((s, v) => s + v, 0) / x.length;
  const sd = Math.sqrt(x.reduce((s, v) => s + (v - m) ** 2, 0) / (x.length - 1));
  return 1.96 * 1.2533 * sd / Math.sqrt(x.length);  // CI of a median, independent samples
};
// Clear offset: +3 dB on noise of 1 dB.
const clear = iid.map((v) => v + 3);

console.log(JSON.stringify({
  iid: S.bootstrap(iid), iidNaive: naive(iid),
  ar: S.bootstrap(ar), arNaive: naive(ar),
  clear: S.bootstrap(clear),
  again: S.bootstrap(ar.slice()),
  withNulls: S.bootstrap([null, 1, NaN, 2, 3]),
  empty: S.bootstrap([null]),
  holm: S.holm([0.01, 0.04, 0.03, 0.005]),
  holmNone: S.holm([0.2, 0.5]),
}));
