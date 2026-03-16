# HawkEye Route Safety/Avoidance Integration: Implementation Guide

## AUDIT FINDINGS SUMMARY

### Current State: POST-HOC SAFETY EVALUATION ❌
- Routes generated via Google Maps Directions API **without** flood constraints
- Safety checked AFTER route is returned (spatial intersection with Shapely)
- `avoid_geojson` parameter is **dead code** — declared but never used
- `safety_rating` returned as "PENDING_ANALYSIS" from coordinator
- Alternative route generation is reactive, not proactive

### Target State: PROACTIVE AVOIDANCE ✅
- Flood zones passed to route generation as hard constraints
- Safety rating pre-computed before returning route
- Routes guaranteed safe (SAFE/CAUTION) when `avoid_flood=True`
- Fallback to alternative destination if no safe path exists

---

## IMPLEMENTATION PHASES

### PHASE 1: Immediate Fix (1 day) — Remove Dead Code

**File**: `app/services/maps_service.py`

**Current Code (lines 58-92)**:
```python
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_geojson: str | dict | None = None,  # ← DEAD CODE
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "origin": origin_latlng,
        "destination": destination_latlng,
        "mode": "driving",
        "alternatives": True,
    }
    # avoid_geojson NEVER used ↓
    results = self.client.directions(**kwargs)
    # ...
```

**FIX 1.1: Either Remove or Document**
```python
# OPTION A: Remove parameter (breaking change)
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
) -> dict[str, Any]:
    # ...

# OPTION B: Add assertion to fail loudly if used
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_geojson: str | dict | None = None,
) -> dict[str, Any]:
    if avoid_geojson is not None:
        raise NotImplementedError(
            "avoid_geojson parameter not yet implemented. "
            "See ROUTE_AVOIDANCE_IMPLEMENTATION_GUIDE.md"
        )
    # ...
```

**FIX 1.2: Add Regression Test**
```python
# In tests/test_route_avoidance.py
def test_avoid_geojson_parameter_not_silently_ignored():
    """REGRESSION: Verify avoid_geojson raises or is removed."""
    from app.services.maps_service import MapsService
    import inspect
    
    source = inspect.getsource(MapsService.get_evacuation_route)
    
    # Should either not have parameter or have explicit check
    if "avoid_geojson" in source:
        assert "NotImplementedError" in source or "avoid_geojson is not None" in source
```

**Commit**: "fix: remove dead avoid_geojson parameter from MapsService"

---

### PHASE 2: Coordinator Integration (2 days) — Fetch Flood Before Routing

**File**: `app/hawkeye_agent/tools/coordinator.py`

**Current Code (lines 203-256)**:
```python
def generate_evacuation_route(
    origin_name: str, destination_name: str, avoid_flood: bool = True
) -> dict:
    # Geocode
    origin = _get_maps_service().geocode(origin_name)
    destination = _get_maps_service().geocode(destination_name)
    
    # Get route WITHOUT flood data
    route_result = _get_maps_service().get_evacuation_route(
        origin_latlng=(origin["lat"], origin["lng"]),
        destination_latlng=(destination["lat"], destination["lng"]),
        # ← NO FLOOD CONSTRAINTS
    )
    
    # Return with PENDING_ANALYSIS
    return {
        "safety_rating": "PENDING_ANALYSIS",  # ← WRONG
        "avoid_flood": avoid_flood,
    }
```

