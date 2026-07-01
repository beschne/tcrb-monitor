#!/usr/bin/env python3
"""
T CrB - Visual light curve from tcrb_history.csv
Plots Vis., V, TG and TB; B/I/R/SU excluded
(I/R = permanently bright M-giant, B/SU systematically offset).
"""

import argparse
import csv
from datetime import datetime, timedelta
import json
import os
import plistlib
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
except (FileNotFoundError, plistlib.InvalidFileException, OSError):
    _data_dir = _BASE

CSV_PATH        = os.path.join(_data_dir, "tcrb_history.csv")
STATE_PATH      = os.path.join(_data_dir, "tcrb_state.json")
ASASSN_CSV_PATH = os.path.join(_BASE, "asassn_history.csv")
OUT_PATH        = os.path.join(_BASE, "tcrb_lightcurve.png")
PLOT_BANDS = {"Vis.", "V", "TG", "TB"}
# style per band: (marker, colour, label)
STYLE = {
    "Vis.": ("o", "#f9a825", "Vis. (visual estimate)"),
    "V":    ("D", "#e8710a", "V (Johnson)"),
    "TG":   ("s", "#188038", "TG (OSC green)"),
    "TB":   ("s", "#1a73e8", "TB (OSC blue)"),
}
ASASSN_STYLE = ("^", "#9c27b0", "ASAS-SN (g)")
# --------------------------------------------------------------------


def jd_to_dt(jd):
    """Julian Date -> UTC datetime."""
    return datetime(2000, 1, 1, 12) + timedelta(days=jd - 2451545.0)


def _smooth_curve(ax, times, mags, color, zorder=4):
    """Draw a smooth PCHIP curve through sparse observer points (no legend entry)."""
    if len(times) < 2:
        return
    pts = sorted(zip(times, mags), key=lambda x: x[0])
    t_s = [p[0] for p in pts]
    m_s = [p[1] for p in pts]
    try:
        from scipy.interpolate import PchipInterpolator
        import numpy as _np
        x = _np.array([t.timestamp() for t in t_s])
        cs = PchipInterpolator(x, m_s)
        x_fine = _np.linspace(x[0], x[-1], 300)
        t_fine = [datetime.fromtimestamp(xi) for xi in x_fine]
        ax.plot(t_fine, cs(x_fine), "-", color=color, lw=1.4, alpha=0.8, zorder=zorder)
    except ImportError:
        ax.plot(t_s, m_s, "-", color=color, lw=1.4, alpha=0.8, zorder=zorder)


