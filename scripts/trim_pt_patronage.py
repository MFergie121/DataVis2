#!/usr/bin/env python3
"""
trim_pt_patronage.py — Melbourne metropolitan PT patronage, monthly, for §7 chart-19.

Source: Department of Transport and Planning Victoria,
        "Monthly public transport patronage by mode", CC BY 4.0.
        https://opendata.transport.vic.gov.au/dataset/monthly-public-transport-patronage-by-mode
        Raw: raw/data/pt_patronage_monthly.csv (Jan 2018 → Jan 2026, 97 months).

The raw file is wide (one column per mode/region) with comma-formatted ints, a UTF-8 BOM,
and a tail of blank padding rows. We melt it long, keep only the three *Metropolitan* modes
(this is a Melbourne story — the §7 section talks about how Melbourne moves, not regional
Victoria), parse to an ISO date, round patronage to millions to 2 dp, and ship a single
small CSV ready for Vega-Lite's temporal/quantitative encoding.

Output: src/data/melbourne_pt_patronage.csv (~290 rows, ~10 KB).

Run: .venv/bin/python3 scripts/trim_pt_patronage.py
Deterministic.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "data" / "pt_patronage_monthly.csv"
OUT = ROOT / "src" / "data" / "melbourne_pt_patronage.csv"

METRO_COLS = ["Metropolitan train", "Metropolitan tram", "Metropolitan bus"]
# Short labels for the chart legend — match the existing house convention
# (Title-case mode names; see chart-04's category × mode heatmap).
MODE_LABEL = {
    "Metropolitan train": "Train",
    "Metropolitan tram": "Tram",
    "Metropolitan bus": "Bus",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    df = pd.read_csv(RAW, thousands=",", encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.dropna(subset=["Year", "Month"])
    log(f"raw rows after drop-blank: {len(df)}")
    log(f"year range: {int(df.Year.min())} → {int(df.Year.max())}")

    df["date"] = pd.to_datetime(
        dict(year=df.Year.astype(int), month=df.Month.astype(int), day=1)
    ).dt.strftime("%Y-%m-%d")

    long = df.melt(
        id_vars=["date"],
        value_vars=METRO_COLS,
        var_name="mode_raw",
        value_name="trips",
    )
    long["mode"] = long["mode_raw"].map(MODE_LABEL)
    long = long.drop(columns=["mode_raw"])
    # Drop month×mode cells that have no value (e.g. the latest month's tram cell
    # before DOT publishes it). Vega-Lite handles missing points fine, but a
    # missing row is cleaner than a NaN cell.
    before = len(long)
    long = long.dropna(subset=["trips"])
    if before != len(long):
        log(f"dropped {before - len(long)} unreported month×mode cell(s)")

    # millions of trips, 2 dp — keeps the file small and the y-axis readable.
    long["trips_millions"] = (long["trips"] / 1_000_000).round(2)
    long = long[["date", "mode", "trips_millions"]].sort_values(["mode", "date"])

    long.to_csv(OUT, index=False)
    log(f"wrote {OUT.name}: {len(long)} rows | {OUT.stat().st_size:,} bytes")
    log(f"latest by mode: " + ", ".join(
        f"{m}={long[long['mode']==m]['date'].max()}" for m in MODE_LABEL.values()
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
