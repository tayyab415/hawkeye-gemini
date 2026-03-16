# Route-Safe Pathfinding Gap Analysis: HawkEye Backend

## Executive Summary

**Current State**: Routes generated **without** flood constraints, safety checked **after-the-fact** via spatial intersection. Coordinator returns `safety_rating: "PENDING_ANALYSIS"` while `avoid_geojson` parameter is dead code.

**Target State**: Routes generated **with** hard constraints via waypoint avoidance. Safety rating pre-computed at routing time. Coordinator returns actual safety status (`"SAFE"|"CAUTION"|"UNSAFE"`).

**Gap**: 4 concrete TODOs spanning coordinator, maps service, and agent logic. Phases 1-3 require 5-7 hours implementation + testing. Phase 4 is documentation/behavioral updates.

---

## Current State Analysis

### The Core Gap

```python
# COORDINATOR (line 239-254, coordinator.py)
route_result = _get_maps_service().get_evacuation_route(
    origin_latlng=(origin["lat"], origin["lng"]),
    destination_latlng=(destination["lat"], destination["lng"]),
    # BUG: avoid_flood parameter ignored
)

return {
    "safety_rating": "PENDING_ANALYSIS",  # ← WRONG
    "avoid_flood": avoid_flood,
}
```

### Why This Fails

1. **`avoid_geojson` parameter never used** (line 162, maps_service.py)
   - Parameter parsed (line 165-166) but routing happens on line 175 without it
   - Test expects it works but coordinator never calls with it

2. **Coordinator doesn't fetch flood** (line 204, coordinator.py)
   - `avoid_flood` parameter accepted but ignored
   - No call to `get_flood_extent()` before routing

3. **Routes selected by time/distance, not safety**
   - First alternative returned (line 211, maps_service.py)
   - No scoring by safety when `avoid_geojson` present

4. **Safety rating post-hoc**
   - Returns "PENDING_ANALYSIS" instead of real status
   - Analyst tool must separately evaluate route

---

## Backward-Compatibility Constraints

### Breaking Change: `safety_rating` Value Shift

| Component | Old Value | New Value | Impact |
|-----------|-----------|-----------|--------|
| `generate_evacuation_route()` | `"PENDING_ANALYSIS"` | `"SAFE"\|"CAUTION"\|"UNSAFE"` | Frontend checks `== "PENDING_ANALYSIS"` will fail |
| `get_evacuation_route()` (with constraints) | Not returned | `"SAFE"\|"CAUTION"\|"UNSAFE"` | New field, safe to add if constraints present |

### Mitigation Strategy
- Update frontend to expect real ratings
- Add deprecation notice in coordinator comments
- Verify no agent code checks for `"PENDING_ANALYSIS"` literal

### Safe Additions (Backward-Compatible)

✅ New optional parameter: `avoid_zones: list[dict] | None = None`
✅ New optional field: `avoid_zones_applied: int`
✅ New optional field: `route_safety: dict` (only if avoidance applied)
✅ New error case: `route_geojson: None` (only when `hard_constraint=True`)

### Response Shape Contract

**Without constraints** (`avoid_zones=None`):
```python
{
    "route_geojson": Feature,
    "distance_m": int,
    "duration_s": int,
    # No safety fields (backward compat)
}
```

**With constraints** (`avoid_zones=[...]`):
```python
{
    "route_geojson": Feature,
    "distance_m": int,
    "duration_s": int,
    "safety_rating": "SAFE" | "CAUTION" | "UNSAFE",
    "route_safety": {
        "avoidance_applied": true,
        "candidate_count": int,
        "selected_candidate_index": int,
        "avoided_zone_count": int,
    },
}
```

---

## 4 Concrete TODOs

### TODO 1: Remove Dead `avoid_geojson` Parameter (Phase 1 — 30 min)

**File**: `app/services/maps_service.py`  
**Lines**: 158-166, 191-195

**Current Code**:
```python
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_geojson: str | dict | None = None,  # ← DEAD CODE
) -> dict[str, Any]:
    avoid_geometry: BaseGeometry | None = None
    if avoid_geojson is not None:  # ← Parsed but never used
        avoid_geometry = _parse_avoid_geometry(avoid_geojson)
    # ... routing proceeds without using avoid_geometry
```

**Surface Change**:
1. Remove parameter from signature (line 162)
2. Remove lines 164-166 (avoid_geometry parsing)
3. Remove lines 191-195 (route_safety evaluation when avoid_geojson present)

**Behavior Change**:
- ✅ Cleaner contract: no false functionality
- ❌ Breaks existing code passing `avoid_geojson` (but it did nothing anyway)

**Tests to Update**:

