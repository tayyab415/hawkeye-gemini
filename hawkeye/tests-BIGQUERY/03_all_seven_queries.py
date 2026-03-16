"""
Test 03: All Seven Queries (via GroundsourceService)
=====================================================
Runs every query from HawkEye_BigQuery_Specification.md Phase 3
through the actual GroundsourceService Python class.
This is the most important test — it validates end-to-end from
Python → BigQuery → results.
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

# Kampung Melayu test polygon (from flood_extent.geojson)
FLOOD_GEOJSON = json.dumps({
    "type": "Polygon",
    "coordinates": [[
        [106.838, -6.205], [106.855, -6.203], [106.868, -6.206],
        [106.874, -6.220], [106.875, -6.234], [106.867, -6.245],
        [106.856, -6.252], [106.843, -6.255], [106.832, -6.249],
        [106.826, -6.238], [106.825, -6.224], [106.828, -6.212],
        [106.838, -6.205],
    ]]
})


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


print("=" * 70)
print("TEST 03: ALL SEVEN QUERIES VIA GroundsourceService")
print("=" * 70)

# ────────────────────────────────────────────────────────────
# QUERY 1: Historical floods intersecting polygon
# ────────────────────────────────────────────────────────────
print("\n─── Query 1: query_floods_intersecting_polygon ───")
try:
    results = service.query_floods_intersecting_polygon(FLOOD_GEOJSON)
    check(f"Returns list ({len(results)} results)", isinstance(results, list))
    check("At least 1 result", len(results) >= 1, f"got {len(results)}")

    if results:
        r = results[0]
        check("Has start_date", "start_date" in r)
        check("Has end_date", "end_date" in r)
        check("Has duration_days", "duration_days" in r)
        check("Has area_sqkm", "area_sqkm" in r)
        check("Has geometry_geojson", "geometry_geojson" in r)
        check("Has overlap_sqkm", "overlap_sqkm" in r)
        check("overlap_sqkm > 0", r.get("overlap_sqkm", 0) > 0,
              f"got {r.get('overlap_sqkm')}")

        print(f"\n  Sample: date={r.get('start_date')}, "
              f"duration={r.get('duration_days')}d, "
              f"area={r.get('area_sqkm')}km², "
              f"overlap={r.get('overlap_sqkm')}km²")
except Exception as e:
    check("Query 1 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 2: Flood frequency for a location
# ────────────────────────────────────────────────────────────
print("\n─── Query 2: get_flood_frequency ───")
try:
    result = service.get_flood_frequency(lat=-6.225, lng=106.855, radius_km=10)
    check("Returns dict", isinstance(result, dict))
    check("Has total_events", "total_events" in result)
    check("total_events > 0", result.get("total_events", 0) > 0,
          f"got {result.get('total_events')}")
    check("Has avg_duration_days", "avg_duration_days" in result)
    check("Has max_duration_days", "max_duration_days" in result)
    check("Has earliest_event", "earliest_event" in result)
    check("Has latest_event", "latest_event" in result)
    check("Has avg_area_sqkm", "avg_area_sqkm" in result)
    check("Has max_area_sqkm", "max_area_sqkm" in result)

    print(f"\n  Events: {result.get('total_events')}, "
          f"avg_dur: {result.get('avg_duration_days')}d, "
          f"range: {result.get('earliest_event')} to {result.get('latest_event')}")
except Exception as e:
    check("Query 2 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 3: Infrastructure at risk
# ────────────────────────────────────────────────────────────
print("\n─── Query 3: get_infrastructure_at_risk ───")
try:
    result = service.get_infrastructure_at_risk(FLOOD_GEOJSON)
    check("Returns dict", isinstance(result, dict))
    check("Has total_at_risk", "total_at_risk" in result)
    check("Has hospitals list", isinstance(result.get("hospitals"), list))
    check("Has schools list", isinstance(result.get("schools"), list))
    check("Has shelters list", isinstance(result.get("shelters"), list))
    check("Has power_stations list", isinstance(result.get("power_stations"), list))

    total = result.get("total_at_risk", 0)
    hosp = len(result.get("hospitals", []))
    sch = len(result.get("schools", []))
    shelt = len(result.get("shelters", []))
    pwr = len(result.get("power_stations", []))
    wtr = len(result.get("water_treatment", []))
    print(f"\n  Total at risk: {total}")
    print(f"  Hospitals: {hosp}, Schools: {sch}, Shelters: {shelt}, "
          f"Power: {pwr}, Water: {wtr}")

    # Verify grouped counts match total
    grouped_total = hosp + sch + shelt + pwr + wtr
    check("Grouped counts match total_at_risk", grouped_total == total,
          f"grouped={grouped_total}, total={total}")

    # Check individual entries have required fields
    if result.get("hospitals"):
        h = result["hospitals"][0]
        check("Hospital has name", "name" in h)
        check("Hospital has latitude", "latitude" in h)
        check("Hospital has longitude", "longitude" in h)
        check("Hospital has distance_to_flood_edge_m", "distance_to_flood_edge_m" in h)
except Exception as e:
    check("Query 3 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 4: Infrastructure at expanded level
# ────────────────────────────────────────────────────────────
print("\n─── Query 4: get_infrastructure_at_expanded_level ───")
try:
    result = service.get_infrastructure_at_expanded_level(FLOOD_GEOJSON, 1000)
    check("Returns dict", isinstance(result, dict))
    check("Has newly_at_risk", "newly_at_risk" in result)

    newly = result.get("newly_at_risk", 0)
    print(f"\n  Newly at risk (+1km buffer): {newly}")
    print(f"  New hospitals: {len(result.get('hospitals', []))}, "
          f"schools: {len(result.get('schools', []))}")

    # Compare with base (query 3)
    base = service.get_infrastructure_at_risk(FLOOD_GEOJSON)
    base_names = {h["name"] for h in base.get("hospitals", [])}
    expanded_names = {h["name"] for h in result.get("hospitals", [])}
    overlap = base_names & expanded_names
    check("No overlap between base and newly-at-risk hospitals",
          len(overlap) == 0, f"overlap: {overlap}")

    # Test with larger buffer
    result_2k = service.get_infrastructure_at_expanded_level(FLOOD_GEOJSON, 2000)
    check("Larger buffer finds more or equal results",
          result_2k.get("newly_at_risk", 0) >= result.get("newly_at_risk", 0),
          f"2km={result_2k.get('newly_at_risk')} vs 1km={result.get('newly_at_risk')}")
except Exception as e:
    check("Query 4 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 5: Pattern match
# ────────────────────────────────────────────────────────────
print("\n─── Query 5: find_pattern_match ───")
try:
    results = service.find_pattern_match(current_area_sqkm=15.0, duration_estimate_days=4)
    check(f"Returns list ({len(results)} results)", isinstance(results, list))
    check("At least 1 match", len(results) >= 1, f"got {len(results)}")
    check("At most 5 matches (LIMIT)", len(results) <= 5)

    if results:
        r = results[0]
        check("Has start_date", "start_date" in r)
        check("Has end_date", "end_date" in r)
        check("Has duration_days", "duration_days" in r)
        check("Has area_sqkm", "area_sqkm" in r)
        check("Has geometry_geojson", "geometry_geojson" in r)

        print(f"\n  Best match: date={r.get('start_date')}, "
              f"dur={r.get('duration_days')}d, area={r.get('area_sqkm')}km²")

        # Verify results are within range bounds
        for i, r in enumerate(results):
            dur_ok = max(1, 4 - 5) <= r["duration_days"] <= (4 + 10)
            area_ok = 15.0 * 0.3 <= r["area_sqkm"] <= 15.0 * 3.0
            if not dur_ok or not area_ok:
                check(f"Result {i} within range bounds", False,
                      f"dur={r['duration_days']}, area={r['area_sqkm']}")
except Exception as e:
    check("Query 5 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 6: Monthly frequency
# ────────────────────────────────────────────────────────────
print("\n─── Query 6: get_monthly_frequency ───")
try:
    results = service.get_monthly_frequency()
    check(f"Returns list ({len(results)} results)", isinstance(results, list))
    check("Has 12 months", len(results) == 12, f"got {len(results)}")

    months_seen = set()
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for r in results:
        months_seen.add(r["month"])
        check(f"{month_names[r['month']]}: {r['flood_count']} floods, avg {r['avg_duration']}d", True)

    check("All 12 months present", months_seen == set(range(1, 13)),
          f"missing months: {set(range(1, 13)) - months_seen}")

    # Monsoon check (Nov-Feb should be higher than Jun-Sep)
    by_month = {r["month"]: r["flood_count"] for r in results}
    monsoon = sum(by_month.get(m, 0) for m in [11, 12, 1, 2])
    dry = sum(by_month.get(m, 0) for m in [6, 7, 8, 9])
    check(f"Monsoon season ({monsoon}) > Dry season ({dry})",
          monsoon > dry, f"monsoon={monsoon}, dry={dry}")

    # Test caching
    results2 = service.get_monthly_frequency()
    check("Caching works (same object returned)", results is results2)
except Exception as e:
    check("Query 6 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# QUERY 7: Yearly trend
# ────────────────────────────────────────────────────────────
print("\n─── Query 7: get_yearly_trend ───")
try:
    results = service.get_yearly_trend()
    check(f"Returns list ({len(results)} results)", isinstance(results, list))
    check("Covers multiple years", len(results) >= 2)

    if results:
        print(f"\n  Year range: {results[0].get('year')} to {results[-1].get('year')}")
        for r in results[-5:]:
            print(f"    {r.get('year')}: {r.get('flood_count')} floods, avg {r.get('avg_area')} km²")

        r = results[0]
        check("Has year field", "year" in r)
        check("Has flood_count field", "flood_count" in r)
        check("Has avg_area field", "avg_area" in r)

    # Test caching
    results2 = service.get_yearly_trend()
    check("Caching works (same object returned)", results is results2)
except Exception as e:
    check("Query 7 execution", False, str(e))

# ────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
total = passed + failed
print(f"RESULT: {passed}/{total} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed > 0 else 0)
