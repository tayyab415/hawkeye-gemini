# Earth Engine Runtime Contract Design
## Design Specification Document for HawkEye EE First-Class Integration

**TODO ID**: design-ee-runtime-contract  
**Status**: COMPLETED (AUDIT + DESIGN)  
**Date**: 2025  
**Scope**: Design/Specification only—no code modifications

---

## EXECUTIVE SUMMARY

This document consolidates a complete audit and design specification for making Earth Engine (EE) a first-class runtime component in HawkEye. **The audit document already exists** (`EE_RUNTIME_AUDIT.md`) and provides extensive detail on the current state and a concrete Phase-1 implementation plan.

### Key Findings:
1. **Current State**: HawkEye uses **static pre-computed flood geometry** + **hardcoded growth rates** + **static provenance metadata**
2. **Live Components**: Only BigQuery queries (infrastructure, historical floods) are live
3. **Gap**: No live EE computation, no raster tile delivery, no streaming analysis updates
4. **Proposed Contract**: Clean, phased approach with separate backend compute/polling and frontend rendering layers

---

## 1. CURRENT ARCHITECTURE (AS-IS)

### A. Backend Flow

```
┌─────────────────────────────────────┐
│  Agent Tool Call: get_flood_extent()│
└──────────────┬──────────────────────┘
               │
               ├─→ analyst.py → EarthEngineService
               │
               └─→ earth_engine_service.py:
                   • _load_geojson() [FILE: flood_extent.geojson] ✓ STATIC
                   • get_flood_area_sqkm() [computed from static geometry]
                   • get_flood_growth_rate() [HARDCODED: 12% per hour]
                   • get_population_at_risk() [HARDCODED: 15,000/km² jakarta]
                   • get_flood_extent_metadata() [FILE: analysis_provenance.json] ✓ STATIC
```

**Files Involved**:
- `app/services/earth_engine_service.py`: Static file loader + hardcoded metrics
- `app/hawkeye_agent/tools/analyst.py`: Tool definitions, calls EarthEngineService
- `app/main.py`: WebSocket emission of tool results via `ee_update` event (line 654-660, 984-988)

### B. WebSocket Emission Path

```
Tool result → main.py line 654
  └─→ if tool_name == "get_flood_extent":
      └─→ emit { type: "ee_update", area_sqkm, growth_rate_pct, metadata }
          └─→ websocket.send_text(json.dumps(...))
```

**Current ee_update Payload** (sparse):
```json
{
  "type": "ee_update",
  "area_sqkm": 1.56,
  "growth_rate_pct": 12.0,
  "metadata": { /* partial provenance */ }
}
```

### C. Frontend Receipt & Rendering

```
WebSocket Event (ee_update)
  ↓
useHawkEyeSocket.js → emit to events array
  ↓
App.jsx → useEffect listener on events
  ↓
applyEarthEngineUpdate() → setEarthEngineOverrides()
  ↓
EarthEnginePanel.jsx → renders metrics
DataLayerPanel.jsx → renders **STATIC IMPORTED GEOJSON** (NOT from API!)
```

**Key Issue**: Frontend imports static GeoJSON directly:
```javascript
import floodExtentRaw from '../../../../data/geojson/flood_extent.geojson?raw';
```
This bypasses the backend entirely.

### D. What is Live vs. Static

| Component | Status | Source |
|-----------|--------|--------|
| Flood extent geometry | **STATIC** | `flood_extent.geojson` file |
| Flood extent area (km²) | **STATIC** | Computed from static geometry |
| Growth rate (12% /hr) | **HARDCODED** | `earth_engine_service.py` line 102 |
| Population density | **HARDCODED** | 15,000/km² fixed for Jakarta |
| Infrastructure queries | ✓ **LIVE** | BigQuery in real time |
| Historical flood patterns | ✓ **LIVE** | BigQuery in real time |
| Timeline/temporal frames | **MOCK** | `demoSimulator.js` |
| Provenance metadata | **STATIC** | `analysis_provenance.json` sidecar |
| Raster tiles (SAR, NDWI, etc.) | **MISSING** | None |
| Confidence per pixel | **MISSING** | None |
| Scene acquisition details | **MISSING** | None |

---

## 2. INTEGRATION GAPS (DETAILED)

