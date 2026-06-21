#!/usr/bin/env python3
"""
Differential photometry for T CrB (TG band) based on a
PixInsight DynamicPSF export (CSV) that additionally contains
sky coordinates (alpha/delta in degrees) for each star.

Workflow:
  1. In PixInsight: apply DynamicPSF to the extracted green channel,
     click T CrB + comparison stars.
  2. Export the table as CSV named dynamicpsf_export.csv (see DYNAMICPSF_CSV
     below), making sure it contains an "alpha" (RA, degrees) and "delta"
     (Dec, degrees) column per star alongside the standard DynamicPSF
     columns (flux, mad, ...).
  3. Run this script -> for each star, queries the AAVSO VSP API by
     coordinates to retrieve its catalog V magnitude, then computes
     the TG magnitude of T CrB via differential photometry.

VSP results are cached in dynamicpsf_vsp_cache.json — catalog magnitudes
for comparison stars are permanent, so the API is only called once per
unique field position (typically once ever for this fixed field).

No FITS header / WCS handling is required - this script relies
entirely on the alpha/delta columns already present in the CSV.
"""

import csv
import json
import sys
import math
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Tunable Constants
# ---------------------------------------------------------------------------

DYNAMICPSF_CSV = "dynamicpsf_export.csv"   # export from PixInsight (with alpha/delta)
VSP_CACHE_FILE = "dynamicpsf_vsp_cache.json"  # persists VSP lookups across runs
BAND_LABEL = "TG"                          # AAVSO notation for DSLR green channel

# Identification of T CrB within the table. DynamicPSF rows have no
# reliable name field, so we identify T CrB by being the row whose
# alpha/delta is closest to its known catalog position.
TCRB_RA_DEG = 239.882021    # T CrB J2000 RA in degrees  (15h59m30.16s)
TCRB_DEC_DEG = 25.920222    # T CrB J2000 Dec in degrees (+25 55 13.0)
TCRB_MATCH_RADIUS_ARCSEC = 30.0  # max distance to accept a row as "T CrB"

# AAVSO VSP API
VSP_API_URL = "https://app.aavso.org/vsp/api/chart/"
VSP_FOV_ARCMIN = 30.0       # search field of view around each star (arcmin)
VSP_MAGLIMIT = 16.0         # faint cutoff for returned comparison stars
VSP_BAND = "V"              # photometric band to extract from VSP results
VSP_MATCH_RADIUS_ARCSEC = 10.0  # max distance to accept a VSP star as a match
VSP_REQUEST_DELAY_S = 1.0   # politeness delay between API calls

# Quality thresholds for fit evaluation (DynamicPSF columns)
MAX_MAD = 0.05          # MAD too high = poor fit, reject star
MIN_FLUX = 1.0          # flux too low = likely noise (PixInsight normalized scale)


# ---------------------------------------------------------------------------
# Read DynamicPSF CSV (with alpha/delta columns)
# ---------------------------------------------------------------------------

def load_dynamicpsf_csv(path: str) -> list[dict]:
    """
    Reads the DynamicPSF export, which must include "alpha" (RA, degrees)
    and "delta" (Dec, degrees) columns alongside the standard DynamicPSF
    columns (flux, mad, ...). Column names are matched case-insensitively
    since they may vary slightly between PixInsight versions or export
    scripts.
    """
    p = Path(path)
    if not p.exists():
        sys.exit(f"Error: file not found: {path}")

    rows = []
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        sys.exit("Error: DynamicPSF CSV is empty.")

    # Fail fast if alpha/delta are missing, rather than failing deep
    # inside the matching logic later.
    sample = rows[0]
    has_alpha = any(col.strip().lower() == "alpha" for col in sample)
    has_delta = any(col.strip().lower() == "delta" for col in sample)
    if not (has_alpha and has_delta):
        sys.exit("Error: CSV is missing 'alpha' and/or 'delta' columns.")

    return rows


def get_float(row: dict, *candidate_keys: str) -> float:
    """Fetch a value robustly in case column names differ between exports."""
    for key in candidate_keys:
        for col in row:
            if col.strip().lower() == key.lower():
                return float(row[col])
    raise KeyError(f"None of the columns {candidate_keys} found in {list(row.keys())}")


def _get_raw_col(row: dict, key: str) -> str:
    for col in row:
        if col.strip().lower() == key.lower():
            return row[col].strip()
    raise KeyError(f"Column '{key}' not found in {list(row.keys())}")


def get_ra_deg(row: dict) -> float:
    """Return RA in decimal degrees from either decimal or 'HH MM SS.ss' format."""
    raw = _get_raw_col(row, "alpha")
    try:
        return float(raw)
    except ValueError:
        h, m, s = (float(x) for x in raw.split())
        return (h + m / 60.0 + s / 3600.0) * 15.0


