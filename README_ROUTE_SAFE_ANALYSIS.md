# Route-Safe Pathfinding Gap Analysis: HawkEye Backend

## 📋 Documentation Index

### Main Documents
- **[ROUTE_SAFE_GAP_ANALYSIS.md](./ROUTE_SAFE_GAP_ANALYSIS.md)** — Full 37KB analysis (1024 lines)
  - Executive summary & gap explanation
  - All 4 TODOs with exact code surfaces
  - Backward-compatibility constraints
  - Edge cases & failure behaviors
  - Commit sequence & verification

- **[ROUTE_SAFE_SUMMARY.txt](./ROUTE_SAFE_SUMMARY.txt)** — Quick reference (21KB)
  - Quick TODO checklist
  - Test commands (copy-paste ready)
  - Summary table
  - File modification summary

---

## �� The Gap (30-Second Summary)

### Current State ❌
Routes generated **without** flood constraints, safety checked **after-the-fact**:
```
Coordinator → MapsService (no constraints) → Returns "PENDING_ANALYSIS" → Analyst validates post-hoc
```

**Problem**: `avoid_geojson` parameter is dead code, `avoid_flood` parameter ignored, routes not proactively safe.

### Target State ✅
Routes generated **with** hard constraints via waypoint avoidance:
```
Coordinator (fetches flood) → MapsService (passes constraints) → Returns "SAFE"|"CAUTION"|"UNSAFE"
```

**Benefit**: Routes guaranteed safe, safety rated pre-computed, faster response.

---

## 📌 4 Concrete TODOs

| TODO | Phase | File | Change | Time | Risk | Breaking |
|------|-------|------|--------|------|------|----------|
| 1. Remove dead `avoid_geojson` | 1 | `maps_service.py` (158-166) | Remove parameter | 30 min | LOW | ✓ param |
| 2. Coordinator fetches flood | 2 | `coordinator.py` (203-256) | Fetch flood, pass constraints | 2-3h | MEDIUM | ✓ rating |
| 3. MapsService avoid_zones | 3 | `maps_service.py` (158-242+) | 3-phase algorithm + helpers | 3-4h | MEDIUM | ✗ param |
| 4. Agent & Analyst updates | 3 | `agent.py`, `analyst.py` | Update docs & routing | 1-2h | LOW | ✗ docs |

**Total Implementation**: 6-9.5 hours

---

## 📍 Exact Code Surfaces

### TODO 1: Remove Dead Code (app/services/maps_service.py)
```python
# REMOVE line 162: avoid_geojson parameter
# REMOVE lines 164-166: avoid_geometry parsing
# REMOVE lines 191-195: route_safety evaluation when avoid_geojson

# Result: Cleaner contract, no false functionality
```

### TODO 2: Coordinator Fetches Flood (app/hawkeye_agent/tools/coordinator.py)
```python
# ADD (after geocoding):
if avoid_flood:
    from app.hawkeye_agent.tools.analyst import get_flood_extent
    flood_result = get_flood_extent()
    if flood_result.get("geojson"):
        avoid_zones = _extract_avoid_zones_from_flood(flood_result["geojson"])

# MODIFY line 239: Pass constraints
route_result = _get_maps_service().get_evacuation_route(
    origin_latlng=(origin["lat"], origin["lng"]),
    destination_latlng=(destination["lat"], destination["lng"]),
    avoid_zones=avoid_zones,  # ← NEW
    hard_constraint=avoid_flood,  # ← NEW
)

# MODIFY line 254: Return real rating
"safety_rating": route_result.get("safety_rating", "SAFE"),  # ← NOT PENDING_ANALYSIS

# ADD helper function _extract_avoid_zones_from_flood() (~50 lines)
```

### TODO 3: MapsService Avoid Zones (app/services/maps_service.py)
```python
# REPLACE signature:
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_zones: list[dict] | None = None,  # ← NEW
    cost_surface: str | None = None,  # ← FUTURE
    hard_constraint: bool = False,  # ← NEW
) -> dict[str, Any]:

# IMPLEMENT 3-phase algorithm:
# Phase A: Generate waypoints from avoid_zones
# Phase B: Score candidates with safety_rating
# Phase C: Select best by (danger_score, duration, distance)

# ADD 2 helper methods:
# _compute_waypoints_around_zones() — Generate routing waypoints
# _compute_safety_rating_preemptive() — Pre-compute safety rating
```

### TODO 4: Agent & Analyst (app/hawkeye_agent/agent.py, analyst.py)
```python
# agent.py lines ~107-121: Update documentation
# "Routes are now SAFE by default when avoid_flood=True"
# "Only call evaluate_route_safety() if user explicitly requests"

# analyst.py line ~814: Extract flood as avoid_zones
avoid_zones = [{
    "lat": flood_centroid.y,
    "lng": flood_centroid.x,
    "radius_m": int(radius_m),
}]

route_result = _get_maps_service().get_evacuation_route(
    origin_latlng=(start[1], start[0]),
    destination_latlng=(end[1], end[0]),
    avoid_zones=avoid_zones,  # ← NEW
    hard_constraint=False,  # ← NEW
)
```

---

## ⚠️ Backward-Compatibility Constraints

### Breaking Change
```
safety_rating value shift:
  OLD: "PENDING_ANALYSIS"
  NEW: "SAFE" | "CAUTION" | "UNSAFE"

Impact: Code checking == "PENDING_ANALYSIS" will fail
Mitigation: Update frontend to expect real ratings
```