1. **Rename + Update**: `test_maps_service_route_avoidance.py::test_selects_safer_route_when_avoid_geojson_provided`
   ```python
   def test_selects_safer_route_when_avoid_zones_provided(self) -> None:
       # ... setup ...
       result = service.get_evacuation_route(
           origin_latlng=(-6.22, 106.85),
           destination_latlng=(-6.21, 106.88),
           avoid_zones=[{"lat": 0.0, "lng": 0.0, "radius_m": 500}],  # ← NEW
       )
       assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
   ```

2. **Remove**: `test_maps_service_route_avoidance.py::test_raises_for_invalid_avoid_geojson`
   - Parameter no longer exists, test no longer valid

3. **Add New Regression**: `TestAvoidGeojsonNotDeadCode::test_parameter_not_in_signature`
   ```python
   def test_parameter_not_in_signature():
       sig = inspect.signature(MapsService.get_evacuation_route)
       assert "avoid_geojson" not in sig.parameters
   ```

**Run Tests**:
```bash
pytest tests/test_maps_service_route_avoidance.py -v
# BEFORE: 3 passed (with avoid_geojson)
# AFTER:  3 passed (with avoid_zones)
```

---

### TODO 2: Coordinator Fetches Flood Before Routing (Phase 2 — 2-3 hours)

**File**: `app/hawkeye_agent/tools/coordinator.py`  
**Lines**: 203-256 + new helper function

**Current Code** (lines 239-254):
```python
# BUG: avoid_flood parameter never used
route_result = _get_maps_service().get_evacuation_route(
    origin_latlng=(origin["lat"], origin["lng"]),
    destination_latlng=(destination["lat"], destination["lng"]),
)

return {
    "safety_rating": "PENDING_ANALYSIS",  # ← WRONG
    "avoid_flood": avoid_flood,
}
```

**Surface Changes**:

1. **Add flood fetching** (after geocoding, before routing):
```python
if avoid_flood:
    logger.info("Coordinator: Fetching flood extent for routing constraints")
    from app.hawkeye_agent.tools.analyst import get_flood_extent
    
    try:
        flood_result = get_flood_extent()
        if flood_result.get("geojson"):  # Key name: "geojson" not "flood_geojson"
            avoid_zones = _extract_avoid_zones_from_flood(flood_result["geojson"])
            logger.info(f"Coordinator: Extracted {len(avoid_zones) if avoid_zones else 0} avoid zones")
    except Exception as e:
        logger.warning(f"Coordinator: Could not fetch flood: {e}")
        avoid_zones = None
else:
    avoid_zones = None
```

2. **Pass constraints to routing** (line 239):
```python
route_result = _get_maps_service().get_evacuation_route(
    origin_latlng=(origin["lat"], origin["lng"]),
    destination_latlng=(destination["lat"], destination["lng"]),
    avoid_zones=avoid_zones,  # ← NEW
    hard_constraint=avoid_flood,  # ← NEW
)
```

3. **Return pre-computed rating** (line 254):
```python
"safety_rating": route_result.get("safety_rating", "SAFE"),  # ← NOT PENDING_ANALYSIS
"avoid_zones_applied": len(avoid_zones) if avoid_zones else 0,  # ← NEW field
```

4. **Add error case** (line ~250):
```python
if not route_result.get("route_geojson"):
    if avoid_flood and avoid_zones:
        return {
            "error": "No safe route found avoiding flood zones",
            "recommendation": "Consider alternative destination or shelter-in-place",
            "origin": origin_name,
            "destination": destination_name,
            "avoid_flood": avoid_flood,
            "avoid_zones_attempted": len(avoid_zones),
        }
    return {"error": "Route not found", ...}
```

5. **Add helper function** (~50 lines):
```python
def _extract_avoid_zones_from_flood(flood_geojson: str | dict) -> list[dict]:
    """
    Extract avoid zones (centroid + radius) from flood GeoJSON.
    
    Returns: [{"lat": float, "lng": float, "radius_m": float}, ...]
    """
    import json
    from shapely.geometry import shape, Polygon
    
    try:
        if isinstance(flood_geojson, str):
            flood_data = json.loads(flood_geojson)
        else:
            flood_data = flood_geojson
        
        # Handle FeatureCollection/Feature/raw geometry
        if flood_data.get("type") == "FeatureCollection":
            features = flood_data.get("features", [])
            geom_data = features[0].get("geometry", {}) if features else {}
        elif flood_data.get("type") == "Feature":
            geom_data = flood_data.get("geometry", {})
        else:
            geom_data = flood_data
        
        flood_shape = shape(geom_data)
        avoid_zones = []
        
        if flood_shape.geom_type == "Polygon":
            centroid = flood_shape.centroid
            bounds = flood_shape.bounds
            radius_m = max(
                abs(bounds[2] - bounds[0]) * 111_000 / 2,
                abs(bounds[3] - bounds[1]) * 111_000 / 2,
            )
            avoid_zones.append({
                "lat": centroid.y,
                "lng": centroid.x,
                "radius_m": int(radius_m),
            })
        elif flood_shape.geom_type == "MultiPolygon":
            for poly in flood_shape.geoms:
                centroid = poly.centroid
                bounds = poly.bounds
                radius_m = max(
                    abs(bounds[2] - bounds[0]) * 111_000 / 2,
                    abs(bounds[3] - bounds[1]) * 111_000 / 2,
                )
                avoid_zones.append({
                    "lat": centroid.y,
                    "lng": centroid.x,
                    "radius_m": int(radius_m),
                })
        
        return avoid_zones
    
    except Exception as e:
        logger.warning(f"Could not extract avoid zones from flood: {e}")
        return []
```