### Gap 1: No Live EE Computation Pipeline
- **Current**: Static GeoJSON loaded once at startup
- **Missing**: Real-time Earth Engine API calls with dynamic geometry/windows
- **Impact**: Cannot respond to changing baselines or user-specified regions

### Gap 2: No Raster/Tile Products
- **Current**: Only vector GeoJSON polygons
- **Missing**: SAR backscatter, NDWI water index, change detection heatmaps, confidence grids
- **Impact**: Cannot show pixel-level analysis or composite products

### Gap 3: No Temporal Frames / Timeline
- **Current**: Single static "before" and "after" geometry
- **Missing**: Time-series imagery, frame-by-frame progression, scene acquisition dates
- **Impact**: Cannot show event progression or validate scene quality

### Gap 4: Incomplete Provenance & Confidence Metadata
- **Current**: Single sidecar JSON with high-level metadata
- **Missing**: Per-scene acquisition info, per-pixel confidence, algorithm parameters, data freshness
- **Impact**: Cannot assess reliability of specific regions

### Gap 5: No Backend REST API for EE Tasks
- **Current**: Only tool results via WebSocket
- **Missing**: Async task submission, polling, result retrieval endpoints
- **Impact**: Cannot manage long-running analyses or recover from disconnects

### Gap 6: Frontend Tile Layer Management
- **Current**: DataLayerPanel can toggle static GeoJSON
- **Missing**: Opacity controls, layer composition, attribution per layer, dynamic addition/removal
- **Impact**: Cannot work with raster layers or composite visualizations

---

## 3. PROPOSED EVENT CONTRACT (NEW)

### A. Backend Endpoints (HTTP REST)

#### POST `/api/earth-engine/analyze`
**Purpose**: Submit new live flood analysis task to Earth Engine

**Request**:
```json
{
  "analysis_type": "flood_extent",
  "parameters": {
    "baseline_window": ["2025-06-01", "2025-09-30"],
    "event_window": ["2026-01-01", "2026-02-28"],
    "geometry": { "type": "Polygon", "coordinates": [...] },
    "dataset": "COPERNICUS/S1_GRD",
    "polarization": "VV",
    "threshold_db": -3.0,
    "min_area_sqm": 20000,
    "output_format": "geojson+tiles"
  },
  "webhook_url": "wss://..." (optional)
}
```

**Response (202 Accepted)**:
```json
{
  "task_id": "ee_task_20260315_abc123",
  "status": "queued",
  "estimated_time_seconds": 45,
  "polling_url": "/api/earth-engine/tasks/ee_task_20260315_abc123"
}
```

#### GET `/api/earth-engine/tasks/{task_id}`
**Purpose**: Poll status of an Earth Engine analysis task

**Response**:
```json
{
  "task_id": "ee_task_20260315_abc123",
  "status": "running",
  "progress_pct": 65,
  "phase": "change_detection",
  "message": "Applying threshold to change detection raster...",
  "result": null,
  "error": null,
  "started_at": "2026-03-15T18:15:00Z",
  "expires_at": "2026-03-15T19:15:00Z"
}
```

**Status Values**: `queued`, `running`, `completed`, `failed`

#### GET `/api/earth-engine/tasks/{task_id}/result`
**Purpose**: Retrieve final analysis result (only when `status='completed'`)

