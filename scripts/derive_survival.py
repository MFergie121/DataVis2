#!/usr/bin/env python3
"""
derive_survival.py — venue-survival aggregates for the proximity-vs-performance section.

Survival proxy: the Victorian liquor-licence "by location" dataset publishes a monthly
snapshot of *currently held* licences (no grant date, no status field). Comparing two
snapshots on the stable `Licence Num` gives an open/close signal:

    a licence in a year-Y cohort that is absent from the year-(Y+2) snapshot = ceased.

We measure 2-year survival for every cohort year we have a snapshot pair for, and break it
down by distance to the nearest public-transport stop and by metro/regional — the same axes
the rest of the page uses. This is association only: a ceased licence can be a
sale/relocation/rename, not just a failure, and proximity is confounded with the
metro/regional divide. See wiki/domain/topic.md.

Cohort discovery: any `raw/data/licences_YYYY-07.xlsx` snapshot becomes a cohort T0 if a
snapshot exactly two years later (`licences_(YYYY+2)-07.xlsx`) also exists. The two-year
survival window is held constant across cohorts so the comparison is fair. Drop more July
snapshots into raw/data/ and rerun — extra cohorts appear automatically; no code change.

Caveat carried into the page: distance-to-stop is computed against the single *current*
public_transport_stops.geojson for every cohort (we have only one stops file). Stops drift
over time, so older cohorts' distances are approximate — acceptable for an association-only
proxy. See wiki/data/derived-fields.md.

Inputs (all gitignored, under raw/):
  raw/data/licences_YYYY-07.xlsx          one or more monthly snapshots; pairs 2 yrs apart
  raw/data/public_transport_stops.geojson 31k PT stops (for nearest-stop distance)
  raw/data/sa2_boundaries.geojson         SA2 polygons (topo2geo'd from the shipped topojson)
  src/data/sa2_summary_web.csv            SA2 access share + metro/regional (for the SA2 charts)

Outputs (shipped, small) — each gains a `cohort_year` column:
  src/data/chart_survival_distance_band.csv
  src/data/chart_sa2_survival.csv
  src/data/chart_survival_summary.csv

Run: .venv/bin/python3 scripts/derive_survival.py
Deterministic — no randomness.
"""
from __future__ import annotations
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "data"
OUT = ROOT / "src" / "data"

STOPS = RAW / "public_transport_stops.geojson"
SA2_GEO = RAW / "sa2_boundaries.geojson"
SA2_SUMMARY = OUT / "sa2_summary_web.csv"

SURVIVAL_WINDOW_YEARS = 2  # cohort year Y is measured against the year Y+2 snapshot
SNAPSHOT_RE = re.compile(r"licences_(\d{4})-07\.xlsx$")

# Only chart-16 (distance bands) carries the year selector across all cohorts. Charts 17
# (access x survival scatter) and 18 (survival choropleth) stay anchored to the §3 hero
# cohort, so their CSVs remain single-cohort and the page's counterfactual narrative holds.
HERO_COHORT_YEAR = 2023

R_EARTH = 6_371_000.0  # metres
LAT0 = -37.0           # reference latitude for the equirectangular projection (Victoria)

# distance bands — identical thresholds to wiki/data/derived-fields.md
BANDS = [(0, 200, "0-200m", 1), (200, 400, "200-400m", 2),
         (400, 800, "400-800m", 3), (800, math.inf, "800m+", 4)]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_snapshot(path: Path) -> pd.DataFrame:
    """Read a licence snapshot, skipping the letterhead to the real header row."""
    head = pd.read_excel(path, header=None, nrows=15)
    hdr = next(i for i in range(len(head))
               if head.iloc[i].astype(str).str.contains("Licence Num", case=False).any())
    df = pd.read_excel(path, header=hdr)
    df.columns = [str(c).strip() for c in df.columns]
    df["Licence Num"] = df["Licence Num"].astype(str).str.strip()
    return df


