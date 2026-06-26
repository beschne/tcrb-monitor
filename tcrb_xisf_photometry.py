#!/usr/bin/env python3
"""
Differential photometry for T CrB (TG band) computed directly from a
plate-solved, stacked XISF master light - no PixInsight DynamicPSF export
needed.

Workflow:
  1. Stack and plate-solve lights in PixInsight as usual (WBPP +
     ImageSolver/StarAlignment's astrometric solution), producing a
     master light XISF with an embedded PCL:AstrometricSolution.
  2. Run this script, passing the XISF file as an argument. It:
     - Parses the XISF header directly (no PixInsight install required)
       to get the green channel pixel data and the astrometric solution.
     - Projects T CrB's catalog position, and every AAVSO VSP
       comparison star within the frame, to pixel coordinates using
       PixInsight's own native<->image spline grid (reproduced via
       bilinear interpolation, not re-derived).
     - Fits a 2D Gaussian + constant background to each star to get its
       flux.
     - Computes T CrB's TG magnitude via the same differential-photometry
       math as tcrb_dynamicpsf_photometry.py (imported from there).

This assumes the XISF image is already debayered (3-channel RGB, as
produced by WBPP's calibration step) - BAYERPAT/CFASourcePattern in the
header is provenance metadata about the original sensor, not an
indication that the stored pixel data is still a Bayer mosaic.

Dependencies: numpy, astropy (already used by plot_tcrb_csv.py / installed
in .venv), requests (for the VSP lookups, via the imported module).
"""

import base64
import math
import re
import statistics
import struct
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from astropy.coordinates import AltAz, EarthLocation, get_body
from astropy.modeling import fitting, models
from astropy.time import Time
import astropy.units as u

# tcrb_dynamicpsf_photometry.py (VSP query/cache + differential_magnitude
# reuse) lives in legacy/ now - not a package, so it needs to be on the
# import path explicitly.
sys.path.insert(0, str(Path(__file__).parent / "legacy"))
import tcrb_dynamicpsf_photometry as dpsf

# ---------------------------------------------------------------------------
# Observer location (Bad Homburg, Germany)
# ---------------------------------------------------------------------------

OBSERVER_LOCATION = EarthLocation(lat=50.2267 * u.deg, lon=8.6183 * u.deg, height=160 * u.m)


def moon_info(obs_time):
    """Return (phase_pct, altitude_deg) for the moon at obs_time from Bad Homburg."""
    altaz_frame = AltAz(obstime=obs_time, location=OBSERVER_LOCATION)
    moon = get_body("moon", obs_time, OBSERVER_LOCATION)
    sun = get_body("sun", obs_time, OBSERVER_LOCATION)
    elongation = moon.separation(sun)
    phase_pct = round((1 - math.cos(elongation.rad)) / 2 * 100)
    altitude_deg = round(moon.transform_to(altaz_frame).alt.deg)
    return phase_pct, altitude_deg


# ---------------------------------------------------------------------------
# Tunable Constants
# ---------------------------------------------------------------------------

BAND_LABEL = "TG"  # same convention as tcrb_dynamicpsf_photometry.py

GREEN_CHANNEL_INDEX = 1  # PixInsight RGB images store channels as R,G,B

# This frame covers ~1.5x2.5 deg - far larger than the 30'/16.0 defaults in
# tcrb_dynamicpsf_photometry.py (tuned for a handful of manually-clicked
# stars). We override that module's globals before calling its VSP query.
XISF_VSP_FOV_ARCMIN = 180.0  # AAVSO VSP's hard maximum
XISF_VSP_MAGLIMIT = 13.0

PSF_FIT_BOX_PX = 20          # half-width of the PSF fit cutout
MIN_AMPLITUDE = 0.005        # reject stars too faint to fit reliably
SATURATION_LEVEL = 0.85      # reject stars with clipped/saturated peaks
MAX_CENTROID_SHIFT_PX = 10.0  # reject fits that wandered from the prediction
STDDEV_BOUNDS_PX = (0.3, 8.0)  # plausible stellar PSF sigma range
SIGMA_CLIP_THRESHOLD = 2.5     # reject derived-magnitude outliers vs. the
                                # consensus of other comparison stars (see
                                # sigma_clip_mask)


