#!/usr/bin/env python3
"""
Computes the true mid-exposure time for a Seestar imaging session, by
scanning its SubframeSelector-output XISF light frames (DATE-OBS + EXPTIME
per frame) - rather than trusting PixInsight ImageIntegration's DATE-OBS on
the stacked master, which is only an approximation (see CLAUDE.md /
tcrb_xisf_photometry.py: it's the midpoint between the first and last
subframe's *start* times, ignoring exposure duration).

Each campaign date folder ("<date> lights") contains, per captured frame,
both the original .fit and a SubframeSelector-processed "..._a.xisf"
sibling. Only the .xisf files are read - .fit/.fits files are ignored
entirely, since SubframeSelector's own measurement output (not the raw
capture) is what's being scanned here.

For each frame: start = DATE-OBS, end = DATE-OBS + EXPTIME seconds (these
subframes don't carry a DATE-END keyword at all). The session's true
mid-exposure time is the midpoint between the earliest start and the
latest end across all frames in the folder - that's the value to
hand-correct the corresponding stack's DATE-OBS to.

Confirmed against all four campaign sessions: each "<date> lights" folder
has far more .fit files than "..._a.xisf" siblings (e.g. 146 vs. 51 for
2026-06-25) - SubframeSelector only writes the "_a.xisf" output for frames
it approved, so scanning just the .xisf files already gives exactly the
approved subset, with no separate rejection filtering needed.

Usage:
  python3 tools/tcrb_session_midtime.py [folder ...]

  With no arguments, scans every "<date> lights" folder inside the default
  campaign directory below. That default is resolved relative to this
  file's own location (not the working directory), so it works the same
  regardless of where you invoke it from.
"""

import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

CAMPAIGN_DIR = (
    Path(__file__).resolve().parent.parent.parent / "T Coronae Borealis CAMPAIGN"
)

XISF_NS = {"x": "http://www.pixinsight.com/xisf"}


def read_fits_keyword(image_el: ET.Element, name: str) -> str | None:
    el = image_el.find(f"x:FITSKeyword[@name='{name}']", XISF_NS)
    if el is None:
        return None
    return el.get("value", "").strip("'")


def read_frame_times(path: Path) -> tuple[datetime, datetime, float | None] | None:
    """Returns (start, end, exptime_seconds_or_None), or None if the file
    isn't a readable XISF with usable DATE-OBS/EXPTIME (or DATE-END, if
    present)."""
    with open(path, "rb") as f:
        if f.read(8) != b"XISF0100":
            return None
        header_len = struct.unpack("<I", f.read(4))[0]
        f.read(4)  # reserved
        xml_bytes = f.read(header_len)

    image_el = ET.fromstring(xml_bytes).find("x:Image", XISF_NS)
    if image_el is None:
        return None

    date_obs = read_fits_keyword(image_el, "DATE-OBS")
    if date_obs is None:
        return None
    start = datetime.fromisoformat(date_obs)

    exptime_str = read_fits_keyword(image_el, "EXPTIME")
    exptime = float(exptime_str) if exptime_str is not None else None

    date_end = read_fits_keyword(image_el, "DATE-END")
    if date_end is not None:
        return start, datetime.fromisoformat(date_end), exptime

    if exptime is None:
        return None
    return start, start + timedelta(seconds=exptime), exptime


def process_folder(folder: Path) -> None:
    xisf_files = sorted(folder.glob("*.xisf"))
    print(f"{folder.name}:")
    if not xisf_files:
        print("  no .xisf files found, skipping.")
        return

    starts, ends, exptimes, skipped = [], [], [], 0
    for f in xisf_files:
        times = read_frame_times(f)
        if times is None:
            skipped += 1
            continue
        starts.append(times[0])
        ends.append(times[1])
        if times[2] is not None:
            exptimes.append(times[2])

    if not starts:
        print(
            f"  {len(xisf_files)} .xisf files found, but none had usable "
            f"DATE-OBS/EXPTIME header data."
        )
        return

    first_start = min(starts)
    last_end = max(ends)
    mid = first_start + (last_end - first_start) / 2

    note = f" ({skipped} skipped - missing header data)" if skipped else ""
    print(f"  number of subframes: {len(starts)}{note}")
    if not exptimes:
        print(f"  exposure time per frame: unknown (no EXPTIME header)")
    elif len(set(exptimes)) == 1:
        print(f"  exposure time per frame: {exptimes[0]:g}s")
    else:
        print(
            f"  exposure time per frame: varies ({min(exptimes):g}s - {max(exptimes):g}s)"
        )
    print(f"  first frame start: {first_start.isoformat()}")
    print(f"  last frame end:    {last_end.isoformat()}")
    print(f"  span: {last_end - first_start}")
    print(f"  mid-exposure time: {mid.isoformat()}  <- set as DATE-OBS on the stack")


def main():
    if len(sys.argv) > 1:
        folders = [Path(p) for p in sys.argv[1:]]
    else:
        if not CAMPAIGN_DIR.exists():
            sys.exit(f"Error: default campaign folder not found: {CAMPAIGN_DIR}")
        folders = sorted(
            p
            for p in CAMPAIGN_DIR.iterdir()
            if p.is_dir() and p.name.endswith("lights")
        )
        if not folders:
            sys.exit(f"Error: no '*lights' folders found in {CAMPAIGN_DIR}")

    for i, folder in enumerate(folders):
        if i:
            print()
        if not folder.is_dir():
            print(f"{folder}: not a directory, skipping.")
            continue
        process_folder(folder)


if __name__ == "__main__":
    main()