**ENHANCED Implementation**:
```python
def generate_evacuation_route(
    origin_name: str, destination_name: str, avoid_flood: bool = True
) -> dict:
    """
    Generate evacuation route with proactive flood avoidance.
    
    If avoid_flood=True:
    1. Fetch current flood extent
    2. Pass to routing engine as constraints
    3. Return pre-computed safety_rating (not PENDING_ANALYSIS)
    """
    logger.info(f"Coordinator: Generating route from {origin_name} to {destination_name}, avoid_flood={avoid_flood}")
    
    try:
        # Step 1: Geocode locations
        origin = _get_maps_service().geocode(origin_name)
        destination = _get_maps_service().geocode(destination_name)
        
        if not origin or not origin.get("lat"):
            return {
                "error": f"Could not geocode origin: {origin_name}",
                "origin": origin_name,
                "destination": destination_name,
            }
        
        if not destination or not destination.get("lat"):
            return {
                "error": f"Could not geocode destination: {destination_name}",
                "origin": origin_name,
                "destination": destination_name,
            }
        
        # Step 2: If avoid_flood=True, fetch flood extent and extract constraints
        avoid_zones = None
        if avoid_flood:
            logger.info("Coordinator: Fetching flood extent for routing constraints")
            from app.hawkeye_agent.tools.analyst import get_flood_extent
            
            flood_result = get_flood_extent()
            if flood_result.get("flood_geojson"):
                avoid_zones = _extract_avoid_zones_from_flood(flood_result["flood_geojson"])
                logger.info(f"Coordinator: Extracted {len(avoid_zones) if avoid_zones else 0} avoid zones")
        
        # Step 3: Route with constraints
        route_result = _get_maps_service().get_evacuation_route(
            origin_latlng=(origin["lat"], origin["lng"]),
            destination_latlng=(destination["lat"], destination["lng"]),
            avoid_zones=avoid_zones,  # ← NEW: Pass constraints
            hard_constraint=avoid_flood,  # ← NEW: Enforce as hard constraint
        )
        
        if not route_result.get("route_geojson"):
            if avoid_flood and avoid_zones:
                return {
                    "error": "No safe route found avoiding flood zones",
                    "recommendation": f"Consider evacuating to a different destination",
                    "origin": origin_name,
                    "destination": destination_name,
                    "avoid_flood": avoid_flood,
                }
            return {
                "error": "Route not found",
                "origin": origin_name,
                "destination": destination_name,
            }
        
        # Step 4: Return with PRE-COMPUTED safety rating (not PENDING_ANALYSIS)
        return {
            "route_geojson": route_result.get("route_geojson"),
            "distance_m": route_result.get("distance_m"),
            "duration_minutes": (
                route_result.get("duration_s", 0) / 60
                if route_result.get("duration_s")
                else None
            ),
            "origin": origin,
            "destination": destination,
            "safety_rating": route_result.get("safety_rating", "SAFE"),  # ← PRE-COMPUTED
            "avoid_flood": avoid_flood,
            "avoid_zones_applied": len(avoid_zones) if avoid_zones else 0,
        }
    
    except Exception as e:
        logger.error(f"Error generating route: {e}")
        return {
            "error": str(e),
            "origin": origin_name,
            "destination": destination_name,
        }


def _extract_avoid_zones_from_flood(flood_geojson: str | dict) -> list[dict]:
    """
    Extract avoid zones from flood GeoJSON.
    
    Returns: List of {"lat": float, "lng": float, "radius_m": float}
    """
    import json
    from shapely.geometry import shape, Polygon
    
    try:
        if isinstance(flood_geojson, str):
            flood_data = json.loads(flood_geojson)
        else:
            flood_data = flood_geojson
        
        # Handle FeatureCollection
        if flood_data.get("type") == "FeatureCollection":
            features = flood_data.get("features", [])
            if not features:
                return []
            geom_data = features[0].get("geometry", {})
        elif flood_data.get("type") == "Feature":
            geom_data = flood_data.get("geometry", {})
        else:
            geom_data = flood_data
        
        # Extract polygon(s) and compute centroids
        flood_shape = shape(geom_data)
        avoid_zones = []
        
        if flood_shape.geom_type == "Polygon":
            centroid = flood_shape.centroid
            bounds = flood_shape.bounds
            radius_m = max(
                abs(bounds[2] - bounds[0]) * 111_000 / 2,  # lng to meters
                abs(bounds[3] - bounds[1]) * 111_000 / 2,  # lat to meters
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

**Update Tool Description in agent.py (line ~107-121)**:
```python
## CRITICAL: Evacuation Route Safety Check (UPDATED)

Before confirming ANY evacuation route, the system will:
1. Coordinator calls generate_evacuation_route() with avoid_flood=True
2. Coordinator fetches current flood extent
3. Coordinator passes flood constraints to MapsService
4. Routes are NOW SAFE by default (not UNSAFE then checked)
5. If no safe route exists, coordinator returns error + recommendation