**Response (200 OK)**:
```json
{
  "task_id": "ee_task_20260315_abc123",
  "completed_at": "2026-03-15T18:17:01Z",
  "analysis_provenance": {
    "task_id": "ee_task_20260315_abc123",
    "source": "earth-engine",
    "source_dataset": "COPERNICUS/S1_GRD",
    "baseline_window": "2025-06-01/2025-09-30",
    "event_window": "2026-01-01/2026-02-28",
    "method": "SAR_change_detection",
    "threshold_db": -3.0,
    "baseline_scene_count": 6,
    "event_scene_count": 5,
    "scene_acquisitions": [
      {
        "date": "2025-06-04",
        "satellite": "Sentinel-1A",
        "orbit_number": 12345,
        "confidence": 0.95,
        "cloud_cover": 2.1,
        "comments": "Clear conditions"
      },
      {
        "date": "2026-02-15",
        "satellite": "Sentinel-1B",
        "confidence": 0.87,
        "cloud_cover": 5.3
      }
    ],
    "confidence": "MEDIUM",
    "generated_at": "2026-03-15T18:17:01Z",
    "pixel_confidence_distribution": {
      "high": 0.92,
      "medium": 0.065,
      "low": 0.015
    }
  },
  "geometry": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "properties": {
          "area_sqkm": 0.23,
          "confidence": 0.92,
          "pixel_count": 23000,
          "class": "flooded",
          "acquisition_date": "2026-02-15"
        },
        "geometry": { "type": "Polygon", "coordinates": [...] }
      }
    ]
  },
  "tiles": {
    "change_detection": {
      "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/change/{z}/{x}/{y}.png",
      "style": {
        "colormap": "RdYlBu_r",
        "vmin": -10,
        "vmax": 5
      },
      "title": "SAR Change (dB): Wet - Baseline"
    },
    "water_mask": {
      "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/water/{z}/{x}/{y}.png",
      "style": {
        "colormap": "Blues",
        "opacity": 0.6
      },
      "title": "Water Body Mask"
    },
    "confidence": {
      "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/confidence/{z}/{x}/{y}.png",
      "style": {
        "colormap": "RdYlGn",
        "vmin": 0,
        "vmax": 1
      },
      "title": "Pixel Confidence"
    }
  },
  "metrics": {
    "flood_area_sqkm": 1.56,
    "polygon_count": 15,
    "largest_polygon_sqkm": 0.23,
    "smallest_polygon_sqkm": 0.002,
    "population_at_risk": 23400,
    "growth_rate_estimate": {
      "rate_pct_per_hour": 8.2,
      "confidence": 0.65,
      "method": "time-series comparison",
      "note": "Estimated from two-hour interval imagery"
    }
  }
}
```

#### GET `/api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png`
**Purpose**: Serve XYZ raster tiles from Earth Engine analysis result

**Behavior**:
- Proxies to Earth Engine tile API or returns cached tiles
- Supports layers: `change`, `water`, `confidence`, `ndwi`, `vv`, `vh`
- Returns PNG with embedded color mapping

### B. WebSocket Events (NEW MESSAGE TYPES)

#### `ee_analysis_start`
**Sent by**: Frontend (when user clicks "Run Live Analysis")  
**Payload**:
```json
{
  "type": "ee_analysis_start",
  "parameters": {
    "baseline_window": ["2025-06-01", "2025-09-30"],
    "event_window": ["2026-01-01", "2026-02-28"],
    "geometry": { "type": "Polygon", "coordinates": [...] },
    "dataset": "COPERNICUS/S1_GRD"
  }
}
```

#### `ee_analysis_update`
**Sent by**: Backend (during streaming computation)  
**Payload**:
```json
{
  "type": "ee_analysis_update",
  "task_id": "ee_task_20260315_abc123",
  "phase": "baseline_composite",
  "progress_pct": 25,
  "message": "Building dry-season baseline composite from 6 scenes...",
  "timestamp": "2026-03-15T18:15:30Z"
}
```

**Phase Values**:
- `"queued"` – Waiting in queue
- `"baseline_composite"` – Creating baseline median composite
- `"event_composite"` – Creating event period composite
- `"change_detection"` – Computing change (dB difference)
- `"thresholding"` – Applying threshold to identify flood extent
- `"vectorization"` – Converting raster to polygons
- `"postprocessing"` – Final cleaning and metrics calculation
- `"generating_tiles"` – Creating XYZ tile pyramids

#### `ee_analysis_complete`
**Sent by**: Backend (when analysis finishes)  
**Payload**:
```json
{
  "type": "ee_analysis_complete",
  "task_id": "ee_task_20260315_abc123",
  "analysis_provenance": { /* full provenance object */ },
  "geometry": { /* GeoJSON FeatureCollection */ },
  "tiles": { /* layer_name → tile config */ },
  "metrics": { /* computed metrics */ },
  "map_actions": [
    {
      "type": "map_update",
      "action": "add_raster_layer",
      "layer_id": "ee_change_detection",
      "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/change/{z}/{x}/{y}.png",
      "title": "SAR Change Detection",
      "opacity": 0.7,
      "style": { "colormap": "RdYlBu_r" }
    },
    {
      "type": "map_update",
      "action": "add_geojson_layer",
      "layer_id": "ee_flood_extent",
      "geojson": { /* FeatureCollection */ },
      "title": "Flood Extent Polygons",
      "style": {
        "color": "#FF6B6B",
        "weight": 2,
        "opacity": 0.8,
        "fillOpacity": 0.3
      }
    }
  ]
}
```

#### `ee_update` (EXISTING, RETAINED FOR BACKWARD COMPAT)
**Status**: DEPRECATED but remains for static data  
**Usage**: Only emitted when static data is served
```json
{
  "type": "ee_update",
  "area_sqkm": 1.56,
  "growth_rate_pct": 12.0,
  "metadata": { /* provenance */ }
}
```

### C. Frontend Message Types (Updated constants)

**File**: `frontend/src/types/messages.js`

```javascript
export const SERVER_MESSAGE_TYPES = {
  // ... existing types ...
  
  // NEW EE events
  EE_ANALYSIS_START: 'ee_analysis_start',
  EE_ANALYSIS_UPDATE: 'ee_analysis_update',
  EE_ANALYSIS_COMPLETE: 'ee_analysis_complete',
  
  // DEPRECATED (retained for backward compat)
  EE_UPDATE: 'ee_update',
};

export const MAP_ACTIONS = {
  // ... existing actions ...
  
  // NEW raster layer actions
  ADD_RASTER_LAYER: 'add_raster_layer',
  UPDATE_RASTER_OPACITY: 'update_raster_opacity',
  REMOVE_RASTER_LAYER: 'remove_raster_layer',
  ADD_GEOJSON_LAYER: 'add_geojson_layer',
};
```

---

## 4. FILE-BY-FILE CHANGE PLAN

### TIER 1: Service & API Layer

#### **File 1**: `app/services/earth_engine_service.py`
**Current State**: Loads static GeoJSON, hardcoded metrics  
**Changes Required**:
- Keep existing methods (lines 1–132) for backward compatibility
- **ADD** async methods (lines 133+):
  - `async def submit_flood_analysis(baseline_window, event_window, geometry_geojson, parameters) → dict`
  - `async def get_analysis_status(task_id) → dict`
  - `async def get_analysis_result(task_id) → dict`
  - `def _generate_tile_urls(task_id, layers) → dict`
- **WHY**: Separates static fallback from live EE computation

**Responsibility**:
- Calls Google Earth Engine Python API (via `ee.Image`, `ee.Geometry`, etc.)
- Manages task submission to EE backends
- Polls task status from EE task management API
- Retrieves results and generates tile URLs

#### **File 2**: `app/main.py`
**Current State**: WebSocket handler + existing REST endpoints  
**Changes Required**:
- **ADD** Pydantic models (after imports, ~line 40):
  ```python
  class EEAnalysisRequest(BaseModel):
      analysis_type: str
      baseline_window: tuple[str, str]
      event_window: tuple[str, str]
      geometry: dict | None = None
      parameters: dict = Field(default_factory=dict)
  
  class EETaskStatus(BaseModel):
      task_id: str
      status: Literal["queued", "running", "completed", "failed"]
      progress_pct: int = 0
      phase: str | None = None
      error: str | None = None
  ```

- **ADD** 4 new HTTP endpoints (after line ~1173):
  - `POST /api/earth-engine/analyze`
  - `GET /api/earth-engine/tasks/{task_id}`
  - `GET /api/earth-engine/tasks/{task_id}/result`
  - `GET /api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png`

- **ADD** background polling task (after line ~1300):
  ```python
  async def _poll_ee_task(websocket: WebSocket, session_id: str, task_id: str):
      """Poll EE task and stream updates via WebSocket."""
      max_polls = 60
      for _ in range(max_polls):
          status = await _get_earth_engine().get_analysis_status(task_id)
          await websocket.send_text(json.dumps({
              "type": "ee_analysis_update",
              "task_id": task_id,
              "phase": status.get("phase"),
              "progress_pct": status.get("progress_pct", 0),
              "message": status.get("message")
          }))
          if status["status"] == "completed":
              result = await _get_earth_engine().get_analysis_result(task_id)
              await websocket.send_text(json.dumps({
                  "type": "ee_analysis_complete",
                  "task_id": task_id,
                  "analysis_provenance": result["analysis_provenance"],
                  "geometry": result["geometry"],
                  "tiles": result["tiles"],
                  "metrics": result["metrics"]
              }))
              for action in result.get("map_actions", []):
                  await websocket.send_text(json.dumps(action))
              break
          elif status["status"] == "failed":
              await websocket.send_text(json.dumps({
                  "type": "error",
                  "message": f"EE analysis failed: {status.get('error')}"
              }))
              break
          await asyncio.sleep(5)
  ```

- **MODIFY** WebSocket handler to recognize `ee_analysis_start` event:
  ```python
  if isinstance(data, dict) and data.get("type") == "ee_analysis_start":
      task_response = await _get_earth_engine().submit_flood_analysis(...)
      await websocket.send_text(json.dumps({
          "type": "ee_analysis_start",
          "task_id": task_response["task_id"]
      }))
      asyncio.create_task(_poll_ee_task(websocket, session_id, task_response["task_id"]))
  ```

**Responsibility**:
- HTTP endpoints for task submission and status/result retrieval
- WebSocket event handling for live analysis requests
- Background polling and event emission to clients
- Tile proxying/serving

### TIER 2: Event Emission & Tool Enhancement

#### **File 3**: `app/hawkeye_agent/tools/analyst.py`
**Current State**: Calls static EarthEngineService  
**Changes Required**:
- **UPDATE** `get_flood_extent()` function to support live mode:
  ```python
  def get_flood_extent(live_mode: bool = False, task_id: str | None = None, **analysis_params) -> dict:
      """
      Get current flood extent.
      
      Args:
          live_mode: If True, submit or poll live EE analysis
          task_id: Existing task ID to poll (if live_mode=True)
          **analysis_params: baseline_window, event_window, geometry, threshold_db
      
      Returns:
          If live and new: { task_id, status, polling_url, message }
          If live and polling: { task_id, status, progress, result? }
          If static: { geojson, area_sqkm, growth_rate, metadata }
      """
      if live_mode and task_id:
          # Poll existing task
          logger.info(f"[ANALYST] Polling EE task {task_id}")
          result = asyncio.run(_get_earth_engine().get_analysis_result(task_id))
          return {
              "task_id": task_id,
              "status": "completed",
              "geojson": result["geometry"],
              "area_sqkm": result["metrics"]["flood_area_sqkm"],
              "metadata": result["analysis_provenance"],
              "tiles": result["tiles"]
          }
      elif live_mode:
          # Submit NEW analysis
          logger.info("[ANALYST] Submitting new live EE analysis")
          response = asyncio.run(_get_earth_engine().submit_flood_analysis(
              baseline_window=analysis_params.get("baseline_window"),
              event_window=analysis_params.get("event_window"),
              geometry_geojson=analysis_params.get("geometry"),
              parameters={k: v for k, v in analysis_params.items()
                         if k not in ["baseline_window", "event_window", "geometry"]}
          ))
          return {
              "task_id": response["task_id"],
              "status": "submitted",
              "estimated_time_seconds": response.get("estimated_time_seconds", 60),
              "polling_url": response["polling_url"],
              "message": "Analysis submitted. Poll for results."
          }
      else:
          # Static fallback
          logger.info("[ANALYST] Using static flood extent")
          return {
              "geojson": _get_earth_engine().get_flood_extent_geojson(),
              "area_sqkm": _get_earth_engine().get_flood_area_sqkm(),
              "growth_rate_pct": _get_earth_engine().get_flood_growth_rate(),
              "metadata": _get_earth_engine().get_flood_extent_metadata()
          }
  ```

**Responsibility**:
- Dual-mode tool that supports both static and live analysis
- Graceful fallback when EE is unavailable

### TIER 3: Frontend Events & UI

#### **File 4**: `frontend/src/types/messages.js`
**Current State**: Basic message types  
**Changes Required**:
- **ADD** new message type constants (lines 28–30):
  ```javascript
  EE_ANALYSIS_START: 'ee_analysis_start',
  EE_ANALYSIS_UPDATE: 'ee_analysis_update',
  EE_ANALYSIS_COMPLETE: 'ee_analysis_complete',
  // Keep EE_UPDATE for backward compat
  EE_UPDATE: 'ee_update',
  ```

- **ADD** new MAP_ACTIONS (lines 94–106):
  ```javascript
  ADD_RASTER_LAYER: 'add_raster_layer',
  UPDATE_RASTER_OPACITY: 'update_raster_opacity',
  REMOVE_RASTER_LAYER: 'remove_raster_layer',
  ADD_GEOJSON_LAYER: 'add_geojson_layer',
  ```

**Responsibility**:
- Single source of truth for message types
- Enables type-safe handling in frontend

#### **File 5**: `frontend/src/App.jsx`
**Current State**: Basic event routing  
**Changes Required**:
- **ADD** new state (after line 171):
  ```javascript
  const [analysisInProgress, setAnalysisInProgress] = useState(false);
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const [analysisPhase, setAnalysisPhase] = useState(null);
  const [analysisTaskId, setAnalysisTaskId] = useState(null);
  ```

- **MODIFY** main event handler (useEffect for events, around line 619):
  ```javascript
  else if (event.type === 'ee_analysis_start') {
    console.log('[EE] Analysis started:', event.task_id);
    setAnalysisTaskId(event.task_id);
    setAnalysisInProgress(true);
    setAnalysisProgress(0);
  }
  else if (event.type === 'ee_analysis_update') {
    console.log('[EE] Progress:', event.progress_pct, event.phase);
    setAnalysisProgress(event.progress_pct);
    setAnalysisPhase(event.phase);
  }
  else if (event.type === 'ee_analysis_complete') {
    console.log('[EE] Analysis complete');
    setAnalysisInProgress(false);
    
    // Update EarthEngine panel state
    applyEarthEngineUpdate({
      metadata: event.analysis_provenance,
      area_sqkm: event.metrics?.flood_area_sqkm
    });
    
    // Display geometry layer
    handleMapUpdate({
      action: 'add_geojson_layer',
      layer_id: event.task_id,
      geojson: event.geometry,
      title: 'Live EE Analysis'
    });
    
    // Add raster layers
    for (const [name, config] of Object.entries(event.tiles || {})) {
      handleMapUpdate({
        action: 'add_raster_layer',
        layer_id: `ee_${name}`,
        url: config.url,
        title: config.title,
        opacity: 0.7,
        style: config.style
      });
    }
  }
  ```

- **ADD** helper to pass analysis state to EarthEnginePanel:
  ```javascript
  analysisInProgress={analysisInProgress}
  analysisProgress={analysisProgress}
  analysisPhase={analysisPhase}
  ```

**Responsibility**:
- Routes events to appropriate handlers
- Manages analysis progress state
- Coordinates map layer additions

#### **File 6**: `frontend/src/components/EarthEnginePanel.jsx`
**Current State**: Displays static provenance and metrics  
**Changes Required**:
- **ADD** props:
  ```javascript
  export default function EarthEnginePanel({
    data,
    analysisInProgress,
    analysisProgress,
    analysisPhase,
    onStartAnalysis
  })
  ```

- **ADD** "Run Live Analysis" button (after line 100):
  ```javascript
  <button
    className="ee-run-analysis-btn"
    onClick={onStartAnalysis}
    disabled={analysisInProgress}
  >
    {analysisInProgress ? `Analyzing... (${analysisPhase})` : 'Run Live Analysis'}
  </button>
  ```

- **ADD** progress display (after line 120):
  ```javascript
  {analysisInProgress && (
    <div className="ee-analysis-progress">
      <div className="phase-label">{analysisPhase}</div>
      <progress value={analysisProgress} max="100" />
      <span className="progress-text">{analysisProgress}%</span>
    </div>
  )}
  ```

**Responsibility**:
- Displays analysis progress UI
- Initiates live analysis requests
- Shows current analysis phase

#### **File 7**: `frontend/src/components/strategic/DataLayerPanel.jsx`
**Current State**: Toggles static GeoJSON layers  
**Changes Required**:
- **ADD** raster layer management methods (after line 200):
  ```javascript
  const handleAddRasterLayer = (layerId, tileUrl, title, style, opacity = 0.7) => {
    if (viewer && viewer.imageryLayers) {
      const tileProvider = new Cesium.UrlTemplateImageryProvider({ url: tileUrl });
      Cesium.ImageryLayer.fromProviderAsync(tileProvider, {
        alpha: opacity
      }).then(layer => {
        viewer.imageryLayers.add(layer);
        layerSourcesRef.current.set(layerId, {
          type: 'raster',
          cesiumLayer: layer,
          title: title
        });
      });
    }
  };
  
  const handleRemoveRasterLayer = (layerId) => {
    const layer = layerSourcesRef.current.get(layerId);
    if (layer && layer.type === 'raster' && viewer) {
      viewer.imageryLayers.remove(layer.cesiumLayer);
      layerSourcesRef.current.delete(layerId);
    }
  };
  
  const handleUpdateRasterOpacity = (layerId, opacity) => {
    const layer = layerSourcesRef.current.get(layerId);
    if (layer && layer.type === 'raster') {
      layer.cesiumLayer.alpha = opacity;
    }
  };
  ```