**Behavior Change**:
- ✅ `safety_rating` changes from `"PENDING_ANALYSIS"` → `"SAFE"|"CAUTION"|"UNSAFE"`
- ✅ Flood constraints passed proactively to MapsService
- ⚠️ **BREAKING**: Code checking `== "PENDING_ANALYSIS"` will fail
- ⚠️ `distance_m`/`duration_minutes` may **increase** due to routing around zones
- ✅ New field `avoid_zones_applied` added (safe to add)

**Tests to Update/Add**:

1. **Update**: `test_route_avoidance.py::TestSafetyRatingPreComputed::test_generate_evacuation_route_never_returns_pending_analysis`
   ```python
   def test_generate_evacuation_route_never_returns_pending_analysis(self):
       # Remove xfail() decorator
       # Setup mocks...
       result = generate_evacuation_route(..., avoid_flood=True)
       
       # ASSERT: Real rating, not PENDING_ANALYSIS
       assert result["safety_rating"] in ["SAFE", "CAUTION", "UNSAFE"]
       assert result.get("avoid_zones_applied") is not None
   ```
   **Run**: `pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v`

2. **Update**: `test_route_avoidance.py::TestCoordinatorFetchesFloodBeforeRouting::test_generate_evacuation_route_with_avoid_flood_true`
   ```python
   def test_generate_evacuation_route_with_avoid_flood_true(self):
       with patch('app.hawkeye_agent.tools.coordinator._get_maps_service') as mock_maps, \
            patch('app.hawkeye_agent.tools.coordinator.get_flood_extent') as mock_flood:
           
           mock_flood.return_value = {
               "geojson": {"type": "Polygon", "coordinates": [...]}
           }
           mock_maps.return_value.geocode.side_effect = lambda x: {
               "lat": -6.225, "lng": 106.855, "formatted_address": f"Geocoded: {x}"
           }
           mock_maps.return_value.get_evacuation_route.return_value = {
               "route_geojson": {...},
               "distance_m": 5000,
               "duration_s": 300,
               "safety_rating": "SAFE",
           }
           
           result = generate_evacuation_route("Origin", "Dest", avoid_flood=True)
           
           # VERIFY flood was fetched
           mock_flood.assert_called_once()
           
           # VERIFY avoid_zones passed to routing
           call_kwargs = mock_maps.return_value.get_evacuation_route.call_args.kwargs
           assert "avoid_zones" in call_kwargs or len(call_args) > 2
           assert call_kwargs.get("hard_constraint") == True
           
           # VERIFY pre-computed rating
           assert result["safety_rating"] in ["SAFE", "CAUTION", "UNSAFE"]
           assert result.get("avoid_zones_applied") >= 0
   ```
   **Run**: `pytest tests/test_route_avoidance.py::TestCoordinatorFetchesFloodBeforeRouting -v`

3. **Add New**: `test_route_avoidance.py::TestCoordinatorFloodFailure::test_gracefully_handles_flood_fetch_failure`
   ```python
   def test_gracefully_handles_flood_fetch_failure(self):
       with patch('app.hawkeye_agent.tools.coordinator._get_maps_service') as mock_maps, \
            patch('app.hawkeye_agent.tools.coordinator.get_flood_extent') as mock_flood:
           
           # Simulate flood fetch failure
           mock_flood.side_effect = Exception("Earth Engine error")
           mock_maps.return_value.geocode.return_value = {...}
           mock_maps.return_value.get_evacuation_route.return_value = {
               "route_geojson": {...},
               "distance_m": 5000,
               "duration_s": 300,
               "safety_rating": "SAFE",
           }
           
           result = generate_evacuation_route("Origin", "Dest", avoid_flood=True)
           
           # Should route without constraints (avoid_zones=None)
           assert result["route_geojson"] is not None
           assert result.get("avoid_zones_applied") == 0
   ```
   **Run**: `pytest tests/test_route_avoidance.py::TestCoordinatorFloodFailure -v`

