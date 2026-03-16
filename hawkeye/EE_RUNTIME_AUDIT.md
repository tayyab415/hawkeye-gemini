# HawkEye Earth Engine Runtime Integration Audit

## EXECUTIVE SUMMARY

HawkEye's Earth Engine integration is currently **hybrid: real BigQuery infrastructure queries + static pre-computed flood geometry**. There is **NO live Earth Engine computation pipeline**. The flood extent, growth rate, and population estimates are hardcoded or loaded from static files. This audit proposes a concrete "Earth Engine first-class runtime" contract with specific payload shapes and file-level implementation changes for Phase 1.

---

## 1. CURRENT END-TO-END DATA FLOW MAP

### A. Offline Compute → Static Data
- **compute_flood_extent.py**: One-time script produces static GeoJSON
  - Reads Sentinel-1 SAR from COPERNICUS/S1_GRD (Earth Engine)
  - Outputs: `/data/geojson/flood_extent.geojson` + `/data/geojson/analysis_provenance.json`
  - **Status: STATIC/PRECOMPUTED** — No live re-execution

### B. Backend Service Layer
1. **EarthEngineService** (`app/services/earth_engine_service.py`)
   - `get_flood_extent_geojson()` → Loads static file
   - `get_flood_area_sqkm()` → Computes from static geometry
   - **`get_flood_growth_rate()` → HARDCODED** `{"rate_pct_per_hour": 12.0, "source": "pre-computed"}`
   - **`get_population_at_risk()` → HARDCODED** Jakarta density: 15,000/km²
   - `get_flood_extent_metadata()` → Loads from analysis_provenance.json

2. **Analyst Tools** (`app/hawkeye_agent/tools/analyst.py`)
   - `get_flood_extent()` → Calls EarthEngineService (static)
   - `get_infrastructure_at_risk()` → **REAL BigQuery query** ✓
   - `get_population_at_risk()` → Calls EarthEngineService (static)
   - `query_historical_floods()` → **REAL BigQuery query** ✓

### C. WebSocket → Frontend Pipeline
1. **Backend Emit**: Agent calls `get_flood_extent()` tool
   - Returns: `{ geojson, area_sqkm, growth_rate_pct, metadata }`
   - **PROBLEM**: Data stuck in agent context; no explicit event emission
   - **Workaround**: `ee_update` events emitted only for specific tool results

2. **Frontend Receive** (`frontend/src/hooks/useHawkEyeSocket.js`)
   - Listens for `ee_update` events
   - Routes to `applyEarthEngineUpdate()` (App.jsx line 263)
   - Updates React state: `earthEngineOverrides`

3. **Frontend Render**
   - `EarthEnginePanel.jsx` → Displays metrics from state
   - `DataLayerPanel.jsx` → Uses **STATIC IMPORTED GEOJSON** (not from API)
   - `CesiumGlobe.jsx` → Renders polygon entities

### D. What is Live vs. Static/Mock

| Component | Status | Source |
|-----------|--------|--------|
| Flood extent geometry | **STATIC** | `flood_extent.geojson` |
| Growth rate (12% /hr) | **HARDCODED** | `earth_engine_service.py:102` |
| Population density | **STATIC** | 15,000/km² fixed |
| Infrastructure queries | ✓ **LIVE** | BigQuery |
| Historical flood patterns | ✓ **LIVE** | BigQuery |
| Timeline/frames | **MOCK** | `demoSimulator.js` |
| Provenance metadata | **STATIC** | `analysis_provenance.json` |

---

## 2. CONCRETE INTEGRATION GAPS

### Gap 1: No Live Raster/Tile Delivery
**Current**: Static GeoJSON vectors only
**Missing**:
- SAR backscatter rasters (VV/VH composite)
- NDWI water-body index layer
- Change detection heatmap
- TMS/XYZ tile endpoint for Cesium

### Gap 2: No Provenance/Age Metadata
**Current**: Single JSON sidecar with static metadata
**Missing**:
- Acquisition timestamp per satellite scene
- Scene selection confidence (cloud cover, quality)
- Data freshness indicator
- Processing confidence per pixel (raster)
- Scene composite algorithm details

### Gap 3: No Live API for EE Computations
**Current**: EarthEngineService loads files only
**Missing**:
- `/api/earth-engine/compute/{analysis_id}` endpoint
- Streaming result delivery (SSE or WebSocket)
- Parameter override capability (threshold_db, baseline window)
- Async task tracking & status polling

### Gap 4: Incomplete WebSocket Contract
**Current**: `ee_update` event is sparse
```javascript
{ type: "ee_update", area_sqkm: 1.56, growth_rate: 12, confidence: 87 }
```
**Missing**:
- Explicit metadata envelope
- Raster tile URL references
- Vector overlay type specification
- Confidence breakdown per component