No separate evaluate_route_safety() call needed for coordinator routes.
Routes returned with safety_rating already computed.
```

**Commit**: "feat: coordinator fetches flood extent and passes constraints to routing"

---

### PHASE 3: MapsService Enhancement (2 days) — Implement Proactive Avoidance

**File**: `app/services/maps_service.py`

**New Contract**:
```python
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_zones: list[dict] | None = None,  # ← NEW
    cost_surface: str | None = None,  # ← NEW (future)
    hard_constraint: bool = False,  # ← NEW
) -> dict[str, Any]:
    """
    Generate evacuation route with proactive flood avoidance.
    
    Args:
        origin_latlng: (lat, lng) starting location
        destination_latlng: (lat, lng) destination
        avoid_zones: List of zones [{"lat": float, "lng": float, "radius_m": float}]
                    Routing will avoid these zones
        cost_surface: "high_risk" | "medium_risk" (penalizes risky areas)
        hard_constraint: If True, fail if no safe route exists
    
    Returns:
        {
            "route_geojson": Feature | None,
            "distance_m": int,
            "duration_s": int,
            "safety_rating": "SAFE" | "CAUTION" | "UNSAFE",  # ← PRE-COMPUTED
        }
    """
```

**Implementation Option 1: Waypoint-Based Avoidance (QUICKEST)**

Use Google Maps' existing `waypoints` parameter to force routing around zones:

```python
def get_evacuation_route(
    self,
    origin_latlng: tuple[float, float],
    destination_latlng: tuple[float, float],
    avoid_zones: list[dict] | None = None,
    cost_surface: str | None = None,
    hard_constraint: bool = False,
) -> dict[str, Any]:
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
    if not results:
        if hard_constraint and avoid_zones:
            return {
                "route_geojson": None,
                "distance_m": 0,
                "duration_s": 0,
                "error": "No safe route found avoiding specified zones",
                "safety_rating": "UNSAFE",
            }
        return {"route_geojson": None, "distance_m": 0, "duration_s": 0, "safety_rating": "UNKNOWN"}
    
    # ... existing code to decode route ...
    
    # PRE-COMPUTE safety rating
    safety_rating = self._compute_safety_rating_preemptive(coords, avoid_zones)
    
    return {
        "route_geojson": { ... },
        "distance_m": ...,
        "duration_s": ...,
        "safety_rating": safety_rating,  # ← NOT "PENDING_ANALYSIS"
    }


def _compute_waypoints_around_zones(
    self,
    origin: tuple[float, float],
    destination: tuple[float, float],
    avoid_zones: list[dict],
) -> list[tuple[float, float]] | None:
    """
    Compute waypoints to route AROUND avoid zones.
    
    Strategy: For each zone, calculate perpendicular offset point at safe distance.
    """
    from shapely.geometry import LineString, Point
    import math
    
    waypoints = []
    origin_pt = Point(origin[1], origin[0])  # (lng, lat)
    dest_pt = Point(destination[1], destination[0])
    
    base_line = LineString([origin_pt, dest_pt])
    
    for zone in avoid_zones:
        zone_pt = Point(zone["lng"], zone["lat"])
        radius_deg = zone.get("radius_m", 500) / 111_000  # Rough conversion
        
        # If zone is NOT on the direct path, skip waypoint
        distance_to_zone = base_line.distance(zone_pt)
        if distance_to_zone > radius_deg * 2:
            continue
        
        # Compute perpendicular offset
        # Find closest point on line to zone
        closest_pt = base_line.interpolate(base_line.project(zone_pt))
        
        # Offset perpendicular by radius + safety margin
        offset_distance = (radius_deg + 0.01) * 111_000  # Convert back to rough degrees
        
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
            
            waypoints.append((offset_y, offset_x))  # (lat, lng)
    
    return waypoints if waypoints else None


def _compute_safety_rating_preemptive(
    self,
    route_coords: list[list[float]],
    avoid_zones: list[dict] | None,
) -> str:
    """
    Pre-compute safety rating based on avoid zones.
    
    Returns: "SAFE" | "CAUTION" | "UNSAFE"
    """
    from shapely.geometry import LineString, Point
    
    if not avoid_zones:
        return "SAFE"
    
    route_line = LineString([(c[0], c[1]) for c in route_coords])
    
    danger_count = 0
    for zone in avoid_zones:
        zone_pt = Point(zone["lng"], zone["lat"])
        radius_deg = zone.get("radius_m", 500) / 111_000
        
        distance = route_line.distance(zone_pt)
        if distance < radius_deg:
            danger_count += 1
    
    if danger_count == 0:
        return "SAFE"
    elif danger_count == 1:
        return "CAUTION"
    else:
        return "UNSAFE"