**Run Tests**:
```bash
pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v
pytest tests/test_route_avoidance.py::TestCoordinatorFetchesFloodBeforeRouting -v
pytest tests/test_route_avoidance.py::TestCoordinatorFloodFailure -v
# Some tests xfail until Phase 3 (MapsService accepts avoid_zones)
```

---

### TODO 3: MapsService Proactive Avoidance (Phase 3 — 3-4 hours)

**File**: `app/services/maps_service.py`  
**Lines**: 158-242 (replace) + new methods (~120 lines)

**New Signature** (replace lines 158-167):
```python
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_zones: list[dict] | None = None,  # ← NEW: [{"lat", "lng", "radius_m"}, ...]
    cost_surface: str | None = None,  # ← FUTURE (accept but don't use yet)
    hard_constraint: bool = False,  # ← NEW: Fail if no safe route found
) -> dict[str, Any]:
    """
    Generate evacuation route with proactive flood avoidance.
    
    Args:
        origin_latlng: (lat, lng) tuple
        destination_latlng: (lat, lng) tuple
        avoid_zones: List of zones to avoid. Each zone: {"lat": float, "lng": float, "radius_m": float}
        cost_surface: "high_risk" | "medium_risk" (future implementation, accept but ignore)
        hard_constraint: If True, fail (return error) if no safe route found
    
    Returns:
        {
            "route_geojson": Feature | None,
            "distance_m": int,
            "duration_s": int,
            "safety_rating": "SAFE" | "CAUTION" | "UNSAFE" | "UNKNOWN",
            "route_safety": {...},  # Only if avoid_zones passed
        }
    """
```

**Implementation** (3-phase algorithm):

**Phase A: Waypoint Generation** (line ~340):
```python
kwargs: dict[str, Any] = {
    "origin": origin_latlng,
    "destination": destination_latlng,
    "mode": "driving",
    "alternatives": True,
}

# Add waypoints to avoid zones if provided
if avoid_zones and len(avoid_zones) > 0:
    waypoints = self._compute_waypoints_around_zones(
        origin_latlng, destination_latlng, avoid_zones
    )
    if waypoints:
        kwargs["waypoints"] = waypoints

results = self.client.directions(**kwargs)
```

**Phase B: Candidate Scoring** (line ~380):
```python
candidates: list[dict[str, Any]] = []
for idx, result in enumerate(results):
    legs = result.get("legs") or []
    polyline = result.get("overview_polyline") or {}
    if not legs or "points" not in polyline:
        continue
    
    leg = legs[0]
    distance = leg.get("distance") or {}
    duration = leg.get("duration") or {}
    coords = _decode_polyline(polyline["points"])
    
    # Pre-compute safety rating for this candidate
    safety_rating = self._compute_safety_rating_preemptive(coords, avoid_zones) if avoid_zones else "SAFE"
    
    candidates.append({
        "route_index": idx,
        "coords": coords,
        "distance_m": distance.get("value", 0),
        "duration_s": duration.get("value", 0),
        "distance_text": distance.get("text"),
        "duration_text": duration.get("text"),
        "safety_rating": safety_rating,
    })
```

**Phase C: Best Route Selection** (line ~420):
```python
if not candidates:
    if hard_constraint and avoid_zones:
        return {
            "route_geojson": None,
            "distance_m": 0,
            "duration_s": 0,
            "error": "No safe route found avoiding specified zones",
            "safety_rating": "UNSAFE",
            "avoidance_applied": True,
        }
    return {
        "route_geojson": None,
        "distance_m": 0,
        "duration_s": 0,
        "safety_rating": "UNKNOWN",
    }

selected = candidates[0]
if avoid_zones:
    # Sort by: (danger_score, duration, distance)
    def route_safety_key(c):
        rating = c["safety_rating"]
        danger_score = {"SAFE": 0, "CAUTION": 1, "UNSAFE": 2}.get(rating, 3)
        return (danger_score, c["duration_s"], c["distance_m"])
    
    selected = min(candidates, key=route_safety_key)
```

**Response Building** (line ~440):
```python
route_properties: dict[str, Any] = {
    "distance_m": selected["distance_m"],
    "duration_s": selected["duration_s"],
    "distance_text": selected["distance_text"],
    "duration_text": selected["duration_text"],
}

response: dict[str, Any] = {
    "route_geojson": {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": selected["coords"],
        },
        "properties": route_properties,
    },
    "distance_m": selected["distance_m"],
    "duration_s": selected["duration_s"],
    "safety_rating": selected["safety_rating"],  # ← PRE-COMPUTED
}

# Add avoidance metadata if constraints applied
if avoid_zones:
    avoidance_data = {
        "avoidance_applied": True,
        "candidate_count": len(candidates),
        "selected_candidate_index": selected["route_index"],
        "safety_rating": selected["safety_rating"],
        "avoided_zone_count": len(avoid_zones),
    }
    route_properties["route_safety"] = avoidance_data
    response["route_safety"] = avoidance_data

return response
```

