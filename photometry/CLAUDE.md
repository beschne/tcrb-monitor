# photometry/CLAUDE.md

Guidance specific to this folder. See the project root `CLAUDE.md` for the overall project. This folder holds the legacy Python differential-photometry scripts for T CrB — superseded by the dedicated PixInsight photometry script but kept as valuable, still-usable artifacts and cross-checks. Neither is part of the automated alert path.

## `tcrb_xisf_photometry.py` (automated, superseded)

Fully automated alternative to the DynamicPSF workflow — no PixInsight session required at run time. Imports `tcrb_dynamicpsf_photometry` from the same folder (VSP query/cache and `differential_magnitude()` code), so changes to `tcrb_dynamicpsf_photometry.py` affect both scripts.

```bash
.venv/bin/python photometry/tcrb_xisf_photometry.py raw_stack_2026-06-06.xisf
.venv/bin/python photometry/tcrb_xisf_photometry.py photometry/*.xisf
```

**Key design points and caveats:** see the "XISF differential photometry" section in the project root `CLAUDE.md`.

## `tcrb_dynamicpsf_photometry.py` (manual, original)

Manual companion script. Workflow:

1. In PixInsight, extract the green channel from the raw-stacked image and run **DynamicPSF** on it, clicking T CrB and surrounding comparison stars.
2. Export the DynamicPSF table as CSV — the export must include `alpha` (RA, degrees) and `delta` (Dec, degrees) columns per star in addition to the standard DynamicPSF columns (`flux`, `mad`, …). Save as `dynamicpsf_export.csv` in this folder (next to the script).
3. Run `python3 photometry/tcrb_dynamicpsf_photometry.py` (from anywhere — paths are resolved relative to the script's own location, not the working directory). The script:
   - Identifies T CrB by matching the nearest row to its known J2000 position (RA 239.882°, Dec +25.920°).
   - Queries the **AAVSO VSP API** for each comparison star's catalog V magnitude using its sky coordinates.
   - Computes differential photometry (`m_TG = m_V_comp − 2.5 log₁₀(F_T CrB / F_comp)`) per comparison star and averages the results.
   - Prints the derived TG magnitude with standard deviation and n.

**Key design points:**
- **Band label:** `TG` (AAVSO notation for DSLR/camera green channel approximating V). The monitor's alert logic ignores TG — only Vis. and V are evaluated for threshold crossings.
- **Quality filters:** rows with MAD > 0.05 or flux < 1.0 are silently rejected as poor PSF fits (PixInsight exports normalized flux, not raw counts).
- **VSP caching:** `dynamicpsf_vsp_cache.json` (this folder) is shared with `tcrb_xisf_photometry.py`, which queries the same sky field.
- **Dependency:** `requests`. Install into `.venv/` alongside matplotlib: `pip install requests`.
- **Input files:** `dynamicpsf_export.csv` and `dynamicpsf_vsp_cache.json` (both in this folder) are gitignored (local imaging data / API cache, not shared).

```bash
python3 photometry/tcrb_dynamicpsf_photometry.py
```

## `tools/`

Helper for the XISF workflow — see `tools/README.md`.
