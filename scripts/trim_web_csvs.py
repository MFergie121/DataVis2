#!/usr/bin/env python3
"""Trim src/data/venues_web.csv to only the columns the Vega-Lite specs actually
use, and round coordinates / distances to a sensible precision. Reproducible and
idempotent: re-running on an already-trimmed file is a no-op.

Dropped columns (referenced by no spec; verified with grep over src/specs/):
  - access_class       (long category string, the biggest single byte sink)
  - late_night_flag    (unused boolean)

Kept: venue_category_group, Latitude, Longitude, metro_regional_label,
sa2_code_2021, nearest_stop_distance_m, distance_band, distance_band_order.

Row count is preserved (the validator asserts 23,658). Run from repo root:
    python3 scripts/trim_web_csvs.py
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "src", "data")
VENUES = os.path.join(DATA, "venues_web.csv")
STOPS = os.path.join(DATA, "transport_stops_web.csv")

DROP = {"access_class", "late_night_flag"}


def coord(x):
    """Round to <=5 dp (~1 m) and drop trailing zeros so we never pad bytes on."""
    return ("%.5f" % float(x)).rstrip("0").rstrip(".")


def trim_venues():
    with open(VENUES, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    keep_idx = [i for i, h in enumerate(header) if h not in DROP]
    idx = {h: i for i, h in enumerate(header)}

    out = [[header[i] for i in keep_idx]]
    for r in rows[1:]:
        r[idx["Latitude"]] = coord(r[idx["Latitude"]])
        r[idx["Longitude"]] = coord(r[idx["Longitude"]])
        r[idx["nearest_stop_distance_m"]] = str(round(float(r[idx["nearest_stop_distance_m"]])))
        out.append([r[i] for i in keep_idx])

    with open(VENUES, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(out)
    print(f"venues_web.csv: {len(out) - 1} rows, columns -> {out[0]}")


def trim_stops():
    with open(STOPS, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    for r in rows[1:]:
        r[idx["Latitude"]] = coord(r[idx["Latitude"]])
        r[idx["Longitude"]] = coord(r[idx["Longitude"]])
    with open(STOPS, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    print(f"transport_stops_web.csv: {len(rows) - 1} rows")


if __name__ == "__main__":
    trim_venues()
    trim_stops()
