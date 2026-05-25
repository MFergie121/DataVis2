// Validation harness for the FIT2179 Data Visualisation 2 page.
//
// Asserts EXTERNAL behaviour, not implementation detail:
//   1. every src/specs/*.vl.json is valid JSON and compiles with the Vega-Lite compiler;
//   2. every data file a spec references exists in src/data/;
//   3. CSV row counts and the SA2 join coverage hold, and no NaN/Infinity strings slipped in.
//
// Run:  npm install && npm run validate
// Exits non-zero on any failure, so it can gate a deploy.

import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, basename, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const vl = require("vega-lite");

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SPECS_DIR = join(ROOT, "src", "specs");
const DATA_DIR = join(ROOT, "src", "data");

let failures = 0;
const fail = (msg) => { console.error("  ✗ " + msg); failures++; };
const pass = (msg) => console.log("  ✓ " + msg);

// Expected data-row counts (rows excluding header; geometries for the TopoJSON).
const EXPECTED_ROWS = {
  "venues_web.csv": 23658,
  "sa2_summary_web.csv": 524,
  "chart_scatter_sa2.csv": 524,
  "transport_stops_web.csv": 31145, // every PT stop within Victoria's extent (chart-11 stop mesh; 25 interstate/border stops clipped)
  "melbourne_pt_patronage.csv": 290, // §4 chart-19: DOT Victoria monthly metro PT patronage, long format, Jan 2018 – latest (one missing month×mode cell dropped)
};
// Charts 04/05/07/08/09/10 were cut in the "Close without the Cigar" restructure
// (issue #9); their four orphaned CSVs were deleted and dropped from the map above.
const EXPECTED_CHART_COUNT = 10; // post-§1 prune: manifest ↔ mounts ↔ spec files must all agree on this.
const EXPECTED_GEOMETRIES = 522; // sa2_summary_simplified.topojson (2 of the 524 SA2s are non-spatial)
const EXPECTED_ROUTE_FEATURES = 54; // pt_routes_simplified.topojson (train+tram routes, variants dissolved)
// Melbourne close-up support files: routes/base clipped to the inner-Melbourne frame
// (now used by chart-15's density-over-network overlay).
const MELBOURNE_TOPO = {
  "pt_routes_melbourne.topojson": { object: "public_transport_lines", features: 53 },
  "sa2_melbourne.topojson": { object: "sa2_summary", features: 224 },
};
const BAD_TOKENS = new Set(["NaN", "nan", "Infinity", "-Infinity", "inf", "-inf"]);

// ---- 1. Specs: valid JSON + compile clean ----------------------------------
console.log("\n[1] Vega-Lite specs compile");
const specFiles = readdirSync(SPECS_DIR).filter((f) => f.endsWith(".vl.json"));
if (specFiles.length === 0) fail("no .vl.json specs found in src/specs/");
const referencedData = new Set();

for (const file of specFiles) {
  let spec;
  try {
    spec = JSON.parse(readFileSync(join(SPECS_DIR, file), "utf8"));
  } catch (e) {
    fail(`${file}: invalid JSON — ${e.message}`);
    continue;
  }
  // collect every data url the spec references (recursively)
  collectUrls(spec, referencedData);

  // compile, capturing error-level log messages
  const errors = [];
  const logger = {
    level: () => logger, error: (...a) => { errors.push(a.join(" ")); return logger; },
    warn: () => logger, info: () => logger, debug: () => logger,
  };
  try {
    vl.compile(spec, { logger });
    if (errors.length) fail(`${file}: compiler errors — ${errors.join("; ")}`);
    else pass(`${file}`);
  } catch (e) {
    fail(`${file}: compile threw — ${e.message}`);
  }
}

// ---- 2. Referenced data files exist ----------------------------------------
console.log("\n[2] Referenced data files exist in src/data/");
for (const url of [...referencedData].sort()) {
  const name = basename(url.split("?")[0]);
  if (existsSync(join(DATA_DIR, name))) pass(name);
  else fail(`spec references data/${name} but it is missing from src/data/`);
}

// ---- 3. Row counts, join coverage, NaN scan --------------------------------
console.log("\n[3] Data integrity");
for (const [name, expected] of Object.entries(EXPECTED_ROWS)) {
  const path = join(DATA_DIR, name);
  if (!existsSync(path)) { fail(`${name}: missing`); continue; }
  const rows = parseCsv(path);
  if (rows.length === expected) pass(`${name}: ${rows.length} rows`);
  else fail(`${name}: expected ${expected} rows, got ${rows.length}`);
  const bad = scanBadTokens(rows);
  if (bad) fail(`${name}: found ${bad} NaN/Infinity cell(s)`);
}

