# photometry/

Legacy Python differential-photometry scripts for T CrB. Both are superseded by the dedicated PixInsight photometry script but are kept as valuable, still-usable artifacts and cross-checks. Neither is part of the automated alert path.

Both have been validated against 30-minute stacks from a ZWO Seestar S30 Pro, demonstrating that compact smart telescopes of this class are capable of producing scientifically useful differential photometry.

---

## `tcrb_xisf_photometry.py` (automated, superseded)

Fully automated differential photometry straight from a plate-solved stacked XISF — no PixInsight session needed at run time. Parses the XISF header directly (green channel pixel data + embedded `PCL:AstrometricSolution`), projects T CrB and in-frame AAVSO comparison stars to pixel coordinates, fits 2D Gaussians, and derives the TG magnitude via the same differential-photometry code as `tcrb_dynamicpsf_photometry.py` (imported from there).

```bash
.venv/bin/python photometry/tcrb_xisf_photometry.py raw_stack_2026-06-06.xisf
.venv/bin/python photometry/tcrb_xisf_photometry.py photometry/*.xisf   # process a batch
```

Requires `numpy`, `astropy`, `scipy`, `requests` (`.venv/`).

See `CLAUDE.md` (project root) for design notes, caveats, and validation results.

---

## `tcrb_dynamicpsf_photometry.py` (manual, original)

The original manual workflow: extract the green channel in PixInsight, run **DynamicPSF**, click T CrB and comparison stars, export a CSV, then run this script to derive the TG magnitude.

```bash
python3 photometry/tcrb_dynamicpsf_photometry.py
```

(Paths are resolved relative to the script's own location, so this works from any working directory.)

Comparison star V magnitudes are fetched from the AAVSO VSP API on the first run and cached in `dynamicpsf_vsp_cache.json` (this folder — shared with `tcrb_xisf_photometry.py`, which queries the same sky field). Subsequent runs use the cache — no network access needed.

Requires `requests` (`pip install requests` into `.venv/`).

---

## `tools/`

Helper for the XISF workflow — see [`tools/README.md`](tools/README.md).
