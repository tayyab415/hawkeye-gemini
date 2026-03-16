"""Sentinel-1 SAR flood extent computation for Jakarta.

Run once to produce data/geojson/flood_extent.geojson.
Requires authenticated Earth Engine access:
    earthengine authenticate
    python data/compute_flood_extent.py

The script computes flood extent by:
1) Filtering Sentinel-1 GRD_FLOAT (linear power) imagery
2) Applying Vollrath terrain flattening (sigma0 → gamma0, layover/shadow mask)
3) Applying Lee speckle filter (MMSE estimator) for noise suppression
4) Log-transforming to normalize the distribution
5) Running per-pixel Student's t-test between baseline and event periods
6) Aggregating t-statistics across multiple orbital tracks (multi-orbit)
7) Combining dual-pol (VV+VH) t-statistics, taking min (strongest flood signal)
8) Multi-scale spatial convolution (50/100/150m Gaussian averaging)
9) Masking to urban/built-up areas via Dynamic World V1
10) Applying denoise / connected-component cleanup
11) Vectorizing cleaned flood pixels to polygons

Implements the Pixel-Wise T-Test (PWTT) algorithm adapted for flood detection:
    https://github.com/oballinger/PWTT
    Ballinger, O. (2024). arXiv:2405.06323
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import ee


PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "").strip()
OUTPUT_DIR = Path(__file__).resolve().parent / "geojson"
OUTPUT_FILE = OUTPUT_DIR / "flood_extent.geojson"
PROVENANCE_FILE = OUTPUT_DIR / "analysis_provenance.json"

# Jakarta bounds with focus around Ciliwung / central Jakarta flood corridor.
JAKARTA_AOI_BOUNDS = (106.74, -6.38, 106.99, -6.08)

DATASET_ID = "COPERNICUS/S1_GRD_FLOAT"
DATASET_DISPLAY = "COPERNICUS/S1_GRD"  # For provenance display
INSTRUMENT_MODE = "IW"
POLARIZATIONS = ["VV", "VH"]

# Signed t-statistic threshold for flood detection.
# Negative t means backscatter decrease (standing water).  PWTT uses t > 3
# for damage; we use t < -3 for flood.  -3.0 corresponds roughly to 99%
# significance for a two-tailed test, tuned conservatively to reduce false
# positives in dense urban Jakarta.
T_STAT_THRESHOLD = -3.0

# Gaussian median filter radius applied after multi-orbit aggregation,
# matching PWTT's focalMedian(10, 'gaussian', 'meters') smoothing pass.
FOCAL_MEDIAN_RADIUS_METERS = 10

CONVOLUTION_RADII_METERS = [50, 100, 150]

DENOISE_RADIUS_METERS = 30
MIN_CONNECTED_PIXELS = 8
MIN_POLYGON_AREA_SQM = 20_000
VECTOR_SCALE_METERS = 30
MAX_OUTPUT_POLYGONS = 50
LEE_KERNEL_SIZE = 2  # pixels; matches PWTT default
LEE_ENL = 5  # equivalent number of looks for S1 GRD multilooked data

# SRTM DEM for terrain flattening (Vollrath et al. 2020).
SRTM_DEM_ID = "USGS/SRTMGL1_003"
TERRAIN_FLATTENING_MODEL = "VOLUME"  # "VOLUME" or "DIRECT"
TERRAIN_BUFFER = 0  # buffer around AOI for DEM clip (meters)

# Dynamic World urban masking: minimum built-area probability to retain a pixel.
DYNAMIC_WORLD_BUILT_THRESHOLD = 0.1


@dataclass(frozen=True)
class DateWindow:
    label: str
    start: str
    end_exclusive: str


BASELINE_WINDOW = DateWindow(
    label="2025-06-01 to 2025-09-30",
    start="2025-06-01",
    end_exclusive="2025-10-01",
)

FLOOD_WINDOWS = [
    DateWindow(
        label="2026-01-01 to 2026-02-28",
        start="2026-01-01",
        end_exclusive="2026-03-01",
    ),
    DateWindow(
        label="2025-12-01 to 2025-12-31",
        start="2025-12-01",
        end_exclusive="2026-01-01",
    ),
    DateWindow(
        label="2025-11-01 to 2025-11-30",
        start="2025-11-01",
        end_exclusive="2025-12-01",
    ),
    DateWindow(
        label="2025-10-01 to 2025-10-31",
        start="2025-10-01",
        end_exclusive="2025-11-01",
    ),
]


def _initialize_earth_engine() -> None:
    print("[INFO] Initializing Earth Engine...")
    if PROJECT_ID:
        print(f"[INFO] Using GCP project: {PROJECT_ID}")
        ee.Initialize(project=PROJECT_ID)
        return

    print("[WARN] GCP_PROJECT_ID not set; using default Earth Engine project.")
    ee.Initialize()


def _jakarta_aoi() -> ee.Geometry:
    return ee.Geometry.BBox(*JAKARTA_AOI_BOUNDS)


def _terrain_flattening(
    collection: ee.ImageCollection,
    model: str = TERRAIN_FLATTENING_MODEL,
) -> ee.ImageCollection:
    """Radiometric slope correction for SAR imagery using SRTM DEM.

    Adapted from PWTT (Ballinger 2024) which implements Vollrath et al. (2020):
      "Angular-Based Radiometric Slope Correction for Sentinel-1 on
       Google Earth Engine"

    Converts sigma0 to gamma0 and applies a slope-dependent correction factor,
    then masks layover and shadow areas where SAR geometry produces unreliable
    backscatter values.

    Must be applied BEFORE Lee filter and BEFORE .select(POLARIZATIONS),
    because it uses the 'angle' band from Sentinel-1 metadata.

    Args:
        collection: Sentinel-1 GRD_FLOAT collection (must have 'angle' band).
        model: Scattering model — 'VOLUME' (default) or 'DIRECT'.

    Returns:
        Terrain-corrected collection with layover/shadow pixels masked.
    """
    aoi = _jakarta_aoi()
    dem = ee.Image(SRTM_DEM_ID).clip(aoi)
    terrain = ee.Algorithms.Terrain(dem)
    slope_img = terrain.select("slope")
    aspect_img = terrain.select("aspect")
    ninetyRad = ee.Image.constant(90).multiply(
        ee.Image.constant(3.14159265358979).divide(180)
    )

    def correct(image: ee.Image) -> ee.Image:
        # Clip to AOI early to reduce memory footprint.
        image = image.clip(aoi)

        # Radar geometry: satellite heading from image metadata.
        heading = (
            ee.Terrain.aspect(image.select("angle"))
            .reduceRegion(ee.Reducer.mean(), aoi, 1000)
            .get("aspect")
        )

        heading = ee.Algorithms.If(
            ee.Algorithms.IsEqual(heading, None),
            ee.Number(0),
            ee.Number(heading),
        )
        heading = ee.Number(heading)

        # Terrain: slope and aspect from pre-computed SRTM terrain.
        slope = slope_img
        aspect = aspect_img

        # Convert degrees to radians.
        deg2rad = ee.Image.constant(3.14159265358979).divide(180)
        slope_rad = slope.multiply(deg2rad)
        aspect_rad = aspect.multiply(deg2rad)
        theta_iRad = image.select("angle").multiply(deg2rad)
        heading_rad = ee.Image.constant(heading).multiply(deg2rad)

        # Relative azimuth: aspect relative to satellite heading.
        phi_iRad = heading_rad.subtract(aspect_rad).add(
            ee.Image.constant(3.14159265358979)
        )

        # Local incidence angle via spherical geometry.
        cos_theta_iRad = theta_iRad.cos()
        sin_theta_iRad = theta_iRad.sin()

        # Projected slope in range direction.
        alpha_sRad = (
            slope_rad.cos()
            .multiply(cos_theta_iRad)
            .add(slope_rad.sin().multiply(sin_theta_iRad).multiply(phi_iRad.cos()))
        ).acos()
        # Range-projected local incidence angle.
        alpha_rRad = alpha_sRad.subtract(theta_iRad)

        # Gamma0 flat: convert sigma0 to gamma0 using incidence angle.
        gamma0 = image.divide(cos_theta_iRad)

        # Apply scattering model correction.
        if model == "VOLUME":
            # Volume scattering: correction ∝ cos(alpha_rRad) / cos(theta_iRad)
            corrected = gamma0.multiply(alpha_rRad.cos().divide(cos_theta_iRad))
        else:
            # Direct/surface scattering: correction based on local incidence
            corrected = gamma0.multiply(alpha_sRad.cos().divide(cos_theta_iRad))

        # Mask layover and shadow.
        # Layover: local slope facing toward radar (alpha_rRad < 0)
        # Shadow: local slope facing away from radar (alpha_rRad > pi/2 - theta_iRad
        #         equivalently: alpha_sRad > pi/2)
        layover_mask = alpha_rRad.gt(0)
        shadow_mask = alpha_sRad.lt(ninetyRad)
        ls_mask = layover_mask.And(shadow_mask)

        return (
            corrected.updateMask(ls_mask)
            .copyProperties(image)
            .copyProperties(image, ["system:time_start", "system:time_end"])
        )

    return collection.map(correct)


def _lee_filter(image: ee.Image) -> ee.Image:
    """Apply Lee speckle filter (MMSE estimator) to SAR imagery.

    Adapted from the PWTT algorithm (Ballinger 2024). Reduces speckle noise
    while preserving edges by computing a pixel-level weight between the local
    mean and the original value based on local variance and the theoretical
    speckle variance for a given equivalent number of looks (ENL).
    """
    band_names = image.bandNames().remove("angle")
    eta = ee.Image.constant(1.0 / LEE_ENL**0.5)
    one_img = ee.Image.constant(1)

    reducers = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.variance(),
        sharedInputs=True,
    )
    stats = image.select(band_names).reduceNeighborhood(
        reducer=reducers,
        kernel=ee.Kernel.square(LEE_KERNEL_SIZE / 2, "pixels"),
        optimization="window",
    )

    mean_band = band_names.map(lambda b: ee.String(b).cat("_mean"))
    var_band = band_names.map(lambda b: ee.String(b).cat("_variance"))

    z_bar = stats.select(mean_band)
    varz = stats.select(var_band)

    # Estimate weight: ratio of signal variance to total variance.
    varx = varz.subtract(z_bar.pow(2).multiply(eta.pow(2))).divide(
        one_img.add(eta.pow(2))
    )
    b = varx.divide(varz)
    # Clamp negative weights to zero.
    b = b.where(b.lt(0), 0)

    output = (
        one_img.subtract(b)
        .multiply(z_bar.abs())
        .add(b.multiply(image.select(band_names)))
    )
    output = output.rename(band_names)
    return image.addBands(output, None, True)


def _build_s1_base() -> ee.ImageCollection:
    """Base Sentinel-1 collection: AOI + instrument mode + VV/VH polarization.

    Does NOT filter by date, orbit pass, or relative orbit number.
    Those filters are applied downstream per-orbit in the t-test pipeline.
    """
    return (
        ee.ImageCollection(DATASET_ID)
        .filterBounds(_jakarta_aoi())
        .filter(ee.Filter.eq("instrumentMode", INSTRUMENT_MODE))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )


def _preprocess_collection(
    base: ee.ImageCollection,
    window: DateWindow,
    orbit: int | None = None,
) -> ee.ImageCollection:
    """Filter base to a date window (and optional orbit), apply terrain correction + Lee + log.

    Pipeline per image:
      1. Filter by date window
      2. Optionally filter by relativeOrbitNumber_start
      3. Apply Vollrath terrain flattening (sigma0 → gamma0, mask layover/shadow)
      4. Apply Lee speckle filter (MMSE noise suppression)
      5. Select VV and VH bands
      6. Log-transform (natural log of linear power values)
    """
    filtered = base.filterDate(window.start, window.end_exclusive)
    if orbit is not None:
        filtered = filtered.filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
    # Terrain flattening must run before Lee filter (needs 'angle' band).
    flattened = _terrain_flattening(filtered)

    def _log_with_props(image: ee.Image) -> ee.Image:
        """Log-transform preserving metadata properties."""
        return (
            image.log()
            .copyProperties(image)
            .copyProperties(image, ["system:time_start", "system:time_end"])
        )

    return flattened.map(_lee_filter).select(POLARIZATIONS).map(_log_with_props)


def _ttest_signed(
    pre: ee.ImageCollection,
    post: ee.ImageCollection,
) -> ee.Image:
    """Compute signed per-pixel t-statistic between two image collections.

    Implements the core PWTT algorithm (Ballinger 2024) adapted for flood:
      t = (post_mean - pre_mean) / pooled_SE

    where:
      pooled_sd = sqrt((pre_sd²*(n1-1) + post_sd²*(n2-1)) / (n1+n2-2))
      pooled_SE = pooled_sd * sqrt(1/n1 + 1/n2)

    PWTT takes abs(t) for damage detection.  For flood, we keep the sign:
    negative t = backscatter decrease = standing water.

    Following PWTT, n1/n2 are counts of distinct orbitNumber_start values
    (unique satellite passes) rather than raw image counts, since images
    from the same pass are not fully independent.

    Returns a two-band image (VV, VH) with signed t-values.
    """
    pre_mean = pre.mean()
    pre_sd = pre.reduce(ee.Reducer.stdDev())
    # Distinct orbit passes = independent observations, matching PWTT.
    pre_n = ee.Number(pre.aggregate_array("orbitNumber_start").distinct().size())

    post_mean = post.mean()
    post_sd = post.reduce(ee.Reducer.stdDev())
    post_n = ee.Number(post.aggregate_array("orbitNumber_start").distinct().size())

    # Pooled standard deviation.
    pooled_sd = (
        pre_sd.pow(2)
        .multiply(pre_n.subtract(1))
        .add(post_sd.pow(2).multiply(post_n.subtract(1)))
        .divide(pre_n.add(post_n).subtract(2))
        .sqrt()
    )

    # Standard error of the difference in means.
    denom = pooled_sd.multiply(
        ee.Image(1).divide(pre_n).add(ee.Image(1).divide(post_n)).sqrt()
    )

    # Signed t-statistic: negative = post lower than pre = flood.
    # EE matches bands by position: post_mean (VV, VH) / denom (VV_stdDev,
    # VH_stdDev) → result inherits band names VV, VH.
    t_stat = post_mean.subtract(pre_mean).divide(denom)

    return t_stat


def _multiscale_convolution(t_image: ee.Image) -> ee.Image:
    """Apply multi-scale Gaussian convolution and average with original.

    Adapted from PWTT (Ballinger 2024).  Convolves the t-statistic image at
    several radii using normalized circular Gaussian kernels and averages the
    results with the original.  This produces a spatially smoother, more
    coherent detection map by incorporating neighborhood context at multiple
    scales.

    PWTT code:
        k50  = image.convolve(ee.Kernel.circle(50,  'meters', True))
        k100 = image.convolve(ee.Kernel.circle(100, 'meters', True))
        k150 = image.convolve(ee.Kernel.circle(150, 'meters', True))
        T_statistic = (max_change + k50 + k100 + k150) / 4

    For flood we keep sign convention: negative values stay negative.
    """
    convolved = [
        t_image.convolve(ee.Kernel.circle(radius, "meters", True))
        for radius in CONVOLUTION_RADII_METERS
    ]
    # Average original + all convolved versions.
    composite = t_image
    for c in convolved:
        composite = composite.add(c)
    composite = composite.divide(len(convolved) + 1)
    return composite


def _get_post_event_orbits(
    base: ee.ImageCollection,
    flood_window: DateWindow,
) -> ee.List:
    """Get distinct relative orbit numbers covering the AOI in the flood window.

    PWTT aggregates across all available orbits rather than filtering to a
    single ascending/descending pass, eliminating viewing-geometry bias.
    """
    return (
        base.filterDate(flood_window.start, flood_window.end_exclusive)
        .aggregate_array("relativeOrbitNumber_start")
        .distinct()
    )


def _compute_flood_ttest(
    baseline_window: DateWindow,
    flood_window: DateWindow,
) -> tuple[ee.Image, int, int, int]:
    """Full PWTT flood detection pipeline with multi-orbit aggregation.

    For each relative orbit number covering the AOI in the flood window:
      1. Build pre-event and post-event collections for that orbit
      2. Compute signed t-test per pixel (VV and VH independently)
      3. Combine VV/VH: min() = most negative = strongest flood signal

    Then aggregate across orbits: min() across all per-orbit t-images.

    PWTT uses max() for damage (any direction of change).  We use min()
    because flood = negative change = lower t-value.

    Returns:
      (t_image, total_baseline_scenes, total_flood_scenes, used_orbit_count)
    """
    base = _build_s1_base()

    # Discover all orbits available in the flood window.
    orbits = _get_post_event_orbits(base, flood_window)
    orbit_list: list[int] = orbits.getInfo()
    orbit_count = len(orbit_list)
    print(f"[INFO] Found {orbit_count} distinct orbital track(s): {orbit_list}")

    if orbit_count == 0:
        raise RuntimeError(
            f"No Sentinel-1 orbits found for flood window: {flood_window.label}"
        )

    total_baseline_scenes = 0
    total_flood_scenes = 0
    per_orbit_images: list[ee.Image] = []

    for orbit_num in orbit_list:
        pre_col = _preprocess_collection(base, baseline_window, orbit_num)
        post_col = _preprocess_collection(base, flood_window, orbit_num)

        pre_count = int(ee.Number(pre_col.size()).getInfo())
        post_count = int(ee.Number(post_col.size()).getInfo())
        print(
            f"[INFO] Orbit {orbit_num}: "
            f"baseline={pre_count} scene(s), flood={post_count} scene(s)"
        )

        # T-test requires ≥2 observations in each period for variance.
        if pre_count < 2 or post_count < 2:
            print(
                f"[WARN] Orbit {orbit_num}: skipping — need ≥2 scenes in both "
                f"periods for t-test (got {pre_count} pre, {post_count} post)"
            )
            continue

        total_baseline_scenes += pre_count
        total_flood_scenes += post_count

        t_image = _ttest_signed(pre_col, post_col)

        # Dual-pol combination: min of VV and VH t-values.
        # Both VV and VH decrease with standing water, so the most negative
        # t-value across polarizations is the strongest flood signal.
        # PWTT uses max(VV, VH) for damage; we use min(VV, VH) for flood.
        orbit_flood_t = t_image.select("VV").min(t_image.select("VH")).rename("flood_t")
        per_orbit_images.append(orbit_flood_t.clip(_jakarta_aoi()))

    if not per_orbit_images:
        raise RuntimeError(
            "No orbital tracks had sufficient scenes (≥2) in both baseline "
            "and flood periods for t-test computation."
        )

    used_orbits = len(per_orbit_images)
    print(f"[INFO] Aggregating t-statistics across {used_orbits} orbit(s)...")

    # Multi-orbit aggregation: min (most negative t across all orbits).
    # PWTT uses max() for damage; we use min() for flood.
    if used_orbits == 1:
        aggregated = per_orbit_images[0]
    else:
        aggregated = ee.ImageCollection(per_orbit_images).min()

    # Post-aggregation Gaussian median smoothing, matching PWTT:
    #   image.focalMedian(10, 'gaussian', 'meters')
    aggregated = aggregated.focalMedian(
        FOCAL_MEDIAN_RADIUS_METERS, "gaussian", "meters"
    )

    # Multi-scale spatial convolution, matching PWTT:
    #   Convolve at 50m/100m/150m and average with original.
    #   Produces spatially smoother, more coherent t-statistic map.
    #   NOTE: Convolution dilutes isolated signals — threshold must be adjusted.
    aggregated = _multiscale_convolution(aggregated)

    return aggregated, total_baseline_scenes, total_flood_scenes, used_orbits


# Post-convolution threshold: the multi-scale convolution averages the
# pixel-level t-stat with neighborhood averages at 50/100/150m, which
# dilutes isolated flood signals toward zero.  Compensate by lowering
# the threshold from the raw t-stat value of -3.0.  -1.5 roughly
# corresponds to a pixel-level t of -3.0 averaged with moderate
# neighborhood support.
T_STAT_THRESHOLD_CONVOLVED = -1.5


def _select_flood_window(
    base: ee.ImageCollection,
) -> tuple[DateWindow, list[int]]:
    """Try each flood window until we find one with sufficient orbital coverage."""
    attempts: list[str] = []
    for window in FLOOD_WINDOWS:
        orbits = _get_post_event_orbits(base, window)
        orbit_list: list[int] = orbits.getInfo()
        orbit_count = len(orbit_list)
        scene_count = int(
            ee.Number(
                base.filterDate(window.start, window.end_exclusive).size()
            ).getInfo()
        )
        attempts.append(f"{window.label} ({scene_count} scenes, {orbit_count} orbits)")
        if orbit_count > 0 and scene_count >= 2:
            print(f"[INFO] Selected flood window: {window.label}")
            return window, orbit_list

    attempted = "; ".join(attempts)
    raise RuntimeError(
        "No Sentinel-1 scenes found in any flood fallback window. "
        f"Attempted: {attempted}"
    )


def _cleanup_flood_mask(raw_mask: ee.Image) -> ee.Image:
    binary = raw_mask.unmask(0).rename("flood_binary")
    smoothed = (
        binary.focal_max(radius=DENOISE_RADIUS_METERS, units="meters")
        .focal_min(radius=DENOISE_RADIUS_METERS, units="meters")
        .rename("flood_binary")
    )
    connected = smoothed.connectedPixelCount(maxSize=512, eightConnected=True)
    cleaned = (
        smoothed.updateMask(smoothed)
        .updateMask(connected.gte(MIN_CONNECTED_PIXELS))
        .selfMask()
        .rename("flood_mask")
    )
    return cleaned


def _vectorize_flood_mask(
    flood_mask: ee.Image,
) -> tuple[list[dict], int, float, float]:
    vectors = ee.FeatureCollection(
        flood_mask.reduceToVectors(
            geometry=_jakarta_aoi(),
            scale=VECTOR_SCALE_METERS,
            geometryType="polygon",
            eightConnected=True,
            maxPixels=1e10,
        )
    )

    def _with_area(feature: ee.Feature) -> ee.Feature:
        return feature.set("area_sqm", feature.geometry().area(maxError=10))

    vectors = vectors.map(_with_area).filter(
        ee.Filter.gte("area_sqm", MIN_POLYGON_AREA_SQM)
    )

    polygon_count = int(ee.Number(vectors.size()).getInfo())
    if polygon_count == 0:
        raise RuntimeError(
            "Flood vectorization produced no polygons after denoise/area filtering."
        )

    vectors_sorted = vectors.sort("area_sqm", False)
    vector_geojson = vectors_sorted.getInfo()
    selected_features = vector_geojson.get("features", [])[:MAX_OUTPUT_POLYGONS]
    if not selected_features:
        raise RuntimeError(
            "Vectorization succeeded but no serializable polygons were found."
        )

    selected_area_sqkm = (
        sum(
            float(feature.get("properties", {}).get("area_sqm", 0.0))
            for feature in selected_features
        )
        / 1_000_000
    )
    total_area_sqkm = float(
        ee.Number(vectors.aggregate_sum("area_sqm")).divide(1_000_000).getInfo()
    )

    print(
        "[INFO] Vectorization complete: "
        f"{polygon_count} polygon(s), exported={len(selected_features)}, "
        f"exported_area={selected_area_sqkm:.2f} km², total={total_area_sqkm:.2f} km²"
    )
    return selected_features, polygon_count, selected_area_sqkm, total_area_sqkm


def main() -> None:
    _initialize_earth_engine()

    base = _build_s1_base()

    print("[INFO] Selecting flood window with fallback...")
    flood_window, orbit_list = _select_flood_window(base)

    print(
        f"[INFO] Running per-orbit t-test change detection "
        f"(baseline: {BASELINE_WINDOW.label}, flood: {flood_window.label})..."
    )
    flood_t, baseline_scene_count, flood_scene_count, orbit_count = (
        _compute_flood_ttest(BASELINE_WINDOW, flood_window)
    )

    # --- Dynamic World urban masking ---
    # Mask analysis to built-up / urban areas, matching PWTT's approach.
    # Applied to the continuous t-stat image before thresholding, so that
    # urban-edge pixels aren't influenced by non-urban neighbors.
    print("[INFO] Applying Dynamic World urban mask...")
    urban_mask = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(BASELINE_WINDOW.start, BASELINE_WINDOW.end_exclusive)
        .select("built")
        .mean()
    )

    # Threshold on convolution-averaged t-statistic.
    # After multi-scale convolution, isolated flood signals are diluted by
    # averaging with non-flood neighbors, so we use the lower post-convolution
    # threshold.  The raw t-test significance (T_STAT_THRESHOLD = -3.0)
    # establishes the statistical standard; T_STAT_THRESHOLD_CONVOLVED
    # compensates for the spatial averaging step.
    flood_raw = (
        flood_t.lt(T_STAT_THRESHOLD_CONVOLVED)
        .updateMask(urban_mask.gt(DYNAMIC_WORLD_BUILT_THRESHOLD))
        .rename("flood_raw")
    )
    flood_clean = _cleanup_flood_mask(flood_raw)

    print("[INFO] Vectorizing flood mask...")
    flood_features, polygon_count, selected_area_sqkm, total_area_sqkm = (
        _vectorize_flood_mask(flood_clean)
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    confidence = (
        "HIGH" if baseline_scene_count >= 8 and flood_scene_count >= 3 else "MEDIUM"
    )

    provenance = {
        "project_id": PROJECT_ID or None,
        "source": DATASET_DISPLAY,
        "source_dataset": DATASET_DISPLAY,
        "source_dataset_detail": (
            f"Sentinel-1 GRD VV+VH all orbits ({INSTRUMENT_MODE})"
        ),
        "analysis_type": "flood_extent",
        "baseline_window": BASELINE_WINDOW.label,
        "event_window": flood_window.label,
        "baseline_period": BASELINE_WINDOW.label,
        "acquisition_period": flood_window.label,
        "method": "SAR_ttest_change_detection",
        "method_detail": (
            "Per-pixel Student's t-test on terrain-flattened (Vollrath 2020), "
            "Lee-filtered, log-transformed, dual-pol (VV+VH) Sentinel-1 "
            "imagery.  Multi-orbit aggregation across all available orbital "
            "tracks.  Multi-scale spatial convolution (50/100/150m).  Dynamic "
            "World urban masking. Implements the PWTT algorithm "
            "(Ballinger 2024, arXiv:2405.06323) adapted for flood detection."
        ),
        "preprocessing": {
            "speckle_filter": "Lee (MMSE)",
            "lee_kernel_size": LEE_KERNEL_SIZE,
            "lee_enl": LEE_ENL,
            "transform": "natural_log",
            "polarizations": POLARIZATIONS,
            "urban_mask": "GOOGLE/DYNAMICWORLD/V1",
            "urban_built_threshold": DYNAMIC_WORLD_BUILT_THRESHOLD,
            "focal_median_radius_m": FOCAL_MEDIAN_RADIUS_METERS,
            "multiscale_convolution_radii_m": CONVOLUTION_RADII_METERS,
            "terrain_flattening": {
                "enabled": True,
                "model": TERRAIN_FLATTENING_MODEL,
                "dem": SRTM_DEM_ID,
                "reference": "Vollrath et al. 2020",
                "masks": ["layover", "shadow"],
            },
        },
        "change_detection": {
            "method": "students_t_test",
            "signed": True,
            "threshold_t": T_STAT_THRESHOLD_CONVOLVED,
            "multi_orbit": True,
            "orbit_count": orbit_count,
            "orbits_used": orbit_list,
            "orbit_aggregation": "min (most negative = strongest flood)",
            "dual_pol_aggregation": "min (most negative = strongest flood)",
        },
        # Backward compatibility: runtime service reads threshold_db.
        "threshold_db": -3.0,
        "cleanup": {
            "focal_radius_m": DENOISE_RADIUS_METERS,
            "min_connected_pixels": MIN_CONNECTED_PIXELS,
            "min_polygon_area_sqm": MIN_POLYGON_AREA_SQM,
        },
        "baseline_scene_count": baseline_scene_count,
        "event_scene_count": flood_scene_count,
        "event_window_candidates": [window.label for window in FLOOD_WINDOWS],
        "selected_event_window": flood_window.label,
        "vector_polygon_count": polygon_count,
        "retained_polygon_count": len(flood_features),
        "estimated_area_sqkm": round(selected_area_sqkm, 2),
        "total_vector_area_sqkm": round(total_area_sqkm, 2),
        "confidence": confidence,
        "generated_at": generated_at,
        "output_geojson": OUTPUT_FILE.name,
        "attribution": "Implements PWTT — https://github.com/oballinger/PWTT",
    }

    output = {
        "type": "FeatureCollection",
        "properties": {
            "source": DATASET_DISPLAY,
            "source_dataset": DATASET_DISPLAY,
            "method": provenance["method"],
            "method_detail": provenance["method_detail"],
            "baseline_period": BASELINE_WINDOW.label,
            "flood_period": flood_window.label,
            "flood_area_sqkm": round(selected_area_sqkm, 2),
            "threshold_t": T_STAT_THRESHOLD_CONVOLVED,
            "threshold_db": -3.0,  # backward compat for runtime service
            "confidence": confidence,
            "computed_at": generated_at,
        },
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                feature["geometry"]["coordinates"]
                for feature in flood_features
                if feature.get("geometry", {}).get("type") == "Polygon"
            ],
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "source": DATASET_DISPLAY,
                    "method": provenance["method"],
                    "baseline": BASELINE_WINDOW.label,
                    "acquisition": flood_window.label,
                    "baseline_period": BASELINE_WINDOW.label,
                    "flood_period": flood_window.label,
                    "flood_area_sqkm": round(selected_area_sqkm, 2),
                    "threshold_t_raw": T_STAT_THRESHOLD,
                    "threshold_t_convolved": T_STAT_THRESHOLD_CONVOLVED,
                    "threshold_db": -3.0,  # backward compat
                    "confidence": confidence,
                    "computed_at": generated_at,
                    "polygon_index": idx + 1,
                    "polygon_area_sqkm": round(
                        float(feature.get("properties", {}).get("area_sqm", 0.0))
                        / 1_000_000,
                        4,
                    ),
                },
                "geometry": feature["geometry"],
            }
            for idx, feature in enumerate(flood_features)
        ],
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as geojson_file:
        json.dump(output, geojson_file, indent=2)
    with open(PROVENANCE_FILE, "w", encoding="utf-8") as provenance_file:
        json.dump(provenance, provenance_file, indent=2)

    print(f"[INFO] Flood extent exported to {OUTPUT_FILE}")
    print(f"[INFO] Analysis provenance exported to {PROVENANCE_FILE}")
    print(
        "[INFO] Final flood area "
        f"(exported polygons / all polygons): "
        f"{selected_area_sqkm:.2f} / {total_area_sqkm:.2f} km²"
    )


if __name__ == "__main__":
    try:
        main()
    except ee.EEException as exc:
        print(f"[ERROR] Earth Engine failure: {exc}")
        raise SystemExit(1) from exc
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc
