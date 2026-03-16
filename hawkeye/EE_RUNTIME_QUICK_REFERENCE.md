# Earth Engine Runtime Contract — Quick Reference Guide

## 📌 Document Index

| Document | Purpose | Audience | Length |
|----------|---------|----------|--------|
| **EE_RUNTIME_AUDIT.md** | Current state analysis + gap identification | Tech lead, architects | 782 lines |
| **EE_RUNTIME_CONTRACT_DESIGN.md** | Implementation-ready specification (THIS IS MAIN) | Developers, reviewers | 960 lines |
| **EE_RUNTIME_QUICK_REFERENCE.md** | Quick lookup (this file) | All | - |

---

## 🎯 Current State Summary

### What's Live?
✅ **Infrastructure queries** — Real BigQuery  
✅ **Historical flood patterns** — Real BigQuery  

### What's Static?
❌ **Flood extent geometry** — Static GeoJSON file  
❌ **Growth rate (12%/hr)** — Hardcoded constant  
❌ **Population density** — Hardcoded 15,000/km²  
❌ **Provenance metadata** — Static JSON sidecar  

### What's Missing?
⚠️ **Live EE computation** — No API calls to Earth Engine  
⚠️ **Raster tiles** — Only vector GeoJSON supported  
⚠️ **Temporal frames** — No time-series imagery  
⚠️ **Pixel-level confidence** — No per-pixel metadata  

---

## �� Files to Change (Implementation Checklist)

### TIER 1: Backend Service & API (CRITICAL PATH)
- [ ] `app/services/earth_engine_service.py`
  - Add: `async def submit_flood_analysis(...)`
  - Add: `async def get_analysis_status(task_id)`
  - Add: `async def get_analysis_result(task_id)`
  - Add: `def _generate_tile_urls(task_id, layers)`
  - Status: ~100 lines of new code

- [ ] `app/main.py`
  - Add: Pydantic models (EEAnalysisRequest, EETaskStatus)
  - Add: POST `/api/earth-engine/analyze`
  - Add: GET `/api/earth-engine/tasks/{task_id}`
  - Add: GET `/api/earth-engine/tasks/{task_id}/result`
  - Add: GET `/api/earth-engine/tiles/{task_id}/{layer}/{z}/{x}/{y}.png`
  - Add: Async polling task `_poll_ee_task()`
  - Status: ~150 lines of new code

### TIER 2: Tool Enhancement
- [ ] `app/hawkeye_agent/tools/analyst.py`
  - Update: `get_flood_extent()` with live_mode flag
  - Status: ~50 lines modified/added

### TIER 3: Frontend Messages & UI
- [ ] `frontend/src/types/messages.js`
  - Add: EE_ANALYSIS_START, EE_ANALYSIS_UPDATE, EE_ANALYSIS_COMPLETE
  - Add: MAP_ACTIONS (ADD_RASTER_LAYER, REMOVE_RASTER_LAYER, etc.)
  - Status: ~20 lines added

- [ ] `frontend/src/App.jsx`
  - Add: analysisInProgress, analysisProgress, analysisPhase state
  - Add: Event handlers for ee_analysis_* events
  - Status: ~80 lines added

- [ ] `frontend/src/components/EarthEnginePanel.jsx`
  - Add: "Run Live Analysis" button
  - Add: Progress bar + phase display
  - Status: ~30 lines added

- [ ] `frontend/src/components/strategic/DataLayerPanel.jsx`
  - Add: Raster layer management methods
  - Add: Raster layer UI in panel
  - Status: ~80 lines added

- [ ] `frontend/src/components/CesiumGlobe.jsx`
  - Add: Raster layer refs
  - Add: Command handlers for add_raster_layer, update_raster_opacity, etc.
  - Status: ~100 lines added

---

## 🔌 Event Contract at a Glance

### REST Endpoints
```
POST   /api/earth-engine/analyze                      → Submit analysis (202)
GET    /api/earth-engine/tasks/{task_id}              → Poll status
GET    /api/earth-engine/tasks/{task_id}/result       → Get result (when complete)
GET    /api/earth-engine/tiles/{task_id}/{layer}/...  → Serve XYZ tiles
```

### WebSocket Events (NEW)
```
EMIT (frontend → backend):
  ee_analysis_start              → { type, parameters }

RECEIVE (backend → frontend):
  ee_analysis_update             → { type, task_id, phase, progress_pct, message }
  ee_analysis_complete           → { type, task_id, analysis_provenance, geometry, tiles, metrics, map_actions }
```

