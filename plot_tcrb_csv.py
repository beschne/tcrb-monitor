#!/usr/bin/env python3
"""
T CrB - Visual light curve from tcrb_history.csv
Plots Vis., V, TG and TB; B/I/R/SU excluded
(I/R = permanently bright M-giant, B/SU systematically offset).
"""

import argparse
import csv
from datetime import datetime, timedelta
import os
import plistlib
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ----------------------------- Tunables -----------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))

# Read WorkingDirectory from the launchd plist so the plotter uses the
# production CSV written by the running monitor, not the one next to this script.
_PLIST = os.path.join(_BASE, "de.agorion.tcrb.plist")
try:
    with open(_PLIST, "rb") as _f:
        _data_dir = plistlib.load(_f).get("WorkingDirectory", _BASE)
except (FileNotFoundError, plistlib.InvalidFileException):
    _data_dir = _BASE

CSV_PATH        = os.path.join(_data_dir, "tcrb_history.csv")
ASASSN_CSV_PATH = os.path.join(_BASE, "asassn_history.csv")
OUT_PATH = os.path.join(_BASE, "tcrb_lightcurve.png")
PLOT_BANDS = {"Vis.", "V", "TG", "TB"}     # bands to evaluate
# style per band: (marker, colour, label)
STYLE = {
    "Vis.": ("o", "#f9a825", "Vis. (visual estimate)"),
    "V":    ("D", "#e8710a", "V (Johnson)"),
    "TG":   ("s", "#188038", "TG (DSLR green)"),
    "TB":   ("s", "#1a73e8", "TB (DSLR blue)"),
}
ASASSN_STYLE = ("^", "#9c27b0", "ASAS-SN (g)")
# --------------------------------------------------------------------


def jd_to_dt(jd):
    """Julian Date -> UTC datetime."""
    return datetime(2000, 1, 1, 12) + timedelta(days=jd - 2451545.0)


# --- CLI ---
_ap = argparse.ArgumentParser()
_ap.add_argument("--observer", default=None,
                 help="AAVSO observer code to highlight in bright green")
HIGHLIGHT_OBSERVER = _ap.parse_args().observer

# --- Read CSV, group by band ---
series = {b: {"t": [], "m": []} for b in PLOT_BANDS}
observer_data = {b: {"t": [], "m": []} for b in PLOT_BANDS}
excluded = {}
with open(CSV_PATH, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        band = row["band"].strip()
        if band not in PLOT_BANDS:
            excluded[band] = excluded.get(band, 0) + 1
            continue
        if row.get("fainter_than", "0").strip() == "1":
            continue  # skip "fainter-than" limits
        t = jd_to_dt(float(row["jd"]))
        m = float(row["mag"])
        series[band]["t"].append(t)
        series[band]["m"].append(m)
        if HIGHLIGHT_OBSERVER and row.get("observer", "").strip() == HIGHLIGHT_OBSERVER:
            observer_data[band]["t"].append(t)
            observer_data[band]["m"].append(m)

print("excluded bands:", excluded)
for b in PLOT_BANDS:
    print(f"  {b}: {len(series[b]['m'])} points")
if HIGHLIGHT_OBSERVER:
    total = sum(len(observer_data[b]["m"]) for b in PLOT_BANDS)
    print(f"  {HIGHLIGHT_OBSERVER} (highlighted): {total} points")

# --- Read ASAS-SN CSV if present and non-empty ---
asassn = {"t": [], "m": []}
if os.path.exists(ASASSN_CSV_PATH):
    with open(ASASSN_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("fainter_than", "0").strip() == "1":
                continue
            if "(ASAS-SN)" not in row.get("band", ""):
                continue
            asassn["t"].append(jd_to_dt(float(row["jd"])))
            asassn["m"].append(float(row["mag"]))
    print(f"  ASAS-SN: {len(asassn['m'])} points")

# --- Plot ---
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.edgecolor": "#444",
    "axes.linewidth": 0.8,
})
fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
fig.patch.set_facecolor("white")

for band in ["Vis.", "V"]:
    if band not in series or not series[band]["m"]:
        continue
    marker, color, label = STYLE[band]
    ax.plot(series[band]["t"], series[band]["m"], marker,
            ms=9, mfc=color, mec="white", mew=1.0,
            ls="none", alpha=0.9, zorder=3, label=label)

