# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Monitors T Coronae Borealis ("Blaze Star") for a nova eruption by polling AAVSO WebObs hourly. When brightness crosses configurable thresholds it sends a macOS notification and/or a Signal message. A local CSV accumulates all observations; a JSON file tracks the current alert level to prevent duplicate alerts.

## Scripts

| File | Purpose |
|------|---------|
| `tcrb_monitor.py` | **Current version.** Standard library only (Python 3.9+). Alerts via macOS notification + Signal. |
| `asassn_fetch.py` | ASAS-SN Sky Patrol fetcher. Analysis companion — **not** in the alert path. Writes `asassn_history.csv`. Requires `skypatrol` (`.venv/`). |
| `plot_tcrb_csv.py` | Plots `tcrb_history.csv` → PNG. Bands: Vis. (yellow), V (orange), TG (green), TB (blue). `--observer CODE` highlights that observer's TG/TB points as pentagrams connected by a smooth PCHIP curve. Requires `matplotlib` + `scipy` (`.venv/`). |
| `photometry/` | Legacy Python differential-photometry scripts — superseded by the PixInsight script but kept as cross-checks. See `photometry/CLAUDE.md`. |
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

# Highlight a specific observer: TG = green pentagram, TB = light-blue pentagram
.venv/bin/python plot_tcrb_csv.py --observer BSLA

# Fetch ASAS-SN data (analysis/plot only, not alerts)
.venv/bin/python asassn_fetch.py

# Read-only ASAS-SN fetch
.venv/bin/python asassn_fetch.py --dry-run

# Include quality-bad points
.venv/bin/python asassn_fetch.py --all-quality
```

## Architecture

`fetch_observations()` scrapes the AAVSO WebObs HTML table (no API key needed, AUID `000-BBW-825`). It returns dicts with `jd`, `mag`, `band`, `fainter_than`, etc.

`append_csv()` deduplicates by **(JD, band)** pair before appending to `tcrb_history.csv`. Keying on JD alone would silently drop same-session multi-filter observations (e.g. TG + TB taken at the same timestamp).

**Band label convention:** TG and TB are produced by one-shot colour (OSC) cameras, not dedicated DSLR sensors, so legend labels read "TG (OSC green)" and "TB (OSC blue)" — not "DSLR".

## Backfilling historical AAVSO data

The hourly monitor fetches only the latest 200 observations. To backfill further into the past, use the `page=N` pagination parameter on the WebObs endpoint (200 obs per page, newest first):

```
https://apps.aavso.org/webobs/results/?star=000-BBW-825&num_results=200&page=N
```

**Key notes:**
- Use the AUID (`000-BBW-825`) rather than the star name in the URL for pagination.
- The star name appears as `"T CRB"` (uppercase) in the HTML table — the row filter must be **case-insensitive** (`"tcrb" in tr.replace(" ", "").lower()`).
- Each page covers roughly 0.2 days at current T CrB observation rates (~200 obs/day). Reaching June 14 from June 30 required ~89 pages.
- `append_csv()` is idempotent — safe to re-merge pages already in the CSV.
- After a bulk merge, sort the CSV in place by JD (column 0) to restore chronological order:

```python
import csv
with open("tcrb_history.csv", newline="") as f:
    reader = csv.reader(f); header = next(reader); rows = list(reader)
rows.sort(key=lambda r: float(r[0]))
with open("tcrb_history.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(header); w.writerows(rows)
```

Alert logic in `run()`:
- Only **Vis.** and **V** bands are evaluated — I/R/B are excluded. The M-giant companion keeps T CrB permanently bright (~6–7 mag) in the infrared, which would cause constant false alarms.
- Three levels: `quiescent` → `warn` (≤ 8.0 mag) → `erupt` (≤ 6.0 mag).
- An alert fires only when the level *escalates*. `tcrb_state.json` persists the last level across runs.

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