### Gap 5: No Frontend Tile Layer Management
**Current**: DataLayerPanel toggles only static GeoJSON
**Missing**:
- Raster layer opacity/blend controls
- Layer composition (SAR + change + water mask)
- Cache invalidation signals
- Attribution/source display per layer

---

## 3. TARGET CONTRACT: "EE FIRST-CLASS RUNTIME"

### A. Backend Endpoints (New)

#### POST `/api/earth-engine/analyze`
```json
Request:
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
    "webhook_url": "wss://..."
}

Response (202 Accepted):
{
    "task_id": "ee_task_20260315_abc123",
    "status": "queued",
    "estimated_time_seconds": 45,
    "polling_url": "/api/earth-engine/tasks/ee_task_20260315_abc123"
}
```

#### GET `/api/earth-engine/tasks/{task_id}`
```json
Response:
{
    "task_id": "ee_task_20260315_abc123",
    "status": "running",
    "progress_pct": 65,
    "phase": "change_detection",
    "result": null,
    "error": null,
    "started_at": "2026-03-15T18:15:00Z",
    "expires_at": "2026-03-15T19:15:00Z"
}
```

#### GET `/api/earth-engine/tasks/{task_id}/result`
```json
Response (when status='completed'):
{
    "task_id": "...",
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
            { "date": "2025-06-04", "confidence": 0.95, "cloud_cover": 2.1 },
            { "date": "2026-02-15", "confidence": 0.87, "cloud_cover": 5.3 }
        ],
        "confidence": "MEDIUM",
        "generated_at": "2026-03-15T18:17:01Z",
        "pixel_confidence": {
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
                    "area_sqkm": 1.56,
                    "confidence": 0.92,
                    "pixel_count": 1560,
                    "class": "flooded"
                },
                "geometry": { "type": "MultiPolygon", "coordinates": [...] }
            }
        ]
    },
    "tiles": {
        "change_detection": {
            "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/change/{z}/{x}/{y}.png",
            "style": { "colormap": "RdYlBu_r", "vmin": -10, "vmax": 5 },
            "title": "SAR Change (dB): Wet - Baseline"
        },
        "water_mask": {
            "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/water/{z}/{x}/{y}.png",
            "style": { "colormap": "Blues", "opacity": 0.6 },
            "title": "Water Body Mask"
        },
        "confidence": {
            "url": "/api/earth-engine/tiles/ee_task_20260315_abc123/confidence/{z}/{x}/{y}.png",
            "style": { "colormap": "RdYlGn", "vmin": 0, "vmax": 1 },
            "title": "Pixel Confidence"
        }
    },
    "metrics": {
        "flood_area_sqkm": 1.56,
        "polygon_count": 15,
        "largest_polygon_sqkm": 0.23,
        "population_at_risk": 23400,
        "growth_rate_estimate": {
            "rate_pct_per_hour": 8.2,
            "confidence": 0.65,
            "note": "Based on time-series comparison"
        }
    }
}
```

#### GET `/api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png`
```
Serves raster tiles from Earth Engine analysis result.
Proxies to Earth Engine tile API or cached local storage.
```

### B. WebSocket Events (New)

```javascript
// Sent during streaming computation
{
    "type": "ee_analysis_update",
    "task_id": "ee_task_20260315_abc123",
    "phase": "baseline_composite",
    "progress_pct": 25,
    "message": "Building dry-season baseline composite from 6 scenes...",
    "timestamp": "2026-03-15T18:15:30Z"
}

// Sent when analysis completes
{
    "type": "ee_analysis_complete",
    "task_id": "ee_task_20260315_abc123",
    "analysis_provenance": { /* full provenance */ },
    "geometry": { /* GeoJSON FeatureCollection */ },
    "tiles": { /* tile URL map */ },
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
            "title": "Flood Extent Polygons"
        }
    ]
}
```

### C. Frontend Message Types (Updated)

```javascript
// In types/messages.js:
export const SERVER_MESSAGE_TYPES = {
    EE_ANALYSIS_START: 'ee_analysis_start',
    EE_ANALYSIS_UPDATE: 'ee_analysis_update',
    EE_ANALYSIS_COMPLETE: 'ee_analysis_complete',
    EE_UPDATE: 'ee_update',  // DEPRECATED
    MAP_UPDATE: 'map_update',
    // ... existing types
};

export const MAP_ACTIONS = {
    ADD_RASTER_LAYER: 'add_raster_layer',
    UPDATE_RASTER_OPACITY: 'update_raster_opacity',
    REMOVE_RASTER_LAYER: 'remove_raster_layer',
    ADD_GEOJSON_LAYER: 'add_geojson_layer',
    // ... existing actions
};
```

---