- **EXPAND** layer rendering to include raster layers in panel UI

**Responsibility**:
- Manages raster layer lifecycle (add, remove, opacity)
- Displays raster layers in UI panel
- Bridges map_update events to Cesium

#### **File 8**: `frontend/src/components/CesiumGlobe.jsx`
**Current State**: Handles vector geometries and entity placement  
**Changes Required**:
- **ADD** props:
  ```javascript
  export default function CesiumGlobe({
    mapCommands,
    onScreenshotCapture,
    ...props
  })
  ```

- **ADD** raster layer ref:
  ```javascript
  const rasterLayersRef = useRef(new Map());
  ```

- **ADD** command handler cases in switch statement (around line 400):
  ```javascript
  case 'add_raster_layer': {
    if (command.url && viewer) {
      const tileProvider = new Cesium.UrlTemplateImageryProvider({
        url: command.url
      });
      const imageryLayer = viewer.imageryLayers.addImageryProvider(tileProvider);
      if (command.opacity !== undefined) {
        imageryLayer.alpha = command.opacity;
      }
      rasterLayersRef.current.set(command.layer_id, imageryLayer);
      console.log(`[Globe] Added raster layer: ${command.layer_id}`);
    }
    break;
  }
  
  case 'remove_raster_layer': {
    const layer = rasterLayersRef.current.get(command.layer_id);
    if (layer && viewer) {
      viewer.imageryLayers.remove(layer);
      rasterLayersRef.current.delete(command.layer_id);
    }
    break;
  }
  
  case 'update_raster_opacity': {
    const layer = rasterLayersRef.current.get(command.layer_id);
    if (layer && command.opacity !== undefined) {
      layer.alpha = command.opacity;
    }
    break;
  }
  
  case 'add_geojson_layer': {
    if (command.geojson && viewer) {
      // Convert GeoJSON to Cesium entities
      const dataSource = Cesium.GeoJsonDataSource.load(command.geojson);
      dataSource.then(ds => {
        viewer.dataSources.add(ds);
        geojsonLayersRef.current.set(command.layer_id, ds);
      });
    }
    break;
  }
  ```

**Responsibility**:
- Renders raster tiles on Cesium globe
- Manages XYZ tile provider creation
- Handles opacity and layer visibility

---

## 5. BACKWARD COMPATIBILITY & RISK MITIGATION

### Risks

| Risk | Mitigation |
|------|-----------|
| EE API downtime | Fall back to static data; check `_get_earth_engine()` exists before use |
| Long-running tasks expire | Implement task result TTL cache; user can restart analysis |
| Network interruption mid-analysis | WebSocket reconnect logic resumes polling from current task_id |
| Auth credentials missing | Graceful degradation; static data used; warning logged |
| Tile generation takes > 5min | Increase poll timeout; implement exponential backoff |
| Old clients don't understand new messages | Keep `ee_update` event; new clients ignore it |

### Backward Compatibility

- **Static data** remains as fallback: If EE submission fails, tools return static GeoJSON + hardcoded metrics
- **Old message type** (`ee_update`) retained: Old frontend code continues to work
- **Existing REST endpoints** unchanged: No breaking changes to HTTP API
- **Existing WebSocket contract** extended: New message types are additive

---

## 6. IMPLEMENTATION PRIORITY & EFFORT

| Rank | File | Component | Effort | Dependencies | Critical |
|------|------|-----------|--------|--------------|----------|
| 1 | `earth_engine_service.py` | Async EE API methods | 6h | EE API keys | YES |
| 2 | `app/main.py` | REST endpoints + polling | 4h | Service methods | YES |
| 3 | `types/messages.js` | Message type constants | 1h | None | NO |
| 4 | `App.jsx` | Event routing | 2h | Message types | YES |
| 5 | `EarthEnginePanel.jsx` | Progress UI + button | 1.5h | App.jsx handlers | NO |
| 6 | `DataLayerPanel.jsx` | Raster layer mgmt | 2h | None | NO |
| 7 | `CesiumGlobe.jsx` | Raster rendering | 2h | Cesium docs | NO |
| 8 | `analyst.py` | Dual-mode tool | 1.5h | earth_engine_service.py | NO |
| 9 | Unit tests | Service layer | 3h | All changes | NO |
| 10 | Integration tests | End-to-end flow | 4h | All changes | NO |

