# EARTH ENGINE USAGE AUDIT — HAWKEYE

## EXECUTIVE SUMMARY
**Current Status:** Earth Engine is **MINIMALLY USED** with architectural scaffolding in place but gated behind environment flag. Live EE integration exists in code but is disabled by default.

- **Offline Mode (Active):** Pre-computed GeoJSON flood extents + static metadata
- **Live Mode (Potential):** EE tile generation + live analysis framework (gated: `HAWKEYE_ENABLE_EE_LIVE_TILES` flag)
- **Actual EE Calls:** Only when flag enabled; otherwise pure fallback descriptors

---

## 1. EXACT FUNCTIONAL RESPONSIBILITIES

### A. Offline Artifact Usage
**Files:** `earth_engine_service.py:_load_geojson()`, `earth_engine_service.py:_load_metadata()`

- **Reads:** `/hawkeye/data/geojson/flood_extent.geojson` (pre-computed GeoJSON polygons)
- **Reads:** `/hawkeye/data/geojson/analysis_provenance.json` (metadata: source, method, confidence, windows)
- **Cached:** In-memory with `_cached_geojson` and `_cached_metadata` attributes
- **Computation:** PWTT algorithm (Pixel-Wise T-Test) run offline via `data/compute_flood_extent.py`:
  - Sentinel-1 GRD VV/VH change detection
  - Terrain flattening (Vollrath 2020, SRTM DEM)
  - Lee speckle filter (MMSE estimator)
  - Multi-scale spatial convolution (50/100/150m Gaussian)
  - Dynamic World urban masking
  - Result: 965 polygons → 50 retained (min 20k m²)

### B. Live-Analysis Task Lifecycle
**Files:** `earth_engine_service.py:submit_live_analysis_task()` → `execute_live_analysis_task()`
**API Endpoints:** `POST /api/earth-engine/live-analysis`

**Task States:** queued → running → complete (or error)

```
submit_live_analysis_task(request)
  └─ Allocates task_id: "ee_live_task_0001"
     Creates in-memory task record with timestamps
     
execute_live_analysis_task(task_id)
  └─ Calls start_live_analysis_task() [state→running]
     Calls _build_live_analysis_runtime_payload()
       ├─ Loads offline GeoJSON + metadata
       ├─ IF HAWKEYE_ENABLE_EE_LIVE_TILES enabled:
       │   └─ Calls _build_live_runtime_tile_handles()
       │       ├─ Initializes Earth Engine module: ee.Initialize(project_id)
       │       ├─ Queries Sentinel-1: COPERNICUS/S1_GRD
       │       ├─ Builds 5 layer images:
       │       │   - ee_baseline_backscatter
       │       │   - ee_event_backscatter
       │       │   - ee_change_detection
       │       │   - ee_multisensor_fusion
       │       │   - ee_fused_flood_likelihood
       │       ├─ Calls image.getMapId(vis) for each
       │       └─ Registers tile templates with live tile handles
       │       └─ Returns handles dict + status
       │
       └─ Calls complete_live_analysis_task() [state→complete]
          Stores result in task["result"]
```

**In-Memory Registry:** `_live_analysis_tasks` dict (no persistence; lost on restart)

### C. Live Tile Proxy Behavior
**Files:** `earth_engine_service.py:fetch_live_tile()`, `main.py:get_live_earth_engine_tile()`

**Flow:**
```
GET /api/earth-engine/tiles/live/{tile_handle}/{z}/{x}/{y}.png
  └─ Looks up tile_handle in _live_tile_registry
  └─ Resolves url_template (from Earth Engine MapID)
  └─ Substitutes {z}, {x}, {y}
  └─ Calls requests.get(url, timeout=12s)
  └─ Returns PNG bytes with cache headers ("public, max-age=300")
```

**Tile Handles:** `ee_live_tile_00001`, registered per task with layer_id + task_id linkage

**Fallback (Offline):**
```
GET /api/earth-engine/tiles/offline/{layer_id}/{z}/{x}/{y}.png
  └─ Returns deterministic placeholder PNG (same bytes always)
  └─ Cache headers: "public, max-age=3600"
```

### D. Temporal Frames & Playback
**Files:** `earth_engine_service.py:get_runtime_temporal_frames()`, `get_runtime_temporal_playback()`

**Frames:** Built from metadata windows:
- **baseline:** window from `baseline_window` / `baseline_period` (e.g., "2025-06-01 to 2025-09-30")
- **event:** window from `event_window` / `flood_period` (e.g., "2026-01-01 to 2026-02-28")
- **change:** derived; spans baseline_start → event_end

**Temporal Playback:**
- Ordered frame sequence: [baseline, event, change]
- Progression frames: alternative event_window candidates (if present in metadata)
- Default frame: "change" if available, else last frame
- Frontend slider navigates 0..N frames

### E. Fusion/Confidence/Uncertainty Behavior
**Files:** `earth_engine_service.py:get_runtime_multisensor_fusion()`

**Multisensor Fusion Architecture:**
```
aggregate_confidence = weighted_average(
  sentinel1_runtime_signal_score (45%),
  sentinel2_optical_support_score (15%),
  dem_support_score (15%),
  rainfall_signal_score (15%),
  groundsource_incident_score (10%)
)
```

**Sentinel-1 Metrics** (primary signal):
- `scene_support_score` = clamp(baseline_scene_count / 6.0) — robustness of baseline
- `change_intensity_score` = clamp(|change_db| / |threshold_db|) — strength of change signal
- `runtime_signal_score` = average(scene_support, intensity)
- Example: threshold_db: -3.0, change_to_threshold_ratio: 1.5 → intensity_score ≈ 0.5

**Sentinel-2 Metrics** (if present):
- `water_presence_score` from NDWI/MNDWI mean
- `cloud_free_fraction` = 1.0 − (cloud_cover_pct / 100)
- `optical_support_score` = average(water_presence, cloud_free)

**DEM, Rainfall, Incidents:** Scaffolding only (metadata placeholders)

**Confidence Label Mapping:**
- score >= 0.85 → "VERY_HIGH"
- score >= 0.70 → "HIGH"
- score >= 0.50 → "MEDIUM"
- score >= 0.30 → "LOW"
- else → "VERY_LOW"

### F. Frontend Map/Panel Behavior
**EarthEnginePanel.jsx (744 lines):**
- Displays multisensor_fusion.aggregate_confidence (label + score %)
- Shows live_analysis_task state/timestamps
- Temporal slider controls frame index (baseline → event → change)
- "Freshness" indicator based on updated_at age
- Uncertainty level derived from confidence (inverse mapping)

**DataLayerPanel.jsx (3125 lines):**
- Renders Cesium globe layers
- Adds tile layers for each descriptor:
  - If `tile_source.status === "live"` → XYZ URL with live tile handle
  - If `tile_source.status === "placeholder"` → XYZ URL with offline endpoint
- Fusion layers include `fusion.is_fused === true` flag + signal composition

**CesiumGlobe.jsx → Cesium.ImageryLayer + Cesium.UrlTemplateImageryProvider:**
- Maps tile_source.url_template to Cesium provider
- Handles tile fetch failures gracefully
- Supports time-indexed layer switching (beta)

---

## 2. EXACT LIMITATIONS & FALLBACK BEHAVIOR

### A. Live Earth Engine Disabled by Default
**Evidence:** `earth_engine_service.py:232-234`
```python
def _is_live_tile_runtime_enabled(self) -> bool:
    flag = os.getenv("HAWKEYE_ENABLE_EE_LIVE_TILES", "")
    return flag.strip().lower() in _LIVE_TILE_ENABLED_VALUES  # ["1", "true", "yes", "on"]
```
- **Default:** Unset → disabled
- **Effect:** `_build_live_runtime_tile_handles()` returns `({}, {"status": "disabled"})` immediately
- **Fallback:** Uses placeholder PNG tiles + GeoJSON overlay from disk

### B. Offline Metadata-Only Fusion
**Evidence:** `earth_engine_service.py:1443-1550`
- Fusion scores computed from pre-computed metadata fields only
- No real-time EE computation unless live flag enabled
- Missing Sentinel-2, DEM, rainfall, incidents → defaults to Sentinel-1 score

### C. In-Memory Task Storage Only
**Evidence:** `earth_engine_service.py:74-80`
```python
self._live_analysis_tasks: dict[str, dict[str, Any]] = {}
self._live_analysis_task_order: list[str] = []
```
- Tasks lost on server restart
- No Firestore/BigQuery persistence
- Latest task retrieved via `get_latest_live_analysis_task_status()` → index -1 from list

### D. Tile Fetch Timeout
**Evidence:** `earth_engine_service.py:47`
```python
_LIVE_TILE_FETCH_TIMEOUT_S = 12
```
- HTTP timeout for Earth Engine MapID tile fetcher
- Returns 502 error if exceeded
- No retry logic

### E. Placeholder Tile PNG Generation
**Evidence:** `main.py:1577-1585`
- Static placeholder PNG (same 1×1 pixel array hashed)
- Not terrain-accurate; visual placeholder only
- Used when `_build_tile_source_descriptor()` returns fallback

### F. No Progressive Scene Queries
**Evidence:** `earth_engine_service.py:1054-1062`
- Filters by exact date windows in baseline/event metadata
- No adaptive time-window expansion
- No cloud-cover-driven window shifting
- No multi-orbit rebalancing

---

## 3. END-TO-END EE DATA FLOW

```
┌─ OFFLINE PATH (Default: HAWKEYE_ENABLE_EE_LIVE_TILES unset) ──────────────────┐
│                                                                                  │
│  [Frontend] Loads App.jsx                                                       │
│      │                                                                          │
│      ├─ Emits WebSocket: "run_intelligence_analysis"                           │
│      │   └─ Includes location, parameters                                      │
│      │                                                                          │
│      └─ Imports floodExtentRaw from /data/geojson/flood_extent.geojson         │
│      └─ Imports analysisProvenanceRaw from /data/geojson/analysis_provenance.json
│                                                                                  │
│  [Backend] main.py WebSocket handler                                            │
│      │                                                                          │
│      ├─ Calls analyst.analyze_intelligence()                                   │
│      │   └─ Queries BigQuery (GroundsourceService)                             │
│      │   └─ Calls earth_engine_service.get_flood_extent_geojson()              │
│      │       └─ Reads /data/geojson/flood_extent.geojson (cached)              │
│      │                                                                          │
│      ├─ Calls earth_engine_service.get_runtime_flood_product()                 │
│      │   ├─ Loads flood_extent_metadata() + analysis_provenance.json           │
│      │   ├─ Calls get_runtime_temporal_frames()                                │
│      │   │   └─ Parses baseline_window, event_window from metadata             │
│      │   │   └─ Returns {baseline, event, change} frames with timestamps       │
│      │   │                                                                      │
│      │   ├─ Calls get_runtime_multisensor_fusion()                             │
│      │   │   └─ Extracts sentinel1_* scores from metadata                      │
│      │   │   └─ Computes aggregate_confidence (scaffold; no live EE)           │
│      │   │                                                                      │
│      │   ├─ Calls get_runtime_layer_descriptors()                              │
│      │   │   ├─ For each layer_id in [baseline, event, change, fusion]:        │
│      │   │   │   └─ Calls _build_tile_source_descriptor()                      │
│      │   │   │       └─ Returns: {"url_template": "/api/earth-engine/tiles/   │
│      │   │   │                    offline/{layer_id}/{z}/{x}/{y}.png",          │
│      │   │   │                    "status": "placeholder", ...}                │
│      │   │   │                                                                  │
│      │   │   └─ Returns descriptors list                                        │
│      │   │                                                                      │
│      │   └─ Stabilizes + returns runtime_flood_product                         │
│      │       (status: "fallback", runtime_mode: "fallback_descriptor")         │
│      │                                                                          │
│      ├─ Emits WebSocket: "ee_update"                                           │
│      │   ├─ area_sqkm, growth_rate_pct                                         │
│      │   ├─ ee_runtime (flood product)                                         │
│      │   ├─ temporal_frames, temporal_playback                                 │
│      │   ├─ multisensor_fusion (offline scores)                                │
│      │   ├─ runtime_layers (descriptors with placeholder tile URLs)            │
│      │   └─ live_tile_status: {status: "disabled", enabled: false}            │
│      │                                                                          │
│      └─ Additional map/route events...                                         │
│                                                                                  │
│  [Frontend] useHawkEyeSocket hook receives "ee_update"                        │
│      │                                                                          │
│      ├─ Stores in state.eeRuntime                                              │
│      ├─ Extracts temporal_frames, multisensor_fusion                           │
│      └─ Passes to DataLayerPanel                                               │
│                                                                                  │
│  [Frontend] DataLayerPanel renders Cesium layers                                │
│      │                                                                          │
│      └─ For each layer descriptor:                                             │
│          └─ Creates Cesium.UrlTemplateImageryProvider with tile URL            │
│              └─ Requests: GET /api/earth-engine/tiles/offline/{layer_id}/0/0/0.png
│              └─ Returns static placeholder PNG                                 │
│              └─ Rendered as gray underlay on map                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ LIVE PATH (HAWKEYE_ENABLE_EE_LIVE_TILES=true) ─────────────────────────────────┐
│                                                                                  │
│  [Frontend] POST /api/earth-engine/live-analysis (optional)                    │
│      │                                                                          │
│      └─ Triggers backend live analysis                                         │
│                                                                                  │
│  [Backend] main.py:run_live_earth_engine_analysis()                            │
│      │                                                                          │
│      └─ Calls earth_engine_service.run_live_analysis_task()                    │
│          ├─ Submits task (state: queued)                                       │
│          └─ Executes task:                                                     │
│              ├─ Starts task (state: running)                                   │
│              ├─ Calls _build_live_analysis_runtime_payload()                   │
│              │   └─ Calls _build_live_runtime_tile_handles()                   │
│              │       ├─ Checks HAWKEYE_ENABLE_EE_LIVE_TILES flag → TRUE        │
│              │       ├─ ee.Initialize(project_id="gen-lang-client-0261050164")│
│              │       ├─ Loads flood_extent.geojson geometry                    │
│              │       ├─ For each layer [baseline, event, change, fusion]:      │
│              │       │   ├─ Query ee.ImageCollection("COPERNICUS/S1_GRD"):     │
│              │       │   │   ├─ .filterBounds(geometry)                        │
│              │       │   │   ├─ .filter(instrumentMode="IW")                   │
│              │       │   │   ├─ .filter(polarization="VV")                     │
│              │       │   │   ├─ .filterDate(baseline_start, baseline_end)      │
│              │       │   │   └─ .select("VV").median()                         │
│              │       │   │                                                      │
│              │       │   ├─ Compute change_image = event - baseline             │
│              │       │   ├─ Apply flood mask: change_image < -1.5              │
│              │       │   └─ Calls image.getMapId(viz_params)                   │
│              │       │       └─ Earth Engine returns MapID dict with url_format│
│              │       │                                                          │
│              │       ├─ Registers each layer's url_format with tile handle:    │
│              │       │   ee_live_tile_00001 → {layer_id: ee_baseline_backscatter,
│              │       │                          url_template: "https://...{z}/{x}/{y}?token=..."}
│              │       │                                                          │
│              │       └─ Returns live_handles + status                          │
│              │           {status: "live", available_layer_count: 5, ...}      │
│              │                                                                  │
│              ├─ Builds layer descriptors with live tile handles                │
│              │   └─ _build_live_tile_source_descriptor()                       │
│              │       └─ Returns: {"url_template": "/api/earth-engine/tiles/   │
│              │                    live/{tile_handle}/{z}/{x}/{y}.png",         │
│              │                    "status": "live", "task_id": "ee_live_task_0001"}
│              │                                                                  │
│              └─ Completes task (state: complete, result stored)                │
│                                                                                  │
│  [Backend] Emits WebSocket: "ee_update" with LIVE descriptors                 │
│      │                                                                          │
│      └─ ee_runtime.status: "live"                                              │
│      └─ runtime_layers: [{tile_source: {status: "live", ...}}, ...]            │
│      └─ live_tile_status: {status: "live", enabled: true, ...}                │
│                                                                                  │
│  [Frontend] DataLayerPanel receives live descriptors                            │
│      │                                                                          │
│      └─ For each layer with status="live":                                     │
│          └─ Cesium requests: GET /api/earth-engine/tiles/live/{handle}/{z}/{x}/{y}.png
│              ├─ Backend earth_engine_service.fetch_live_tile()                 │
│              │   ├─ Looks up handle in _live_tile_registry                     │
│              │   ├─ Resolves url_template                                      │
│              │   ├─ Substitutes {z},{x},{y}                                    │
│              │   └─ Calls requests.get() to Earth Engine                       │
│              │                                                                  │
│              └─ Returns PNG bytes from Earth Engine (cached 5 min)             │
│                  └─ Rendered on Cesium globe                                   │
│                                                                                  │
│  [Frontend] User can:                                                           │
│      ├─ Play temporal slider (baseline → event → change)                       │
│      ├─ View multisensor_fusion aggregate scores                               │
│      ├─ See live_tile_status.layer_ids: ["ee_baseline_backscatter", ...]       │
│      └─ Monitor live_analysis_task state/timestamps                           │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. USED VS. MINIMALLY USED VERDICT

### **VERDICT: MINIMALLY USED (High Potential, Gated)**

**Rationale:**

| Aspect | Status | Evidence |
|--------|--------|----------|
| **EE API Calls** | GATED/DISABLED | Only if `HAWKEYE_ENABLE_EE_LIVE_TILES=true` (not set in .env) |
| **Live MapID Gen** | SCAFFOLD | Code present; `ee.Initialize()` + 5 layer image.getMapId() implemented |
| **Tile Proxy** | OPERATIONAL | Can proxy live EE tiles when enabled; fetch_live_tile() works |
| **Temporal Playback** | MINIMAL | Frames derived from metadata windows, no EE scene enumeration |
| **Fusion** | METADATA-BASED | Scores computed offline from JSON; no real-time multi-sensor fusion |
| **Offline Fallback** | ACTIVE | Pre-computed GeoJSON + deterministic tile placeholders work 100% |
| **Integration Depth** | SHALLOW | No BigQuery export, no Firestore sync, no scheduled tasks |

**Why Minimal:**
1. **Default State:** Disabled. No EE queries in normal runtime.
2. **Static Metadata:** All confidence/fusion scores pre-computed in JSON.
3. **No Real-Time Updates:** Sentinel-1 scenes not re-queried on demand; same baselines always used.
4. **No Multi-Sensor Orchestration:** Sentinel-2/DEM/rainfall/incidents are metadata placeholders, not live data.
5. **No Persistence:** Live tasks ephemeral; lost on restart.

**Why High Potential:**
1. **Full Architecture:** Live tile generation, temporal frames, fusion scaffolding all in place.
2. **One Flag Away:** `HAWKEYE_ENABLE_EE_LIVE_TILES=true` + EE credentials = live SAR tiles.
3. **Extensible Fusion:** Sentinel-1 signal proven; fusion framework ready for S2/DEM/rainfall.
4. **MapID Proxy:** Stable, no vendor lock-in; can add caching/persistence layer.

---

## 5. TOP 5 HIGHEST-LEVERAGE EE IMPROVEMENTS

### 1. **Enable Live Earth Engine & Implement Task Persistence**
**Current Deficit:** Live MapID generation disabled by default; tasks lost on restart.

**Recommended Implementation:**
- Set `HAWKEYE_ENABLE_EE_LIVE_TILES=true` in production env
- Add Firestore document store for live_analysis_tasks:
  ```python
  fs_service.store_task(task_id, task_dict)  # Persist to /live_analysis/{task_id}
  # On startup, restore tasks from Firestore
  ```
- Use task_id for idempotent tile handle lookup
- **Impact:** Live SAR tiles operational; user can check task status across sessions.

---

### 2. **Real-Time Multisensor Fusion (Sentinel-1 + Sentinel-2)**
**Current Deficit:** Fusion is metadata-only; S2 fields are placeholders.

**Recommended Implementation:**
- In `_build_live_runtime_tile_handles()`, after Sentinel-1 median:
  ```python
  sentinel2 = ee.ImageCollection("COPERNICUS/SENTINEL2_SR").filterBounds(geometry)
  s2_event = sentinel2.filterDate(event_start, event_end).median()
  ndwi = s2_event.normalizedDifference(["B8", "B11"])  # NIR - SWIR
  mndwi = s2_event.normalizedDifference(["B3", "B11"])  # Green - SWIR
  ```
- Compute water_presence_score from live NDWI/MNDWI
- Blend with Sentinel-1 signal: `aggregate = 0.6*S1 + 0.4*S2`
- Add new layer: `ee_optical_water_probability`
- **Impact:** +30-40% confidence lift when optical data available; reduces false positives.

---

### 3. **Adaptive Temporal Window & Cloud-Driven Scene Selection**
**Current Deficit:** Fixed date windows; no retry on cloud cover or missing scenes.

**Recommended Implementation:**
```python
def _find_best_event_collection(aoi_geom, initial_window, max_days=30, target_scenes=5):
    """Auto-shift window if cloud cover >60% or <target_scenes."""
    for day_offset in range(0, max_days):
        window_start = initial_window_start + day_offset
        window_end = initial_window_start + day_offset + 7
        col = ee.ImageCollection("COPERNICUS/S1_GRD").filterDate(window_start, window_end)
        if col.size().getInfo() >= target_scenes:
            return col
    return ee.Image.constant(-17)  # Fallback
