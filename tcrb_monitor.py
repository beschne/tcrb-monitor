#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tcrb_monitor.py  --  Brightness monitor for T CrB ("Blaze Star")

Fetches the latest observations from the AAVSO VSX database, writes them
to a local CSV history, and raises an alert when brightness crosses a
configurable threshold (star gets brighter -> smaller mag number).

Standard library only -- no external packages needed. Tested with Python 3.9+.

Intended for daily (or hourly) invocation via cron/launchd.

Source:  https://vsx.aavso.org/index.php?view=api.delim   (public, no login)
AUID T CrB: 000-BBW-825

This is version 2 without email delivery. Alerts sent via Signal instead.
"""

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

def _err(msg):
    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)

# --------------------------------------------------------------------------
# CONFIGURATION  (can be fully overridden via command line/ENV)
# --------------------------------------------------------------------------
STAR          = "T CrB"
LOOKBACK_DAYS = 5            # re-request this many trailing days on every run
                              # (VSX has no "latest N observations" mode; a
                              # generous rolling window also catches
                              # observations submitted late by observers)

# Alert thresholds (mag). Brighter = smaller number. T CrB quiescent at ~10.
WARN_MAG      = 8.0          # early warning: unusually bright, keep an eye on it
ERUPT_MAG     = 6.0          # eruption very likely -> head outside immediately

# IMPORTANT: Only evaluate these bands for alerts.
# T CrB is a symbiotic star with an M-giant -> in the infrared (I, R) permanently
# bright (~6-7 mag), even though the star sits at ~10 mag visually at quiescence.
# Treating all bands equally would cause constant false alarms. For "visible
# to the naked eye", visual and Johnson-V estimates are what count.
ALERT_BANDS   = {"Vis.", "V"}

# Files (default: next to the script; works for offline workflow)
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CSV_PATH      = os.path.join(BASE_DIR, "tcrb_history.csv")
STATE_PATH    = os.path.join(BASE_DIR, "tcrb_state.json")

# Notification
USE_MACOS_NOTIFY = True       # macOS notification via osascript

# --- Signal via signal-cli ------------------------------------------------
# Prerequisite: signal-cli installed and set up ONCE, e.g.
#   brew install signal-cli
#   signal-cli link -n "TCrB-Monitor"      # scan QR with phone
# List your group IDs (base64) with:
#   signal-cli -u +1YOURNUMBER listGroups
SIGNAL_ENABLED    = True
SIGNAL_CLI        = "/opt/homebrew/bin/signal-cli"  # Apple Silicon; Intel: /usr/local/bin
SIGNAL_ACCOUNT    = ""        # loaded from tcrb_monitor_config.py
SIGNAL_GROUP_ID   = ""        # base64 group ID (takes priority if set)
SIGNAL_RECIPIENTS = []        # or: one or more phone numbers

# Load secrets from tcrb_monitor_config.py (not in repo, see tcrb_monitor_config.sample.py)
try:
    import tcrb_monitor_config as _cfg
    SIGNAL_CLI        = getattr(_cfg, "SIGNAL_CLI",        SIGNAL_CLI)
    SIGNAL_ACCOUNT    = getattr(_cfg, "SIGNAL_ACCOUNT",    SIGNAL_ACCOUNT)
    SIGNAL_GROUP_ID   = getattr(_cfg, "SIGNAL_GROUP_ID",   SIGNAL_GROUP_ID)
    SIGNAL_RECIPIENTS = getattr(_cfg, "SIGNAL_RECIPIENTS", SIGNAL_RECIPIENTS)
except ImportError:
    if SIGNAL_ENABLED:
        _err("Note: tcrb_monitor_config.py missing – Signal disabled.")
    SIGNAL_ENABLED = False

# AAVSO VSX delimited-data API and User-Agent.
# (AAVSO retired the old server-rendered WebObs HTML table in Sep 2026 in
# favor of a JS single-page app; its own light-curve tool pulls raw data
# from this VSX endpoint, which returns a header row plus one row per
# observation, fields separated by DELIMITER.)
VSX_URL    = "https://vsx.aavso.org/index.php"
DELIMITER  = "@@@"
USER_AGENT = "AGO-TCrB-Monitor/1.3 (Volkssternwarte Hochtaunus)"

_MONTH_ABBR = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "Jun.",
               "Jul.", "Aug.", "Sep.", "Oct.", "Nov.", "Dec."]

# --------------------------------------------------------------------------
# Fetch + Parse
# --------------------------------------------------------------------------
def _jd_now():
    """Current Julian Date (UT)."""
    epoch = dt.datetime(1858, 11, 17, tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - epoch).total_seconds() / 86400 + 2400000.5

def jd_to_calendar(jd):
    """Julian Date -> AAVSO-style calendar string, e.g. '2026 Jun. 14.45764'."""
    jd2 = jd + 0.5
    z = int(jd2)
    f = jd2 - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day_frac = b - d - int(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return f"{year} {_MONTH_ABBR[month - 1]} {day_frac:08.5f}"

def fetch_observations(star=STAR, lookback_days=LOOKBACK_DAYS, fromjd=None, tojd=None):
    """Returns (obs, error_kind) where obs is a list of dicts and error_kind is
    None on success, 'http' for network/server errors, or 'parse' for unexpected
    response structure.

    By default requests the trailing `lookback_days` window. Pass explicit
    `fromjd`/`tojd` instead to backfill an arbitrary historical range (e.g.
    to fill a gap left by a launchd/network outage) -- VSX has no "latest N
    observations" mode, so any specific period has to be requested by date."""
    jd_now = _jd_now()
    if fromjd is None:
        fromjd = jd_now - lookback_days
    if tojd is None:
        tojd = jd_now + 0.5
    qs = urllib.parse.urlencode({
        "view": "api.delim",
        "ident": star,
        "fromjd": f"{fromjd:.5f}",
        "tojd": f"{tojd:.5f}",
        "delimiter": DELIMITER,
    })
    url = VSX_URL + "?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            text = r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        _err(f"AAVSO fetch failed: {e}")
        return [], "http"

    if text[:200].find("DB Error") != -1:
        _err("AAVSO VSX API returned a database error.")
        return [], "http"

    lines = text.strip("\r\n").split("\n")
    if not lines or not lines[0].startswith(f"JD{DELIMITER}"):
        _err("AAVSO VSX API structure changed: header row missing/unexpected.")
        return [], "parse"

    header = lines[0].split(DELIMITER)
    try:
        cols = {name: header.index(name)
                for name in ("JD", "mag", "band", "by", "fainterThan", "starName")}
    except ValueError:
        _err("AAVSO VSX API structure changed: expected column missing.")
        return [], "parse"

    star_key = star.replace(" ", "").lower()
    obs = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(DELIMITER)
        if len(fields) <= max(cols.values()):
            continue
        if fields[cols["starName"]].replace(" ", "").lower() != star_key:
            continue
        try:
            jd  = float(fields[cols["JD"]])
            mag = float(fields[cols["mag"]])
        except ValueError:
            continue
        obs.append({
            "jd": jd,
            "date": jd_to_calendar(jd),
            "mag": mag,
            "band": fields[cols["band"]].strip(),
            "observer": fields[cols["by"]].strip(),
            "fainter_than": fields[cols["fainterThan"]].strip() == "1",
        })
    return obs, None

# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def brightest_real(obs, bands=ALERT_BANDS):
    """Brightest *real* measurement in the relevant bands (no 'fainter-than'
    limits, no infrared). Returns None if nothing suitable is found."""
    real = [o for o in obs if not o["fainter_than"] and o["band"] in bands]
    return min(real, key=lambda o: o["mag"]) if real else None

def _csv_safe(s):
    """Prevents CSV formula injection in spreadsheet applications."""
    s = str(s)
    return "'" + s if s and s[0] in ("=", "+", "-", "@", "\t", "\r") else s

def append_csv(obs, path=CSV_PATH):
    """Write new observations to the CSV history by (JD, band) (deduplicated)."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add((row["jd"], row["band"]))
    new = [o for o in obs if (f"{o['jd']:.5f}", o["band"]) not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["jd", "date", "mag", "band", "observer", "fainter_than"])
        for o in sorted(new, key=lambda x: x["jd"]):
            w.writerow([f"{o['jd']:.5f}", _csv_safe(o["date"]), o["mag"],
                        _csv_safe(o["band"]), _csv_safe(o["observer"]),
                        int(o["fainter_than"])])
    return len(new)

def load_state(path=STATE_PATH):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            _err(f"Warning: {path} unreadable, resetting state.")
    return {"level": "quiescent", "last_jd": 0.0}

def save_state(state, path=STATE_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

# --------------------------------------------------------------------------
# Notification
# --------------------------------------------------------------------------
def macos_notify(title, message):
    if not USE_MACOS_NOTIFY:
        return
    # Pass strings as argv, not interpolated into the AppleScript source,
    # to prevent AppleScript injection via scraped observer data.
    script = (
        "on run argv\n"
        "  display notification (item 1 of argv)"
        " with title (item 2 of argv) sound name \"Glass\"\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, message, title],
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

def send_signal(text):
    """Sends a message via signal-cli to a group or to individual numbers.
    Does not kill the script if signal-cli is missing or fails --
    the macOS notification remains as a second channel."""
    if not SIGNAL_ENABLED:
        return
    if SIGNAL_GROUP_ID:
        targets = ["-g", SIGNAL_GROUP_ID]
    else:
        targets = list(SIGNAL_RECIPIENTS)
    if not targets:
        _err("Signal: no target configured -- skipped.")
        return
    cmd = [SIGNAL_CLI, "-u", SIGNAL_ACCOUNT, "send", "-m", text, "--"] + targets
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            _err(f"Signal send failed (rc={res.returncode}): {res.stderr.strip()}")
    except FileNotFoundError:
        _err(f"Signal: '{SIGNAL_CLI}' not found -- check path.")
    except subprocess.TimeoutExpired:
        _err("Signal: timeout while sending.")

def recent_context(obs, bands=ALERT_BANDS, window_days=1.0, max_points=5):
    """Short trend block for the alert message: range and rate of change of
    relevant measurements in the time window (default 24 h from newest observation),
    plus the most recent individual points. Returns multi-line text."""
    real = [o for o in obs if not o["fainter_than"] and o["band"] in bands]
    if not real:
        return "No Vis./V context available."
    real.sort(key=lambda o: o["jd"], reverse=True)   # neueste zuerst
    newest_jd = real[0]["jd"]
    window = [o for o in real if o["jd"] >= newest_jd - window_days]

    mags = [o["mag"] for o in window]
    bright, faint = min(mags), max(mags)
    lines = [f"Last {window_days*24:.0f} h: {len(window)} Vis./V reading(s), "
             f"{bright:.2f}-{faint:.2f} mag (quiescent ~10)."]

    # Rate of change across the window (negative = getting brighter)
    if len(window) >= 2:
        delta_days = window[0]["jd"] - window[-1]["jd"]
        dmag = window[0]["mag"] - window[-1]["mag"]
        if delta_days > 0:
            rate = dmag / delta_days
            direction = "brighter" if dmag < 0 else ("fainter" if dmag > 0 else "stable")
            lines.append(f"Trend: {abs(dmag):.2f} mag {direction} over "
                         f"{delta_days*24:.1f} h  (~{abs(rate):.2f} mag/day).")

    lines.append("Recent points:")
    for o in real[:max_points]:
        obs_name = o["observer"] or "?"
        lines.append(f"  {o['date']}  {o['mag']:.2f} {o['band']}  ({obs_name})")
    return "\n".join(lines)

def alert(level, obs, brightest):
    icon = {"warn": "[!] T CrB NOTABLE", "erupt": "[!!!] T CrB ERUPTION?"}[level]
    line = (f"{brightest['mag']:.2f} mag ({brightest['band']}) "
            f"on {brightest['date']}  -- obs. {brightest['observer']}")
    body = (f"{icon}\n\n"
            f"Brightest current reading: {line}\n\n"
            f"{recent_context(obs)}\n\n"
            f"Live light curve: https://app.aavso.org/v2/lcg/  (star: T CRB)\n"
            f"Campaign #875:  https://forums.aavso.org/t/observing-campaigns-875-monitoring-t-crb/946\n\n"
            f"Constellation Corona Borealis -- at eruption ~2-3 mag, visible to the naked eye.")
    print(body)
    macos_notify(icon, line)
    send_signal(body)

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run(warn_mag, erupt_mag, dry_run=False):
    obs, fetch_err = fetch_observations()
    if not obs:
        if fetch_err == "http":
            _err("No observations received -- transient server error, will retry next run.")
        elif fetch_err == "parse":
            _err("No observations received -- page structure may have changed, check script.")
        else:
            _err("No observations received -- possibly no recent data or unexpected structure.")
        return 2

    n_new = 0 if dry_run else append_csv(obs)
    b = brightest_real(obs)
    if b is None:
        print("No usable Vis./V measurements in the latest data "
              "(limits or other bands only) -- nothing to report.")
        return 0

    state = load_state()
    prev_level = state.get("level", "quiescent")

    if b["mag"] <= erupt_mag:
        level = "erupt"
    elif b["mag"] <= warn_mag:
        level = "warn"
    else:
        level = "quiescent"

    rank = {"quiescent": 0, "warn": 1, "erupt": 2}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] brightest real reading: {b['mag']:.2f} {b['band']} "
          f"({b['date']}) | status: {level} | new records: {n_new}")

    # Alert only on *escalation* of level (debounce against duplicate alerts)
    if rank[level] > rank[prev_level] and level in ("warn", "erupt"):
        alert(level, obs, b)

    if not dry_run:
        state["level"] = level
        state["last_jd"] = max(o["jd"] for o in obs)
        save_state(state)
    return 0

def parse_args():
    p = argparse.ArgumentParser(description="AAVSO brightness monitor for T CrB")
    p.add_argument("--warn-mag", type=float, default=WARN_MAG,
                   help=f"early-warning threshold in mag (default {WARN_MAG})")
    p.add_argument("--erupt-mag", type=float, default=ERUPT_MAG,
                   help=f"eruption threshold in mag (default {ERUPT_MAG})")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch and display only, no writes or alerts")
    p.add_argument("--test-alert", action="store_true",
                   help="send test notification on all channels and exit")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    if args.test_alert:
        msg = ("[TEST] T CrB-Monitor: notification working. "
               "No eruption -- this is a connectivity test only.")
        print(msg)
        macos_notify("[TEST] T CrB-Monitor", "Signal/notification test successful.")
        send_signal(msg)
        sys.exit(0)
    sys.exit(run(args.warn_mag, args.erupt_mag, dry_run=args.dry_run))