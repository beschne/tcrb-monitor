# PixInsight DynamicPSF photometry (manual)

This is the original manual differential-photometry workflow for owner-acquired images — **not** part of the automated alert path. It has been superseded as the primary workflow by [`../tcrb_xisf_photometry.py`](../tcrb_xisf_photometry.py), which does the same differential photometry fully automatically straight from a plate-solved stacked XISF. This script is kept here as a valuable, still-usable artifact and manual cross-check — its VSP query/cache and differential-magnitude code is reused directly by the automated script.

Validated with 30-minute stacks from a ZWO Seestar S30 Pro, demonstrating that compact smart telescopes of this class are capable of producing scientifically useful differential photometry.

## Workflow

1. In PixInsight, extract the green channel from your stacked image and run **DynamicPSF** on it, clicking T CrB and several comparison stars.
2. Export the DynamicPSF table as CSV. The export must include `alpha` (RA, degrees) and `delta` (Dec, degrees) per star alongside the standard `flux`/`mad` columns. Save it as `dynamicpsf_export.csv` in this folder — or change the filename at the top of the script (`DYNAMICPSF_CSV`).
3. Run the script — it auto-identifies T CrB by proximity to its known catalog position, queries the AAVSO VSP API for each comparison star's V magnitude, and derives the TG magnitude via differential photometry.

```bash
python3 legacy/tcrb_dynamicpsf_photometry.py
```

(Paths are resolved relative to the script's own location, so this works from any working directory.)

Comparison star V magnitudes are fetched from the AAVSO VSP API on the first run and cached in `dynamicpsf_vsp_cache.json` (this folder — shared with `../tcrb_xisf_photometry.py`, which queries the same sky field). Subsequent runs use the cache — no network access needed.

Requires `requests` (`pip install requests` into `.venv/`).
