// Build the statewide Victoria venue hexbin layer for chart-11.
//
// Same approach as build-hexbins.mjs (inner-Melbourne, chart-15) but with a
// larger frame and coarser cells suited to the state-scale view.
//
// Run:  npm run build:hexbins:victoria

import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { topology } = require("topojson-server");

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATA = join(ROOT, "src", "data");
const VENUES = join(DATA, "venues_web.csv");
const OUT = join(DATA, "venue_hexbins_victoria.topojson");

// ---- tunables ---------------------------------------------------------------
// Full Victoria extent.
const BBOX = { lonMin: 140.8, lonMax: 150.1, latMin: -39.3, latMax: -33.9 };
// Hex circumradius in latitude-degree-equivalents. 0.08° ≈ 8 km, appropriate
// for a state-scale overview where Melbourne reads as dense and regional towns
// appear as isolated clusters. Increase R if the layer is too noisy; decrease
// if regional town clusters look too coarse.
const R = 0.08;
// Drop hexes with fewer than this many venues so sparse rural areas don't
// produce a uniform low-density wash. Tune alongside R.
const THRESHOLD = 3;
// Latitude the longitude axis is corrected against (state centroid latitude).
const PHI0 = (-36.5 * Math.PI) / 180;
const COSPHI0 = Math.cos(PHI0);

// ---- hex lattice (d3-hexbin algorithm, run in corrected (X, lat) space) -----
const DX = R * Math.sqrt(3);
const DY = R * 1.5;

function binOf(x, y) {
  let py = y / DY;
  let pj = Math.round(py);
  let px = x / DX - (pj & 1 ? 0.5 : 0);
  let pi = Math.round(px);
  const py1 = py - pj;
  if (Math.abs(py1) * 3 > 1) {
    const px1 = px - pi;
    const pi2 = pi + (px < pi ? -1 : 1) / 2;
    const pj2 = pj + (py < pj ? -1 : 1);
    const px2 = px - pi2;
    const py2 = py - pj2;
    if (px1 * px1 + py1 * py1 > px2 * px2 + py2 * py2) {
      pi = pi2 + (pj & 1 ? 1 : -1) / 2;
      pj = pj2;
    }
  }
  return { pi, pj };
}

function centreOf(pi, pj) {
  return { cx: (pi + (pj & 1 ? 0.5 : 0)) * DX, cy: pj * DY };
}

function hexRing(cx, cy) {
  const ring = [];
  for (let k = 5; k >= 0; k--) {
    const a = (k * Math.PI) / 3;
    const X = cx + Math.sin(a) * R;
    const lat = cy - Math.cos(a) * R;
    ring.push([round6(X / COSPHI0), round6(lat)]);
  }
  ring.push(ring[0]);
  return ring;
}

const round6 = (v) => Math.round(v * 1e6) / 1e6;

// ---- main -------------------------------------------------------------------
const rows = readFileSync(VENUES, "utf8").replace(/\r\n/g, "\n").trim().split("\n");
const header = rows[0].split(",");
const lonIdx = header.indexOf("Longitude");
const latIdx = header.indexOf("Latitude");
if (lonIdx < 0 || latIdx < 0) throw new Error("venues_web.csv: missing Longitude/Latitude columns");

const counts = new Map();
let kept = 0;
for (let i = 1; i < rows.length; i++) {
  const cells = rows[i].split(",");
  const lon = +cells[lonIdx];
  const lat = +cells[latIdx];
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
  if (lon < BBOX.lonMin || lon > BBOX.lonMax || lat < BBOX.latMin || lat > BBOX.latMax) continue;
  kept++;
  const { pi, pj } = binOf(lon * COSPHI0, lat);
  const key = pi + "-" + pj;
  const e = counts.get(key);
  if (e) e.count++;
  else counts.set(key, { pi, pj, count: 1 });
}

const features = [];
let min = Infinity;
let max = -Infinity;
for (const { pi, pj, count } of counts.values()) {
  if (count < THRESHOLD) continue;
  const { cx, cy } = centreOf(pi, pj);
  features.push({
    type: "Feature",
    properties: { count },
    geometry: { type: "Polygon", coordinates: [hexRing(cx, cy)] },
  });
  if (count < min) min = count;
  if (count > max) max = count;
}

const topo = topology({ venue_hexbins: { type: "FeatureCollection", features } }, 1e5);
writeFileSync(OUT, JSON.stringify(topo));

console.log(
  `venue_hexbins_victoria.topojson: ${features.length} hexes (count ${min}–${max}), ` +
    `from ${kept} venues in frame, R=${R}°, threshold=${THRESHOLD}`,
);