# ---------------------------------------------------------------------------
# XISF parsing
# ---------------------------------------------------------------------------

XISF_NS = {"x": "http://www.pixinsight.com/xisf"}


class XisfAstrometry:
    """Reproduces PixInsight's native<->image coordinate mapping for a
    plate-solved XISF, using the astrometric solution's reference point
    plus its precomputed NativeToImage spline grid (bilinear-interpolated
    rather than re-deriving the surface-spline math)."""

    def __init__(self, image_el: ET.Element, path: Path):
        self.ref_cel = self._read_vector(image_el, path,
            "PCL:AstrometricSolution:ReferenceCelestialCoordinates")
        self.ref_img = self._read_vector(image_el, path,
            "PCL:AstrometricSolution:ReferenceImageCoordinates")
        self.grid_rect = self._read_vector(image_el, path,
            "PCL:AstrometricSolution:SplineWorldTransformation:"
            "PointGridInterpolation:NativeToImage:Rect")
        self.grid_delta = self._read_scalar(image_el,
            "PCL:AstrometricSolution:SplineWorldTransformation:"
            "PointGridInterpolation:NativeToImage:Delta")
        self.grid_x = self._read_matrix(image_el, path,
            "PCL:AstrometricSolution:SplineWorldTransformation:"
            "PointGridInterpolation:NativeToImage:GridX")
        self.grid_y = self._read_matrix(image_el, path,
            "PCL:AstrometricSolution:SplineWorldTransformation:"
            "PointGridInterpolation:NativeToImage:GridY")
        lin_mat = self._read_matrix(image_el, path,
            "PCL:AstrometricSolution:LinearTransformationMatrix")
        self.lin_mat_inv = np.linalg.inv(lin_mat)

    @staticmethod
    def _find(image_el, pid):
        el = image_el.find(f"x:Property[@id='{pid}']", XISF_NS)
        if el is None:
            sys.exit(f"Error: astrometric solution property '{pid}' not found - "
                      f"is this XISF plate-solved?")
        return el

    @classmethod
    def _read_bytes(cls, image_el, path, pid, expect_count):
        el = cls._find(image_el, pid)
        loc = el.get("location")
        if loc.startswith("inline:base64"):
            return base64.b64decode(el.text.strip())
        _, off, length = loc.split(":")
        with open(path, "rb") as f:
            f.seek(int(off))
            return f.read(int(length))

    @classmethod
    def _read_vector(cls, image_el, path, pid):
        el = cls._find(image_el, pid)
        n = int(el.get("length"))
        raw = cls._read_bytes(image_el, path, pid, n)
        return np.frombuffer(raw, dtype="<f8", count=n)

    @classmethod
    def _read_matrix(cls, image_el, path, pid):
        el = cls._find(image_el, pid)
        rows, cols = int(el.get("rows")), int(el.get("columns"))
        raw = cls._read_bytes(image_el, path, pid, rows * cols)
        return np.frombuffer(raw, dtype="<f8", count=rows * cols).reshape(rows, cols)

    @classmethod
    def _read_scalar(cls, image_el, pid):
        return float(cls._find(image_el, pid).get("value"))

    def radec_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        """Standard gnomonic (tangent-plane) projection, matching PixInsight's
        AstrometricSolution convention (CelestialPoleNativeCoordinates =
        (180, 90), ReferenceNativeCoordinates = (0, 90) - i.e. the reference
        point is the native pole, the standard zenithal-projection setup)."""
        ra, dec, ra0, dec0 = map(math.radians,
                                  (ra_deg, dec_deg, self.ref_cel[0], self.ref_cel[1]))
        dra = ra - ra0
        denom = math.sin(dec0) * math.sin(dec) + math.cos(dec0) * math.cos(dec) * math.cos(dra)
        xi = math.cos(dec) * math.sin(dra) / denom
        eta = (math.cos(dec0) * math.sin(dec)
               - math.sin(dec0) * math.cos(dec) * math.cos(dra)) / denom
        nx, ny = math.degrees(xi), math.degrees(eta)
        return self._native_to_pixel(nx, ny)

    def _native_to_pixel(self, nx: float, ny: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.grid_rect
        rows, cols = self.grid_x.shape
        if x0 <= nx <= x1 and y0 <= ny <= y1:
            col_f = (nx - x0) / self.grid_delta
            row_f = (ny - y0) / self.grid_delta
            col_f = min(max(col_f, 0.0), cols - 1.0001)
            row_f = min(max(row_f, 0.0), rows - 1.0001)
            c0, r0 = int(col_f), int(row_f)
            fc, fr = col_f - c0, row_f - r0

            def bilerp(grid):
                return (grid[r0, c0] * (1 - fr) * (1 - fc)
                        + grid[r0, c0 + 1] * (1 - fr) * fc
                        + grid[r0 + 1, c0] * fr * (1 - fc)
                        + grid[r0 + 1, c0 + 1] * fr * fc)

            return float(bilerp(self.grid_x)), float(bilerp(self.grid_y))

        # Outside the spline grid's domain (shouldn't happen for stars inside
        # the frame) - fall back to the linear approximation.
        off = self.lin_mat_inv @ np.array([nx, ny])
        return float(self.ref_img[0] + off[0]), float(self.ref_img[1] + off[1])


def read_fits_keyword(image_el: ET.Element, name: str) -> str | None:
    """Reads a <FITSKeyword> value, stripping the FITS single-quote string
    convention PixInsight preserves (e.g. value="'2026-06-05T23:55:28.971'")."""
    el = image_el.find(f"x:FITSKeyword[@name='{name}']", XISF_NS)
    if el is None:
        return None
    return el.get("value", "").strip("'")


def read_stack_frame_count(image_el: ET.Element) -> int | None:
    """Reads the number of subframes ImageIntegration actually combined,
    from its parameters embedded in the PixInsight:ProcessingHistory
    property. This can differ from the number of SubframeSelector-approved
    "..._a.xisf" files on disk for that session (ImageIntegration's own
    pixel rejection can exclude a few more) - so it's read from the master
    itself rather than inferred from the lights folder."""
    el = image_el.find("x:Property[@id='PixInsight:ProcessingHistory']", XISF_NS)
    if el is None or el.text is None:
        return None
    m = re.search(r'<parameter id="numberOfImages" value="(\d+)"/>', el.text)
    return int(m.group(1)) if m else None


def load_xisf(path: Path) -> tuple[np.ndarray, XisfAstrometry, str | None, int | None, str | None]:
    """Returns (green_channel_array, astrometry, date_obs_isot_or_None,
    num_subframes_or_None, exptime_seconds_str_or_None).

    date_obs is the FITS DATE-OBS keyword, taken as-is and used directly as
    the reported observation time. PixInsight's ImageIntegration sets it to
    the midpoint between the first and last subframe's start times (not
    true mid-exposure, since it ignores exposure duration) - close enough
    for short subs, but unreliable in practice for Seestar stacks, so these
    values are corrected by hand to genuine mid-exposure time before running
    this script. DATE-END is not read or used at all."""
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"XISF0100":
            sys.exit(f"Error: {path} is not an XISF 1.0 file (signature={sig!r}).")
        header_len = struct.unpack("<I", f.read(4))[0]
        f.read(4)  # reserved
        xml_bytes = f.read(header_len)

    root = ET.fromstring(xml_bytes)
    image_el = root.find("x:Image", XISF_NS)
    if image_el is None:
        sys.exit(f"Error: no <Image> element found in {path}.")

    width, height, nchannels = (int(v) for v in image_el.get("geometry").split(":"))
    if nchannels < 2:
        sys.exit(f"Error: image has only {nchannels} channel(s) - expected a "
                  f"debayered RGB master light.")
    if image_el.get("sampleFormat") != "Float32":
        sys.exit(f"Error: unsupported sampleFormat "
                  f"{image_el.get('sampleFormat')!r} (expected Float32).")

    loc = image_el.get("location")
    if not loc.startswith("attachment:"):
        sys.exit(f"Error: unsupported pixel data location {loc!r} "
                  f"(only uncompressed attachment blocks are supported).")
    _, off, length = loc.split(":")
    with open(path, "rb") as f:
        f.seek(int(off))
        raw = f.read(int(length))
    data = np.frombuffer(raw, dtype="<f4").reshape(nchannels, height, width)
    green = data[GREEN_CHANNEL_INDEX]

    astrometry = XisfAstrometry(image_el, path)
    date_obs = read_fits_keyword(image_el, "DATE-OBS")
    num_frames = read_stack_frame_count(image_el)
    exptime = read_fits_keyword(image_el, "EXPTIME")
    return green, astrometry, date_obs, num_frames, exptime


# ---------------------------------------------------------------------------
# PSF photometry
# ---------------------------------------------------------------------------

def fit_psf(channel: np.ndarray, x0: float, y0: float, box: int = PSF_FIT_BOX_PX):
    """Fits a 2D Gaussian + constant background around (x0, y0). Returns a
    dict with flux/amplitude/centroid/stddev, or None if the cutout would
    run off the edge of the frame."""
    h, w = channel.shape
    xi0, yi0 = int(round(x0)), int(round(y0))
    x_lo, x_hi = xi0 - box, xi0 + box + 1
    y_lo, y_hi = yi0 - box, yi0 + box + 1
    if x_lo < 0 or y_lo < 0 or x_hi > w or y_hi > h:
        return None

    cutout = channel[y_lo:y_hi, x_lo:x_hi]
    yy, xx = np.mgrid[y_lo:y_hi, x_lo:x_hi]

    bg_guess = float(np.median(cutout))
    amp_guess = float(cutout.max() - bg_guess)

    gauss = models.Gaussian2D(
        amplitude=amp_guess, x_mean=x0, y_mean=y0,
        x_stddev=2.0, y_stddev=2.0,
        bounds={
            "amplitude": (0.0, 1.0),
            "x_stddev": STDDEV_BOUNDS_PX,
            "y_stddev": STDDEV_BOUNDS_PX,
        },
        fixed={"theta": True},  # orientation is irrelevant for flux; fixing it
                                 # avoids a known LevMar/TRF degeneracy where
                                 # theta and the stddevs run away together.
    )
    const = models.Const2D(amplitude=bg_guess)
    # TRFLSQFitter (trust-region reflective) actually respects the bounds
    # above; LevMarLSQFitter silently ignores them and is prone to
    # diverging to degenerate (near-zero-width, huge-theta) solutions.
    fitter = fitting.TRFLSQFitter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = fitter(gauss + const, xx, yy, cutout)

    g, c = fitted[0], fitted[1]
    flux = 2 * math.pi * g.amplitude.value * abs(g.x_stddev.value) * abs(g.y_stddev.value)
    return {
        "flux": flux,
        "amplitude": g.amplitude.value,
        "background": c.amplitude.value,
        "x_fit": g.x_mean.value,
        "y_fit": g.y_mean.value,
        "x_stddev": g.x_stddev.value,
        "y_stddev": g.y_stddev.value,
    }


def quality_ok(fit: dict, x_pred: float, y_pred: float) -> tuple[bool, str]:
    if fit["amplitude"] < MIN_AMPLITUDE:
        return False, f"amplitude {fit['amplitude']:.4f} below {MIN_AMPLITUDE}"
    if fit["amplitude"] + fit["background"] > SATURATION_LEVEL:
        return False, f"peak {fit['amplitude'] + fit['background']:.3f} likely saturated"
    shift = math.hypot(fit["x_fit"] - x_pred, fit["y_fit"] - y_pred)
    if shift > MAX_CENTROID_SHIFT_PX:
        return False, f"fit centroid shifted {shift:.1f}px from predicted position"
    return True, ""


def sigma_clip_mask(values: list[float], sigma: float = SIGMA_CLIP_THRESHOLD,
                     max_iter: int = 5) -> list[bool]:
    """Iterative median/MAD sigma-clipping. Returns a keep-mask the same
    length as values.

    This exists because SATURATION_LEVEL is a fixed peak-value cutoff
    calibrated against one stack's depth - it doesn't generalize to
    shallower stacks, where a very bright star's peak can sit just under
    the cutoff while its PSF is still distorted enough (blooming,
    non-Gaussian wings) to throw off the Gaussian flux fit by a magnitude
    or more. Sigma-clipping catches that case directly, by comparing each
    star's derived T CrB magnitude against the consensus of the others,
    regardless of why it's wrong.

    No-ops (everything kept) below 4 points - not enough to clip
    meaningfully without risking discarding genuine measurements.
    """
    mask = [True] * len(values)
    if len(values) < 4:
        return mask
    for _ in range(max_iter):
        kept_vals = [v for v, k in zip(values, mask) if k]
        if len(kept_vals) < 4:
            break
        median = statistics.median(kept_vals)
        mad = statistics.median(abs(v - median) for v in kept_vals)
        scale = mad * 1.4826  # normal-equivalent sigma from the MAD
        if scale == 0:
            break
        new_mask = [k and abs(v - median) <= sigma * scale for v, k in zip(values, mask)]
        if new_mask == mask:
            break
        mask = new_mask
    return mask


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_file(xisf_path: Path) -> None:
    if not xisf_path.exists():
        sys.exit(f"Error: file not found: {xisf_path}")

    print(f"Reading {xisf_path} ...")
    green, astrometry, date_obs, num_frames, exptime = load_xisf(xisf_path)
    print(f"  green channel: {green.shape[1]}x{green.shape[0]} px, "
          f"range [{green.min():.4f}, {green.max():.4f}]")
    if date_obs is None:
        sys.exit("Error: no DATE-OBS FITS keyword found - can't determine observation time.")
    obs_time = Time(date_obs, format="isot", scale="utc")
    jd = obs_time.jd
    print(f"  DATE-OBS: {date_obs} UTC  ->  JD {jd:.5f}  ({obs_time.iso} UTC)")
    if num_frames is not None and exptime is not None:
        print(f"  stack: {num_frames} x {float(exptime):g}s")

    print("\nLocating T CrB ...")
    x_pred, y_pred = astrometry.radec_to_pixel(dpsf.TCRB_RA_DEG, dpsf.TCRB_DEC_DEG)
    fit_tcrb = fit_psf(green, x_pred, y_pred)
    if fit_tcrb is None:
        sys.exit("Error: T CrB's predicted position is too close to the frame edge.")
    ok, reason = quality_ok(fit_tcrb, x_pred, y_pred)
    print(f"  predicted pixel ({x_pred:.1f}, {y_pred:.1f}) -> "
          f"fit centroid ({fit_tcrb['x_fit']:.1f}, {fit_tcrb['y_fit']:.1f}), "
          f"flux={fit_tcrb['flux']:.4f}")
    if not ok:
        sys.exit(f"Error: T CrB PSF fit rejected: {reason}")

    print(f"\nQuerying AAVSO VSP for comparison stars "
          f"(fov={XISF_VSP_FOV_ARCMIN}', maglimit={XISF_VSP_MAGLIMIT}) ...")
    # Override tcrb_dynamicpsf_photometry's defaults (tuned for a 30' manual
    # field) for this much wider stacked frame.
    dpsf.VSP_FOV_ARCMIN = XISF_VSP_FOV_ARCMIN
    dpsf.VSP_MAGLIMIT = XISF_VSP_MAGLIMIT

    field_cache = dpsf.load_vsp_cache(dpsf.VSP_CACHE_FILE)
    cache_key = (round(dpsf.TCRB_RA_DEG, 2), round(dpsf.TCRB_DEC_DEG, 2))
    if cache_key not in field_cache:
        field_cache[cache_key] = dpsf.query_vsp_field(dpsf.TCRB_RA_DEG, dpsf.TCRB_DEC_DEG)
        dpsf.save_vsp_cache(field_cache, dpsf.VSP_CACHE_FILE)
        print(f"  VSP cache updated: {dpsf.VSP_CACHE_FILE}")
    else:
        print("  VSP cache hit - no API call needed.")
    candidates = field_cache[cache_key]
    print(f"  {len(candidates)} candidate stars returned.")

    print("\nMeasuring comparison stars:")
    accepted = []
    for entry in candidates:
        try:
            ra = dpsf.parse_sexagesimal_ra(entry["ra"])
            dec = dpsf.parse_sexagesimal_dec(entry["dec"])
        except (KeyError, ValueError):
            continue

        sep_from_tcrb = dpsf.angular_separation_arcsec(ra, dec, dpsf.TCRB_RA_DEG, dpsf.TCRB_DEC_DEG)
        if sep_from_tcrb < dpsf.TCRB_MATCH_RADIUS_ARCSEC:
            continue  # this is T CrB itself

        mag_comp = mag_err = None
        for band_entry in entry.get("bands", []):
            if band_entry.get("band") == dpsf.VSP_BAND:
                mag_comp = band_entry.get("mag")
                mag_err = band_entry.get("error")
                break
        if mag_comp is None:
            continue

        x_pred, y_pred = astrometry.radec_to_pixel(ra, dec)
        if not (PSF_FIT_BOX_PX <= x_pred < green.shape[1] - PSF_FIT_BOX_PX
                and PSF_FIT_BOX_PX <= y_pred < green.shape[0] - PSF_FIT_BOX_PX):
            continue  # outside frame (or too close to its edge for the fit box)

        fit = fit_psf(green, x_pred, y_pred)
        if fit is None:
            continue
        ok, reason = quality_ok(fit, x_pred, y_pred)
        if not ok:
            print(f"  Star at RA={ra:.5f} Dec={dec:.5f} (V={mag_comp:.2f}) rejected: {reason}")
            continue

        m_est = dpsf.differential_magnitude(fit_tcrb["flux"], fit["flux"], mag_comp)
        accepted.append({
            "label": entry.get("label"), "ra": ra, "dec": dec,
            "mag": mag_comp, "mag_err": mag_err, "flux": fit["flux"], "m_est": m_est,
        })
        print(f"  Star at RA={ra:.5f} Dec={dec:.5f} (VSP {dpsf.VSP_BAND}={mag_comp:.2f}, "
              f"pixel=({x_pred:.0f},{y_pred:.0f})): derived T CrB magnitude = {m_est:.3f}")

    if not accepted:
        sys.exit("Error: no valid comparison star left - check thresholds and frame coverage.")

    keep_mask = sigma_clip_mask([a["m_est"] for a in accepted])
    clipped = [a for a, keep in zip(accepted, keep_mask) if not keep]
    accepted = [a for a, keep in zip(accepted, keep_mask) if keep]
    if clipped:
        print(f"\n  Sigma-clipped as outliers ({SIGMA_CLIP_THRESHOLD}-sigma vs. the other "
              f"comparison stars' derived magnitudes):")
        for a in clipped:
            print(f"    label={a['label'] or '?'} RA={a['ra']:.5f} Dec={a['dec']:.5f} "
                  f"(V={a['mag']:.2f}): derived T CrB magnitude {a['m_est']:.3f} is inconsistent "
                  f"with the rest")
    if not accepted:
        sys.exit("Error: no comparison stars left after outlier rejection.")

    estimates = [a["m_est"] for a in accepted]
    mean_mag = sum(estimates) / len(estimates)
    if len(estimates) > 1:
        variance = sum((x - mean_mag) ** 2 for x in estimates) / (len(estimates) - 1)
        std_dev = math.sqrt(variance)
    else:
        std_dev = float("nan")

    print(f"\n--- Result (averaged over all accepted comparison stars) ---")
    print(f"T CrB ({BAND_LABEL}): {mean_mag:.3f} +/- {std_dev:.3f} mag "
          f"(n={len(estimates)} comparison stars)")

    # ---------------------------------------------------------------------
    # AAVSO submission data: a single comp star + a single check star, per
    # https://apps.aavso.org/v2/data/submit/photometry/ - not the multi-star
    # average above. Auto-picked each run as the two labeled, quality-passed
    # stars with the smallest AAVSO catalog magnitude error.
    #
    # Magnitude Error is std_dev from the multi-star average above (the
    # ensemble scatter across all accepted, sigma-clipped comparison stars'
    # independent magnitude estimates) - not formal photon-noise/SNR error
    # propagation from comp/check alone, which this script doesn't compute.
    # This is the standard ad-hoc external error estimate for ensemble
    # differential photometry, and captures real per-observation effects
    # (seeing, flat-fielding, individual comp stars' own catalog accuracy)
    # that a single internal SNR estimate would miss.
    # ---------------------------------------------------------------------
    labeled = sorted(
        (a for a in accepted if a["label"] and a["mag_err"] is not None),
        key=lambda a: a["mag_err"],
    )
    if len(labeled) < 2:
        print("\nWarning: fewer than 2 labeled comparison stars with a catalog error "
              "passed quality checks - can't auto-pick a comp/check pair for AAVSO.")
    else:
        comp, check = labeled[0], labeled[1]
        m_tcrb = dpsf.differential_magnitude(fit_tcrb["flux"], comp["flux"], comp["mag"])
        m_check_derived = dpsf.differential_magnitude(check["flux"], comp["flux"], comp["mag"])
        check_delta = m_check_derived - check["mag"]

        print(f"\n--- AAVSO submission data ---")
        print(f"Star: T CRB")
        print(f"Date (JD): {jd:.5f}  ({obs_time.iso} UTC)")
        print(f"Filter: {BAND_LABEL}")
        print(f"Magnitude: {m_tcrb:.3f}")
        print(f"Magnitude Error: {std_dev:.3f}" if not math.isnan(std_dev) else
              f"Magnitude Error: n/a (only one comparison star passed quality checks)")
        print(f"Comp Star 1: label={comp['label']}  Mag={comp['mag']:.3f} "
              f"(err {comp['mag_err']:.3f})")
        print(f"Check Star: label={check['label']}  Mag={check['mag']:.3f} "
              f"(err {check['mag_err']:.3f})")
        if num_frames is not None and exptime is not None:
            moon_phase, moon_alt = moon_info(obs_time)
            moon_pos = (f"{moon_alt}° above horizon" if moon_alt >= 0
                        else f"{-moon_alt}° below horizon")
            print(f"Comments: Measurement based on a stack of {num_frames} x "
                  f"{float(exptime):g}s exposures. "
                  f"Moon: {moon_phase}% illuminated, {moon_pos}.")
        print(f"  consistency check: check star's derived mag = {m_check_derived:.3f} "
              f"vs. catalog {check['mag']:.3f}  (Delta={check_delta:+.3f})")


def main():
    if len(sys.argv) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <stacked_master_light.xisf> [more.xisf ...]")
    paths = [Path(p) for p in sys.argv[1:]]

    failed = []
    for i, xisf_path in enumerate(paths):
        if len(paths) > 1:
            print(("\n" if i else "") + f"==================== {xisf_path} ====================")
        try:
            process_file(xisf_path)
        except SystemExit as exc:
            print(f"  {exc.code}")
            failed.append(xisf_path)

    if len(paths) > 1 and failed:
        print(f"\n{len(failed)}/{len(paths)} file(s) failed: "
              + ", ".join(str(p) for p in failed))
    if failed and len(failed) == len(paths):
        sys.exit(1)


if __name__ == "__main__":
    main()
