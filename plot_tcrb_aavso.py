#!/usr/bin/env python3
"""
T CrB - error-bar light curve for a single AAVSO observer (TG/TB bands only).

Fetches live from the AAVSO VSX API rather than tcrb_history.csv, because the
CSV pipeline (tcrb_monitor.py) does not store the per-observation magnitude
uncertainty ("uncert" field) -- only the live API has it.
"""

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from math import floor

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

STAR = "000-BBW-825"  # T CrB AUID
VSX_URL = "https://vsx.aavso.org/index.php"
DELIMITER = "@@@"
USER_AGENT = "AGO-TCrB-Monitor/1.3 (Volkssternwarte Hochtaunus)"
MAX_POINTS = 200  # sanity limit for a readable error-bar chart; see --force/--complete
DEFAULT_LOOKBACK_DAYS = 365  # window used when --start is not given (VSX has no "all-time" query)

# style per band: (marker, colour, label) -- matches plot_tcrb_csv.py
STYLE = {
    "TG": ("s", "#188038", "TG (OSC green)"),
    "TB": ("s", "#1a73e8", "TB (OSC blue)"),
}


def jd_to_ut(jd):
    """Julian Date -> UTC datetime."""
    return datetime.fromtimestamp((jd - 2440587.5) * 86400, tz=timezone.utc)


def _jd_now():
    """Current Julian Date (UT)."""
    return (datetime.now(timezone.utc) - datetime(1858, 11, 17, tzinfo=timezone.utc)).total_seconds() / 86400 + 2400000.5


def _date_to_jd(year, month, day):
    """Calendar date (UT midnight) -> Julian Date."""
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5


def _parse_bound(value):
    """--start/--end value (JD or YYYY-MM-DD) -> JD float."""
    try:
        return float(value)
    except ValueError:
        pass
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"Cannot parse date/JD: {value!r} (expected a JD number or YYYY-MM-DD).")
    return _date_to_jd(d.year, d.month, d.day)


def fetch_observations(observer, star=STAR, start=None, end=None):
    """Fetch this observer's TG/TB-capable observations of `star` from the
    AAVSO VSX delimited-data API, optionally restricted to [start, end]
    (each JD or YYYY-MM-DD). Without --start, defaults to the trailing
    DEFAULT_LOOKBACK_DAYS -- VSX has no "give me everything" query mode."""
    jd_now = _jd_now()
    fromjd = _parse_bound(start) if start else jd_now - DEFAULT_LOOKBACK_DAYS
    tojd = _parse_bound(end) if end else jd_now + 0.5

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
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"AAVSO fetch failed: {e}")

    if text[:200].find("DB Error") != -1:
        sys.exit("AAVSO VSX API returned a database error.")

    lines = text.strip("\r\n").split("\n")
    if not lines or not lines[0].startswith(f"JD{DELIMITER}"):
        sys.exit("AAVSO VSX API structure changed: header row missing/unexpected.")

    header = lines[0].split(DELIMITER)
    try:
        cols = {name: header.index(name)
                for name in ("JD", "mag", "uncert", "band", "by", "fainterThan")}
    except ValueError:
        sys.exit("AAVSO VSX API structure changed: expected column missing.")

    obs = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(DELIMITER)
        if len(fields) <= max(cols.values()):
            continue
        if fields[cols["by"]].strip() != observer:
            continue
        if fields[cols["fainterThan"]].strip() == "1":
            continue  # upper limit, not a real detection
        try:
            jd = float(fields[cols["JD"]])
            mag = float(fields[cols["mag"]])
        except ValueError:
            continue
        err_raw = fields[cols["uncert"]].strip()
        err = float(err_raw) if err_raw not in ("", "—") else None
        obs.append({"jd": jd, "mag": mag, "err": err, "band": fields[cols["band"]].strip()})
    return obs


def bin_nightly(obs):
    """Collapse observations to one point per (band, observing night), where
    night = floor(JD) (JD's integer part changes at 12:00 UT, i.e. roughly
    local midday -- so a single evening-to-morning session stays together).
    The plotted value is the inverse-variance-weighted mean magnitude; the
    error bar is the weighted scatter within that night, not the (often much
    smaller) formal per-observation error -- night-to-night scatter is what
    matters when comparing against sparser data."""
    groups = {}
    for o in obs:
        key = (o["band"], floor(o["jd"]))
        groups.setdefault(key, []).append(o)

    binned = []
    for (band, _night), pts in groups.items():
        n = len(pts)
        jd_mean = sum(p["jd"] for p in pts) / n
        if n == 1:
            mag = pts[0]["mag"]
            err = pts[0]["err"] if pts[0]["err"] is not None else 0.0
        else:
            weights = [1.0 / (p["err"] ** 2) if p["err"] else 1.0 for p in pts]
            wsum = sum(weights)
            mag = sum(w * p["mag"] for w, p in zip(weights, pts)) / wsum
            var = sum(w * (p["mag"] - mag) ** 2 for w, p in zip(weights, pts)) / wsum
            err = var ** 0.5
        binned.append({"jd": jd_mean, "mag": mag, "err": err, "band": band, "n": n})
    return binned