def get_dec_deg(row: dict) -> float:
    """Return Dec in decimal degrees from either decimal or '±DD MM SS.ss' format."""
    raw = _get_raw_col(row, "delta")
    try:
        return float(raw)
    except ValueError:
        sign = -1.0 if raw.startswith("-") else 1.0
        d, m, s = (float(x) for x in raw.lstrip("+-").split())
        return sign * (d + m / 60.0 + s / 3600.0)


# ---------------------------------------------------------------------------
# Spherical geometry helpers
# ---------------------------------------------------------------------------

def angular_separation_arcsec(ra1_deg: float, dec1_deg: float,
                               ra2_deg: float, dec2_deg: float) -> float:
    """
    Angular separation between two sky positions using the haversine
    formula (sufficiently accurate for field-of-view scale separations,
    avoids small-angle cos(dec) pitfalls near the poles - not relevant
    here, but cheap to do correctly).
    """
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    d_ra = ra2 - ra1
    d_dec = dec2 - dec1
    a = (math.sin(d_dec / 2) ** 2
         + math.cos(dec1) * math.cos(dec2) * math.sin(d_ra / 2) ** 2)
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return math.degrees(c) * 3600.0


# ---------------------------------------------------------------------------
# Persistent VSP cache (JSON, keyed by "ra,dec" rounded to 2 decimal places)
# ---------------------------------------------------------------------------

def load_vsp_cache(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {tuple(float(x) for x in k.split(",")): v for k, v in raw.items()}


def save_vsp_cache(cache: dict, path: str) -> None:
    raw = {f"{k[0]},{k[1]}": v for k, v in cache.items()}
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)


# ---------------------------------------------------------------------------
# AAVSO VSP lookup
# ---------------------------------------------------------------------------

