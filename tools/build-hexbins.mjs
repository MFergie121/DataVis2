// Build the inner-Melbourne venue hexbin layer for chart-15.
//
// Vega-Lite has no hexbin transform, and fixed-pixel hexagon point marks can't
// tile cleanly on an auto-fit projected map. So we precompute a true hexagonal
// tessellation here, offline, and ship it as a small TopoJSON that chart-15
// renders with `geoshape` under the same conicEqualArea projection as the other
// maps. The hexagons are real geographic polygons, so they tile seamlessly and
// stay aligned with the route layer at any container width.
//
// Two things this tool gets right by construction:
//   1. Aspect correction. 1° of longitude is only cos(φ₀) of a degree of
//      latitude on the ground at Melbourne, so we bin in (lon·cosφ₀, lat) space.
//      That makes the cells look like regular hexagons under the equal-area
//      projection instead of being horizontally squashed.
//   2. Winding order. d3/Vega render the *sphere complement* for wrongly-wound
//      outer rings (a solid fan under conicEqualArea). We emit each hexagon's
//      six vertices in clockwise order, so no rewind step is needed.
//
// Run:  npm run build:hexbins   (reproducible + idempotent)

import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { topology } = require("topojson-server");

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATA = join(ROOT, "src", "data");
const VENUES = join(DATA, "venues_web.csv");
const OUT = join(DATA, "venue_hexbins_melbourne.topojson");

// ---- tunables ---------------------------------------------------------------
// Inner-Melbourne frame (same bbox the old chart-15 spec filtered to).
const BBOX = { lonMin: 144.75, lonMax: 145.35, latMin: -37.92, latMax: -37.67 };
// Hex circumradius in latitude-degree-equivalents. ~0.0035° ≈ 350 m, giving a
// cell area comparable to the old ~500 m square bins. Tune after the in-browser
// screenshot if cells read too coarse / too fine.
const R = 0.005;
// Drop hexes with fewer than this many venues so the layer reads as density,
// not speckle. The old spec used >= 2.
const THRESHOLD = 2;
// Latitude the longitude axis is corrected against (centre of the frame).
const PHI0 = (-37.8 * Math.PI) / 180;
const COSPHI0 = Math.cos(PHI0);

// ---- hex lattice (d3-hexbin algorithm, run in corrected (X, lat) space) -----
const DX = R * Math.sqrt(3); // horizontal centre spacing
const DY = R * 1.5; // vertical (row) centre spacing

// Assign a corrected-space point to its hex cell; returns integer axial key.
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

// Centre of a hex cell, back in corrected (X, lat) space.
function centreOf(pi, pj) {
  return { cx: (pi + (pj & 1 ? 0.5 : 0)) * DX, cy: pj * DY };
}

// Six vertices of a pointy-top hexagon, in CLOCKWISE order (so the GeoJSON outer
// ring is wound the way d3-geo expects — see header note 2). lon = X / cosφ₀.
function hexRing(cx, cy) {
  const ring = [];
  // d3-hexbin's natural vertex order (angles 0..300°, x=sin, y=−cos) is
  // counter-clockwise in (east, north); iterate in reverse for clockwise.
  for (let k = 5; k >= 0; k--) {
    const a = (k * Math.PI) / 3;
    const X = cx + Math.sin(a) * R;
    const lat = cy - Math.cos(a) * R;
    ring.push([round6(X / COSPHI0), round6(lat)]);
  }
  ring.push(ring[0]); // close the ring
  return ring;
}

const round6 = (v) => Math.round(v * 1e6) / 1e6;

// ---- main -------------------------------------------------------------------
const rows = readFileSync(VENUES, "utf8").replace(/\r\n/g, "\n").trim().split("\n");
const header = rows[0].split(",");
const lonIdx = header.indexOf("Longitude");
const latIdx = header.indexOf("Latitude");
if (lonIdx < 0 || latIdx < 0) throw new Error("venues_web.csv: missing Longitude/Latitude columns");

const counts = new Map(); // "pi-pj" -> { pi, pj, count }
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
  `venue_hexbins_melbourne.topojson: ${features.length} hexes (count ${min}–${max}), ` +
    `from ${kept} venues in frame, R=${R}°, threshold=${THRESHOLD}`,
);