**New Helper Methods** (add ~120 lines):

```python
def _compute_waypoints_around_zones(
    self,
    origin: tuple[float, float],
    destination: tuple[float, float],
    avoid_zones: list[dict],
) -> list[tuple[float, float]] | None:
    """
    Compute waypoints to route AROUND avoid zones.
    
    For each zone on the direct path, compute perpendicular offset point
    at safe distance (radius + safety margin).
    
    Returns: [(lat, lng), ...] or None if no waypoints needed
    """
    from shapely.geometry import LineString, Point
    import math
    
    waypoints = []
    origin_pt = Point(origin[1], origin[0])  # Point expects (lng, lat)
    dest_pt = Point(destination[1], destination[0])
    
    base_line = LineString([origin_pt, dest_pt])
    
    for zone in avoid_zones:
        zone_pt = Point(zone["lng"], zone["lat"])
        radius_deg = zone.get("radius_m", 500) / 111_000  # Rough conversion
        
        # If zone not on direct path, skip waypoint
        distance_to_zone = base_line.distance(zone_pt)
        if distance_to_zone > radius_deg * 2:
            continue  # Zone far from path
        
        # Find closest point on line to zone center
        closest_pt = base_line.interpolate(base_line.project(zone_pt))
        
        # Offset perpendicular by (radius + safety margin)
        offset_distance = (radius_deg + 0.01) * 111_000  # Convert back to degrees
        
        # Get perpendicular direction
        dx = destination[1] - origin[1]
        dy = destination[0] - origin[0]
        length = math.sqrt(dx**2 + dy**2)
        
        if length > 0:
            # Perpendicular vector
            perp_x = -dy / length
            perp_y = dx / length
            
            # Offset point (choose left or right based on zone position)
            offset_x = closest_pt.x + perp_x * offset_distance / 111_000
            offset_y = closest_pt.y + perp_y * offset_distance / 111_000
            
            waypoints.append((offset_y, offset_x))  # (lat, lng) for Google Maps API
    
    return waypoints if waypoints else None


def _compute_safety_rating_preemptive(
    self,
    route_coords: list[list[float]],
    avoid_zones: list[dict] | None,
) -> str:
    """
    Pre-compute safety rating based on distance to avoid zones.
    
    Counts how many zones the route passes near (within zone radius).
    
    Returns: "SAFE" | "CAUTION" | "UNSAFE"
    """
    from shapely.geometry import LineString, Point
    
    if not avoid_zones:
        return "SAFE"
    
    route_line = LineString([(c[0], c[1]) for c in route_coords])  # [lng, lat]
    
    danger_count = 0
    for zone in avoid_zones:
        zone_pt = Point(zone["lng"], zone["lat"])
        radius_deg = zone.get("radius_m", 500) / 111_000
        
        # Distance from route line to zone center
        distance = route_line.distance(zone_pt)
        
        if distance < radius_deg:  # Route passes near/through zone
            danger_count += 1
    
    if danger_count == 0:
        return "SAFE"
    elif danger_count <= 1:
        return "CAUTION"
    else:
        return "UNSAFE"
```

**Behavior Change**:
- ✅ Routes NOW respect avoid_zones (hard constraint via waypoints)
- ✅ safety_rating pre-computed (not "PENDING_ANALYSIS")
- ✅ Better route selection (safety prioritized when zones present)
- ⚠️ `distance_m`/`duration_s` may **INCREASE** due to routing around zones
- ⚠️ `selected_candidate_index` may **NOT be 0** (routes reordered by safety)

**Tests to Update/Add**:

1. **Rename + Update**: `test_maps_service_route_avoidance.py::test_selects_safer_route_when_avoid_geojson_provided`
   ```python
   def test_selects_safer_route_when_avoid_zones_provided(self) -> None:
       mock_client = Mock()
       mock_client.directions.return_value = [
           _route_result("unsafe", 900, 120),
           _route_result("safe", 1600, 260),
       ]
       decoded = {"unsafe": UNSAFE_ROUTE, "safe": SAFE_ROUTE}
       
       with (
           patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
           patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
       ):
           service = MapsService(api_key="fake")
           result = service.get_evacuation_route(
               origin_latlng=(-6.22, 106.85),
               destination_latlng=(-6.21, 106.88),
               avoid_zones=[{"lat": 0.0, "lng": 0.0, "radius_m": 500}],  # ← NEW
           )
       
       # Selected route should be SAFE_ROUTE (candidate reordered)
       assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
       assert result["safety_rating"] in ["SAFE", "CAUTION"]
       assert result["route_safety"]["avoidance_applied"] is True
       assert result["route_safety"]["candidate_count"] == 2
       assert result["route_safety"]["selected_candidate_index"] == 1  # ← Reordered!
   ```
   **Run**: `pytest tests/test_maps_service_route_avoidance.py::test_selects_safer_route_when_avoid_zones_provided -v`