def main():
    ap = argparse.ArgumentParser(
        description="Error-bar T CrB light curve (TG/TB only) for one AAVSO "
                     "observer, fetched live from the AAVSO VSX API.")
    ap.add_argument("--observer", required=True, help="AAVSO observer code, e.g. BSLA")
    ap.add_argument("--start", help=f"Start of period: JD or YYYY-MM-DD "
                     f"(default: {DEFAULT_LOOKBACK_DAYS} days ago)")
    ap.add_argument("--end", help="End of period: JD or YYYY-MM-DD (default: now)")
    ap.add_argument("--out", help="Output PNG path (default: <observer>_lightcurve.png)")
    ap.add_argument("--nightly-mean", action="store_true",
                     help="Bin observations by night (one point per band per night: "
                          "inverse-variance-weighted mean, error bar = night's scatter). "
                          "Recommended for very high-cadence automated observers.")
    ap.add_argument("--force", action="store_true",
                     help=f"Plot anyway even with more than {MAX_POINTS} points "
                          "(chart may be dense/unreadable).")
    ap.add_argument("--complete", action="store_true",
                     help="Plot every raw observation instead of the nightly mean, "
                          "even above the point sanity limit. Implies --force; "
                          "mutually exclusive with --nightly-mean.")
    args = ap.parse_args()
    if args.complete and args.nightly_mean:
        sys.exit("--complete and --nightly-mean are mutually exclusive.")
    if args.complete:
        args.force = True

    obs = fetch_observations(args.observer, start=args.start, end=args.end)
    obs = [o for o in obs if o["band"] in STYLE]
    if not obs:
        sys.exit(f"No TG/TB observations found for observer '{args.observer}' "
                  "in the given period.")

    if args.nightly_mean:
        obs = bin_nightly(obs)
        if len(obs) > MAX_POINTS and not args.force:
            sys.exit(
                f"Still {len(obs)} points after nightly averaging -- more than the "
                f"{MAX_POINTS}-point sanity limit for a readable chart.\n"
                "Recommendation: choose a narrower period with --start/--end.\n"
                "Use --force to plot anyway."
            )
    elif len(obs) > MAX_POINTS and not args.force:
        n_nights = len(bin_nightly(obs))
        sys.exit(
            f"{len(obs)} data points to plot -- more than the {MAX_POINTS}-point "
            "sanity limit for a readable error-bar chart.\n"
            "Recommendation: choose a narrower period with --start/--end, or add "
            f"--nightly-mean to average per observing night ({n_nights} points instead).\n"
            f"To plot all {len(obs)} raw points anyway, use --complete "
            "(or --force to override this check without changing the data)."
        )

    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
    fig.patch.set_facecolor("white")

    for band in ("TG", "TB"):
        pts = sorted((o for o in obs if o["band"] == band), key=lambda o: o["jd"])
        if not pts:
            continue
        marker, color, label = STYLE[band]
        jds = [o["jd"] for o in pts]
        mags = [o["mag"] for o in pts]
        errs = [o["err"] if o["err"] is not None else 0.0 for o in pts]
        ax.errorbar(jds, mags, yerr=errs, fmt=marker, ms=6, mfc=color, mec="white",
                    mew=0.8, ecolor=color, elinewidth=1.2, capsize=3,
                    ls="none", alpha=0.9, zorder=3, label=label)
        print(f"  {band}: {len(pts)} points")

    ax.invert_yaxis()
    ax.set_xlabel("JD")
    ax.set_ylabel("Magnitude")
    ax.grid(True, ls=":", color="#ccc", alpha=0.7)
    ax.legend(loc="best", frameon=True, framealpha=0.9, fontsize=10)

    # Show full JD values on the axis instead of matplotlib's default
    # "+2.4612e6" offset notation, which hides the actual date.
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    all_jd = [o["jd"] for o in obs]
    # Simultaneous TG/TB exposures from one OSC frame can share an identical JD;
    # with a zero (or tiny) x-range matplotlib's autoscale pads by a % of the
    # ~2.46e6 JD value itself (hundreds of thousands of days!) instead of a
    # sensible absolute margin. Set explicit limits to avoid that.
    jd_lo, jd_hi = min(all_jd), max(all_jd)
    pad = max((jd_hi - jd_lo) * 0.1, 0.02)
    ax.set_xlim(jd_lo - pad, jd_hi + pad)
    t0, t1 = jd_to_ut(min(all_jd)), jd_to_ut(max(all_jd))
    if t0.date() == t1.date():
        span = f"{t0:%Y-%m-%d} UT"
    else:
        span = f"{t0:%Y-%m-%d} – {t1:%Y-%m-%d} UT"
    ax.set_title(f"T CrB – {args.observer}\n{span}", fontsize=13, pad=12)

    fig.tight_layout()
    out = args.out or f"{args.observer}_lightcurve.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("saved:", out)


if __name__ == "__main__":
    main()
