"""
Test 04: Cascade Integration (End-to-End)
==========================================
Tests the compute_cascade tool function which is the core differentiator.
This calls BigQuery (infrastructure at risk + expanded levels) and
Earth Engine (population) to produce a multi-order cascade analysis.
Matches Phase 5 of HawkEye_BigQuery_Specification.md.
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
os.environ.setdefault("GCP_PROJECT_ID", "gen-lang-client-0261050164")

from app.services.bigquery_service import GroundsourceService
from app.services.earth_engine_service import EarthEngineService

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
passed = 0
failed = 0

# Use the synthetic flood extent from data/geojson
FLOOD_GEOJSON_PATH = Path(__file__).resolve().parent.parent / "data" / "geojson" / "flood_extent.geojson"


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


print("=" * 70)
print("TEST 04: CASCADE INTEGRATION")
print("=" * 70)

# Load the actual flood extent GeoJSON (geometry only)
with open(FLOOD_GEOJSON_PATH) as f:
    flood_feature = json.load(f)
flood_geometry_str = json.dumps(flood_feature["geometry"])

bq = GroundsourceService(project_id=PROJECT_ID)
ee = EarthEngineService(project_id=PROJECT_ID)

# ───── Order 0: Current flood extent ─────
print("\n─── Order 0: Current Flood Extent ───")
try:
    area = ee.get_flood_area_sqkm()
    check(f"Flood area: {area} km²", area > 0)

    pop = ee.get_population_at_risk()
    check(f"Population at risk: {pop['total']:,}", pop["total"] > 0)
    check(f"Children under 5: {pop['children_under_5']:,}", pop["children_under_5"] > 0)
    check(f"Elderly over 65: {pop['elderly_over_65']:,}", pop["elderly_over_65"] > 0)
except Exception as e:
    check("Order 0 data", False, str(e))

# ───── Order 1: Infrastructure inside current flood ─────
print("\n─── Order 1: Infrastructure at Risk (Current) ───")
try:
    infra = bq.get_infrastructure_at_risk(flood_geometry_str)
    check(f"Total at risk: {infra['total_at_risk']}", infra["total_at_risk"] >= 0)

    print(f"  Hospitals: {len(infra.get('hospitals', []))}")
    print(f"  Schools: {len(infra.get('schools', []))}")
    print(f"  Shelters: {len(infra.get('shelters', []))}")
    print(f"  Power: {len(infra.get('power_stations', []))}")
    print(f"  Water: {len(infra.get('water_treatment', []))}")

    for h in infra.get("hospitals", [])[:3]:
        print(f"    → {h['name']} ({h.get('distance_to_flood_edge_m', '?')}m from edge)")
except Exception as e:
    check("Order 1 query", False, str(e))

# ───── Order 2: +1m expansion ─────
print("\n─── Order 2: Expansion +1m (500m buffer) ───")
try:
    expanded_1 = bq.get_infrastructure_at_expanded_level(flood_geometry_str, 500)
    check(f"Newly at risk (+1m): {expanded_1['newly_at_risk']}", True)
    print(f"  New hospitals: {len(expanded_1.get('hospitals', []))}")
    print(f"  New schools: {len(expanded_1.get('schools', []))}")
except Exception as e:
    check("Order 2 query", False, str(e))

# ───── Order 3: +2m expansion ─────
print("\n─── Order 3: Expansion +2m (1000m buffer) ───")
try:
    expanded_2 = bq.get_infrastructure_at_expanded_level(flood_geometry_str, 1000)
    check(f"Newly at risk (+2m): {expanded_2['newly_at_risk']}", True)
    print(f"  New hospitals: {len(expanded_2.get('hospitals', []))}")
    print(f"  New schools: {len(expanded_2.get('schools', []))}")

    # Cumulative assessment
    total_hosp = (len(infra.get("hospitals", [])) +
                  len(expanded_1.get("hospitals", [])) +
                  len(expanded_2.get("hospitals", [])))
    total_sch = (len(infra.get("schools", [])) +
                 len(expanded_1.get("schools", [])) +
                 len(expanded_2.get("schools", [])))
    print(f"\n  Cumulative hospitals at risk: {total_hosp}")
    print(f"  Cumulative schools at risk: {total_sch}")
except Exception as e:
    check("Order 3 query", False, str(e))

# ───── Historical pattern match ─────
print("\n─── Historical Pattern Match ───")
try:
    area = ee.get_flood_area_sqkm()
    matches = bq.find_pattern_match(current_area_sqkm=area, duration_estimate_days=3)
    check(f"Found {len(matches)} historical matches for area={area}km²", len(matches) > 0)

    for m in matches[:3]:
        print(f"  → {m.get('start_date')} to {m.get('end_date')}: "
              f"dur={m.get('duration_days')}d, area={m.get('area_sqkm')}km²")
except Exception as e:
    check("Pattern match", False, str(e))

# ───── Full cascade via analyst tool ─────
print("\n─── Full Cascade via compute_cascade tool ───")
try:
    from app.hawkeye_agent.tools.analyst import compute_cascade

    cascade = compute_cascade(flood_geometry_str, water_level_delta_m=2.0)
    check("cascade returns dict", isinstance(cascade, dict))
    check("Has 'first_order' key", "first_order" in cascade)
    check("Has 'second_order' key", "second_order" in cascade)
    check("Has 'third_order' key", "third_order" in cascade)
    check("Has 'fourth_order' key", "fourth_order" in cascade)
    check("Has 'summary' key", "summary" in cascade)
    check("Has 'recommendation' key", "recommendation" in cascade)

    # Validate first_order
    fo = cascade.get("first_order", {})
    check("first_order has population_at_risk",
          "population_at_risk" in fo, str(fo.keys()))
    check("population_at_risk > 0",
          fo.get("population_at_risk", 0) > 0)
    print(f"\n  1st: pop={fo.get('population_at_risk'):,}")

    # Validate second_order
    so = cascade.get("second_order", {})
    check("second_order has hospitals_at_risk", "hospitals_at_risk" in so)
    print(f"  2nd: {so.get('hospitals_at_risk')} hospitals, "
          f"{so.get('schools_at_risk')} schools")

    # Validate third_order
    to = cascade.get("third_order", {})
    check("third_order has power_stations_at_risk", "power_stations_at_risk" in to)
    print(f"  3rd: {to.get('power_stations_at_risk')} power stations, "
          f"{to.get('estimated_residents_without_power'):,} without power")

    # Validate fourth_order
    fo4 = cascade.get("fourth_order", {})
    check("fourth_order has children_under_5", "children_under_5" in fo4)
    print(f"  4th: {fo4.get('children_under_5'):,} children, "
          f"{fo4.get('elderly_over_65'):,} elderly")

    print(f"\n  Summary: {cascade.get('summary', '')[:200]}...")
    print(f"  Recommendation: {cascade.get('recommendation', '')[:200]}")
except Exception as e:
    check("compute_cascade tool", False, str(e))

# ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
total = passed + failed
print(f"RESULT: {passed}/{total} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed > 0 else 0)
