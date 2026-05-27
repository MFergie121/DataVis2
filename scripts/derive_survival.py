#!/usr/bin/env python3
"""
derive_survival.py — venue-survival aggregates for the proximity-vs-performance section.

Survival proxy: the Victorian liquor-licence "by location" dataset publishes a monthly
snapshot of *currently held* licences (no grant date, no status field). Comparing two
snapshots on the stable `Licence Num` gives an open/close signal:

    a licence in a year-Y cohort that is absent from the year-(Y+2) snapshot = ceased.

Two complementary views come out of this, both broken down by distance to the nearest stop
and by metro/regional (the axes the rest of the page uses):

  * chart-16 (chart_survival_distance_band.csv): ONE base cohort (July CHART16_BASE_YEAR)
    tracked at 1/2/3-year survival horizons — was a July-2022 venue still licensed in July
    2023 / 2024 / 2025? The reader picks the horizon; survival falls with time but stays flat
    across distance. Keyed by horizon_years, not cohort_year.
  * charts 17/18/20/22 (chart_sa2_survival.csv): the same July-2022 base cohort as chart-16,
    with one row per SA2 and separate 1/2/3-year survival columns. The charts pick a horizon
    with a Vega-Lite param while the TopoJSON lookups stay one-to-one.
  * chart_sa2_hospitality_health.csv: legacy net active-venue growth companion from the
    §3 hero cohort (HERO_COHORT_YEAR), retained for audit/backtracking but no longer mounted.

This is association only: a ceased licence can be a sale/relocation/rename, not just a
failure, and proximity is confounded with the metro/regional divide. See wiki/domain/topic.md.

Cohort discovery (hero charts): any `raw/data/licences_YYYY-07.xlsx` snapshot becomes a
cohort T0 if a snapshot exactly two years later (`licences_(YYYY+2)-07.xlsx`) also exists;
the two-year window is held constant so the comparison is fair. chart-16's horizons instead
read the July (base+1/2/3) snapshots directly. Drop more July snapshots into raw/data/ and
rerun — extra hero cohorts and survival horizons appear automatically; no code change.

Caveat carried into the page: distance-to-stop is computed against the single *current*
public_transport_stops.geojson for every cohort (we have only one stops file). Stops drift
over time, so older cohorts' distances are approximate — acceptable for an association-only
proxy. See wiki/data/derived-fields.md.

Inputs (all gitignored, under raw/):
  raw/data/licences_YYYY-07.xlsx          one or more monthly snapshots; pairs 2 yrs apart
  raw/data/public_transport_stops.geojson 31k PT stops (for nearest-stop distance)
  raw/data/sa2_boundaries.geojson         SA2 polygons (topo2geo'd from the shipped topojson)
  src/data/sa2_summary_web.csv            SA2 access share + metro/regional (for the SA2 charts)

Outputs (shipped, small):
  src/data/chart_survival_distance_band.csv  keyed by horizon_years (chart-16, 2022 cohort)
  src/data/chart_sa2_survival.csv            2022 cohort SA2 survival, wide by horizon
  src/data/chart_survival_summary.csv        2022 cohort near/far survival, long by horizon
  src/data/chart_sa2_hospitality_health.csv  hero cohort

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

# Charts 16/17/18/20/22 carry the survival-horizon selector over the July 2022 base cohort.
# The legacy hospitality-health CSV still uses the 2023→2025 hero pair because it measures
# net active-venue growth, not survival rate.
HERO_COHORT_YEAR = 2023

# chart-16 tracks ONE cohort (July CHART16_BASE_YEAR) and lets the reader pick how long
# after that the venue had to still be licensed: a 1/2/3-year survival horizon. Each
# horizon is the same base cohort measured against the July (base + h) snapshot, so the
# distance-band comparison is held fair and only the elapsed time changes. Horizons whose
# outcome snapshot is not in raw/data/ are skipped automatically.
CHART16_BASE_YEAR = 2022
CHART16_HORIZONS = [1, 2, 3]

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


def all_snapshots() -> dict[int, Path]:
    """Map every July snapshot year present in raw/data/ to its file path."""
    by_year = {}
    for p in RAW.glob("licences_*-07.xlsx"):
        m = SNAPSHOT_RE.search(p.name)
        if m:
            by_year[int(m.group(1))] = p
    return by_year


def discover_cohorts() -> list[tuple[int, Path, Path]]:
    """Find (year, T0, T1) triples: every snapshot with a partner SURVIVAL_WINDOW_YEARS later."""
    by_year = all_snapshots()
    cohorts = []
    for year in sorted(by_year):
        outcome = year + SURVIVAL_WINDOW_YEARS
        if outcome in by_year:
            cohorts.append((year, by_year[year], by_year[outcome]))
    return cohorts


def tag_distance_and_region(df: pd.DataFrame,
                            stop_tree: cKDTree, slon: np.ndarray, slat: np.ndarray) -> pd.DataFrame:
    """Keep valid-coord Victorian Metro/Regional venues and attach nearest-stop distance band.

    Shared by the per-cohort survival pass and the chart-16 horizon pass so both bin venues
    into bands the same way.
    """
    df = df.copy()
    df["metro_regional"] = df["Metro/Regional"].astype(str).str.strip().str.title()
    df = df[df["metro_regional"].isin(["Metro", "Regional"])]
    df = df.dropna(subset=["Latitude", "Longitude"])
    df = df[(df["Latitude"].between(-39.5, -33.5)) & (df["Longitude"].between(140.5, 150.5))].copy()

    lon = df["Longitude"].to_numpy(float)
    lat = df["Latitude"].to_numpy(float)
    _, idx = stop_tree.query(project(lon, lat), k=1)
    dist_m = haversine_m(lon, lat, slon[idx], slat[idx])
    df["nearest_stop_distance_m"] = dist_m
    bands = [band_of(d) for d in dist_m]
    df["distance_band"] = [b[0] for b in bands]
    df["distance_band_order"] = [b[1] for b in bands]
    df["near_400m"] = dist_m <= 400
    return df


def build_chart16(base_year: int, snaps: dict[int, Path],
                  stop_tree: cKDTree, slon: np.ndarray, slat: np.ndarray) -> pd.DataFrame:
    """chart-16 source: one base cohort, survival measured at 1/2/3-year horizons.

    The base cohort (July `base_year`) is binned into distance bands once; for each horizon
    `h` with an outcome snapshot present, a licence "survived" if it is still in the July
    (base_year + h) snapshot. Rows are keyed by horizon × distance band × metro/regional, plus
    a venue-weighted `overall_survival_pct` per horizon for the reference floor.
    """
    if base_year not in snaps:
        raise SystemExit(f"chart-16 base cohort July {base_year} not found in {RAW}")
    base = tag_distance_and_region(load_snapshot(snaps[base_year]), stop_tree, slon, slat)
    log(f"chart-16 base cohort July {base_year}: {len(base)} venues with valid coords + region")

    frames = []
    for h in CHART16_HORIZONS:
        outcome_year = base_year + h
        if outcome_year not in snaps:
            log(f"  horizon {h}yr skipped — no July {outcome_year} snapshot")
            continue
        out_ids = set(load_snapshot(snaps[outcome_year])["Licence Num"])
        base["survived"] = base["Licence Num"].isin(out_ids)
        overall = round(base["survived"].mean() * 100, 1)
        log(f"  horizon {h}yr (→ July {outcome_year}): {base['survived'].sum()} of {len(base)} "
            f"survived ({overall:.1f}%)")

        g = (base.groupby(["distance_band", "distance_band_order", "metro_regional"])
                 .agg(cohort_count=("survived", "size"), survived_count=("survived", "sum"))
                 .reset_index())
        g["survival_rate_pct"] = (g["survived_count"] / g["cohort_count"] * 100).round(1)
        g["horizon_years"] = h
        g["horizon_label"] = f"{h} year" if h == 1 else f"{h} years"
        g["outcome_year"] = outcome_year
        g["overall_survival_pct"] = overall
        frames.append(g)

    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["horizon_years", "metro_regional", "distance_band_order"])


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

    # normalise metro/regional, drop invalid coords, attach nearest-stop distance band
    t0 = tag_distance_and_region(t0, stop_tree, slon, slat)
    log(f"  cohort with valid Victorian coords + metro/regional: {len(t0)}")

    lon = t0["Longitude"].to_numpy(float)
    lat = t0["Latitude"].to_numpy(float)

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


def process_active_snapshot(year: int, path: Path,
                            sa2_geoms: list, sa2_codes: list, sa2_tree: STRtree) -> pd.DataFrame:
    """Attach SA2 to one active-licence snapshot for net venue-count comparisons."""
    df = load_snapshot(path)
    df["metro_regional"] = df["Metro/Regional"].astype(str).str.strip().str.title()
    df = df[df["metro_regional"].isin(["Metro", "Regional"])].copy()
    df = df.dropna(subset=["Latitude", "Longitude"])
    df = df[(df["Latitude"].between(-39.5, -33.5)) & (df["Longitude"].between(140.5, 150.5))].copy()

    sa2_assigned = []
    for x, y in zip(df["Longitude"].to_numpy(float), df["Latitude"].to_numpy(float)):
        pt = Point(x, y)
        hit = None
        for j in sa2_tree.query(pt):
            if sa2_geoms[j].contains(pt):
                hit = sa2_codes[j]
                break
        sa2_assigned.append(hit)
    df["sa2_code_2021"] = sa2_assigned
    df = df.dropna(subset=["sa2_code_2021"]).copy()
    log(f"July {year} active snapshot SA2 matched: {len(df)} licences")
    return df


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

    # ---- 4a. chart-16: one base cohort, survival at 1/2/3-year horizons -----------
    snaps = all_snapshots()
    g = build_chart16(CHART16_BASE_YEAR, snaps, stop_tree, slon, slat)
    g.to_csv(OUT / "chart_survival_distance_band.csv", index=False)
    log(f"wrote chart_survival_distance_band.csv ({len(g)} rows, "
        f"{g['horizon_years'].nunique()} horizons of the July {CHART16_BASE_YEAR} cohort)")

    # paste-ready hints for keeping chart-16's hand-maintained spec in sync with the data
    horizons = sorted(int(h) for h in g["horizon_years"].unique())
    lo = math.floor(g["survival_rate_pct"].min() / 5) * 5
    hi = math.ceil(g["survival_rate_pct"].max() / 5) * 5
    floors = (g[["horizon_years", "overall_survival_pct"]].drop_duplicates()
              .sort_values("horizon_years"))
    floor_map = {int(r.horizon_years): r.overall_survival_pct for r in floors.itertuples()}
    log(f"chart-16 spec hint -> horizonYears options: {horizons}  "
        f"| y scale.domain: [{lo}, {hi}]  | overall floors by horizon: {floor_map}")

    # charts 17/18/22 use the same base cohort and horizons as chart-16. Keep the SA2 output
    # wide (one row per SA2) so TopoJSON lookups remain one-to-one.
    base = tag_distance_and_region(load_snapshot(snaps[CHART16_BASE_YEAR]), stop_tree, slon, slat)
    lon = base["Longitude"].to_numpy(float)
    lat = base["Latitude"].to_numpy(float)
    sa2_assigned = []
    for x, y in zip(lon, lat):
        pt = Point(x, y)
        hit = None
        for j in sa2_tree.query(pt):
            if sa2_geoms[j].contains(pt):
                hit = sa2_codes[j]
                break
        sa2_assigned.append(hit)
    base["sa2_code_2021"] = sa2_assigned
    base = base.dropna(subset=["sa2_code_2021"]).copy()

    horizon_sa2 = None
    summary_frames = []
    for h in horizons:
        outcome_year = CHART16_BASE_YEAR + h
        out_ids = set(load_snapshot(snaps[outcome_year])["Licence Num"])
        base["survived"] = base["Licence Num"].isin(out_ids)

        base_h = base.assign(
            proximity=np.where(base["near_400m"], "Within 400 m of a stop", "Further than 400 m")
        )
        s = (base_h.groupby(["metro_regional", "proximity"])
                   .agg(cohort_count=("survived", "size"),
                        survived_count=("survived", "sum"))
                   .reset_index())
        s["horizon_years"] = h
        s["horizon_label"] = f"{h} year" if h == 1 else f"{h} years"
        s["outcome_year"] = outcome_year
        s["survival_rate_pct"] = (s["survived_count"] / s["cohort_count"] * 100).round(1)
        summary_frames.append(s)

        sa2_h = (base.dropna(subset=["sa2_code_2021"])
                     .groupby("sa2_code_2021")
                     .agg(cohort_count=("survived", "size"),
                          survived_count=("survived", "sum"))
                     .reset_index())
        sa2_h = sa2_h[sa2_h["cohort_count"] >= 5].copy()
        suffix = f"{h}yr"
        sa2_h[f"survived_count_{suffix}"] = sa2_h["survived_count"]
        sa2_h[f"survival_rate_{suffix}_pct"] = (
            sa2_h["survived_count"] / sa2_h["cohort_count"] * 100
        ).round(1)
        sa2_h = sa2_h.drop(columns=["survived_count"])
        if horizon_sa2 is None:
            horizon_sa2 = sa2_h.rename(columns={"cohort_count": "cohort_count_2022"})
        else:
            horizon_sa2 = horizon_sa2.merge(
                sa2_h.drop(columns=["cohort_count"]),
                on="sa2_code_2021",
                how="outer",
            )

    summary_out = pd.concat(summary_frames, ignore_index=True)
    summary_out = summary_out[[
        "horizon_years",
        "horizon_label",
        "outcome_year",
        "metro_regional",
        "proximity",
        "cohort_count",
        "survived_count",
        "survival_rate_pct",
    ]]
    summary_out.to_csv(OUT / "chart_survival_summary.csv", index=False)
    log(f"wrote chart_survival_summary.csv ({len(summary_out)} horizon × near/far rows)")

    summary = pd.read_csv(SA2_SUMMARY, dtype={"sa2_code_2021": str})
    keep = ["sa2_code_2021", "sa2_name_2021", "access_share_pct", "metro_regional_majority"]
    keep = [c for c in keep if c in summary.columns]
    sa2 = horizon_sa2.merge(summary[keep], on="sa2_code_2021", how="left")
    sa2["cohort_count"] = sa2["cohort_count_2022"]
    sa2["survived_count"] = sa2["survived_count_2yr"]
    sa2["survival_rate_pct"] = sa2["survival_rate_2yr_pct"]
    sa2["access_share_pct"] = sa2["access_share_pct"].round(1)
    sa2.to_csv(OUT / "chart_sa2_survival.csv", index=False)
    log(f"wrote chart_sa2_survival.csv ({len(sa2)} SA2 rows, wide 1/2/3-year survival)")

    # Legacy hospitality-health CSV stays on the hero cohort — fall back to the latest
    # cohort if 2023 is absent.
    cohort_years = sorted(venues["cohort_year"].unique())
    hero = HERO_COHORT_YEAR if HERO_COHORT_YEAR in cohort_years else cohort_years[-1]
    hero_venues = venues[venues["cohort_year"] == hero]
    log(f"hero cohort for chart-20: {hero}")

    # ---- 4d. per-SA2 net hospitality health, 2023 active count vs 2025 active count ----
    # This complements chart-17's fixed-cohort survival view: it asks whether each SA2's
    # active hospitality ecosystem grew or shrank overall, including churn and new venues.
    if hero == HERO_COHORT_YEAR and (RAW / f"licences_{hero + SURVIVAL_WINDOW_YEARS}-07.xlsx").exists():
        active_2025 = process_active_snapshot(
            hero + SURVIVAL_WINDOW_YEARS,
            RAW / f"licences_{hero + SURVIVAL_WINDOW_YEARS}-07.xlsx",
            sa2_geoms,
            sa2_codes,
            sa2_tree,
        )
        health = (hero_venues.dropna(subset=["sa2_code_2021"])
                             .groupby("sa2_code_2021")
                             .agg(active_venues_2023=("Licence Num", "size"),
                                  venues_within_400m_pt_2023=("near_400m", "sum"))
                             .reset_index())
        counts_2025 = (active_2025.groupby("sa2_code_2021")
                                  .size()
                                  .rename("active_venues_2025")
                                  .reset_index())
        health = health.merge(counts_2025, on="sa2_code_2021", how="left")
        health["active_venues_2025"] = health["active_venues_2025"].fillna(0).astype(int)
        health = health.rename(columns={
            "sa2_code_2021": "sa2_code",
            "active_venues_2023": "total_venues_2023",
        })
        health["active_venues_2023"] = health["total_venues_2023"]
        health["access_rate_pct"] = (
            health["venues_within_400m_pt_2023"] / health["total_venues_2023"] * 100
        ).round(1)
        health["venue_growth_rate_pct"] = (
            (health["active_venues_2025"] - health["active_venues_2023"])
            / health["active_venues_2023"] * 100
        ).round(1)

        summary_for_health = summary.rename(columns={
            "sa2_code_2021": "sa2_code",
            "sa2_name_2021": "sa2_name",
            "metro_regional_majority": "region_type",
        })
        health = health.merge(
            summary_for_health[["sa2_code", "sa2_name", "region_type"]],
            on="sa2_code",
            how="left",
        )
        health = health[health["active_venues_2023"] >= 10].copy()
        health = health[health["region_type"].isin(["Metro", "Regional"])].copy()
        health = health[[
            "sa2_code",
            "sa2_name",
            "region_type",
            "active_venues_2023",
            "active_venues_2025",
            "venues_within_400m_pt_2023",
            "total_venues_2023",
            "access_rate_pct",
            "venue_growth_rate_pct",
        ]].sort_values(["region_type", "sa2_name"])
        health.to_csv(OUT / "chart_sa2_hospitality_health.csv", index=False)
        log(f"wrote chart_sa2_hospitality_health.csv ({len(health)} SA2 rows with 2023 venues >= 10)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
