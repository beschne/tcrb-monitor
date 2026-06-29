# photometry/tools/

Ad-hoc helper scripts for the legacy Python photometry workflow — standalone, run manually, not imported by anything else in this repo.

## `tcrb_session_midtime.py`

Computes the true mid-exposure time for a Seestar imaging session, by scanning its SubframeSelector-output XISF light frames (`DATE-OBS` + `EXPTIME` per frame) — rather than trusting PixInsight `ImageIntegration`'s `DATE-OBS` on the stacked master, which is only an approximation. See `../../CLAUDE.md` for why that matters.

```bash
python3 photometry/tools/tcrb_session_midtime.py                 # scan every "<date> lights" folder in the default campaign dir
python3 photometry/tools/tcrb_session_midtime.py "/path/to/some lights folder"   # or scan specific folder(s)
```

For each session folder, it:
- Reads every `..._a.xisf` light frame (the SubframeSelector-approved output) and ignores `.fit`/`.fits` siblings entirely.
- Reports the number of subframes, exposure time per frame, first frame start, last frame end, total span, and the session's true mid-exposure time.

That mid-exposure time is the value to hand-correct the corresponding stack's `DATE-OBS` to, before running `../tcrb_xisf_photometry.py` on it.

No third-party dependencies — standard library only.