def query_vsp_field(ra_deg: float, dec_deg: float) -> list[dict]:
    """
    Queries the AAVSO VSP API for comparison stars around a given sky
    position. Returns the raw list of photometry entries from the
    response (each with 'auid', 'ra', 'dec', 'bands').
    """
    params = {
        "format": "json",
        "ra": ra_deg,
        "dec": dec_deg,
        "fov": VSP_FOV_ARCMIN,
        "maglimit": VSP_MAGLIMIT,
    }
    try:
        resp = requests.get(VSP_API_URL, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Warning: VSP request failed for RA={ra_deg:.5f} Dec={dec_deg:.5f}: {exc}")
        return []

    data = resp.json()
    return data.get("photometry", [])


def parse_sexagesimal_ra(ra_str: str) -> float:
    """Converts VSP's 'HH:MM:SS.ss' RA string to decimal degrees."""
    h, m, s = (float(x) for x in ra_str.split(":"))
    return (h + m / 60.0 + s / 3600.0) * 15.0


def parse_sexagesimal_dec(dec_str: str) -> float:
    """Converts VSP's '+DD:MM:SS.s' Dec string to decimal degrees."""
    sign = -1.0 if dec_str.strip().startswith("-") else 1.0
    d, m, s = (float(x) for x in dec_str.lstrip("+-").split(":"))
    return sign * (d + m / 60.0 + s / 3600.0)


def find_v_magnitude(ra_deg: float, dec_deg: float,
                      field_cache: dict) -> tuple[float | None, float]:
    """
    Looks up the catalog V magnitude for a star at (ra_deg, dec_deg) via
    VSP. Uses field_cache (keyed by rounded RA/Dec) to avoid repeating
    the same VSP field query for stars that are close together - all
    comparison stars in one frame typically fall within one VSP field
    of view, so in practice this means a single API call covers them all.

    Returns (magnitude_or_None, separation_arcsec_of_best_match).
    """
    cache_key = (round(ra_deg, 2), round(dec_deg, 2))
    if cache_key not in field_cache:
        field_cache[cache_key] = query_vsp_field(ra_deg, dec_deg)
        time.sleep(VSP_REQUEST_DELAY_S)

    candidates = field_cache[cache_key]
    best_mag = None
    best_sep = float("inf")

    for entry in candidates:
        try:
            cand_ra = parse_sexagesimal_ra(entry["ra"])
            cand_dec = parse_sexagesimal_dec(entry["dec"])
        except (KeyError, ValueError):
            continue

        sep = angular_separation_arcsec(ra_deg, dec_deg, cand_ra, cand_dec)
        if sep > VSP_MATCH_RADIUS_ARCSEC or sep >= best_sep:
            continue

        for band_entry in entry.get("bands", []):
            if band_entry.get("band") == VSP_BAND:
                best_mag = band_entry.get("mag")
                best_sep = sep
                break

    return best_mag, best_sep


# ---------------------------------------------------------------------------
# Differential photometry
# ---------------------------------------------------------------------------

def differential_magnitude(flux_target: float, flux_comp: float, mag_comp: float) -> float:
    """
    Classic differential photometry:
    m_target = m_comp - 2.5 * log10(F_target / F_comp)
    """
    if flux_target <= 0 or flux_comp <= 0:
        raise ValueError("Flux values must be positive.")
    return mag_comp - 2.5 * math.log10(flux_target / flux_comp)


def find_tcrb_row(rows: list[dict]) -> dict:
    """Identifies the T CrB row by proximity to its known catalog position."""
    best_row = None
    best_sep = float("inf")
    for row in rows:
        ra = get_ra_deg(row)
        dec = get_dec_deg(row)
        sep = angular_separation_arcsec(ra, dec, TCRB_RA_DEG, TCRB_DEC_DEG)
        if sep < best_sep:
            best_sep = sep
            best_row = row

    if best_row is None or best_sep > TCRB_MATCH_RADIUS_ARCSEC:
        sys.exit(f"Error: no row found within {TCRB_MATCH_RADIUS_ARCSEC}\" of T CrB "
                  f"(closest was {best_sep:.1f}\").")
    return best_row


def estimate_tcrb_magnitude(rows: list[dict]) -> tuple[float, float, int]:
    """
    Computes the TG magnitude of T CrB using all other rows in the table
    as comparison stars, with V magnitudes resolved automatically via
    the AAVSO VSP API based on each row's alpha/delta.
    Returns (mean, standard deviation, number of comparison stars used).
    """
    tcrb_row = find_tcrb_row(rows)
    flux_tcrb = get_float(tcrb_row, "flux")
    mad_tcrb = get_float(tcrb_row, "mad")

    if mad_tcrb > MAX_MAD:
        print(f"  Warning: T CrB fit has high MAD ({mad_tcrb:.4f}) - result may be unreliable.")

    field_cache = load_vsp_cache(VSP_CACHE_FILE)
    cache_size_before = len(field_cache)
    estimates = []

    for row in rows:
        if row is tcrb_row:
            continue

        ra = get_ra_deg(row)
        dec = get_dec_deg(row)
        flux_comp = get_float(row, "flux")
        mad_comp = get_float(row, "mad")

        if mad_comp > MAX_MAD:
            print(f"  Star at RA={ra:.5f} Dec={dec:.5f} rejected: "
                  f"MAD {mad_comp:.4f} > threshold {MAX_MAD}")
            continue
        if flux_comp < MIN_FLUX:
            print(f"  Star at RA={ra:.5f} Dec={dec:.5f} rejected: "
                  f"flux {flux_comp:.1f} < threshold {MIN_FLUX}")
            continue

        mag_comp, sep = find_v_magnitude(ra, dec, field_cache)
        if mag_comp is None:
            print(f"  Star at RA={ra:.5f} Dec={dec:.5f} rejected: "
                  f"no VSP {VSP_BAND}-band match within {VSP_MATCH_RADIUS_ARCSEC}\"")
            continue

        m_est = differential_magnitude(flux_tcrb, flux_comp, mag_comp)
        estimates.append(m_est)
        print(f"  Comparison star at RA={ra:.5f} Dec={dec:.5f} "
              f"(VSP {VSP_BAND}={mag_comp:.2f}, match {sep:.1f}\"): "
              f"derived T CrB magnitude = {m_est:.3f}")

    if len(field_cache) > cache_size_before:
        save_vsp_cache(field_cache, VSP_CACHE_FILE)
        print(f"  VSP cache updated: {VSP_CACHE_FILE}")
    else:
        print(f"  VSP cache hit — no API calls needed.")

    if not estimates:
        sys.exit("Error: no valid comparison star left - check thresholds and VSP matches.")

    mean_mag = sum(estimates) / len(estimates)
    if len(estimates) > 1:
        variance = sum((x - mean_mag) ** 2 for x in estimates) / (len(estimates) - 1)
        std_dev = math.sqrt(variance)
    else:
        std_dev = float("nan")

    return mean_mag, std_dev, len(estimates)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Reading DynamicPSF export: {DYNAMICPSF_CSV}")
    rows = load_dynamicpsf_csv(DYNAMICPSF_CSV)
    print(f"  {len(rows)} stars found.\n")

    print("Computing differential magnitude for T CrB (V magnitudes via AAVSO VSP):")
    mag, std, n = estimate_tcrb_magnitude(rows)

    print(f"\n--- Result ---")
    print(f"T CrB ({BAND_LABEL}): {mag:.3f} +/- {std:.3f} mag  (n={n} comparison stars)")


if __name__ == "__main__":
    main()