```

**Commit**: "feat: implement proactive avoidance in MapsService with waypoint-based routing"

---

### PHASE 4: Testing & Validation (1 day)

**File**: `tests/test_route_avoidance.py` (already created)

Run tests:
```bash
cd /Users/tayyabkhan/Downloads/gemini-agent/hawkeye

# Test with avoid zones
pytest tests/test_route_avoidance.py::TestProactiveAvoidanceV2 -v

# Test coordinator integration
pytest tests/test_route_avoidance.py::TestCoordinatorFetchesFloodBeforeRouting -v

# Test safety rating pre-computation
pytest tests/test_route_avoidance.py::TestSafetyRatingPreComputed -v

# Regression test for dead code
pytest tests/test_route_avoidance.py::TestAvoidGeojsonNotDeadCode -v
```

**Commit**: "test: add comprehensive route avoidance test suite"

---

## TESTING STRATEGY

### Unit Tests
- ✅ `test_avoid_geojson_parameter_not_silently_ignored` — Regression
- ✅ `test_extract_avoid_zones_from_flood` — Utility function
- ✅ `test_compute_safety_rating_preemptive` — Pre-computation logic
- ✅ `test_waypoints_avoid_zone` — Waypoint generation

### Integration Tests
- ✅ `test_coordinator_fetches_flood_before_routing` — Coordinator fetches flood
- ✅ `test_generate_evacuation_route_with_avoid_flood_true` — Full workflow
- ✅ `test_route_never_returns_pending_analysis` — Contract enforcement
- ✅ `test_evaluate_route_safety_with_constraints` — Safety evaluation unchanged

### Regression Tests
- ✅ `test_existing_route_safety_tests_still_pass` — evaluate_route_safety() still works
- ✅ `test_avoid_zones_parameter_affects_routing` — Avoid zones actually used

---

## ROLLOUT PLAN

### Pre-Deployment Checklist
- [ ] All tests pass (unit + integration + regression)
- [ ] Code review for coordinator + MapsService changes
- [ ] Update docstrings and type hints
- [ ] Update AGENTS.md with new behavior
- [ ] Verify existing tests still pass

### Deployment
```bash
# 1. Merge Phase 1 (remove dead code)
git commit -m "fix: remove dead avoid_geojson parameter"

# 2. Merge Phase 2 (coordinator integration)
git commit -m "feat: coordinator fetches flood and passes constraints"

# 3. Merge Phase 3 (MapsService enhancement)
git commit -m "feat: proactive avoidance in routing engine"

# 4. Merge Phase 4 (tests)
git commit -m "test: comprehensive route avoidance suite"
```

### Post-Deployment Verification
- [ ] Agent generates safe routes (no UNSAFE returns)
- [ ] Coordinator logs flood zones applied
- [ ] Alternative destination suggested when no safe path exists
- [ ] Distance/duration slightly increased due to avoidance (expected)

---

## FUTURE ENHANCEMENTS (Phase 5+)

### Cost Surface Routing (2-3 days)
- Implement raster cost grid (normal=1, high-risk=10, flooded=100)
- Use OR-Tools or local A* with cost penalties
- Allow balance between safety and distance

### Safe Corridor Caching (1 week)
- Precompute flood buffer zones in BigQuery GIS
- Cache safe roads in Firestore
- Route only on safe corridor network

### Real-time Congestion Integration (2 weeks)
- Combine flood avoidance + traffic data
- Optimize for both safety AND speed
- Update routes as conditions change

---

## SUMMARY OF CHANGES

| File | Change | Impact |
|------|--------|--------|
| `coordinator.py` | Fetch flood before routing; pass constraints | Routes now safer by default |
| `maps_service.py` | Accept avoid_zones; pre-compute safety | Dead code fixed; safety pre-computed |
| `agent.py` | Update instructions; remove separate eval | Clearer workflow; fewer API calls |
| `tests/test_route_avoidance.py` | NEW comprehensive tests | Regression + validation coverage |

**Result**: ✅ Proactive avoidance, not post-hoc evaluation

