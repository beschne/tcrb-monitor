#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asassn_fetch.py -- ASAS-SN Sky Patrol data fetcher for T CrB ("Blaze Star")

Companion to tcrb_monitor.py. Pulls the ASAS-SN light curve for T CrB and
writes it, deduplicated by JD, to a SEPARATE CSV (asassn_history.csv) using
the SAME column layout as tcrb_history.csv, so plot_tcrb_csv.py can read both
files uniformly and overlay the series.

DESIGN NOTES
------------
- This module is intentionally NOT part of the alert path. The hourly
  AAVSO monitor (tcrb_monitor.py) remains stdlib-only and dependency-free.
  ASAS-SN is added purely as an independent, instrumentally-calibrated
  reference series for analysis / the light-curve plot.
- Recommended cadence: DAILY (launchd / cron). ASAS-SN updates ~nightly,
  so hourly polling would add nothing.
- Caveat (important): ASAS-SN standard aperture photometry SATURATES for
  bright stars. T CrB sits near the g-band saturation edge already at
  quiescence and is unusable in standard photometry during the ~2 mag
  eruption peak. The quality filter below drops most bad points, but do
  NOT treat this series as reliable for the bright phase -- that is what
  the visual/V AAVSO data (and ASAS-SN's ML "saturated stars" method,
  not used here) are for.

DEPENDENCY
----------
    pip install skypatrol       # ASAS-SN Sky Patrol client (replaces pyasassn)
Install into the same .venv that plot_tcrb_csv.py already uses for matplotlib.
See requirements.txt.

Verified working: skypatrol 0.6.21, Python 3.14, pandas 3.x, pyarrow 24.x
(2026-06-19). T CrB is present in master_list. Cone search returns a DataFrame
with columns: asas_sn_id, jd, flux, flux_err, mag, mag_err, limit, fwhm,
image_id, camera, quality (G/B), phot_filter. All columns in _normalise_rows()
match the live schema.