## 4. PHASE-1 IMPLEMENTATION: FILE-LEVEL CHANGES

### TIER 1: Service & API Layer

#### File 1: `app/services/earth_engine_service.py`
**Changes**: Add async live analysis methods
```python
# Keep existing (lines 1-132) for backward compat
# Add NEW methods (lines 133+):

async def submit_flood_analysis(
    self,
    baseline_window: tuple[str, str],
    event_window: tuple[str, str],
    geometry_geojson: dict | None = None,
    parameters: dict | None = None
) -> dict:
    """
    Submit live flood extent analysis to Earth Engine.
    Returns task_id for polling and streaming result.
    
    Returns:
        { task_id, status, estimated_time_seconds, polling_url }
    """
    # Implementation: Call EE client API

async def get_analysis_status(self, task_id: str) -> dict:
    """
    Poll Earth Engine task status.
    
    Returns:
        { task_id, status, progress_pct, phase, error? }
    """
    # Implementation: Query EE task state

async def get_analysis_result(self, task_id: str) -> dict:
    """
    Retrieve completed analysis with tile URLs and metrics.
    
    Returns:
        {
            analysis_provenance: dict,
            geometry: dict (GeoJSON FeatureCollection),
            tiles: dict (layer_name → tile_url_template),
            metrics: dict
        }
    """
    # Implementation: Fetch EE result and generate tile URLs

def _generate_tile_urls(self, task_id: str, layers: list[str]) -> dict:
    """
    Map Earth Engine tile outputs to frontend-accessible URLs.
    
    Returns:
        { layer_name: "/api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png", ... }
    """
```

#### File 2: `app/main.py`
**Changes**: Add 3 new REST endpoints + WebSocket polling task
**Lines to add after line 1173**:

```python
@app.post("/api/earth-engine/analyze")
async def submit_ee_analysis(request: EEAnalysisRequest) -> JSONResponse:
    """Submit live flood analysis to Earth Engine."""
    try:
        response = await _get_earth_engine().submit_flood_analysis(
            baseline_window=request.baseline_window,
            event_window=request.event_window,
            geometry_geojson=request.geometry,
            parameters=request.parameters
        )
        return JSONResponse(status_code=202, content=response)
    except Exception as e:
        logger.error(f"Failed to submit EE analysis: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/earth-engine/tasks/{task_id}")
async def get_ee_task_status(task_id: str) -> JSONResponse:
    """Poll status of an Earth Engine analysis task."""
    try:
        status = await _get_earth_engine().get_analysis_status(task_id)
        return JSONResponse(content=status)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/earth-engine/tasks/{task_id}/result")
async def get_ee_task_result(task_id: str) -> JSONResponse:
    """Retrieve final analysis result (only when completed)."""
    try:
        result = await _get_earth_engine().get_analysis_result(task_id)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png")
async def get_ee_tile(task_id: str, layer: str, z: int, x: int, y: int):
    """Serve raster tiles from Earth Engine analysis."""
    # Proxy to Earth Engine or fetch from cache
    pass
```

**Also add in WebSocket handler (around line 1300)**:
```python
# Background task for polling EE analysis
async def _poll_ee_task(websocket: WebSocket, session_id: str, task_id: str):
    """Poll EE task and stream updates via WebSocket."""
    max_polls = 60
    for _ in range(max_polls):
        try:
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
        
        except Exception as e:
            logger.error(f"Error polling EE task {task_id}: {e}")
            break
```

### TIER 2: Event Emission & Tool Enhancement

#### File 3: `app/hawkeye_agent/tools/analyst.py`
**Changes**: Enhance `get_flood_extent()` to support live mode
**Lines 357-384 (update)**:

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

### TIER 3: Frontend Events & UI

#### File 4: `frontend/src/types/messages.js`
**Changes**: Add new message types
**Lines 28-30 (update + add)**:

```javascript
EE_UPDATE: 'ee_update',             // DEPRECATED
EE_ANALYSIS_START: 'ee_analysis_start',
EE_ANALYSIS_UPDATE: 'ee_analysis_update',
EE_ANALYSIS_COMPLETE: 'ee_analysis_complete',
```

**Lines 94-106 (add new MAP_ACTIONS)**:
```javascript
ADD_RASTER_LAYER: 'add_raster_layer',
UPDATE_RASTER_OPACITY: 'update_raster_opacity',
REMOVE_RASTER_LAYER: 'remove_raster_layer',
ADD_GEOJSON_LAYER: 'add_geojson_layer',
```

#### File 5: `frontend/src/App.jsx`
**Changes**: Add WebSocket event handlers
**Lines 169-171 (add new state)**:

```javascript
const [analysisInProgress, setAnalysisInProgress] = useState(false);
const [analysisProgress, setAnalysisProgress] = useState(0);
const [analysisPhase, setAnalysisPhase] = useState(null);
const [analysisTaskId, setAnalysisTaskId] = useState(null);
```

