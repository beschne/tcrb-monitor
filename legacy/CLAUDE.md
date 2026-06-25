# legacy/CLAUDE.md

Guidance specific to this folder. See the project root `CLAUDE.md` for the
overall project. This folder holds `tcrb_dynamicpsf_photometry.py`, the
original manual PixInsight DynamicPSF differential-photometry workflow —
kept as a valuable, still-usable artifact and cross-check, superseded as
the primary workflow by `../tcrb_xisf_photometry.py` (which imports this
module's VSP query/cache and `differential_magnitude()` code directly, so
changes here affect both scripts).

## PixInsight DynamicPSF photometry (`tcrb_dynamicpsf_photometry.py`)

Manual companion script for owner-acquired imaging data. Workflow:

1. In PixInsight, extract the green channel from the raw-stacked image (Rohsummenstack) and run **DynamicPSF** on it, clicking T CrB and surrounding comparison stars.
2. Export the DynamicPSF table as CSV — the export must include `alpha` (RA, degrees) and `delta` (Dec, degrees) columns per star in addition to the standard DynamicPSF columns (`flux`, `mad`, …). Save as `dynamicpsf_export.csv` in this folder (next to the script).
3. Run `python3 legacy/tcrb_dynamicpsf_photometry.py` (from anywhere — paths are resolved relative to the script's own location, not the working directory). The script:
   - Identifies T CrB by matching the nearest row to its known J2000 position (RA 239.882°, Dec +25.920°).
   - Queries the **AAVSO VSP API** for each comparison star's catalog V magnitude using its sky coordinates.
   - Computes differential photometry (`m_TG = m_V_comp − 2.5 log₁₀(F_T CrB / F_comp)`) per comparison star and averages the results.
   - Prints the derived TG magnitude with standard deviation and n.

**Key design points:**
- **Not in the alert path.** Run manually after each imaging session.
- **Band label:** `TG` (AAVSO notation for DSLR/camera green channel approximating V). The monitor's alert logic ignores TG — only Vis. and V are evaluated for threshold crossings.
- **Quality filters:** rows with MAD > 0.05 or flux < 1.0 are silently rejected as poor PSF fits (PixInsight exports normalized flux, not raw counts).
- **VSP caching:** all comparison stars in a frame typically fit within one VSP field of view, so the API is usually called only once per run. The cache file (`dynamicpsf_vsp_cache.json`, in this folder) is shared with `../tcrb_xisf_photometry.py`, which queries the same sky field.
- **Dependency:** `requests`. Install into `.venv/` alongside matplotlib: `pip install requests`.
- **Input files:** `dynamicpsf_export.csv` and `dynamicpsf_vsp_cache.json` (both in this folder) are gitignored (local imaging data / API cache, not shared).

```bash
python3 legacy/tcrb_dynamicpsf_photometry.py
```
