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
| `tcrb_dynamicpsf_photometry.py` | PixInsight DynamicPSF differential photometry — manual tool, **not** in the alert path. See section below. Requires `requests`. |
| `de.agorion.tcrb.plist` | launchd job — fires `tcrb_monitor.py` hourly from `~/Scripts/tcrb/`. |

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

## PixInsight DynamicPSF photometry (`tcrb_dynamicpsf_photometry.py`)

Manual companion script for owner-acquired imaging data. Workflow:

1. In PixInsight, extract the green channel from the raw-stacked image (Rohsummenstack) and run **DynamicPSF** on it, clicking T CrB and surrounding comparison stars.
2. Export the DynamicPSF table as CSV — the export must include `alpha` (RA, degrees) and `delta` (Dec, degrees) columns per star in addition to the standard DynamicPSF columns (`flux`, `mad`, …). Save as `dynamicpsf_export.csv` next to the script.
3. Run `python3 tcrb_dynamicpsf_photometry.py`. The script:
   - Identifies T CrB by matching the nearest row to its known J2000 position (RA 239.882°, Dec +25.920°).
   - Queries the **AAVSO VSP API** for each comparison star's catalog V magnitude using its sky coordinates.
   - Computes differential photometry (`m_TG = m_V_comp − 2.5 log₁₀(F_T CrB / F_comp)`) per comparison star and averages the results.
   - Prints the derived TG magnitude with standard deviation and n.

**Key design points:**
- **Not in the alert path.** Run manually after each imaging session.
- **Band label:** `TG` (AAVSO notation for DSLR/camera green channel approximating V). The monitor's alert logic ignores TG — only Vis. and V are evaluated for threshold crossings.
- **Quality filters:** rows with MAD > 0.05 or flux < 1.0 are silently rejected as poor PSF fits (PixInsight exports normalized flux, not raw counts).
- **VSP caching:** all comparison stars in a frame typically fit within one VSP field of view, so the API is usually called only once per run.
- **Dependency:** `requests`. Install into `.venv/` alongside matplotlib: `pip install requests`.
- **Input file:** `dynamicpsf_export.csv` is gitignored (local imaging data, not shared).

```bash
python3 tcrb_dynamicpsf_photometry.py
```

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

Sensitive values (phone numbers, SMTP credentials) live in `config.py`, which is gitignored. Copy `config.sample.py` → `config.py` and fill in your values. Both monitor scripts import it at startup and disable the affected channel gracefully if the file is missing.

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