Tested target: standard library + skypatrol. Python 3.9+.
"""

import argparse
import csv
import os
import sys

# --------------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------------
TARGET = "T CrB"

# T CrB position (J2000, optical). Source: SIMBAD 2026-06-19.
RA_DEG = 239.875676  # 15h 59m 30.1622265912s
DEC_DEG = 25.920170  # +25d 55' 12.613382940"
CONE_RADIUS = 5.0    # search radius
CONE_UNITS = "arcsec"
CATALOG = "master_list"

# Keep only quality-good points by default (ASAS-SN flag 'G' vs 'B').
GOOD_ONLY = True

# Files: live next to the script, mirroring tcrb_monitor.py's convention.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "asassn_history.csv")

# CSV schema -- IDENTICAL to tcrb_history.csv so the plotter stays simple.
# (ASAS-SN also offers mag_err / camera / quality. They are dropped here to
#  keep the schema drop-in compatible; add them later if the plot needs them.)
CSV_HEADER = ["jd", "date", "mag", "band", "observer", "fainter_than"]

OBSERVER_TAG = "ASAS-SN"


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
def fetch_asassn(ra=RA_DEG, dec=DEC_DEG, radius=CONE_RADIUS,
                 units=CONE_UNITS, catalog=CATALOG, good_only=GOOD_ONLY):
    """Query ASAS-SN Sky Patrol and return a list of dicts with the same keys
    as tcrb_monitor.fetch_observations():
        jd, date, mag, band, observer, fainter_than
    Returns [] on any failure (so a daily job never crashes hard)."""
    try:
        from pyasassn.client import SkyPatrolClient
    except ImportError:
        print("pyasassn not installed. Run: pip install pyasassn "
              "(into the same .venv as matplotlib).", file=sys.stderr)
        return []

    try:
        client = SkyPatrolClient()
        lcs = client.cone_search(
            ra_deg=ra,
            dec_deg=dec,
            radius=radius,
            units=units,
            catalog=catalog,
            download=True,
            threads=1,
        )
    except Exception as e:  # broad on purpose: unattended daily job
        print(f"ASAS-SN cone search failed: {e}", file=sys.stderr)
        return []

    df = _extract_dataframe(lcs)
    if df is None or len(df) == 0:
        print("ASAS-SN returned no light-curve rows "
              "(target not in catalog, or no data).", file=sys.stderr)
        return []

    return _normalise_rows(df, good_only=good_only)


def _extract_dataframe(lcs):
    """pyasassn returns a LightCurveCollection; .data is the combined
    pandas DataFrame. Fall back gracefully if the object is already a frame."""
    data = getattr(lcs, "data", None)
    if data is not None:
        return data
    # Some versions return the DataFrame directly.
    try:
        import pandas as pd
        if isinstance(lcs, pd.DataFrame):
            return lcs
    except ImportError:
        pass
    return None


def _normalise_rows(df, good_only=True):
    """Map the ASAS-SN DataFrame to our row dicts.

    Verified schema (skypatrol 0.6.21, 2026-06-19):
        asas_sn_id, jd, flux, flux_err, mag, mag_err, limit, fwhm,
        image_id, camera, quality (G/B), phot_filter
    The column lookup is case-insensitive with aliases for forward-compatibility.
    """
    import pandas as pd

    # --- column-name normalisation (defensive) ---------------------------
    cols = {c.lower(): c for c in df.columns}

    def col(*candidates, required=True):
        for cand in candidates:
            if cand in cols:
                return cols[cand]
        if required:
            raise KeyError(f"none of {candidates} in ASAS-SN columns "
                           f"{list(df.columns)}")
        return None

    c_jd = col("jd", "hjd")
    c_mag = col("mag", "magnitude")
    c_lim = col("limit", "mag_limit", required=False)
    c_filt = col("phot_filter", "filter", "band", required=False)
    c_qual = col("quality", "qual", required=False)

    rows = []
    for _, r in df.iterrows():
        # Quality filter (ASAS-SN: 'G' good, 'B' bad).
        if good_only and c_qual is not None:
            q = str(r[c_qual]).strip().upper()
            if q and q != "G":
                continue

        try:
            jd = float(r[c_jd])
        except (TypeError, ValueError):
            continue

        # Non-detection handling: if mag is missing/NaN but a limit exists,
        # record the limit as a "fainter-than" point (mirrors AAVSO logic).
        mag_val = r[c_mag]
        fainter = False
        mag = None
        if pd.isna(mag_val):
            if c_lim is not None and not pd.isna(r[c_lim]):
                mag = float(r[c_lim])
                fainter = True
            else:
                continue
        else:
            mag = float(mag_val)

        # Band label, kept distinct from AAVSO Vis./V so it never gets pooled
        # into the alert thresholds if the CSVs are ever read together.
        filt = str(r[c_filt]).strip() if c_filt is not None else "g"
        band = f"{filt} (ASAS-SN)"

        date_str = _jd_to_utc_string(jd)

        rows.append({
            "jd": jd,
            "date": date_str,
            "mag": mag,
            "band": band,
            "observer": OBSERVER_TAG,
            "fainter_than": fainter,
        })

    return rows


def _jd_to_utc_string(jd):
    """JD -> 'YYYY-MM-DD HH:MM:SS UTC' via pandas (already a dependency)."""
    import pandas as pd
    ts = pd.to_datetime(jd, unit="D", origin="julian", utc=True)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


# --------------------------------------------------------------------------
# CSV history (mirrors tcrb_monitor.py)
# --------------------------------------------------------------------------
def _csv_safe(s):
    """Prevents CSV formula injection in spreadsheet applications."""
    s = str(s)
    return "'" + s if s and s[0] in ("=", "+", "-", "@", "\t", "\r") else s


def append_csv(rows, path=CSV_PATH):
    """Append new rows to the CSV history, deduplicated by JD (5 decimals,
    same key format as tcrb_monitor.py)."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["jd"])

    new = [o for o in rows if f"{o['jd']:.5f}" not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(CSV_HEADER)
        for o in sorted(new, key=lambda x: x["jd"]):
            w.writerow([f"{o['jd']:.5f}", _csv_safe(o["date"]), o["mag"],
                        _csv_safe(o["band"]), _csv_safe(o["observer"]),
                        int(o["fainter_than"])])
    return len(new)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
START_DATE_DEFAULT = "2026-06-14"


def _date_to_jd(date_str):
    """'YYYY-MM-DD' → Julian Date (0h UTC)."""
    import pandas as pd
    return pd.Timestamp(date_str, tz="UTC").to_julian_date()


def run(dry_run=False, good_only=GOOD_ONLY, radius=CONE_RADIUS,
        start_date=START_DATE_DEFAULT):
    rows = fetch_asassn(radius=radius, good_only=good_only)
    if not rows:
        print("No ASAS-SN rows obtained -- nothing written.", file=sys.stderr)
        return 2

    if start_date:
        start_jd = _date_to_jd(start_date)
        rows = [r for r in rows if r["jd"] >= start_jd]
        print(f"Filtered to JD ≥ {start_jd:.1f} ({start_date}): {len(rows)} rows remain.")

    detections = [r for r in rows if not r["fainter_than"]]
    if detections:
        brightest = min(detections, key=lambda r: r["mag"])
        print(f"ASAS-SN: {len(rows)} rows ({len(detections)} detections). "
              f"Brightest: {brightest['mag']:.2f} {brightest['band']} "
              f"on {brightest['date']}.")
    else:
        print(f"ASAS-SN: {len(rows)} rows (no detections, limits only).")

    if dry_run:
        print("[dry-run] No writes.")
        return 0

    n_new = append_csv(rows)
    print(f"Appended {n_new} new record(s) to {CSV_PATH}.")
    return 0


def parse_args():
    p = argparse.ArgumentParser(
        description="ASAS-SN Sky Patrol data fetcher for T CrB "
                    "(analysis companion to tcrb_monitor.py).")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch and display only, no CSV writes")
    p.add_argument("--all-quality", action="store_true",
                   help="keep quality-bad points too (default: good only)")
    p.add_argument("--radius", type=float, default=CONE_RADIUS,
                   help=f"cone-search radius in {CONE_UNITS} "
                        f"(default {CONE_RADIUS})")
    p.add_argument("--start-date", default=START_DATE_DEFAULT,
                   metavar="YYYY-MM-DD",
                   help="discard observations before this date "
                        f"(default {START_DATE_DEFAULT}); pass '' to keep all")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(dry_run=args.dry_run,
                 good_only=not args.all_quality,
                 radius=args.radius,
                 start_date=args.start_date))