def main():
    ap = argparse.ArgumentParser(description="Plot T CrB light curve from tcrb_history.csv")
    ap.add_argument("--observer", default=None,
                    help="AAVSO observer code to highlight (TG/TB pentagrams + smooth curve)")
    args = ap.parse_args()
    observer = args.observer

    # --- Read CSV, group by band ---
    if not os.path.exists(CSV_PATH):
        raise SystemExit(
            f"CSV not found: {CSV_PATH}\n"
            "Run tcrb_monitor.py first, or check the plist WorkingDirectory.")

    series = {b: {"t": [], "m": []} for b in PLOT_BANDS}
    observer_data = {b: {"t": [], "m": []} for b in PLOT_BANDS}
    excluded = {}
    bad_rows = 0
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            band = row["band"].strip()
            if band not in PLOT_BANDS:
                excluded[band] = excluded.get(band, 0) + 1
                continue
            if row.get("fainter_than", "0").strip() == "1":
                continue
            try:
                t = jd_to_dt(float(row["jd"]))
                m = float(row["mag"])
            except (ValueError, KeyError):
                bad_rows += 1
                continue
            series[band]["t"].append(t)
            series[band]["m"].append(m)
            if observer and row.get("observer", "").strip() == observer:
                observer_data[band]["t"].append(t)
                observer_data[band]["m"].append(m)
    if bad_rows:
        print(f"  warning: skipped {bad_rows} malformed row(s) in {CSV_PATH}")

    print("excluded bands:", excluded)
    for b in PLOT_BANDS:
        print(f"  {b}: {len(series[b]['m'])} points")
    if observer:
        total = sum(len(observer_data[b]["m"]) for b in PLOT_BANDS)
        print(f"  {observer} (highlighted): {total} points")
        if total == 0:
            print(f"  warning: observer '{observer}' not found in CSV — check the observer code.")

    # --- Read ASAS-SN CSV if present ---
    asassn = {"t": [], "m": []}
    if os.path.exists(ASASSN_CSV_PATH):
        with open(ASASSN_CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("fainter_than", "0").strip() == "1":
                    continue
                if "(ASAS-SN)" not in row.get("band", ""):
                    continue
                try:
                    asassn["t"].append(jd_to_dt(float(row["jd"])))
                    asassn["m"].append(float(row["mag"]))
                except (ValueError, KeyError):
                    continue
        print(f"  ASAS-SN: {len(asassn['m'])} points")

    # --- Plot ---
    all_times = [t for b in PLOT_BANDS for t in series[b]["t"]] + asassn["t"]
    if not all_times:
        raise SystemExit("No plottable data found in the CSV (all rows filtered out).")

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.edgecolor": "#444",
        "axes.linewidth": 0.8,
    })
    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
    fig.patch.set_facecolor("white")

    leg = {}  # label -> handle, built up explicitly

    for band in ["Vis.", "V", "TG", "TB"]:
        if not series[band]["m"]:
            continue
        marker, color, label = STYLE[band]
        leg[label], = ax.plot(series[band]["t"], series[band]["m"], marker,
                              ms=6, mfc=color, mec="white", mew=0.8,
                              ls="none", alpha=0.9, zorder=3)

    if observer and observer_data["TG"]["m"]:
        _smooth_curve(ax, observer_data["TG"]["t"], observer_data["TG"]["m"], "#00e676")
        leg[f"TG by {observer}"], = ax.plot(
            observer_data["TG"]["t"], observer_data["TG"]["m"], "p",
            ms=6, mfc="#00e676", mec="#000", mew=0.6, ls="none", alpha=1.0, zorder=5)
    if observer and observer_data["TB"]["m"]:
        _smooth_curve(ax, observer_data["TB"]["t"], observer_data["TB"]["m"], "#29b6f6")
        leg[f"TB by {observer}"], = ax.plot(
            observer_data["TB"]["t"], observer_data["TB"]["m"], "p",
            ms=6, mfc="#29b6f6", mec="#000", mew=0.6, ls="none", alpha=1.0, zorder=5)

    if asassn["m"]:
        marker, color, label = ASASSN_STYLE
        leg[label], = ax.plot(asassn["t"], asassn["m"], marker,
                              ms=7, mfc=color, mec="white", mew=0.8,
                              ls="none", alpha=0.7, zorder=2)

    ax.invert_yaxis()
    ax.set_ylim(ax.get_ylim()[0] + 0.4, ax.get_ylim()[1])
    ax.set_ylabel("Brightness [mag]")
    ax.set_xlabel("Date / Time (UT)")

    _t0, _t1 = min(all_times), max(all_times)
    _date_fmt = "%d%b%y"
    _span = (_t0.strftime(_date_fmt) if _t0.date() == _t1.date()
             else f"{_t0.strftime(_date_fmt)} - {_t1.strftime(_date_fmt)}")
    _sources = "AAVSO + ASAS-SN" if asassn["m"] else "AAVSO"
    _bands   = "Vis. + V + TG + TB + g(ASAS-SN)" if asassn["m"] else "Vis. + V + TG + TB"
    ax.set_title(f"T CrB – Visual Light Curve ({_sources})\n{_span} · {_bands}",
                 fontsize=13, pad=12)
    ax.grid(True, ls=":", color="#ccc", alpha=0.7)

    # Legend — matplotlib fills column-first (items 0,1 → col0; 2,3 → col1; …)
    if observer:
        _order = [
            "Vis. (visual estimate)", "V (Johnson)",
            "TG (OSC green)",         "TB (OSC blue)",
            f"TG by {observer}",      f"TB by {observer}",
        ]
        _ncol = 3
    else:
        _order = ["Vis. (visual estimate)", "V (Johnson)", "TG (OSC green)", "TB (OSC blue)"]
        _ncol = 4
    _handles = [leg[k] for k in _order if k in leg]
    _labels  = [k       for k in _order if k in leg]
    ax.legend(_handles, _labels,
              loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=_ncol,
              frameon=True, framealpha=0.9, fontsize=10,
              handlelength=1.5, handletextpad=0.5, columnspacing=1.2)

    _locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    ax.xaxis.set_major_locator(_locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
        _locator,
        formats=["%Y", "%-d%b%y", "%d%b%y", "%H:%M", "%H:%M", "%S.%f"],
        offset_formats=["", "", "", "", "", ""]))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    fig.autofmt_xdate(rotation=45, ha="right")

    # Quiescent band + status from state file
    ax.axhspan(9.5, 10.2, color="#f1f3f4", zorder=0)
    _status = "quiescent"
    try:
        with open(STATE_PATH, encoding="utf-8") as sf:
            _status = json.load(sf).get("level", "quiescent")
    except (FileNotFoundError, OSError, ValueError):
        pass
    ax.text(0.99, 0.04, f"Status: {_status}  (B/I/R/SU excluded)",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, color="#666", style="italic")

    fig.tight_layout()
    fig.subplots_adjust(bottom=0.22)
    fig.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print("saved:", OUT_PATH)


if __name__ == "__main__":
    main()