// NaN scan already covers the CSVs above; confirm clean overall
const totalBad = Object.keys(EXPECTED_ROWS).reduce((acc, name) => {
  const path = join(DATA_DIR, name);
  return acc + (existsSync(path) ? scanBadTokens(parseCsv(path)) : 0);
}, 0);
if (totalBad === 0) pass("no NaN/Infinity strings in any CSV");

// SA2 join coverage: TopoJSON geometries ⊆ summary codes; 524 vs 522 gap expected
console.log("\n[4] SA2 join coverage (sa2_code_2021)");
try {
  const topo = JSON.parse(readFileSync(join(DATA_DIR, "sa2_summary_simplified.topojson"), "utf8"));
  const geoms = topo.objects?.sa2_summary?.geometries ?? [];
  if (geoms.length === EXPECTED_GEOMETRIES) pass(`topojson: ${geoms.length} geometries`);
  else fail(`topojson: expected ${EXPECTED_GEOMETRIES} geometries, got ${geoms.length}`);

  const geoCodes = new Set(geoms.map((g) => String(g.properties.sa2_code_2021)));
  const summary = parseCsv(join(DATA_DIR, "sa2_summary_web.csv"));
  const sumCodes = new Set(summary.map((r) => r.sa2_code_2021));
  const missing = [...geoCodes].filter((c) => !sumCodes.has(c));
  if (missing.length === 0) pass("every geometry code is present in sa2_summary_web.csv");
  else fail(`geometry codes missing from summary: ${missing.slice(0, 5).join(", ")}`);

  const gap = sumCodes.size - geoCodes.size;
  if (gap === 2) pass(`524 summary vs 522 geometries — 2 non-spatial SA2s as expected`);
  else fail(`unexpected summary/geometry gap: ${gap} (expected 2)`);

  // venue SA2 codes ⊆ summary codes
  const venueCodes = new Set(parseCsv(join(DATA_DIR, "venues_web.csv")).map((r) => r.sa2_code_2021));
  const orphanVenues = [...venueCodes].filter((c) => c && !sumCodes.has(c));
  if (orphanVenues.length === 0) pass("every venue SA2 code is present in the summary");
  else fail(`venue SA2 codes not in summary: ${orphanVenues.slice(0, 5).join(", ")}`);
} catch (e) {
  fail(`join coverage check failed: ${e.message}`);
}

// ---- 5. Transport routes topojson (chart-11) -------------------------------
console.log("\n[5] Transport routes topojson (chart-11)");
try {
  const routes = JSON.parse(readFileSync(join(DATA_DIR, "pt_routes_simplified.topojson"), "utf8"));
  const obj = routes.objects?.public_transport_lines;
  if (!obj) fail("pt_routes_simplified.topojson: missing object 'public_transport_lines'");
  else {
    const feats = obj.geometries ?? [];
    if (feats.length === EXPECTED_ROUTE_FEATURES) pass(`routes: ${feats.length} train+tram features`);
    else fail(`routes: expected ${EXPECTED_ROUTE_FEATURES} features, got ${feats.length}`);
    const modes = new Set(feats.map((g) => g.properties?.MODE));
    const stray = [...modes].filter((m) => m && m.indexOf("TRAIN") < 0 && m.indexOf("TRAM") < 0);
    if (stray.length === 0) pass("every route is a train or tram mode (no bus/coach leaked in)");
    else fail(`routes: unexpected non-rail/tram modes: ${stray.join(", ")}`);
  }
} catch (e) {
  fail(`routes topojson check failed: ${e.message}`);
}

// ---- 6. Melbourne close-up topojson (chart-15) -----------------------------
console.log("\n[6] Melbourne close-up topojson (chart-15)");
for (const [name, { object, features }] of Object.entries(MELBOURNE_TOPO)) {
  try {
    const t = JSON.parse(readFileSync(join(DATA_DIR, name), "utf8"));
    const geoms = t.objects?.[object]?.geometries;
    if (!geoms) { fail(`${name}: missing object '${object}'`); continue; }
    if (geoms.length === features) pass(`${name}: ${geoms.length} features (object '${object}')`);
    else fail(`${name}: expected ${features} features, got ${geoms.length}`);
  } catch (e) {
    fail(`${name}: ${e.message}`);
  }
}

