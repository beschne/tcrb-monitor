# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Monitors T Coronae Borealis ("Blaze Star") for a nova eruption by polling AAVSO WebObs hourly. When brightness crosses configurable thresholds it sends a macOS notification and/or a Signal message. A local CSV accumulates all observations; a JSON file tracks the current alert level to prevent duplicate alerts.

## Scripts

| File | Purpose |
|------|---------|
| `tcrb_monitor.py` | **Current version.** Standard library only (Python 3.9+). Alerts via macOS notification + Signal. |
| `asassn_fetch.py` | ASAS-SN Sky Patrol fetcher. Analysis companion — **not** in the alert path. Writes `asassn_history.csv`. Requires `skypatrol` (`.venv/`). |
| `plot_tcrb_csv.py` | Plots `tcrb_history.csv` → PNG. Requires `matplotlib` (`.venv/` in this folder). |
| `legacy/tcrb_dynamicpsf_photometry.py` | PixInsight DynamicPSF differential photometry — manual tool, **not** in the alert path. Superseded by `tcrb_xisf_photometry.py` but kept as a cross-check. See `legacy/CLAUDE.md`. Requires `requests`. |
| `tcrb_xisf_photometry.py` | Fully automated differential photometry straight from a plate-solved stacked XISF — no PixInsight session needed. **Not** in the alert path. See section below. Imports `legacy/tcrb_dynamicpsf_photometry.py` for VSP/differential-magnitude code. Requires `numpy`, `astropy`, `scipy`, `requests`. |
| `tools/tcrb_session_midtime.py` | Computes a session's true mid-exposure time from its SubframeSelector `..._a.xisf` light frames, to hand-correct a stack's `DATE-OBS` for `tcrb_xisf_photometry.py`. Standard library only. See `tools/README.md` and the note below. |
| `de.agorion.tcrb.plist` | launchd job — fires `tcrb_monitor.py` hourly from `~/Scripts/tcrb/`. |
| `docs/FINDER_CHART.md` | AAVSO finder chart X42597QE (1° FOV) with V-band comparison star table. Reference only, not used by any script. Also in `docs/`: the chart image (`X42597QE.png`), its full photometry table (`X42597QE_photometry.csv`), `SECURITY_AUDIT.md`, and `PRIVATE_NOTES.md` (the latter two gitignored). |

## Running

```bash
# Normal run (fetches AAVSO, appends CSV, may alert)
python3 tcrb_monitor.py

# Read-only: fetch and print, no writes, no alerts
python3 tcrb_monitor.py --dry-run

# Send a test alert over all active channels (no state change)
python3 tcrb_monitor.py --test-alert

# Plot the CSV (uses .venv)
.venv/bin/python plot_tcrb_csv.py

# Fetch ASAS-SN data (analysis/plot only, not alerts)
.venv/bin/python asassn_fetch.py

# Read-only ASAS-SN fetch
.venv/bin/python asassn_fetch.py --dry-run

# Include quality-bad points
.venv/bin/python asassn_fetch.py --all-quality
```

## Architecture

`fetch_observations()` scrapes the AAVSO WebObs HTML table (no API key needed, AUID `000-BBW-825`). It returns dicts with `jd`, `mag`, `band`, `fainter_than`, etc.

`append_csv()` deduplicates by JD (`.5f` precision) before appending to `tcrb_history.csv`.

Alert logic in `run()`:
- Only **Vis.** and **V** bands are evaluated — I/R/B are excluded. The M-giant companion keeps T CrB permanently bright (~6–7 mag) in the infrared, which would cause constant false alarms.
- Three levels: `quiescent` → `warn` (≤ 8.0 mag) → `erupt` (≤ 6.0 mag).
- An alert fires only when the level *escalates*. `tcrb_state.json` persists the last level across runs.

## PixInsight DynamicPSF photometry (`legacy/tcrb_dynamicpsf_photometry.py`)

Manual companion script for owner-acquired imaging data, kept as a valuable cross-check alongside the automated XISF pipeline below. Moved into `legacy/` since it's no longer the primary workflow — see `legacy/CLAUDE.md` for the full workflow, design points, and caveats.

```bash
python3 legacy/tcrb_dynamicpsf_photometry.py
```

## XISF differential photometry (`tcrb_xisf_photometry.py`)

Fully automated alternative to the DynamicPSF workflow above — no PixInsight session required at run time. Workflow:

```bash
.venv/bin/python tcrb_xisf_photometry.py raw_stack_2026-06-06.xisf
.venv/bin/python tcrb_xisf_photometry.py *.xisf   # accepts multiple files, processed in turn
```

