#!/usr/bin/env python3
"""
derive_access_thresholds.py — per-SA2 transport-access share at 200/400/800 m, for §3 chart-17's
distance-threshold slider, plus two SA2 attributes for richer tooltips.

Why this exists:
    Chart-17 (access x survival) asks whether venues nearer a stop survive better. The answer is
    "no" — but a sceptic can argue we drew "near" at the wrong distance. The slider lets the reader
    redefine "near" as 200 m, 400 m, or 800 m and watch the dot cloud slide horizontally while the
    survival axis (and the flat regression) stay put. To drive that without re-running the heavy geo
    pipeline, we recompute each SA2's "share of venues within T metres of a stop" straight from the
    already-shipped venue-level distances.

Inputs (already shipped — no raw/ access needed):
    src/data/venues_web.csv          one row per venue, with nearest_stop_distance_m + sa2_code_2021
    src/data/sa2_summary_web.csv     per-SA2 attributes (SEIFA decile, median nearest-stop distance)
    src/data/chart_sa2_survival.csv  per-SA2 two-year survival (the file we augment)

What it writes back into chart_sa2_survival.csv (idempotent — safe to re-run):
    share_200, share_400, share_800       % of the SA2's venues within that walk of a stop
    irsad_aus_decile                       SEIFA IRSAD decile (1 disadvantaged -> 10 advantaged)
    median_nearest_stop_distance_m         median walk to the nearest stop, metres

Consistency guarantee: share_400 is computed with the SAME definition and venue set that the
choropleth (chart-03) uses for its 400 m share, so the slider's default (400 m) position reproduces
the existing access_share_pct to within rounding — the default view does not silently shift. This is
asserted at the bottom; the script exits non-zero if the gap ever exceeds 2 points for any SA2.

Survival itself is threshold-independent (a venue either stayed licensed or didn't), so only the
x-axis moves with the slider. Association only, as everywhere in §3.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "data"
VENUES = DATA / "venues_web.csv"
SUMMARY = DATA / "sa2_summary_web.csv"
SURVIVAL = DATA / "chart_sa2_survival.csv"

THRESHOLDS = [200, 400, 800]  # metres; identical bands to wiki/data/derived-fields.md


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    venues = pd.read_csv(VENUES, dtype={"sa2_code_2021": str})
    summary = pd.read_csv(SUMMARY, dtype={"sa2_code_2021": str})
    survival = pd.read_csv(SURVIVAL, dtype={"sa2_code_2021": str})

    # ---- per-SA2 share of venues within each threshold -----------------------------
    g = venues.dropna(subset=["sa2_code_2021"]).groupby("sa2_code_2021")["nearest_stop_distance_m"]
    shares = pd.DataFrame(
        {f"share_{t}": (g.apply(lambda s, t=t: (s <= t).mean() * 100)).round(1) for t in THRESHOLDS}
    ).reset_index()

    # ---- two SA2 attributes for richer tooltips ------------------------------------
    attrs = summary[["sa2_code_2021", "irsad_aus_decile", "median_nearest_stop_distance_m"]].copy()
    attrs["irsad_aus_decile"] = attrs["irsad_aus_decile"].astype("Int64")  # 3 not 3.0; blank if NA
    attrs["median_nearest_stop_distance_m"] = attrs["median_nearest_stop_distance_m"].round(0).astype("Int64")

    # ---- merge (drop any prior copies of these columns so re-runs stay idempotent) --
    added = ["share_200", "share_400", "share_800", "irsad_aus_decile", "median_nearest_stop_distance_m"]
    survival = survival.drop(columns=[c for c in added if c in survival.columns])
    out = survival.merge(shares, on="sa2_code_2021", how="left").merge(attrs, on="sa2_code_2021", how="left")

    # ---- consistency assertion: share_400 == existing access_share_pct -------------
    gap = (out["access_share_pct"] - out["share_400"]).abs()
    worst = gap.max()
    log(f"share_400 vs access_share_pct: max abs gap {worst:.2f} pts (mean {gap.mean():.3f})")
    if worst > 2.0:
        log("ABORT: share_400 diverges from access_share_pct by >2 pts — definitions drifted.")
        return 1

    grand = {t: round(out[f"share_{t}"].mean(), 1) for t in THRESHOLDS}
    log(f"survival-set grand-mean access: 200 m {grand[200]}% | 400 m {grand[400]}% | 800 m {grand[800]}%")

    out.to_csv(SURVIVAL, index=False)
    log(f"wrote {SURVIVAL.relative_to(ROOT)} ({len(out)} rows, +{len(added)} columns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