// ---- 7. Structural parity: manifest ↔ mounts ↔ spec files ------------------
// Motivated by the chart-numbering collision during the §6/§7 build (two charts
// claimed the same IDs). A deletion-and-renumber change is exactly where this earns
// its keep: every manifest ID maps to one mounted element and one spec file on disk,
// no orphans either way, and the totals all match EXPECTED_CHART_COUNT.
console.log("\n[7] Structural parity (manifest ↔ mounts ↔ spec files)");
try {
  const html = readFileSync(join(ROOT, "src", "index.html"), "utf8");

  // Manifest: id -> spec basename, parsed from the CHARTS object.
  const manifest = new Map();
  const manifestRe = /"(chart-\d+)":\s*"(specs\/[^"]+\.vl\.json)"/g;
  let mm;
  while ((mm = manifestRe.exec(html)) !== null) manifest.set(mm[1], basename(mm[2]));

  // Mounted elements: every <div id="chart-NN" class="chart-mount">.
  const mounts = [];
  const mountRe = /id="(chart-\d+)"\s+class="chart-mount"/g;
  while ((mm = mountRe.exec(html)) !== null) mounts.push(mm[1]);
  const mountSet = new Set(mounts);

  const diskSpecs = new Set(specFiles); // .vl.json basenames on disk (from section 1)
  const manifestIds = new Set(manifest.keys());
  const manifestSpecs = new Set(manifest.values());

  // a) all three totals agree on the expected count
  manifest.size === EXPECTED_CHART_COUNT
    ? pass(`manifest lists ${manifest.size} charts`)
    : fail(`manifest lists ${manifest.size} charts, expected ${EXPECTED_CHART_COUNT}`);
  mounts.length === EXPECTED_CHART_COUNT
    ? pass(`${mounts.length} mounted chart elements`)
    : fail(`${mounts.length} mounted chart elements, expected ${EXPECTED_CHART_COUNT}`);
  diskSpecs.size === EXPECTED_CHART_COUNT
    ? pass(`${diskSpecs.size} spec files on disk`)
    : fail(`${diskSpecs.size} spec files on disk, expected ${EXPECTED_CHART_COUNT}`);

  // b) no duplicate mount ids (the collision guard)
  const dupes = mounts.filter((id, i) => mounts.indexOf(id) !== i);
  dupes.length === 0
    ? pass("no duplicate mounted chart ids")
    : fail(`duplicate mounted chart ids: ${[...new Set(dupes)].join(", ")}`);

  // c) manifest ids ↔ mounted ids, exactly
  const noMount = [...manifestIds].filter((id) => !mountSet.has(id));
  const noManifest = [...mountSet].filter((id) => !manifestIds.has(id));
  if (noMount.length === 0 && noManifest.length === 0) {
    pass("every manifest id has exactly one mount, and every mount is in the manifest");
  } else {
    if (noMount.length) fail(`manifest ids with no mount: ${noMount.join(", ")}`);
    if (noManifest.length) fail(`mounted ids missing from manifest: ${noManifest.join(", ")}`);
  }

  // d) manifest specs ↔ spec files on disk, exactly (no orphan spec either way)
  const missingOnDisk = [...manifestSpecs].filter((f) => !diskSpecs.has(f));
  const orphanOnDisk = [...diskSpecs].filter((f) => !manifestSpecs.has(f));
  if (missingOnDisk.length === 0 && orphanOnDisk.length === 0) {
    pass("every manifest spec exists on disk, and every spec file is in the manifest");
  } else {
    if (missingOnDisk.length) fail(`manifest references missing spec file(s): ${missingOnDisk.join(", ")}`);
    if (orphanOnDisk.length) fail(`orphan spec file(s) on disk (not in manifest): ${orphanOnDisk.join(", ")}`);
  }
} catch (e) {
  fail(`structural parity check failed: ${e.message}`);
}

// ---- summary ----------------------------------------------------------------
console.log("");
if (failures === 0) {
  console.log("✅ All checks passed.");
  process.exit(0);
} else {
  console.error(`❌ ${failures} check(s) failed.`);
  process.exit(1);
}

// ---- helpers ----------------------------------------------------------------
function collectUrls(node, out) {
  if (Array.isArray(node)) { node.forEach((n) => collectUrls(n, out)); return; }
  if (node && typeof node === "object") {
    if (typeof node.url === "string" && node.url.startsWith("data/")) out.add(node.url);
    for (const v of Object.values(node)) collectUrls(v, out);
  }
}

// Minimal CSV parser sufficient for these files (no embedded newlines; quoted commas handled).
function parseCsv(path) {
  const text = readFileSync(path, "utf8").replace(/\r\n/g, "\n").trim();
  const lines = text.split("\n");
  const header = splitCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const cells = splitCsvLine(line);
    const row = {};
    header.forEach((h, i) => { row[h] = cells[i] ?? ""; });
    return row;
  });
}

function splitCsvLine(line) {
  const out = [];
  let cur = "", inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') inQ = !inQ;
    else if (c === "," && !inQ) { out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

function scanBadTokens(rows) {
  let n = 0;
  for (const row of rows) for (const v of Object.values(row)) if (BAD_TOKENS.has(v)) n++;
  return n;
}