The script:
- Parses the XISF header (XML + attached binary blocks) directly to read the green channel and the embedded PixInsight `PCL:AstrometricSolution` plate-solve.
- Reproduces PixInsight's native↔image coordinate mapping (standard gnomonic projection, reference point + `LinearTransformationMatrix`) using the solution's own precomputed `NativeToImage` spline grid via bilinear interpolation — i.e. it reuses PixInsight's tabulated distortion correction rather than re-deriving the surface-spline math.
- Projects T CrB's catalog position and every AAVSO VSP comparison star within the frame to pixel coordinates, fits a 2D Gaussian + constant background (`astropy.modeling`) to each, and computes differential photometry the same way as `tcrb_dynamicpsf_photometry.py` (imported from there — same VSP query/cache, same `differential_magnitude()`).

**Key design points:**
- **Not in the alert path.** Run manually per stacked session.
- **Input format assumption:** the XISF must be an already-debayered 3-channel RGB master light (`colorSpace="RGB"`, `geometry="W:H:3"`), as produced by PixInsight WBPP. `BAYERPAT`/`CFASourcePattern` in the header is provenance metadata about the original sensor, not an indication the pixel data is still a Bayer mosaic — the script does not attempt CFA-aware debayering.
- **Channel order:** assumes PixInsight's standard R,G,B channel ordering (index 1 = green).
- **VSP field of view:** capped at 180′ (AAVSO VSP's hard maximum; `maglimit` must be ≤12 above that, which is why this script uses `maglimit=13` at exactly 180′). 180′ comfortably covers this frame's ~90′ corner-to-center radius.
- **Quality filters:** rejects PSF fits with amplitude too low (no real star), peak too high (saturated — bright comparison stars commonly clip in a stack deep enough for T CrB), or a centroid that wandered too far from the predicted position.
- **PSF fit:** `astropy.modeling`'s `TRFLSQFitter`, not `LevMarLSQFitter` — LevMar silently ignores parameter bounds and is prone to converging on degenerate (near-zero-width, runaway-rotation) solutions for this data; `theta` is fixed at 0 since orientation doesn't affect the integrated flux.
- **Observation time (JD):** read from the stack's `DATE-OBS` FITS keyword, taken as-is — `DATE-END` is not read or used at all. **Known caveat:** PixInsight's `ImageIntegration` sets `DATE-OBS` to the midpoint between the *first* and *last* subframe's start times, ignoring exposure duration entirely — not true mid-exposure time, and unreliable in practice for Seestar stacks. Source: [Cosmic Canvas, "Guide to Preprocessing of Raw Data with PixInsight"](https://sh-cosmiccanvas.s3.us-west-2.amazonaws.com/Resources/20230101_GuideToPreprocessingOfRawDataWithPixInsight.pdf). Until that's fixed upstream, `DATE-OBS` is corrected by hand to genuine mid-exposure time (start of the first sub-frame + half the total integration span) before running the script — use `tools/tcrb_session_midtime.py` to compute that value from the session's actual SubframeSelector-approved light frames, rather than estimating it.
- **Watch for duplicate `DATE-OBS` keywords:** when hand-editing the keyword in PixInsight, it's easy to end up with two `DATE-OBS` FITSKeyword entries instead of replacing the existing one. XML parsing (`ElementTree.find`) silently returns the first match, which may be the stale value — if a correction doesn't seem to take effect, check for a duplicate before assuming the script is wrong. Also note: a fresh WBPP/`ImageIntegration` re-export regenerates `DATE-OBS` from scratch (its own buggy midpoint calculation), overwriting any prior hand-correction — re-apply the fix after every re-export, and re-run `tools/tcrb_session_midtime.py` if the input frame set changed.
- **Subframe count and exposure time** (printed in the log and the AAVSO `Comments` field as e.g. "21 x 30s"): `EXPTIME` comes from the usual FITSKeyword, but the frame count is parsed out of the `numberOfImages` parameter embedded in the `PixInsight:ProcessingHistory` property's `ImageIntegration` instance — not inferred from counting files in the session's lights folder. Those can genuinely differ: e.g. `raw_stack_2026-06-24.xisf`'s `ImageIntegration` log records 20 images, while its `"2026-06-24 lights"` folder has 23 `SubframeSelector`-approved `..._a.xisf` files — `ImageIntegration`'s own pixel rejection excluded 3 more. Reading the master's own log is the authoritative source.
- **Validated 2026-06-25:** projected pixel positions for T CrB and 6 comparison stars all landed within ~1px of the true PSF peak (T CrB itself was ~6px off, attributable to its catalog position vs. the stack's plate solve rather than a projection error). Derived T CrB magnitude (10.076 ± 0.029, n=7) closely matched concurrent AAVSO V-band observations from the same week (~9.95–10.1).
- **Dependency:** `numpy`, `astropy`, `scipy` (required by astropy's fitters), `requests`. Install into `.venv/`: `pip install scipy` (numpy/astropy/requests already present for the other scripts).
- **Input files:** `raw_stack_*.xisf` are gitignored (local imaging data, not shared).

## ASAS-SN fetcher (`asassn_fetch.py`)

Fetches the ASAS-SN Sky Patrol light curve via a cone search (RA 239.8757°, Dec +25.9202°, radius 5″) and appends results to `asassn_history.csv` with the identical schema as `tcrb_history.csv` (`jd`, `date`, `mag`, `band`, `observer`, `fainter_than`), so `plot_tcrb_csv.py` can overlay both series.

**Key design points:**
- **Not in the alert path.** AAVSO remains the sole alert source. ASAS-SN is reference/analysis only.
- **Recommended cadence: daily.** ASAS-SN updates ~nightly; hourly polling adds nothing.
- **Band label:** stored as `g (ASAS-SN)` (etc.) to prevent accidental pooling with AAVSO Vis./V if CSVs are ever merged.
- **Non-detections** are recorded as `fainter_than=1` with the limiting magnitude, mirroring AAVSO convention.
- **Dependency:** `pyasassn` (plus pandas). Install into the same `.venv` as matplotlib: `pip install pyasassn`.

**Saturation caveat:** ASAS-SN standard aperture photometry saturates near T CrB's quiescent brightness (~10 mag in g). Data quality degrades as the star brightens and is completely unreliable at eruption peak (~2 mag brighter). Use AAVSO Vis./V for the bright phase. ASAS-SN's ML "saturated stars" pipeline could handle it, but is not used here.

**Status (verified 2026-06-19, skypatrol 0.6.21, Python 3.14):** Fully working.
Use `skypatrol` (PyPI), not the obsolete `pyasassn 0.6.4` — the old package hardcoded
data-server hostnames that no longer exist. `skypatrol` discovers servers dynamically
via `/get_block_servers` and uses `pd.read_parquet` for deserialisation.

Live DataFrame columns returned by the cone search:
`asas_sn_id, jd, flux, flux_err, mag, mag_err, limit, fwhm, image_id, camera, quality (G/B), phot_filter`
All columns in `_normalise_rows()` match this schema.

## Deployment (launchd)

The production copy lives at `~/Scripts/tcrb/`, not in this photo directory. After editing:

```bash
# Reload after plist changes
launchctl bootout gui/$(id -u)/de.agorion.tcrb
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist

# Trigger immediately without waiting for the hour
launchctl kickstart -k gui/$(id -u)/de.agorion.tcrb

# Check logs
cat ~/Scripts/tcrb/tcrb.log
cat ~/Scripts/tcrb/tcrb.err.log
```

## Secrets / local config

Sensitive values (phone numbers, SMTP credentials) live in `tcrb_monitor_config.py`, which is gitignored. Copy `tcrb_monitor_config.sample.py` → `tcrb_monitor_config.py` and fill in your values. `tcrb_monitor.py` imports it at startup and disables the affected channel gracefully if the file is missing.

The launchd plist (`de.agorion.tcrb.plist`) is also gitignored because it contains hardcoded user paths. Copy `de.agorion.tcrb.sample.plist` → `de.agorion.tcrb.plist`, replace `USERNAME` with your macOS username (`whoami`), and verify the Python path with `which python3`.

## Signal setup (one-time)

```bash
brew install signal-cli
signal-cli link -n "TCrB-Monitor"          # scan QR in Phone → Settings → Linked Devices
signal-cli -u +49NUMMER receive            # fetch contacts/groups
signal-cli -u +49NUMMER listGroups         # get base64 group ID for SIGNAL_GROUP_ID
```

Configure `SIGNAL_ENABLED`, `SIGNAL_CLI`, `SIGNAL_ACCOUNT`, and either `SIGNAL_GROUP_ID` or `SIGNAL_RECIPIENTS` at the top of `tcrb_monitor.py`.

## Git

Do not add co-author lines or any mention of Claude in commits, commit messages, or files.