### Safe Additions
- ✅ New parameter: `avoid_zones` (optional, default None)
- ✅ New field: `avoid_zones_applied` (int)
- ✅ New field: `route_safety` (object, only if avoidance applied)
- ✅ New error case: `route_geojson: None` (only when `hard_constraint=True`)

### Response Shape Contract
**Without constraints** (`avoid_zones=None`):
```python
{
    "route_geojson": Feature,
    "distance_m": int,
    "duration_s": int,
}  # Unchanged (backward compat)
```

**With constraints** (`avoid_zones=[...]`):
```python
{
    "route_geojson": Feature,
    "distance_m": int,
    "duration_s": int,
    "safety_rating": "SAFE" | "CAUTION" | "UNSAFE",  # ← NEW
    "route_safety": {
        "avoidance_applied": true,
        "candidate_count": int,
        "selected_candidate_index": int,
        "avoided_zone_count": int,
    },
}
```

---

## 🧪 Test Commands

### Phase 1 Verification
```bash
pytest tests/test_maps_service_route_avoidance.py -v
# BEFORE: 3 passed (with avoid_geojson)
# AFTER:  3 passed (with avoid_zones)
```

### Phase 2 Verification
```bash
pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v
pytest tests/test_route_avoidance.py::TestCoordinatorFetchesFloodBeforeRouting -v
```

### Phase 3 Verification
```bash
pytest tests/test_maps_service_route_avoidance.py -v
# BEFORE: 3 passed
# AFTER:  5 passed (3 existing + 2 new)
```

### Final Verification
```bash
pytest tests/test_maps_service_route_avoidance.py tests/test_route_avoidance.py -v
# Expected: 15-17 passed, NO xfail/skip ✓
```

---

## 🔍 Edge Cases & Failures

| Case | Scenario | Expected | Test |
|------|----------|----------|------|
| No Safe Route | Flood blocks all paths, `hard_constraint=True` | Return error, not unsafe | `test_hard_constraint_fails_if_no_safe_route` |
| Multiple Zones | 3+ disjoint zones | Waypoints for zones on path | `test_multiple_avoid_zones_generates_waypoints` |
| Zone at Origin | Evacuation point in flood | Return error | `test_origin_in_avoid_zone_returns_error` |
| Empty Zones | `avoid_zones=[]` | Route normally | `test_empty_avoid_zones_routes_normally` |
| Invalid GeoJSON | Malformed flood data | Log warning, route unconstrained | `test_invalid_flood_geojson_graceful_degradation` |
| Waypoint Failure | Invalid waypoints (water, etc) | Fail if hard_constraint, else unconstrained | `test_invalid_waypoints_fallback_to_unconstrained` |

---

## 📝 Files to Modify

```
app/services/maps_service.py
  ├─ Lines 158-166: Remove avoid_geojson (TODO 1)
  ├─ Lines 158-242: Replace get_evacuation_route() (TODO 3)
  └─ New methods: _compute_waypoints_around_zones(), 
                  _compute_safety_rating_preemptive()

app/hawkeye_agent/tools/coordinator.py
  ├─ Lines 203-256: Update generate_evacuation_route() (TODO 2)
  └─ New: _extract_avoid_zones_from_flood() helper

app/hawkeye_agent/tools/analyst.py
  └─ Lines ~814: Update _suggest_alternative_route() (TODO 4)

app/hawkeye_agent/agent.py
  └─ Lines ~107-121: Update documentation (TODO 4)

tests/test_maps_service_route_avoidance.py
  └─ Rename 1, update 1, add 2 tests

tests/test_route_avoidance.py
  └─ Update 2, add 2-3 new tests
```

---

## 🚀 Implementation Path

1. **Start with TODO 1** (30 min) — Remove dead code, low risk
   - Test: `pytest tests/test_maps_service_route_avoidance.py::test_* -v`

2. **Move to TODO 2** (2-3 hours) — Coordinator integration, medium risk
   - Test: `pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v`
   - Note: Some tests xfail until Phase 3

3. **Complete TODO 3** (3-4 hours) — Core implementation, medium risk
   - Test: `pytest tests/test_maps_service_route_avoidance.py -v`

4. **Finish with TODO 4** (1-2 hours) — Documentation & agent updates, low risk
   - Test: `pytest tests/test_route_avoidance.py -v`

5. **Final Verification** — All tests pass
   - Test: `pytest tests/test_*route_avoidance*.py -v`

---

## 📊 Summary

| Aspect | Current | After Phase 3 |
|--------|---------|---------------|
| Route Safety | Post-hoc warning | Proactive avoidance |
| Safety Rating | "PENDING_ANALYSIS" | "SAFE"\|"CAUTION"\|"UNSAFE" |
| Flood Constraints | None | Hard constraints via waypoints |
| Coordinator Role | Passive | Active (fetches flood, validates routes) |
| Route Selection | By time/distance | By safety first, then time |
| Agent Overhead | High (post-hoc validation needed) | Low (pre-computed) |

---

## 📞 Questions?

Refer to the full analysis documents for detailed code examples, test cases, and edge case handling:
- **[ROUTE_SAFE_GAP_ANALYSIS.md](./ROUTE_SAFE_GAP_ANALYSIS.md)** — Complete reference
- **[ROUTE_SAFE_SUMMARY.txt](./ROUTE_SAFE_SUMMARY.txt)** — Quick checklist