**Total Phase-1 Effort**: ~27 hours (backend-focused)  
**Critical Path**: Files 1 → 2 → 4 (backend API + frontend event routing)

---

## 7. SUCCESS CRITERIA (POST-IMPLEMENTATION)

- [ ] User can click "Run Live Analysis" button in EarthEnginePanel
- [ ] Analysis task submitted to Earth Engine; task_id returned in `ee_analysis_start` event
- [ ] Frontend displays progress bar (0–100%) with phase updates every 5 seconds
- [ ] Upon completion, WebSocket emits `ee_analysis_complete` event
- [ ] GeoJSON flood extent vectors automatically rendered on Cesium globe
- [ ] Raster tiles (change detection, confidence, water mask) displayed as imagery layers
- [ ] All three tile layers can be toggled on/off independently via DataLayerPanel
- [ ] Provenance metadata displays "LIVE" status + completion timestamp
- [ ] Static data serves as fallback when EE service unavailable (no crashes)
- [ ] WebSocket reconnect resumes polling from current task_id (no data loss)
- [ ] Old clients (sending text messages) continue to work without code changes

---

## 8. DECISION POINTS FOR IMPLEMENTATION

1. **Async Pattern**: Use `asyncio` for non-blocking task polling (recommended over threads)
2. **Tile Caching**: Hybrid approach—proxy live from EE API with local cache fallback
3. **Task Expiration**: Store results with 1-hour TTL; GC after expiry
4. **Error Recovery**: On EE error, emit `error` event + trigger static data fallback
5. **Auth**: Ensure `GOOGLE_APPLICATION_CREDENTIALS` loaded before service init
6. **Rate Limiting**: Implement poll backoff (1s, 2s, 4s, 8s, 16s) to avoid API throttling
7. **Test Data**: Use Earth Engine sample datasets for CI/CD (no real credentials needed)

---

## 9. EXACT TOUCHPOINTS SUMMARY

### Backend Touchpoints

| Layer | File | Change Type | Lines | Purpose |
|-------|------|------------|-------|---------|
| Service | `earth_engine_service.py` | Add methods | 133+ | Live EE API calls |
| API | `app/main.py` | Add endpoints | 40+, 1173+, 1300+ | Task submission, status, polling |
| Tools | `analyst.py` | Update function | 357–384 | Dual-mode (static/live) |

### Frontend Touchpoints

| Layer | File | Change Type | Lines | Purpose |
|-------|------|------------|-------|---------|
| Types | `types/messages.js` | Add constants | 28–30, 94–106 | Event type definitions |
| App | `App.jsx` | Add event handlers | 169–171, 619–700 | Progress state, event routing |
| Panel | `EarthEnginePanel.jsx` | Add UI | 100, 120 | Run button, progress bar |
| Panel | `DataLayerPanel.jsx` | Add methods | 200–250 | Raster layer management |
| Globe | `CesiumGlobe.jsx` | Add cases | 400–450 | Tile rendering |

---

## 10. DELIVERABLES CHECKLIST

✅ **Complete current state audit** (what is live vs. sidecar/static)  
✅ **Proposed event contract** (message names + payload schemas)  
✅ **Exact files to change** (file-by-file plan with line numbers)  
✅ **Concrete implementation ordering** (TIER 1 → TIER 2 → TIER 3 → TIER 4)  
✅ **Backward compatibility notes** (no breaking changes)  
✅ **Risk mitigation strategy** (fallback mechanisms)  
✅ **Success criteria** (testable outcomes)  

---

## CONCLUSION

This design document consolidates the **audit + specification** for making Earth Engine a first-class runtime component in HawkEye. The proposed contract achieves:

1. **Clean separation**: Backend handles computation polling; frontend handles rendering
2. **Streaming feedback**: Progress updates + phase labels during live analysis
3. **Rich outputs**: GeoJSON vectors + raster tiles with per-pixel confidence
4. **Full provenance**: Scene-level acquisition details, per-polygon confidence
5. **Graceful degradation**: Static fallback if EE unavailable
6. **Backward compatible**: Old code continues to work; new features are additive

**Estimated Phase-1 delivery**: 4–5 weeks (assuming concurrent frontend/backend work).

---

**Document Status**: COMPLETE (DESIGN/AUDIT ONLY)  
**Next Step**: Code implementation following TIER priority order.