# Rolling 48h median over V + Vis. (centered window) — drawn behind data points
_trend_pts = sorted(
    zip(series["V"]["t"] + series["Vis."]["t"],
        series["V"]["m"] + series["Vis."]["m"]),
    key=lambda x: x[0]
)
if len(_trend_pts) >= 2:
    _tt = np.array([t.timestamp() for t, _ in _trend_pts])
    _mm = np.array([m for _, m in _trend_pts])
    _half = 24 * 3600  # 24 h either side → 48 h window
    _trend_t, _trend_m = [], []
    for i, (t, _) in enumerate(_trend_pts):
        mask = np.abs(_tt - _tt[i]) <= _half
        if mask.sum() >= 2:
            _trend_t.append(t)
            _trend_m.append(float(np.median(_mm[mask])))
    if _trend_t:
        ax.plot(_trend_t, _trend_m, "-", color="#c62828", lw=1.5,
                alpha=0.7, zorder=2, label="48 h rolling median (V+Vis.)")

for band in ["TG", "TB"]:
    if band not in series or not series[band]["m"]:
        continue
    marker, color, label = STYLE[band]
    ax.plot(series[band]["t"], series[band]["m"], marker,
            ms=9, mfc=color, mec="white", mew=1.0,
            ls="none", alpha=0.9, zorder=3, label=label)

if HIGHLIGHT_OBSERVER and observer_data["TG"]["m"]:
    ax.plot(observer_data["TG"]["t"], observer_data["TG"]["m"], "p",
            ms=12, mfc="#00e676", mec="#000", mew=0.6,
            ls="none", alpha=1.0, zorder=5, label=f"TG by {HIGHLIGHT_OBSERVER}")
if HIGHLIGHT_OBSERVER and observer_data["TB"]["m"]:
    ax.plot(observer_data["TB"]["t"], observer_data["TB"]["m"], "p",
            ms=12, mfc="#29b6f6", mec="#000", mew=0.6,
            ls="none", alpha=1.0, zorder=5, label=f"TB by {HIGHLIGHT_OBSERVER}")

if asassn["m"]:
    marker, color, label = ASASSN_STYLE
    ax.plot(asassn["t"], asassn["m"], marker,
            ms=7, mfc=color, mec="white", mew=0.8,
            ls="none", alpha=0.7, zorder=2, label=label)

ax.invert_yaxis()
ax.set_ylabel("Brightness [mag]")
ax.set_xlabel("Date / Time (UT)")
all_times = [t for b in PLOT_BANDS for t in series[b]["t"]] + asassn["t"]
_t0, _t1 = min(all_times), max(all_times)
_date_fmt = "%d%b%y"
_span = _t0.strftime(_date_fmt) if _t0.date() == _t1.date() else f"{_t0.strftime(_date_fmt)} - {_t1.strftime(_date_fmt)}"
_sources = "AAVSO + ASAS-SN" if asassn["m"] else "AAVSO"
_bands   = "Vis. + V + TG + TB + g(ASAS-SN)" if asassn["m"] else "Vis. + V + TG + TB"
ax.set_title(f"T CrB \u2013 Visual Light Curve ({_sources})\n{_span} \u00b7 {_bands}",
             fontsize=13, pad=12)
ax.grid(True, ls=":", color="#ccc", alpha=0.7)
ax.legend(loc="upper left", frameon=True, framealpha=0.9, fontsize=10)

_locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
ax.xaxis.set_major_locator(_locator)
ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
    _locator,
    formats=["%Y", "%b%y", "%d%b%y", "%H:%M", "%H:%M", "%S.%f"],
    offset_formats=["", "", "", "", "", ""]))
fig.autofmt_xdate(rotation=90, ha="right")

# shade quiescent level subtly
ax.axhspan(9.5, 10.2, color="#f1f3f4", zorder=0)
ax.text(0.99, 0.04, "Status: quiescent  (B/I/R/SU excluded)",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9, color="#666", style="italic")

fig.tight_layout()
fig.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
print("saved:", OUT_PATH)
