# T CrB Monitor

Alert when [T Coronae Borealis](https://en.wikipedia.org/wiki/T_Coronae_Borealis) (T CrB, the "Blaze Star") erupts after 80 years of quiescence.
Polls the AAVSO WebObs database (AUID 000-BBW-825). Standard library only, no external packages.

T CrB is a binary star system about 3,000 light-years away: a bloated [red giant](https://en.wikipedia.org/wiki/Red_giant) slowly shedding its outer layers onto a dense [white dwarf](https://en.wikipedia.org/wiki/White_dwarf) companion. Over millennia, stolen hydrogen piles up on the white dwarf's surface until it reaches a critical pressure and temperature — then it all ignites at once in a thermonuclear explosion called a nova. The star briefly blazes from ~10th magnitude (invisible to the naked eye) to around 2nd magnitude, rivalling the North Star, before fading back to obscurity over the following weeks. The last time this happened was 1946; the time before that, 1866.

## What it does

- Fetches the latest 200 observations and appends them deduplicated to `tcrb_history.csv` (all bands, for your own analysis). The AAVSO International Database includes observations from the British Astronomical Association, Variable Star Section (BAAVSS, merged December 2014) and AFOEV (Association Française des Observateurs d'Étoiles Variables, ongoing cooperation), so those are covered automatically.
- Evaluates thresholds on Vis. and V observations only — the M-giant companion keeps T CrB permanently ~7 mag in the I/R bands, which would otherwise cause constant false alarms. 
  - TG (transformed Green, a green-channel DSLR filter approximating V) is excluded because it tracks closely with V but adds calibration scatter; V and Vis. observations are sufficient and cleaner for threshold detection.
  - Non-detections (AAVSO `<mag` upper limits, e.g. a shallow-telescope observer reporting `<4.9`) are parsed but excluded from threshold evaluation — only confirmed measurements trigger alerts.
- Two levels: `--warn-mag 8.0` (notable) and `--erupt-mag 6.0` (eruption likely). Alerts only on escalation; `tcrb_state.json` prevents duplicate notifications.
- Alerts via macOS notification (`osascript`) and optionally via Signal.

## Usage

```bash
python3 tcrb_monitor.py             # normal run
python3 tcrb_monitor.py --dry-run   # fetch and display only, no writes
python3 tcrb_monitor.py --test-alert  # send test message on all active channels
```

## Setup

Place the script and plist in a fixed location and update all `/Users/USERNAME/Scripts/tcrb` paths in the plist to match your setup. `which python3` shows the correct interpreter path — `/usr/bin/python3` is Apple's system Python and is sufficient since the script uses only the standard library.

```bash
mkdir -p ~/Scripts/tcrb
cp tcrb_monitor.py ~/Scripts/tcrb/
cp tcrb_monitor_config.py ~/Scripts/tcrb/  # secrets; launchd loads from WorkingDirectory
cp de.agorion.tcrb.plist ~/Library/LaunchAgents/

# validate syntax (no output = ok)
plutil -lint ~/Library/LaunchAgents/de.agorion.tcrb.plist

# load (modern syntax; "gui/$(id -u)" is your login session)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist

# trigger immediately without waiting for the next hour
launchctl kickstart -k gui/$(id -u)/de.agorion.tcrb
```

Check whether it is running:

```bash
launchctl print gui/$(id -u)/de.agorion.tcrb | grep -i state
cat ~/Scripts/tcrb/tcrb.log
```

To reload after plist changes, unload first then load again:

```bash
launchctl bootout gui/$(id -u)/de.agorion.tcrb
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist
```

## Two notes

- The plist fires hourly on the hour, around the clock — sensible because eruption reports arrive worldwide at any time and T CrB fades quickly after peak. If that is too frequent, `StartCalendarInterval` can be written as an array of specific hours (e.g. 6, 12, 18, 22 only).
- The macOS notification via `osascript` runs from the LaunchAgent inside your GUI session and normally appears without issue; on first run you may need to grant permission under System Settings → Notifications. If notifications feel too unreliable, Signal delivery is the more robust channel — every hour counts when an eruption happens.

## Signal alerts

Fill in `tcrb_monitor_config.py` (template: `tcrb_monitor_config.sample.py`):

- `SIGNAL_CLI` — verify path with `which signal-cli` (Apple Silicon usually `/opt/homebrew/bin/signal-cli`)
- `SIGNAL_ACCOUNT` — your linked phone number
- Either `SIGNAL_GROUP_ID` (takes priority) or `SIGNAL_RECIPIENTS`
- `SIGNAL_ENABLED` is set in the script header and defaults to `True`; if `tcrb_monitor_config.py` is missing, Signal disables itself automatically.

Two more things: the signal-cli path must be absolute because launchd provides only a minimal PATH. And signal-cli stores its state in `~/.local/share/signal-cli` — since the LaunchAgent runs under your user account, this works without extra configuration.

## Setting up signal-cli

```bash
brew install signal-cli
signal-cli link -n "TCrB-Monitor"          # shows a QR code; scan in Phone → Settings → Linked Devices
signal-cli -u +1YOURNUMBER receive         # run once to fetch contacts and groups
signal-cli -u +1YOURNUMBER listGroups      # returns the base64 group ID
```

Linking (rather than registering) is the easier path: your phone stays the primary device and the Mac becomes a secondary linked device.

## Testing

```bash
python3 tcrb_monitor.py --test-alert
```

This sends a clearly marked test message on all active channels and exits without changing state — ideal for verifying Signal delivery before going live.

## Plotting the light curve

The script `plot_tcrb_csv.py` reads `tcrb_history.csv` and produces `tcrb_lightcurve.png` — a visual light curve with Vis., V, and TG band measurements. B, I, R, CV, and SU are excluded (I/R: permanently bright M-giant; B: systematically offset). Fainter-than limits are also skipped. The x-axis shows UTC date/time derived from Julian Dates; the y-axis is inverted as usual (brighter up). The title shows the date range of the available data automatically.

```bash
.venv/bin/python plot_tcrb_csv.py
```

Requires `matplotlib`. Create a virtual environment with:

```bash
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

The plotter reads the production CSV path from `de.agorion.tcrb.plist` (`WorkingDirectory`) if the plist is present — otherwise from the script directory. When `asassn_history.csv` contains data, the ASAS-SN g-band series is overlaid automatically as a fourth series (see below); the title and legend update accordingly.

<img src="tcrb_lightcurve.sample.png">

## ASAS-SN reference data

The ASAS-SN fetcher (`asassn_fetch.py`) is a daily companion script that pulls the T CrB light curve from the [ASAS-SN Sky Patrol](https://asas-sn.ifa.hawaii.edu/skypatrol/) and appends new observations to `asassn_history.csv`. The CSV uses the same column layout as `tcrb_history.csv`, so `plot_tcrb_csv.py` can overlay both series for comparison.

ASAS-SN provides instrumentally-calibrated g-band photometry independent of AAVSO's visual observers — useful as a cross-check but **not** used for alerts. Note that ASAS-SN standard aperture photometry saturates near T CrB's brightness at eruption peak; the AAVSO Vis./V data remains the primary alert source.

```bash
.venv/bin/python asassn_fetch.py             # fetch and append (≥ 2026-06-14 by default)
.venv/bin/python asassn_fetch.py --dry-run   # fetch and print, no writes
.venv/bin/python asassn_fetch.py --start-date 2026-01-01  # override start date
.venv/bin/python asassn_fetch.py --start-date ''          # fetch full archive
```

Requires `skypatrol` (and its dependencies). Install everything via:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> **Current issue (as of June 2026):** ASAS-SN has been reprocessing their photometry database since 22 May 2025 and has not yet published observations for 2026. The daily run will silently produce 0 new rows until the pipeline catches up. No action needed — the script handles this gracefully.

## Differential photometry (own imaging)

Two companion scripts derive a calibrated TG magnitude for T CrB from your own stacked images via differential photometry against AAVSO comparison stars. Neither is part of the automated alert path. Both have been validated against 30-minute stacks from a ZWO Seestar S30 Pro, demonstrating that compact smart telescopes of this class are capable of producing scientifically useful differential photometry.

- **`tcrb_xisf_photometry.py`** (current, automated) — point it at a plate-solved, stacked XISF master light and it does everything: reads the green channel and PixInsight's embedded astrometric solution directly from the file, locates T CrB and every in-frame AAVSO comparison star by sky coordinates, fits each with a 2D Gaussian, and computes the differential magnitude. No PixInsight session needed at run time.

  ```bash
  .venv/bin/python tcrb_xisf_photometry.py raw_stack_2026-06-06.xisf
  .venv/bin/python tcrb_xisf_photometry.py *.xisf   # or process a whole batch at once
  ```

  Requires `numpy`, `astropy`, `scipy` (`.venv/bin/pip install scipy` — numpy/astropy already present), `requests`.

  The reported observation time (JD) is read from the stack's `DATE-OBS` FITS keyword, taken as-is — `DATE-END` is not read or used at all. **Known caveat:** PixInsight's `ImageIntegration` sets `DATE-OBS` to the midpoint between the *first* and *last* subframe's start times, ignoring exposure duration entirely — not true mid-exposure time, and unreliable in practice for Seestar stacks (source: [Cosmic Canvas, "Guide to Preprocessing of Raw Data with PixInsight"](https://sh-cosmiccanvas.s3.us-west-2.amazonaws.com/Resources/20230101_GuideToPreprocessingOfRawDataWithPixInsight.pdf)). Until that's fixed upstream, `DATE-OBS` is corrected by hand to genuine mid-exposure time (start of the first sub-frame + half the total integration span) based on the actual light frames used for each stack, before running the script.

- **[`legacy/tcrb_dynamicpsf_photometry.py`](legacy/README.md)** (manual, kept as a cross-check) — the original workflow: click T CrB and comparison stars by hand in PixInsight's DynamicPSF tool, export a CSV, then run the script to derive the same differential magnitude. See [`legacy/README.md`](legacy/README.md) for the full workflow.

Both share a VSP magnitude cache (`legacy/dynamicpsf_vsp_cache.json`) so repeated runs against the same sky field don't re-query AAVSO.

## Finder chart

See [docs/FINDER_CHART.md](docs/FINDER_CHART.md) — AAVSO chart X42597QE (1° FOV, V mag limit 14.50) with comparison star V magnitudes.

## Links

- [T CrB current – TheSkyLive](https://theskylive.com/sky/stars/hr-5958-star) — live brightness and current information on T Coronae Borealis
- [AAVSO Photometry Database Search](https://apps.aavso.org/v2/data/search/photometry/) — search raw data from all AAVSO observations
- [My AAVSO observations (BSLA)](https://apps.aavso.org/v2/data/search/user/?observer=BSLA) — own submitted observations, including those produced by `tcrb_xisf_photometry.py`. Requires a free AAVSO account (logged in) to view.