def project(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Equirectangular projection to metres (accurate enough for nearest-neighbour)."""
    x = np.radians(lon) * math.cos(math.radians(LAT0)) * R_EARTH
    y = np.radians(lat) * R_EARTH
    return np.column_stack([x, y])


def haversine_m(lon1, lat1, lon2, lat2):
    """Exact great-circle distance in metres, used to refine the matched nearest stop."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


def band_of(d: float):
    for lo, hi, label, order in BANDS:
        if lo <= d < hi:
            return label, order
    return BANDS[-1][2], BANDS[-1][3]


def discover_cohorts() -> list[tuple[int, Path, Path]]:
    """Find (year, T0, T1) triples: every snapshot with a partner SURVIVAL_WINDOW_YEARS later."""
    by_year = {}
    for p in RAW.glob("licences_*-07.xlsx"):
        m = SNAPSHOT_RE.search(p.name)
        if m:
            by_year[int(m.group(1))] = p
    cohorts = []
    for year in sorted(by_year):
        outcome = year + SURVIVAL_WINDOW_YEARS
        if outcome in by_year:
            cohorts.append((year, by_year[year], by_year[outcome]))
    return cohorts


def process_cohort(year: int, t0_path: Path, t1_path: Path,
                   stop_tree: cKDTree, slon: np.ndarray, slat: np.ndarray,
                   sa2_geoms: list, sa2_codes: list, sa2_tree: STRtree) -> pd.DataFrame:
    """Flag a cohort's survival and attach distance band + SA2; returns per-venue rows."""
    # ---- cohort + survival flag ---------------------------------------------------
    t0 = load_snapshot(t0_path)
    t1_ids = set(load_snapshot(t1_path)["Licence Num"])
    n_cohort = len(t0)
    t0["survived"] = t0["Licence Num"].isin(t1_ids)
    log(f"July {year} cohort: {n_cohort} | survived to July {year + SURVIVAL_WINDOW_YEARS}: "
        f"{t0['survived'].sum()} ({t0['survived'].mean() * 100:.1f}%)")

    # normalise metro/regional to the page's vocabulary ("Metro" / "Regional")
    t0["metro_regional"] = t0["Metro/Regional"].astype(str).str.strip().str.title()
    t0 = t0[t0["metro_regional"].isin(["Metro", "Regional"])].copy()

    # drop rows without coordinates (can't place them spatially)
    t0 = t0.dropna(subset=["Latitude", "Longitude"])
    t0 = t0[(t0["Latitude"].between(-39.5, -33.5)) & (t0["Longitude"].between(140.5, 150.5))].copy()
    log(f"  cohort with valid Victorian coords + metro/regional: {len(t0)}")

    lon = t0["Longitude"].to_numpy(float)
    lat = t0["Latitude"].to_numpy(float)

    # ---- nearest-stop distance (KD-tree, refined with haversine) ------------------
    _, idx = stop_tree.query(project(lon, lat), k=1)
    dist_m = haversine_m(lon, lat, slon[idx], slat[idx])
    t0["nearest_stop_distance_m"] = dist_m
    bands = [band_of(d) for d in dist_m]
    t0["distance_band"] = [b[0] for b in bands]
    t0["distance_band_order"] = [b[1] for b in bands]
    t0["near_400m"] = dist_m <= 400

    # ---- SA2 via point-in-polygon -------------------------------------------------
    sa2_assigned = []
    for x, y in zip(lon, lat):
        pt = Point(x, y)
        hit = None
        for j in sa2_tree.query(pt):  # candidate indices whose bbox intersects
            if sa2_geoms[j].contains(pt):
                hit = sa2_codes[j]
                break
        sa2_assigned.append(hit)
    t0["sa2_code_2021"] = sa2_assigned
    matched = t0["sa2_code_2021"].notna().sum()
    log(f"  SA2 point-in-polygon matched: {matched}/{len(t0)} ({matched / len(t0) * 100:.1f}%)")

    t0["cohort_year"] = year
    return t0


def main() -> int:
    cohorts = discover_cohorts()
    if not cohorts:
        log(f"no snapshot pairs {SURVIVAL_WINDOW_YEARS} years apart found in {RAW} — "
            "need at least licences_Y-07.xlsx and licences_(Y+2)-07.xlsx")
        return 1
    log(f"cohorts: {[y for y, _, _ in cohorts]}")

    # ---- load the geo references ONCE (shared across all cohorts) ------------------
    stops = json.loads(STOPS.read_text())["features"]
    slon = np.array([f["geometry"]["coordinates"][0] for f in stops], float)
    slat = np.array([f["geometry"]["coordinates"][1] for f in stops], float)
    stop_tree = cKDTree(project(slon, slat))

    sa2_feats = json.loads(SA2_GEO.read_text())["features"]
    sa2_geoms = [shape(f["geometry"]) for f in sa2_feats]
    sa2_codes = [f["properties"]["sa2_code_2021"] for f in sa2_feats]
    sa2_tree = STRtree(sa2_geoms)

    # ---- process each cohort, then stack ------------------------------------------
    frames = [process_cohort(y, t0, t1, stop_tree, slon, slat, sa2_geoms, sa2_codes, sa2_tree)
              for y, t0, t1 in cohorts]
    venues = pd.concat(frames, ignore_index=True)

    # ---- 4a. survival by cohort_year x distance band x metro/regional -------------
    g = (venues.groupby(["cohort_year", "distance_band", "distance_band_order", "metro_regional"])
              .agg(cohort_count=("survived", "size"),
                   survived_count=("survived", "sum"))
              .reset_index())
    g["survival_rate_pct"] = (g["survived_count"] / g["cohort_count"] * 100).round(1)
    g = g.sort_values(["cohort_year", "metro_regional", "distance_band_order"])
    g.to_csv(OUT / "chart_survival_distance_band.csv", index=False)
    log(f"wrote chart_survival_distance_band.csv ({len(g)} rows, {g['cohort_year'].nunique()} cohorts)")

    # paste-ready hints for keeping chart-16's hand-maintained spec in sync with the data
    years = sorted(int(y) for y in g["cohort_year"].unique())
    lo = math.floor(g["survival_rate_pct"].min() / 5) * 5
    hi = math.ceil(g["survival_rate_pct"].max() / 5) * 5
    log(f"chart-16 spec hint -> param options: {years}  | value: {max(years)}  "
        f"| y scale.domain: [{lo}, {hi}]")

    # charts 17/18 stay on the hero cohort — fall back to the latest cohort if 2023 is absent
    cohort_years = sorted(venues["cohort_year"].unique())
    hero = HERO_COHORT_YEAR if HERO_COHORT_YEAR in cohort_years else cohort_years[-1]
    hero_venues = venues[venues["cohort_year"] == hero]
    log(f"hero cohort for charts 17/18: {hero}")

    # ---- 4b. near/far x metro/regional headline (hero cohort only) ----------------
    hero_venues = hero_venues.assign(
        proximity=np.where(hero_venues["near_400m"],
                           "Within 400 m of a stop", "Further than 400 m"))
    s = (hero_venues.groupby(["metro_regional", "proximity"])
                    .agg(cohort_count=("survived", "size"),
                         survived_count=("survived", "sum"))
                    .reset_index())
    s["survival_rate_pct"] = (s["survived_count"] / s["cohort_count"] * 100).round(1)
    s.to_csv(OUT / "chart_survival_summary.csv", index=False)
    log(f"wrote chart_survival_summary.csv ({len(s)} rows)")

    # ---- 4c. per-SA2 survival, joined to access share (hero cohort only) ----------
    sa2 = (hero_venues.dropna(subset=["sa2_code_2021"])
                      .groupby("sa2_code_2021")
                      .agg(cohort_count=("survived", "size"),
                           survived_count=("survived", "sum"))
                      .reset_index())
    sa2["survival_rate_pct"] = (sa2["survived_count"] / sa2["cohort_count"] * 100).round(1)

    summary = pd.read_csv(SA2_SUMMARY, dtype={"sa2_code_2021": str})
    keep = ["sa2_code_2021", "sa2_name_2021", "access_share_pct", "metro_regional_majority"]
    keep = [c for c in keep if c in summary.columns]
    sa2 = sa2.merge(summary[keep], on="sa2_code_2021", how="left")
    # only keep SA2s with a meaningful cohort so rates aren't noise
    sa2 = sa2[sa2["cohort_count"] >= 5].copy()
    sa2["access_share_pct"] = sa2["access_share_pct"].round(1)
    sa2.to_csv(OUT / "chart_sa2_survival.csv", index=False)
    log(f"wrote chart_sa2_survival.csv ({len(sa2)} SA2-cohort rows with cohort >= 5)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