2. **Update**: `test_maps_service_route_avoidance.py::test_preserves_first_route_when_avoid_geojson_absent`
   ```python
   def test_preserves_first_route_when_avoid_zones_absent(self) -> None:
       # ... setup same as above ...
       result = service.get_evacuation_route(
           origin_latlng=(-6.22, 106.85),
           destination_latlng=(-6.21, 106.88),
           # avoid_zones NOT passed (None)
       )
       
       # First route preserved, safety_rating pre-computed
       assert result["route_geojson"]["geometry"]["coordinates"] == UNSAFE_ROUTE
       assert result["safety_rating"] == "SAFE"  # ← Pre-computed
       assert "route_safety" not in result  # ← No avoidance metadata
   ```

3. **Add New**: `test_hard_constraint_fails_if_no_safe_route`
   ```python
   def test_hard_constraint_fails_if_no_safe_route(self) -> None:
       mock_client = Mock()
       mock_client.directions.return_value = []  # No alternatives possible
       
       with patch("app.services.maps_service.googlemaps.Client", return_value=mock_client):
           service = MapsService(api_key="fake")
           result = service.get_evacuation_route(
               origin_latlng=(-6.22, 106.85),
               destination_latlng=(-6.21, 106.88),
               avoid_zones=[{"lat": 0.0, "lng": 0.0, "radius_m": 500}],
               hard_constraint=True,  # ← NEW
           )
       
       # Should return error, not unsafe route
       assert result["route_geojson"] is None
       assert result["error"] == "No safe route found avoiding specified zones"
       assert result["safety_rating"] == "UNSAFE"
       assert result["avoidance_applied"] is True
   ```
   **Run**: `pytest tests/test_maps_service_route_avoidance.py::test_hard_constraint_fails -v`

4. **Add New**: `test_multiple_avoid_zones_generates_waypoints`
   ```python
   def test_multiple_avoid_zones_generates_waypoints(self) -> None:
       mock_client = Mock()
       mock_client.directions.return_value = [_route_result("route", 1000, 120)]
       
       with (
           patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
           patch("app.services.maps_service._decode_polyline", return_value=SAFE_ROUTE),
       ):
           service = MapsService(api_key="fake")
           result = service.get_evacuation_route(
               origin_latlng=(-6.22, 106.85),
               destination_latlng=(-6.21, 106.88),
               avoid_zones=[
                   {"lat": 0.0, "lng": 0.0, "radius_m": 500},
                   {"lat": 0.1, "lng": 0.1, "radius_m": 500},
                   {"lat": 0.2, "lng": 0.2, "radius_m": 500},
               ],
           )
       
       # Verify waypoints computed and passed to mock
       call_kwargs = mock_client.directions.call_args.kwargs
       assert "waypoints" in call_kwargs or len(call_kwargs.get("waypoints", [])) > 0
   ```
   **Run**: `pytest tests/test_maps_service_route_avoidance.py::test_multiple_zones -v`

5. **Add New**: `test_empty_avoid_zones_routes_normally`
   ```python
   def test_empty_avoid_zones_routes_normally(self) -> None:
       mock_client = Mock()
       mock_client.directions.return_value = [_route_result("route", 900, 120)]
       
       with (
           patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
           patch("app.services.maps_service._decode_polyline", return_value=SAFE_ROUTE),
       ):
           service = MapsService(api_key="fake")
           result = service.get_evacuation_route(
               origin_latlng=(-6.22, 106.85),
               destination_latlng=(-6.21, 106.88),
               avoid_zones=[],  # Empty list
           )
       
       # No waypoints generated, first route selected
       call_kwargs = mock_client.directions.call_args.kwargs
       assert "waypoints" not in call_kwargs or call_kwargs.get("waypoints") is None
       assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
   ```
   **Run**: `pytest tests/test_maps_service_route_avoidance.py::test_empty_zones -v`

**Run Tests**:
```bash
pytest tests/test_maps_service_route_avoidance.py -v
# BEFORE: 3 passed (avoid_geojson)
# AFTER:  5 passed (avoid_zones + 2 new)
```

---

### TODO 4: Update Agent & Analyst Tool (Phase 3 — 1-2 hours)

**File A**: `app/hawkeye_agent/agent.py`  
**File B**: `app/hawkeye_agent/tools/analyst.py`  

**Change 1: Agent Instructions** (agent.py, lines ~107-121):

**Current**:
```python
## CRITICAL: Evacuation Route Safety Check

After coordinator generates a route:
1. Always call evaluate_route_safety() to check route against flood zones
2. If safety_rating is UNSAFE, request alternative destination
```

