# Earth Engine: PWTT-Inspired Improvements

## Source of Inspiration

**Pixel-Wise T-Test (PWTT)** by Ollie Ballinger
- Repository: https://github.com/oballinger/PWTT
- Paper: [arXiv:2405.06323](https://arxiv.org/pdf/2405.06323)

The PWTT is a battle-damage detection algorithm that uses Sentinel-1 SAR imagery
to detect structural changes in urban areas. While designed for conflict damage
assessment (Gaza, Ukraine, Syria, Iraq), its SAR preprocessing and statistical
change-detection techniques transfer directly to flood detection, since both
problems rely on detecting backscatter amplitude changes between a baseline
period and an event period.

## What We Adopted (Steps 1-4)

### Step 1: Lee Speckle Filter

**Before:** Raw Sentinel-1 imagery fed directly into median composite.
**After:** Each scene passes through a Lee filter (MMSE estimator) before compositing.

The Lee filter is an adaptive noise suppression technique for SAR imagery. It
computes a pixel-level weight between the local neighborhood mean and the
original pixel value, based on the ratio of local signal variance to total
variance. The weight is derived from the theoretical speckle model for a given
Equivalent Number of Looks (ENL = 5 for Sentinel-1 GRD data).

```
weight = signal_variance / total_variance
filtered = (1 - weight) * local_mean + weight * original
```

Pixels in homogeneous areas get smoothed toward the local mean (strong noise
suppression), while pixels at edges retain their original values (edge
preservation). This is a direct port of the `lee_filter()` function from
`PWTT/code/pwtt.py`.

**File:** `data/compute_flood_extent.py`, function `_lee_filter()`

### Step 2: Log-Transform Preprocessing

**Before:** Operated on `COPERNICUS/S1_GRD` (dB scale) directly.
**After:** Switched to `COPERNICUS/S1_GRD_FLOAT` (linear power) and apply natural log.

PWTT operates on `S1_GRD_FLOAT` (linear power values) and applies `.log()` to
normalize the distribution before computing statistics. This is distinct from the
standard GRD product which is already in decibel scale. The natural log of linear
power has better statistical properties for change detection:

- More symmetric distribution (closer to Gaussian)
- Additive noise model instead of multiplicative
- Better suited for computing meaningful mean/variance statistics

**File:** `data/compute_flood_extent.py`, constant `DATASET_ID` changed to
`COPERNICUS/S1_GRD_FLOAT`, `.log()` added in `_preprocess_collection()`

### Step 3: Dual-Polarization (VV + VH)

**Before:** Single-pol VV only.
**After:** Both VV and VH bands processed, flood signal taken as min (most negative change).

PWTT uses both VV and VH polarizations and takes the maximum of the t-statistics
across both bands. VV is sensitive to surface roughness changes, while VH
captures volume scattering changes from vegetation and debris. For flood
detection, we take the minimum (most negative) change across both polarizations,
since standing water causes backscatter to decrease in both channels.

This dual-pol approach catches floods that might be ambiguous in a single
polarization — for example, areas where VV change is marginal but VH shows a
clear signal due to flooded vegetation.

**File:** `data/compute_flood_extent.py`, `POLARIZATIONS = ["VV", "VH"]`,
dual-pol combination in `_compute_flood_ttest()`

### Step 4: Dynamic World Urban Masking

**Before:** No land-use filtering — all terrain types treated equally.
**After:** Results masked to built-up areas using Google Dynamic World V1.

PWTT uses the `GOOGLE/DYNAMICWORLD/V1` `built` band to mask analysis to urban
and built-up areas. This eliminates false positives from:

- Natural water bodies (rivers, lakes, reservoirs)
- Agricultural fields (wet rice paddies register as flood-like changes)
- Vegetation changes (seasonal leaf-off mimics backscatter decrease)

We compute a temporal mean of the `built` probability over the baseline period
and retain only pixels where `built > 0.1`. For Jakarta's flood corridor, this
focuses the analysis on the neighborhoods and infrastructure that matter for
disaster response, rather than natural floodplain areas where water is expected.

**File:** `data/compute_flood_extent.py`, `DYNAMIC_WORLD_BUILT_THRESHOLD = 0.1`,
urban mask applied in `main()`

## What We Adopted (Steps 5-8)

### Step 5: T-Test Change Detection

**Before:** Simple difference of medians: `(flood_median - baseline_median) < threshold`.
**After:** Per-pixel Student's t-test with pooled variance and orbit-count degrees of freedom.

The core PWTT algorithm replaces ad-hoc thresholding with a proper statistical
test that accounts for the variance in both the baseline and event periods:

```
t = (post_mean - pre_mean) / pooled_SE
pooled_sd = sqrt((pre_sd^2 * (n1-1) + post_sd^2 * (n2-1)) / (n1+n2-2))
pooled_SE = pooled_sd * sqrt(1/n1 + 1/n2)
```

Following PWTT, n1/n2 are counts of **distinct `orbitNumber_start` values**
(unique satellite passes) rather than raw image counts, since images from the
same pass are not fully independent.

PWTT takes `abs(t)` for damage detection (any direction of backscatter change).
We keep the sign: negative t = backscatter decrease = standing water. The
threshold changed from `LOG_CHANGE_THRESHOLD = -0.35` to `T_STAT_THRESHOLD = -3.0`,
which corresponds roughly to 99% significance for a two-tailed test.

**File:** `data/compute_flood_extent.py`, function `_ttest_signed()`

### Step 6: Multi-Orbit Aggregation

**Before:** Filtered to `ORBIT_PASS = "DESCENDING"` (single viewing geometry).
**After:** Discovers all orbital tracks in the flood window, runs t-test per orbit, aggregates.

PWTT discovers all `relativeOrbitNumber_start` values covering the AOI in the
post-event period, computes t-statistics per orbit, then aggregates across
orbits. This eliminates viewing-geometry bias from any single orbital track.

For each orbit:
1. Build pre-event and post-event collections for that orbit number
2. Compute signed t-test per pixel (VV and VH independently)
3. Combine VV/VH: `min()` = most negative = strongest flood signal

Then aggregate across orbits: `min()` across all per-orbit t-images.

PWTT uses `max()` for damage (most extreme change in any direction). We use
`min()` because flood = negative change = lower t-value is stronger flood signal.

A `focalMedian(10, 'gaussian', 'meters')` smoothing pass is applied after
orbit aggregation, matching PWTT's post-aggregation filtering.

**File:** `data/compute_flood_extent.py`, functions `_get_post_event_orbits()`,
`_compute_flood_ttest()`

### Step 7: Multi-Scale Spatial Convolution

**Before:** T-statistic used directly after focal median smoothing.
**After:** Gaussian convolution at 50m/100m/150m radii averaged with original.

PWTT convolves the t-statistic image at multiple spatial scales using normalized
circular Gaussian kernels and averages the result with the original:

```python
k50  = image.convolve(ee.Kernel.circle(50,  'meters', True))
k100 = image.convolve(ee.Kernel.circle(100, 'meters', True))
k150 = image.convolve(ee.Kernel.circle(150, 'meters', True))
T_statistic = (original + k50 + k100 + k150) / 4
```

This produces a spatially smoother, more coherent detection map by incorporating
neighborhood context at multiple scales. Small flood patches that might be noise
at the original pixel level become more prominent when they align with broader
neighborhood-level backscatter changes. The sign convention is preserved:
negative values stay negative.

Applied after orbit aggregation + focalMedian, before thresholding.

**File:** `data/compute_flood_extent.py`, function `_multiscale_convolution()`,
constant `CONVOLUTION_RADII_METERS = [50, 100, 150]`

### Step 8: Terrain Flattening (Vollrath et al. 2020)

**Before:** Raw sigma0 values used directly (systematic slope-induced backscatter variations).
**After:** Radiometric slope correction using SRTM DEM, with layover/shadow masking.

PWTT applies the Vollrath et al. (2020) angular-based radiometric slope
correction per image, before the Lee filter. This removes systematic backscatter
variations caused by terrain slope:

1. Compute terrain slope and aspect from SRTM DEM
2. Derive local incidence angle from terrain geometry and satellite heading
3. Convert sigma0 to gamma0 (flat-earth correction)
4. Apply volume-scattering model correction factor
5. Mask layover areas (alpha_rRad < 0) and shadow areas (alpha_sRad > pi/2)

For Jakarta's mostly flat coastal plain (0-50m elevation), the impact is modest
in the lowlands, but meaningful along the southern hill approaches where the
Ciliwung river descends from the highlands. The layover/shadow masking also
removes pixels with unreliable SAR geometry regardless of terrain height.

Must be applied before Lee filter because it uses the `angle` band from
Sentinel-1 metadata, which is stripped during polarization band selection.

**File:** `data/compute_flood_extent.py`, function `_terrain_flattening()`,
constants `SRTM_DEM_ID`, `TERRAIN_FLATTENING_MODEL`

## Key Differences: PWTT vs HawkEye

| Aspect | PWTT | HawkEye |
|--------|------|---------|
| **Domain** | Battle damage (positive change = rubble) | Flood detection (negative change = water) |
| **Output** | Raster t-statistic map + per-building scores | Vector polygons (GeoJSON) for real-time display |
| **Runtime** | Batch / offline (Colab notebook) | Offline compute → runtime metadata → WebSocket → 3D globe |
| **Integration** | Standalone analysis tool | Part of a 5-agent multi-modal disaster response system |
| **Validation** | 873,072 building footprints across 23 cities | Demo-focused for Jakarta flood corridor |

## Attribution

All eight PWTT-inspired improvements (Steps 1-8) are adapted from the PWTT
algorithm. The Lee filter, t-test, multi-orbit aggregation, multi-scale
convolution, and terrain flattening implementations are ported from `pwtt.py`.
The key adaptation throughout is inverting aggregation operators: PWTT uses
`max()` / `abs()` for damage detection (any direction of backscatter change),
while HawkEye uses `min()` and signed values for flood detection (negative
backscatter change = standing water).
