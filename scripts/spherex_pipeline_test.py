"""
Full SPHEREx pipeline test — Step 2 through Step 7.
Runs in GitHub Actions CI. Saves all results to ./outputs/

Robust against IRSA SIA outages:
  - Tries SIA (both collections) → TAP → hardcoded MEF URLs
  - If ALL external services fail → exits 0 with warning
    (external degradation should not block PRs)
  - Only exits 1 when our own code has a real bug
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
from astropy import units as u
from astroquery.ipac.irsa import Irsa

# ------------------------------------------------------------
# Setup
# ------------------------------------------------------------
OUT = Path("outputs"); OUT.mkdir(exist_ok=True)
LOG = []

def log(msg=""):
    print(msg)
    LOG.append(str(msg))

def summary(md):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(md + "\n")

def graceful_exit(msg: str):
    """Exit 0 with warning — external service degradation, not our bug."""
    log(f"\n⚠️  {msg}")
    log("Skipping remaining steps — external service issue, CI passes.")
    summary(f"## SPHEREx Pipeline Test — SKIPPED ⚠️\n\n{msg}")
    OUT.joinpath("pipeline_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    sys.exit(0)

# ------------------------------------------------------------
# Target
# ------------------------------------------------------------
coord = SkyCoord("08h55m10.831s", "-07d14m42.53s", frame="icrs")
log(f"Target: WISE 0855  RA={coord.ra.deg:.6f}  Dec={coord.dec.deg:.6f}")

# Fallback MEF (updated to a file known to be live)
FALLBACK_URL = (
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/"
    "2025W44_2B/l2b-v20-2025-307/1/"
    "level2_2025W44_2B_0116_1D6_spx_l2b-v20-2025-307.fits"
)

# ------------------------------------------------------------
# Step 2: Query — SIA first, then TAP
# ------------------------------------------------------------
log("\n=== Step 2: SIA query ===")
full_url = None
used = None

for collection in ["spherex_qr3", "spherex_qr2"]:
    log(f"Trying collection={collection}")
    try:
        res = Irsa.query_sia(pos=(coord, 0.05 * u.deg), collection=collection)
        log(f"  results: {len(res)}")
        if len(res) > 0:
            full_url = str(res["access_url"][0])
            used = collection
            break
    except Exception as e:
        log(f"  error: {e}")

# ── TAP fallback ──
if full_url is None:
    log("\n=== Step 2b: TAP fallback ===")
    try:
        import pyvo
        tap = pyvo.dal.TAPService("https://irsa.ipac.caltech.edu/TAP")
        query = f"""
            SELECT 'https://irsa.ipac.caltech.edu/' || a.uri AS access_url,
                   p.time_bounds_lower AS t_min
            FROM spherex.artifact a
            JOIN spherex.plane p ON a.planeid = p.planeid
            WHERE 1 = CONTAINS(POINT('ICRS', {coord.ra.deg}, {coord.dec.deg}), p.poly)
            ORDER BY p.time_bounds_lower
        """
        tap_result = tap.search(query)
        if len(tap_result) > 0:
            full_url = str(tap_result[0]["access_url"])
            used = "tap"
            log(f"  TAP returned {len(tap_result)} artifacts")
        else:
            log("  TAP returned 0 rows")
    except Exception as e:
        log(f"  TAP failed: {e}")

# ── Hardcoded fallback URL ──
if full_url is None:
    log("\n=== Step 2c: hardcoded fallback URL ===")
    full_url = FALLBACK_URL
    used = "hardcoded"

log(f"\nUsing source: {used}")

# ------------------------------------------------------------
# Step 3: Download cutout
# ------------------------------------------------------------
center = f"{coord.ra.deg:.8f},{coord.dec.deg:.8f}"
cutout_url = f"{full_url}?{urlencode({'center': center, 'size': '0.1'})}"
log(f"\n=== Step 3: Download cutout ===\n{cutout_url}")

try:
    hdul = fits.open(cutout_url)
except Exception as e:
    graceful_exit(f"Could not download cutout from {used}: {e}")

cutout_path = OUT / "spherex_cutout_wise0855.fits"
hdul.writeto(cutout_path, overwrite=True)
log(f"Saved: {cutout_path}  ({cutout_path.stat().st_size/1024:.1f} KB)")

# ------------------------------------------------------------
# Step 4: Inspect HDUs
# ------------------------------------------------------------
log("\n=== Step 4: HDU inspection ===")
hdu_names = [h.name for h in hdul]
log(f"HDUs: {hdu_names}")

required = {"IMAGE", "FLAGS", "VARIANCE"}
present = required & set(hdu_names)
log(f"Required present: {present}")
if present != required:
    log(f"❌ Missing required HDUs: {required - present}")
    summary(f"## SPHEREx Pipeline Test — FAIL ❌\n\nMissing required HDUs: {required - present}")
    OUT.joinpath("pipeline_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    sys.exit(1)

image = hdul["IMAGE"].data
flags = hdul["FLAGS"].data.astype(np.int64)
var = hdul["VARIANCE"].data
log(f"IMAGE shape={image.shape} dtype={image.dtype}")
log(f"FLAGS shape={flags.shape} dtype={flags.dtype}")
log(f"VARIANCE shape={var.shape} dtype={var.dtype}")

# ------------------------------------------------------------
# Step 5: Visualization
# ------------------------------------------------------------
log("\n=== Step 5: Visualization ===")
wcs = WCS(hdul["IMAGE"].header)
has_wcs = wcs.has_celestial
log(f"WCS celestial present: {has_wcs}")

fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
vmin, vmax = np.nanpercentile(image, [5, 95])
im0 = axes[0].imshow(image, origin="lower", cmap="inferno", vmin=vmin, vmax=vmax)
axes[0].set_title(f"SPHEREx IMAGE ({image.shape[0]}×{image.shape[1]})")
plt.colorbar(im0, ax=axes[0], label="MJy/sr")

im1 = axes[1].imshow(flags, origin="lower", cmap="viridis")
axes[1].set_title("FLAGS")
plt.colorbar(im1, ax=axes[1], label="bitmask")

plt.tight_layout()
plot_path = OUT / "spherex_cutout_plot.png"
plt.savefig(plot_path, dpi=120, bbox_inches="tight")
log(f"Saved plot: {plot_path}")

# ------------------------------------------------------------
# Step 6: Quality
# ------------------------------------------------------------
log("\n=== Step 6: Data quality ===")
BENIGN_BITS = {0, 2, 11, 17, 19, 21}
BENIGN_MASK = sum(1 << b for b in BENIGN_BITS)
STRICT_MASK = ~BENIGN_MASK & 0xFFFFFFFF

n_bad = int(np.sum(flags != 0))
strict_bad = int(np.sum((flags & STRICT_MASK) != 0))
frac_strict = strict_bad / flags.size

log(f"Raw non-zero flags: {n_bad}/{flags.size} ({n_bad/flags.size*100:.2f}%)")
log(f"Strict-bad (excluding benign): {strict_bad}/{flags.size} ({frac_strict*100:.2f}%)")

finite_var = var[np.isfinite(var)]
finite_img = image[np.isfinite(image)]
if finite_var.size:
    log(f"VARIANCE median: {np.median(finite_var):.3e}")
if finite_img.size:
    log(f"IMAGE valid pixels: {finite_img.size}/{image.size} ({finite_img.size/image.size*100:.1f}%)")

checks = {
    "WCS present": has_wcs,
    "Low strict-bad flags (<5%)": frac_strict < 0.05,
    "VARIANCE finite": finite_var.size > 0,
    "IMAGE finite": finite_img.size > 0,
}
for name, ok in checks.items():
    log(f"  [{'OK' if ok else 'FAIL'}] {name}")

n_ok = sum(checks.values())
verdict = ("PASS ✅" if n_ok == len(checks)
           else f"REVIEW ⚠️ ({n_ok}/{len(checks)})")
log(f"\nVerdict: {verdict}")

# ------------------------------------------------------------
# Step 7: Report
# ------------------------------------------------------------
OUT.joinpath("pipeline_log.txt").write_text("\n".join(LOG), encoding="utf-8")
summary(f"""
## SPHEREx Pipeline Test — {verdict}

| Check | Value |
|---|---|
| Source | `{used}` |
| HDUs | `{', '.join(hdu_names)}` |
| IMAGE shape | `{image.shape}` |
| Strict-bad | `{strict_bad}/{flags.size}` ({frac_strict*100:.2f}%) |
| WCS present | `{has_wcs}` |
""")

log("\nScript complete.")