#!/usr/bin/env python3
"""
derive_hospitality_modes.py — how trips to hospitality venues are made, for §4 chart-21.

Source: Victorian Integrated Survey of Travel and Activity (VISTA), Department of
        Transport and Planning Victoria, CC BY 4.0.
        https://opendata.transport.vic.gov.au/dataset/victorian-integrated-survey-of-travel-and-activity-vista
        Raw "Trips" CSVs (one row per trip leg link), gitignored in raw/data/:
          - trips_vista_2022_2023.csv  (FY 2022-23)
          - trips_vista_2023_2024.csv  (FY 2023-24)
        These are the only two modern annual waves published; there is no 2024-25 file.

What this produces and why:
    The §4 question is *why* proximity to a stop doesn't predict venue survival. One
    observable answer: public transport is not how most people reach a venue. We take
    every VISTA trip whose DESTINATION is a hospitality/social venue (destplace2 in the
    six "go out to eat/drink/socialise" categories below), recode the trip's main mode
    (linkmode) into five groups, and compute each group's WEIGHTED share of hospitality
    trips, POOLED across both waves for a stable estimate. The pooled result is a
    composition (a snapshot), deliberately *not* a year-on-year trend — two survey waves
    on a filtered subset is too thin for a trend, and the rideshare-growth story lives in
    §4's cited Roy Morgan prose, not here.

    Weighting: trippoststratweight is VISTA's post-stratified trip expansion weight (each
    surveyed trip stands for ~N trips in the population). Shares must be weighted; raw
    counts would over-represent oversampled groups. We also keep the *unweighted* n per
    group so the chart and prose can be honest about sample size (rideshare especially is
    a handful of trips).

    Hospitality set is the six categories the chart claims, matched to VISTA's exact
    destplace2 labels. Adjacent categories (Fast Food, Cinema, Theatre, Winery, Hotel or
    Motel) are deliberately excluded to keep the set to licensed-style "going out".

Output: src/data/hospitality_modes.csv (5 rows: one per mode group). A few hundred bytes.

Run: .venv/bin/python3 scripts/derive_hospitality_modes.py
Deterministic.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WAVES = {
    "2022-23": ROOT / "raw" / "data" / "trips_vista_2022_2023.csv",
    "2023-24": ROOT / "raw" / "data" / "trips_vista_2023_2024.csv",
}
OUT = ROOT / "src" / "data" / "hospitality_modes.csv"

# destplace2 values that count as a hospitality / social "going out" destination.
HOSPITALITY_DESTPLACE2 = {
    "Pub or Bar",
    "Restaurant or Cafe",
    "Nightclub",
    "Club",
    "Reception Centre",
    "Social NEC",
}

# linkmode (main trip mode) → the five reported groups. Anything not listed → "Other"
# (School Bus, Running/jogging, Motorcycle, Mobility Scooter, e-Scooter, Plane, Other…).
# School Bus is intentionally NOT counted as public transport here: it is not a
# venue-access mode for the hospitality story.
MODE_GROUP = {
    "Train": "Public transport",
    "Tram": "Public transport",
    "Public Bus": "Public transport",
    "Vehicle Driver": "Private vehicle",
    "Vehicle Passenger": "Private vehicle",
    "Taxi": "Taxi / rideshare",
    "Rideshare Service": "Taxi / rideshare",
    "Walking": "Walking / cycling",
    "Bicycle": "Walking / cycling",
}
OTHER = "Other"
COLS = ["destplace2", "linkmode", "arrhour", "trippoststratweight"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    parts = []
    for wave, path in WAVES.items():
        if not path.exists():
            log(f"ERROR: missing {path} — download the VISTA Trips CSV first.")
            return 1
        d = pd.read_csv(path, usecols=COLS, low_memory=False)
        d = d[d["destplace2"].isin(HOSPITALITY_DESTPLACE2)].copy()
        d["wave"] = wave
        parts.append(d)
        log(f"{wave}: {len(d)} hospitality trips (of the wave's total)")

    df = pd.concat(parts, ignore_index=True)
    df["mode_group"] = df["linkmode"].map(MODE_GROUP).fillna(OTHER)
    total_n = len(df)
    total_w = df["trippoststratweight"].sum()
    log(f"pooled hospitality trips: {total_n} (unweighted) | {total_w:,.0f} (weighted)")

    g = (
        df.groupby("mode_group")
        .agg(
            weighted_trips=("trippoststratweight", "sum"),
            n_unweighted=("trippoststratweight", "size"),
        )
        .reset_index()
    )
    g["share_pct"] = (g["weighted_trips"] / total_w * 100).round(1)
    g["weighted_trips"] = g["weighted_trips"].round().astype(int)
    g = g.sort_values("share_pct", ascending=False).reset_index(drop=True)

    g = g[["mode_group", "share_pct", "weighted_trips", "n_unweighted"]]
    g.to_csv(OUT, index=False)

    log(f"wrote {OUT.name}: {len(g)} rows | {OUT.stat().st_size:,} bytes")
    log("shares: " + ", ".join(f"{r.mode_group}={r.share_pct}% (n={r.n_unweighted})"
                               for r in g.itertuples()))
    log(f"share total: {g['share_pct'].sum():.1f}% (rounding may not be exactly 100)")

    # Night-time sensitivity (arrive 18:00–02:00) — reported in the §4 prose, NOT shipped.
    night = df[(df["arrhour"] >= 18) | (df["arrhour"] <= 2)]
    if len(night):
        nw = night.groupby("mode_group")["trippoststratweight"].sum()
        ns = (nw / nw.sum() * 100).round(1).sort_values(ascending=False)
        log(f"[night-only sensitivity, n={len(night)}] " +
            ", ".join(f"{m}={v}%" for m, v in ns.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