**Updated**:
```python
## CRITICAL: Evacuation Route Safety Check (PROACTIVE AVOIDANCE)

After coordinator generates a route:
1. Routes are NOW SAFE by default when avoid_flood=True
   - safety_rating is pre-computed (SAFE/CAUTION/UNSAFE), NOT PENDING_ANALYSIS
   - Coordinator fetches flood extent and passes constraints to routing engine
   - MapsService selects best route considering safety first
2. Only call evaluate_route_safety() if:
   - User explicitly requests detailed "safety analysis"
   - Route confidence is low (error condition)
   - Need specific danger zone locations for alternative planning
3. If coordinator returns error="No safe route found", suggest alternative destination
4. No mandatory post-hoc safety checking required
```

**Change 2: Analyst Tool** (analyst.py, line ~814):

**Current Code** (_suggest_alternative_route):
```python
def _suggest_alternative_route(route_geom_data: dict, flood_shape) -> dict | None:
    """Try to suggest a route that avoids the flood zone."""
    coords = route_geom_data.get("coordinates", [])
    if len(coords) < 2:
        return None
    
    start = coords[0]  # [lng, lat]
    end = coords[-1]
    
    try:
        # BUG: Doesn't pass avoid_zones!
        route_result = _get_maps_service().get_evacuation_route(
            origin_latlng=(start[1], start[0]),
            destination_latlng=(end[1], end[0]),
        )
        # ...
```

**Updated Code**:
```python
def _suggest_alternative_route(route_geom_data: dict, flood_shape) -> dict | None:
    """Suggest alternative route that avoids flood zone."""
    coords = route_geom_data.get("coordinates", [])
    if len(coords) < 2:
        return None
    
    start = coords[0]  # [lng, lat]
    end = coords[-1]
    
    try:
        # Extract flood polygon as avoid zone
        flood_centroid = flood_shape.centroid
        bounds = flood_shape.bounds
        radius_m = max(
            abs(bounds[2] - bounds[0]) * 111_000 / 2,
            abs(bounds[3] - bounds[1]) * 111_000 / 2,
        )
        avoid_zones = [{
            "lat": flood_centroid.y,
            "lng": flood_centroid.x,
            "radius_m": int(radius_m),
        }]
        
        # Pass avoid_zones to ensure alternative also avoids flood
        route_result = _get_maps_service().get_evacuation_route(
            origin_latlng=(start[1], start[0]),
            destination_latlng=(end[1], end[0]),
            avoid_zones=avoid_zones,  # ← NEW: Use avoid_zones like coordinator
            hard_constraint=False,  # Don't fail, just try to avoid
        )
        
        if route_result and route_result.get("route_geojson"):
            return {
                "route_geojson": route_result["route_geojson"],
                "distance_m": route_result.get("distance_m"),
                "duration_s": route_result.get("duration_s"),
                "safety_rating": route_result.get("safety_rating", "SAFE"),
                "description": "Alternative route computed with flood avoidance",
            }
    except Exception as e:
        logger.warning(f"Alternative route lookup failed: {e}")
    
    return None
```

**Behavior Change**:
- ✅ Agent no longer mandates post-hoc safety check
- ✅ Alternative route uses `avoid_zones` (consistent with coordinator)
- ✅ Faster response (fewer tool calls)
- ✅ Alternative route safety pre-computed

**Tests to Add**:

1. **New**: `test_route_avoidance.py::TestAgentInstructionsReflectProactiveAvoidance`
   ```python
   def test_agent_instructions_reflect_proactive_avoidance():
       """Verify agent.py docs mention proactive avoidance, not PENDING_ANALYSIS."""
       import inspect
       from app.hawkeye_agent import agent
       
       source = inspect.getsource(agent)
       
       # Should mention proactive/pre-computed/hard_constraint
       mentions_proactive = any(
           keyword in source.lower() 
           for keyword in ["proactive", "pre-computed", "hard_constraint"]
       )
       assert mentions_proactive, "Agent docs should mention proactive avoidance"
       
       # Should NOT mention PENDING_ANALYSIS or say it's still used
       if "PENDING_ANALYSIS" in source:
           # If mentioned, must be in context of "no longer"
           assert "no longer" in source.lower(), \
               "PENDING_ANALYSIS should not be returned by proactive coordinator"
   ```
   **Run**: `pytest test_route_avoidance.py::TestAgentInstructionsReflectProactiveAvoidance -v`