### Payload Schemas
```json
SUBMIT:
{
  "analysis_type": "flood_extent",
  "parameters": {
    "baseline_window": ["2025-06-01", "2025-09-30"],
    "event_window": ["2026-01-01", "2026-02-28"],
    "geometry": { "type": "Polygon", "coordinates": [...] },
    "dataset": "COPERNICUS/S1_GRD",
    "threshold_db": -3.0
  }
}

RESULT:
{
  "analysis_provenance": { /* detailed metadata */ },
  "geometry": { "type": "FeatureCollection", "features": [...] },
  "tiles": {
    "change_detection": { "url": "...", "style": {...} },
    "water_mask": { "url": "...", "style": {...} },
    "confidence": { "url": "...", "style": {...} }
  },
  "metrics": {
    "flood_area_sqkm": 1.56,
    "population_at_risk": 23400,
    "growth_rate_estimate": { "rate_pct_per_hour": 8.2, "confidence": 0.65 }
  }
}
```

---

## ⚙️ Key Implementation Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Async pattern | `asyncio` | Non-blocking task polling |
| Tile caching | Hybrid (live + local) | Fast response + fresh data |
| Task TTL | 1 hour | Reasonable cleanup window |
| Fallback | Static data | Zero downtime if EE fails |
| Auth | Environment vars | Standard practice |
| Rate limit | Exponential backoff | Avoid API throttling |

---

## ✅ Success Criteria

After implementation, verify:
- [ ] User can click "Run Live Analysis" button
- [ ] Task submitted to EE, task_id returned
- [ ] Progress bar shows 0–100% with phase labels
- [ ] GeoJSON vectors render on globe when complete
- [ ] Raster tiles (change, confidence, water) display correctly
- [ ] Each tile layer can be toggled independently
- [ ] Provenance shows "LIVE" status + completion time
- [ ] Static data serves as fallback if EE unavailable
- [ ] WebSocket reconnect resumes from task_id (no data loss)
- [ ] Old clients continue working (backward compatible)

---

## 🚀 Effort Estimates

| Task | Hours | Dependencies |
|------|-------|--------------|
| earth_engine_service.py | 6 | EE API setup |
| app/main.py | 4 | Service methods |
| analyst.py | 1.5 | Service |
| types/messages.js | 1 | None |
| App.jsx | 2 | Message types |
| EarthEnginePanel.jsx | 1.5 | App event handlers |
| DataLayerPanel.jsx | 2 | None |
| CesiumGlobe.jsx | 2 | Cesium docs |
| Pydantic models | 1 | None |
| Tests | 7 | All above |
| **TOTAL** | **~28 hours** | |

**Critical path**: Service → App → Event handlers (must be sequential)  
**Parallelizable**: Frontend components can be worked on simultaneously

---

## 🛡️ Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| EE API downtime | Check for service → use static data |
| Network interruption | WebSocket reconnect resumes from task_id |
| Task expires | Implement 1-hour TTL + UI to restart |
| Auth missing | Graceful degradation to static-only mode |
| Tile generation slow | Increase poll timeout, add exponential backoff |
| Backward compatibility | Keep `ee_update` message for old clients |

---

## 📝 File Modification Summary

```
Total files to modify: 8
├─ Backend services: 2 (earth_engine_service.py, main.py)
├─ Tools: 1 (analyst.py)
├─ Frontend types: 1 (messages.js)
├─ Frontend components: 4 (App.jsx, EarthEnginePanel.jsx, DataLayerPanel.jsx, CesiumGlobe.jsx)
└─ Test files: ~5 (to be created)

Lines of code:
├─ Added: ~450 lines
├─ Modified: ~100 lines
└─ Deleted: ~0 lines (backward compatible)
```

---

## 🔗 Implementation Roadmap

```
Week 1: TIER 1 (Backend Service & API)
  Mon–Wed: earth_engine_service.py async methods
  Wed–Fri: app/main.py endpoints + polling task

Week 2: TIER 2–3 (Tools & Frontend)
  Mon–Tue: analyst.py dual-mode support
  Tue–Wed: types/messages.js + basic event handlers
  Wed–Fri: UI components (button, progress, panels)

Week 3: TIER 4–5 (Polish & Testing)
  Mon–Tue: Pydantic models + error handling
  Tue–Fri: Unit + integration tests
  Fri: QA + staging deployment

Week 4: Deployment & Monitoring
  Mon–Fri: Production rollout with feature flag
  Monitor: Error rates, task success rate, tile latency
```

---

## 📚 References

- **Google Earth Engine Python API**: https://developers.google.com/earth-engine/guides/python_install
- **Cesium.js Imagery Layers**: https://cesium.com/docs/cesiumjs-ref-doc/ImageryProvider.html
- **Pydantic Documentation**: https://docs.pydantic.dev/
- **WebSocket Best Practices**: https://developer.mozilla.org/en-US/docs/Web/API/WebSocket
- **XYZ Tile Format**: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames

---

**Last Updated**: 2025-03-16  
**Status**: READY FOR IMPLEMENTATION  
**Document**: Part of design-ee-runtime-contract TODO