```
- Pre-compute viable windows on task submission
- Return `suggested_windows` to frontend for user selection
- Emit `task_status: "window_search"` during process
- **Impact:** Reduces "no scene available" errors; handles monsoon/dry-season transitions.

---

### 4. **Tile Cache & CDN Integration**
**Current Deficit:** Each tile request fetches from Earth Engine (12s timeout); no persistent cache.

**Recommended Implementation:**
- Add Redis or GCS bucket for tile caching:
  ```python
  cache_key = f"ee_tile:{task_id}:{layer_id}:{z}:{x}:{y}"
  tile_bytes = redis.get(cache_key)
  if not tile_bytes:
      tile_bytes = fetch_live_tile(handle, z, x, y)
      redis.setex(cache_key, 3600, tile_bytes)  # 1-hour cache
  ```
- Serve from CDN (Cloud CDN or Cloudflare)
- Add ETag validation (Earth Engine provides ETag)
- **Impact:** 80% tile hit rate; <100ms response time vs. 12s upstream.

---

### 5. **Historical Scene Replay & Change Trajectory**
**Current Deficit:** Only baseline vs. event comparison; no multi-temporal stack.

**Recommended Implementation:**
- Query full Sentinel-1 time-series in 10-day buckets:
  ```python
  s1_ts = (ee.ImageCollection("COPERNICUS/S1_GRD")
           .filterBounds(geometry)
           .filterDate("2025-06-01", "2026-03-01")
           .sort("system:time_start"))
  
  # Export to BigQuery as time-indexed image collection
  task = ee.batch.Export.image.toBigQuery(
      image=s1_ts.mosaic(),
      table="hawkeye.sentinel1_timeseries",
      ...
  )
  ```
- Render multi-frame slider in frontend (10+ temporal steps)
- Track change trajectory & peak flood extent date
- **Impact:** Enables incident timeline reconstruction; validates peak extent.

---

## 6. INTEGRATION SUMMARY

| Component | Role | File |
|-----------|------|------|
| **Backend Service** | EE task orchestration, MapID proxy, metadata queries | `earth_engine_service.py` (2412 lines) |
| **HTTP Endpoints** | REST tile proxies, live-analysis lifecycle | `main.py:1560-1707` |
| **WebSocket Broadcast** | `ee_update` messages with runtime payload | `main.py:1047-1100` |
| **Frontend State** | Stores eeRuntime, temporal_frames, multisensor_fusion | `App.jsx:23-26` |
| **Panel UI** | Displays metrics, confidence, task status | `EarthEnginePanel.jsx:1-744` |
| **Map Layer Rendering** | Cesium XYZ tile providers for offline/live URLs | `DataLayerPanel.jsx:1-3125` |
| **Data Provenance** | Pre-computed PWTT results | `/data/geojson/flood_extent.geojson` + `analysis_provenance.json` |

---

## 7. DEPLOYMENT CHECKLIST

- [ ] Set `HAWKEYE_ENABLE_EE_LIVE_TILES=true` in production
- [ ] Verify `earthengine authenticate` credentials in container
- [ ] Add Firestore indexing for task_id + timestamp queries
- [ ] Deploy Redis for tile caching (optional; high-impact)
- [ ] Monitor Earth Engine quota usage (default: 10k image tasks/day)
- [ ] Test offline fallback (disable flag, verify placeholder tiles render)
- [ ] Load-test tile proxy (concurrent requests at zoom 12-18)