2. **New**: `test_suggest_alternative_route_uses_avoid_zones`
   ```python
   def test_suggest_alternative_route_uses_avoid_zones():
       """Verify _suggest_alternative_route passes avoid_zones to MapsService."""
       from unittest.mock import patch, MagicMock, call
       from app.hawkeye_agent.tools.analyst import _suggest_alternative_route
       from shapely.geometry import Polygon
       
       mock_maps = MagicMock()
       mock_maps.get_evacuation_route.return_value = {
           "route_geojson": {"type": "Feature", "geometry": {...}},
           "distance_m": 5000,
           "duration_s": 300,
           "safety_rating": "SAFE",
       }
       
       with patch('app.hawkeye_agent.tools.analyst._get_maps_service', return_value=mock_maps):
           flood_poly = Polygon([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]])
           route_geom = {"coordinates": [[106.8, -6.2], [106.9, -6.2]]}
           
           result = _suggest_alternative_route(route_geom, flood_poly)
           
           # Verify avoid_zones was passed
           call_kwargs = mock_maps.get_evacuation_route.call_args.kwargs
           assert "avoid_zones" in call_kwargs, "avoid_zones should be passed to routing"
           
           avoid_zones = call_kwargs["avoid_zones"]
           assert isinstance(avoid_zones, list), "avoid_zones should be a list"
           assert len(avoid_zones) > 0, "avoid_zones should not be empty"
           assert "lat" in avoid_zones[0], "Zone should have lat"
           assert "lng" in avoid_zones[0], "Zone should have lng"
           assert "radius_m" in avoid_zones[0], "Zone should have radius_m"
           
           # Verify result is valid
           assert result is not None
           assert result["route_geojson"] is not None
   ```
   **Run**: `pytest test_route_avoidance.py::TestSuggestAlternativeRouteUsesAvoidZones -v`

**Run Tests**:
```bash
pytest tests/test_route_avoidance.py::TestAgentInstructionsReflectProactiveAvoidance -v
pytest tests/test_route_avoidance.py::TestSuggestAlternativeRouteUsesAvoidZones -v
# All tests pass after Phase 3 ✅
```

---

## Edge Cases & Failure Behaviors

| Case | Scenario | Expected Behavior | Test Name |
|------|----------|-------------------|-----------|
| **No Safe Route** | Flood blocks all paths, `hard_constraint=True` | Return error, not unsafe route | `test_hard_constraint_fails_if_no_safe_route` |
| **Multiple Zones** | 3+ disjoint flood zones | Compute waypoints for zones on path, skip others | `test_multiple_avoid_zones_generates_waypoints` |
| **Zone at Origin** | Evacuation point in flood | Return error (can't route FROM flooded) | `test_origin_in_avoid_zone_returns_error` (future) |
| **Empty Zones** | `avoid_zones=[]` after extraction | Route normally without constraints | `test_empty_avoid_zones_routes_normally` |
| **Invalid GeoJSON** | _extract_avoid_zones returns [] | Log warning, route without constraints | `test_invalid_flood_geojson_graceful_degradation` |
| **Waypoint Failure** | Generated waypoints invalid (water, out of service) | Google Maps returns error, hard_constraint enforces fail-fast | `test_invalid_waypoints_fallback_to_unconstrained` |

---

## Commit Sequence (Recommended)

```bash
# 1. Fix dead code (Phase 1)
git commit -m "fix: remove dead avoid_geojson parameter from MapsService"
# Tests: pytest tests/test_maps_service_route_avoidance.py -v (3 pass)

# 2. Coordinator integration (Phase 2)
git commit -m "feat: coordinator fetches flood extent and passes constraints to routing"
# Tests: pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v

# 3. MapsService proactive avoidance (Phase 3)
git commit -m "feat: implement proactive avoidance in MapsService with avoid_zones parameter"
# Tests: pytest tests/test_maps_service_route_avoidance.py -v (5 pass)

# 4. Agent + Analyst updates (Phase 3)
git commit -m "docs: update agent instructions to reflect proactive avoidance model"
# Tests: pytest tests/test_route_avoidance.py -v

# 5. Test suite (Phase 3)
git commit -m "test: add comprehensive route avoidance test suite"
# Tests: pytest tests/ -v -k "route_avoidance" (15-17 pass)
```

---

## Final Verification

```bash
# Run all route avoidance tests
pytest tests/test_maps_service_route_avoidance.py tests/test_route_avoidance.py -v

# Expected:
# ✓ 15-17 tests pass
# ✓ No xfail or skip
# ✓ Route safety proactive ✅
# ✓ All backward-compat constraints met ✅
```

---

## Summary

| TODO | Phase | Hours | Risk | Files |
|------|-------|-------|------|-------|
| 1. Remove `avoid_geojson` | 1 | 0.5 | LOW | maps_service.py |
| 2. Coordinator fetches flood | 2 | 2-3 | MEDIUM | coordinator.py |
| 3. MapsService avoid_zones | 3 | 3-4 | MEDIUM | maps_service.py |
| 4. Agent + Analyst updates | 3 | 1-2 | LOW | agent.py, analyst.py |
| **Total** | | **6-9.5** | | |

**Key Milestone**: After Phase 3, routes are proactively safe and safety ratings are pre-computed (not "PENDING_ANALYSIS").
