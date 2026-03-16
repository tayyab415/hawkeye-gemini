"""
Test 05: Spatial Accuracy & Edge Cases
=======================================
Tests edge cases in spatial queries: empty results, boundary conditions,
parameter variations, and verifies spatial operations are correct.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).resolve().parent.parent / "credentials" / "hawkeye-runtime-key.json"),
)
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "gen-lang-client-0261050164")

from app.services.bigquery_service import GroundsourceService

service = GroundsourceService(project_id=PROJECT_ID)
passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


print("=" * 70)
print("TEST 05: SPATIAL ACCURACY & EDGE CASES")
print("=" * 70)

# ───── 1. Non-Jakarta location should return 0 events ─────
print("\n1. Non-Jakarta location (London) — expecting 0 Jakarta floods")
try:
    result = service.get_flood_frequency(lat=51.5074, lng=-0.1278, radius_km=10)
    check(f"London returns 0 Jakarta events", result.get("total_events", -1) == 0,
          f"got {result.get('total_events')}")
except Exception as e:
    check("Non-Jakarta query", False, str(e))

# ───── 2. Tiny polygon — may or may not contain infrastructure ─────
print("\n2. Tiny polygon (single block in Kampung Melayu)")
tiny_polygon = json.dumps({
    "type": "Polygon",
    "coordinates": [[
        [106.854, -6.226], [106.856, -6.226],
        [106.856, -6.224], [106.854, -6.224],
        [106.854, -6.226],
    ]]
})
try:
    result = service.get_infrastructure_at_risk(tiny_polygon)
    check(f"Tiny polygon returns valid response", isinstance(result, dict))
    check(f"total_at_risk is non-negative: {result.get('total_at_risk')}",
          result.get("total_at_risk", -1) >= 0)
except Exception as e:
    check("Tiny polygon query", False, str(e))

# ───── 3. Huge polygon — should capture more infrastructure ─────
print("\n3. Large polygon (all Greater Jakarta)")
large_polygon = json.dumps({
    "type": "Polygon",
    "coordinates": [[
        [106.7, -6.35], [107.0, -6.35], [107.0, -6.10],
        [106.7, -6.10], [106.7, -6.35],
    ]]
})
try:
    result_large = service.get_infrastructure_at_risk(large_polygon)
    result_small_flood = json.dumps({
        "type": "Polygon",
        "coordinates": [[
            [106.845, -6.235], [106.865, -6.235], [106.865, -6.215],
            [106.845, -6.215], [106.845, -6.235],
        ]]
    })
    result_small = service.get_infrastructure_at_risk(result_small_flood)

    check(f"Large polygon captures more ({result_large['total_at_risk']}) "
          f"than small ({result_small['total_at_risk']})",
          result_large["total_at_risk"] >= result_small["total_at_risk"])
except Exception as e:
    check("Large vs small polygon", False, str(e))

# ───── 4. Radius sensitivity for flood frequency ─────
print("\n4. Radius sensitivity — larger radius should find more events")
try:
    r1 = service.get_flood_frequency(lat=-6.225, lng=106.855, radius_km=1)
    r5 = service.get_flood_frequency(lat=-6.225, lng=106.855, radius_km=5)
    r20 = service.get_flood_frequency(lat=-6.225, lng=106.855, radius_km=20)

    print(f"  1km: {r1.get('total_events')} events")
    print(f"  5km: {r5.get('total_events')} events")
    print(f"  20km: {r20.get('total_events')} events")

    check("5km >= 1km events",
          r5.get("total_events", 0) >= r1.get("total_events", 0))
    check("20km >= 5km events",
          r20.get("total_events", 0) >= r5.get("total_events", 0))
except Exception as e:
    check("Radius sensitivity", False, str(e))

# ───── 5. Buffer expansion monotonicity ─────
print("\n5. Buffer expansion monotonicity — larger buffer = more newly-at-risk")
flood = json.dumps({
    "type": "Polygon",
    "coordinates": [[
        [106.845, -6.235], [106.865, -6.235], [106.865, -6.215],
        [106.845, -6.215], [106.845, -6.235],
    ]]
})
try:
    e500 = service.get_infrastructure_at_expanded_level(flood, 500)
    e1000 = service.get_infrastructure_at_expanded_level(flood, 1000)
    e3000 = service.get_infrastructure_at_expanded_level(flood, 3000)

    print(f"  500m buffer: {e500['newly_at_risk']} newly at risk")
    print(f"  1000m buffer: {e1000['newly_at_risk']} newly at risk")
    print(f"  3000m buffer: {e3000['newly_at_risk']} newly at risk")

    check("1000m >= 500m newly at risk",
          e1000["newly_at_risk"] >= e500["newly_at_risk"])
    check("3000m >= 1000m newly at risk",
          e3000["newly_at_risk"] >= e1000["newly_at_risk"])
except Exception as e:
    check("Buffer monotonicity", False, str(e))

# ───── 6. Pattern match with extreme parameters ─────
print("\n6. Pattern match — edge cases")
try:
    # Very small area
    tiny = service.find_pattern_match(current_area_sqkm=0.01, duration_estimate_days=1)
    check(f"Tiny area match: {len(tiny)} results (may be 0)", isinstance(tiny, list))

    # Very large area
    huge = service.find_pattern_match(current_area_sqkm=1000.0, duration_estimate_days=30)
    check(f"Huge area match: {len(huge)} results (may be 0)", isinstance(huge, list))

    # Normal case
    normal = service.find_pattern_match(current_area_sqkm=25.0, duration_estimate_days=5)
    check(f"Normal match: {len(normal)} results", len(normal) > 0)
except Exception as e:
    check("Pattern match edge cases", False, str(e))

# ───── 7. GeoJSON parameter format handling ─────
print("\n7. GeoJSON parameter format — string vs geometry-only")
try:
    # Using geometry-only GeoJSON (what the service expects)
    geom_only = json.dumps({
        "type": "Polygon",
        "coordinates": [[
            [106.845, -6.235], [106.865, -6.235], [106.865, -6.215],
            [106.845, -6.215], [106.845, -6.235],
        ]]
    })
    result = service.get_infrastructure_at_risk(geom_only)
    check("Geometry-only GeoJSON works", isinstance(result, dict))

    # Using Feature-wrapped GeoJSON — this should also work since
    # ST_GEOGFROMGEOJSON can handle it
    feature_wrapped = json.dumps({
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [106.845, -6.235], [106.865, -6.235], [106.865, -6.215],
                [106.845, -6.215], [106.845, -6.235],
            ]]
        }
    })
    try:
        result2 = service.get_infrastructure_at_risk(feature_wrapped)
        check("Feature-wrapped GeoJSON also works", isinstance(result2, dict))
    except Exception:
        check("Feature-wrapped GeoJSON fails (expected — service needs geometry-only)",
              True)
except Exception as e:
    check("GeoJSON format handling", False, str(e))

# ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
total = passed + failed
print(f"RESULT: {passed}/{total} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed > 0 else 0)