**Lines 619-700 (add event handlers in useEffect)**:
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

#### File 6: `frontend/src/components/EarthEnginePanel.jsx`
**Changes**: Add live analysis submission UI
**After line 100 (add button)**:

```javascript
<button
  onClick={() => sendText(JSON.stringify({
    type: 'ee_analysis_start',
    parameters: {
      baseline_window: data.provenance?.baselineWindow?.split(' to '),
      event_window: data.provenance?.acquisitionWindow?.split(' to ')
    }
  }))}
  disabled={analysisInProgress}
>
  {analysisInProgress ? 'Analyzing...' : 'Run Live Analysis'}
</button>
```

**After line 120 (add progress display)**:
```javascript
{analysisInProgress && (
  <div className="ee-analysis-progress">
    <div className="phase-label">{analysisPhase}</div>
    <progress value={analysisProgress} max="100" />
    <span className="progress-text">{analysisProgress}%</span>
  </div>
)}
```

#### File 7: `frontend/src/components/strategic/DataLayerPanel.jsx`
**Changes**: Add raster layer toggle capability
**Lines 200-250 (add handlers)**:

```javascript
const handleAddRasterLayer = (layerId, tileUrl, title, style, opacity = 0.7) => {
  if (viewer && viewer.imageryLayers) {
    const tileProvider = new Cesium.UrlTemplateImageryProvider({
      url: tileUrl
    });
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

#### File 8: `frontend/src/components/CesiumGlobe.jsx`
**Changes**: Add raster tile layer rendering
**Lines 1-30 (add to props)**:
```javascript
export default function CesiumGlobe({
  mapCommands,
  onScreenshotCapture,
  onAddRasterLayer,
  onRemoveRasterLayer,
  onUpdateRasterOpacity,
  ...props
})
```

**Lines 400-450 (add in command handler switch)**:
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
```

### TIER 4: Type Safety (Optional but Recommended)

#### File 9: Add Pydantic models to `app/main.py`
```python
from pydantic import BaseModel, Field
from typing import Literal

class EEAnalysisRequest(BaseModel):
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

---

## 5. IMPLEMENTATION PRIORITY & EFFORT

| Rank | File | Change Type | Effort | Dependencies |
|------|------|-------------|--------|--------------|
| 1 | `earth_engine_service.py` | Refactor + Add async | 4h | EE API setup |
| 2 | `app/main.py` | Add 3 endpoints + polling | 3h | Service methods |
| 3 | `types/messages.js` | Add message types | 1h | None |
| 4 | `App.jsx` | Add event handlers | 2h | Message types |
| 5 | `EarthEnginePanel.jsx` | Add progress UI | 1.5h | Event handlers |
| 6 | `DataLayerPanel.jsx` | Raster layer management | 1.5h | None |
| 7 | `CesiumGlobe.jsx` | Tile rendering | 2h | Cesium API |
| 8 | `analyst.py` | Enhance get_flood_extent | 1h | Service |
| 9 | Pydantic models | Type safety | 1h | None |
| 10 | Tests | Unit + integration | 4h | All above |

**Total Phase-1 Effort**: ~21 hours

---

## 6. KEY DECISION POINTS FOR IMPLEMENTATION

1. **Async vs. Sync Pattern**: Use `asyncio` for long-running EE tasks (recommended)
2. **Tile Caching**: Store EE tiles locally or proxy live from EE API (hybrid recommended)
3. **Task Expiration**: Store task results with TTL (e.g., 1 hour)
4. **Error Handling**: Graceful fallback to static data if EE fails
5. **Auth**: Ensure EE service account credentials are loaded from environment

---

## 7. SUCCESS CRITERIA (POST-IMPLEMENTATION)

- [x] User can click "Run Live Analysis" button in EarthEnginePanel
- [x] Analysis task submitted to Earth Engine, task_id returned
- [x] Frontend displays progress bar with phase updates
- [x] Upon completion, WebSocket emits `ee_analysis_complete` event
- [x] GeoJSON flood extent vectors rendered on Cesium globe
- [x] Raster tiles (change detection, confidence, water mask) displayed with opacity control
- [x] Provenance metadata displayed in EarthEnginePanel (updated_at = task completion time)
- [x] All three tile layers can be toggled on/off independently
- [x] Backward compatibility maintained: static data used if EE service unavailable

---

## CONCLUSION

This audit identifies a **21-hour Phase-1 effort** to elevate HawkEye's Earth Engine integration from static files to a live, runtime-powered system. The proposed contract ensures clean separation of concerns: backend handles computation polling, frontend handles result display and layer management. Implementation should follow TIER priority order for incremental value delivery.

