"""
BigQuery End-to-End Validation Script
Validates all GroundsourceService methods return correct, meaningful data.

Run this after Step 0 BigQuery setup to verify data integrity.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.bigquery_service import GroundsourceService

# Test coordinates (Kampung Melayu, Jakarta)
TEST_LAT = -6.225
TEST_LNG = 106.855
TEST_RADIUS_KM = 10

# Test flood GeoJSON (simple polygon around Kampung Melayu)
TEST_FLOOD_GEOJSON = json.dumps({
    "type": "Polygon",
    "coordinates": [[
        [106.845, -6.235],
        [106.865, -6.235],
        [106.865, -6.215],
        [106.845, -6.215],
        [106.845, -6.235],
    ]]
})


def validate_flood_frequency(service: GroundsourceService) -> dict:
    """Validate Query 2: Flood frequency stats."""
    print("\n" + "=" * 60)
    print("TEST 1: Flood Frequency Query")
    print("=" * 60)
    
    result = service.get_flood_frequency(TEST_LAT, TEST_LNG, TEST_RADIUS_KM)
    
    checks = {
        "has_total_events": result.get("total_events") is not None,
        "total_events_positive": result.get("total_events", 0) > 0,
        "has_avg_duration": result.get("avg_duration_days") is not None,
        "has_earliest_event": result.get("earliest_event") is not None,
        "has_latest_event": result.get("latest_event") is not None,
    }
    
    print(f"Location: ({TEST_LAT}, {TEST_LNG}), radius={TEST_RADIUS_KM}km")
    print(f"Total Events: {result.get('total_events')}")
    print(f"Avg Duration: {result.get('avg_duration_days')} days")
    print(f"Max Duration: {result.get('max_duration_days')} days")
    print(f"Earliest: {result.get('earliest_event')}")
    print(f"Latest: {result.get('latest_event')}")
    print(f"Avg Area: {result.get('avg_area_sqkm')} km²")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "flood_frequency",
        "passed": passed,
        "checks": checks,
        "data": result,
    }


def validate_infrastructure_at_risk(service: GroundsourceService) -> dict:
    """Validate Query 3: Infrastructure at risk."""
    print("\n" + "=" * 60)
    print("TEST 2: Infrastructure at Risk")
    print("=" * 60)
    
    result = service.get_infrastructure_at_risk(TEST_FLOOD_GEOJSON)
    
    total = result.get("total_at_risk", 0)
    hospitals = result.get("hospitals", [])
    schools = result.get("schools", [])
    
    checks = {
        "has_total": total >= 0,
        "returns_hospitals_or_schools": len(hospitals) > 0 or len(schools) > 0,
        "hospitals_is_list": isinstance(hospitals, list),
        "schools_is_list": isinstance(schools, list),
    }
    
    print(f"Total at risk: {total}")
    print(f"Hospitals: {len(hospitals)}")
    print(f"Schools: {len(schools)}")
    print(f"Shelters: {len(result.get('shelters', []))}")
    print(f"Power Stations: {len(result.get('power_stations', []))}")
    
    if hospitals:
        print(f"\nSample hospitals:")
        for h in hospitals[:3]:
            print(f"  - {h.get('name')} ({h.get('type')})")
    
    if schools:
        print(f"\nSample schools:")
        for s in schools[:3]:
            print(f"  - {s.get('name')} ({s.get('type')})")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "infrastructure_at_risk",
        "passed": passed,
        "checks": checks,
        "data": {
            "total_at_risk": total,
            "hospitals_count": len(hospitals),
            "schools_count": len(schools),
        },
    }


def validate_infrastructure_expanded(service: GroundsourceService) -> dict:
    """Validate Query 4: Infrastructure at expanded level."""
    print("\n" + "=" * 60)
    print("TEST 3: Infrastructure at Expanded Level (+1m)")
    print("=" * 60)
    
    # Get current
    current = service.get_infrastructure_at_risk(TEST_FLOOD_GEOJSON)
    
    # Get expanded (1m rise = 500m buffer)
    expanded = service.get_infrastructure_at_expanded_level(TEST_FLOOD_GEOJSON, 500)
    
    checks = {
        "returns_newly_at_risk": "newly_at_risk" in expanded,
        "newly_at_risk_different": expanded.get("newly_at_risk", 0) != current.get("total_at_risk", 0),
    }
    
    print(f"Current at risk: {current.get('total_at_risk')}")
    print(f"Newly at risk (+1m): {expanded.get('newly_at_risk')}")
    print(f"New hospitals: {len(expanded.get('hospitals', []))}")
    print(f"New schools: {len(expanded.get('schools', []))}")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "infrastructure_expanded",
        "passed": passed,
        "checks": checks,
        "data": {
            "current": current.get("total_at_risk"),
            "newly_at_risk": expanded.get("newly_at_risk"),
        },
    }


def validate_pattern_match(service: GroundsourceService) -> dict:
    """Validate Query 5: Pattern matching."""
    print("\n" + "=" * 60)
    print("TEST 4: Pattern Match")
    print("=" * 60)
    
    result = service.find_pattern_match(current_area_sqkm=15.0, duration_estimate_days=4)
    
    checks = {
        "returns_list": isinstance(result, list),
        "has_matches": len(result) > 0,
        "has_start_date": len(result) > 0 and result[0].get("start_date") is not None,
        "has_area_sqkm": len(result) > 0 and result[0].get("area_sqkm") is not None,
    }
    
    print(f"Found {len(result)} similar historical events")
    
    for i, event in enumerate(result[:3]):
        print(f"\nMatch {i+1}:")
        print(f"  Date: {event.get('start_date')} to {event.get('end_date')}")
        print(f"  Duration: {event.get('duration_days')} days")
        print(f"  Area: {event.get('area_sqkm')} km²")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "pattern_match",
        "passed": passed,
        "checks": checks,
        "data": {
            "matches_found": len(result),
            "first_match": result[0] if result else None,
        },
    }


def validate_monthly_frequency(service: GroundsourceService) -> dict:
    """Validate Query 6: Monthly frequency."""
    print("\n" + "=" * 60)
    print("TEST 5: Monthly Frequency")
    print("=" * 60)
    
    result = service.get_monthly_frequency()
    
    checks = {
        "returns_12_months": len(result) == 12,
        "has_monsoon_peak": any(r.get("month") in [11, 12, 1, 2] and r.get("flood_count", 0) > 0 for r in result),
    }
    
    # Find peak months
    sorted_by_count = sorted(result, key=lambda x: x.get("flood_count", 0), reverse=True)
    
    print("Monthly flood counts:")
    for r in result:
        month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        month_name = month_names[int(r.get("month", 0))]
        print(f"  {month_name}: {r.get('flood_count')} events (avg {r.get('avg_duration')} days)")
    
    print(f"\nPeak months: {[r.get('month') for r in sorted_by_count[:3]]}")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "monthly_frequency",
        "passed": passed,
        "checks": checks,
        "data": {
            "months": len(result),
            "peak_months": [r.get("month") for r in sorted_by_count[:3]],
        },
    }


def validate_yearly_trend(service: GroundsourceService) -> dict:
    """Validate Query 7: Yearly trend."""
    print("\n" + "=" * 60)
    print("TEST 6: Yearly Trend")
    print("=" * 60)
    
    result = service.get_yearly_trend()
    
    checks = {
        "returns_multiple_years": len(result) > 5,
        "has_year_data": len(result) > 0 and result[0].get("year") is not None,
        "has_flood_counts": len(result) > 0 and result[0].get("flood_count") is not None,
    }
    
    print(f"Data available for {len(result)} years")
    print("\nRecent years:")
    for r in result[-5:]:
        print(f"  {r.get('year')}: {r.get('flood_count')} events (avg {r.get('avg_area')} km²)")
    
    passed = all(checks.values())
    print(f"\n✓ PASSED" if passed else f"\n✗ FAILED: {checks}")
    
    return {
        "test": "yearly_trend",
        "passed": passed,
        "checks": checks,
        "data": {
            "years": len(result),
            "year_range": f"{result[0].get('year')}-{result[-1].get('year')}" if result else None,
        },
    }


def main():
    """Run all validation tests."""
    print("=" * 60)
    print("HAWKEYE BIGQUERY VALIDATION")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print("=" * 60)
    
    project_id = os.getenv("GCP_PROJECT_ID", "")
    if not project_id:
        print("\n⚠️  Warning: GCP_PROJECT_ID not set, using default")
    
    print(f"\nProject: {project_id or 'default'}")
    print(f"Test location: Kampung Melayu, Jakarta ({TEST_LAT}, {TEST_LNG})")
    
    try:
        service = GroundsourceService(project_id=project_id)
    except Exception as e:
        print(f"\n✗ Failed to initialize GroundsourceService: {e}")
        sys.exit(1)
    
    # Run all tests
    results = [
        validate_flood_frequency(service),
        validate_infrastructure_at_risk(service),
        validate_infrastructure_expanded(service),
        validate_pattern_match(service),
        validate_monthly_frequency(service),
        validate_yearly_trend(service),
    ]
    
    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    
    for r in results:
        status = "✓ PASS" if r["passed"] else "✗ FAIL"
        print(f"{status}: {r['test']}")
    
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    
    # Write report
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "project_id": project_id,
        "test_location": {"lat": TEST_LAT, "lng": TEST_LNG},
        "summary": {"passed": passed, "failed": failed, "total": len(results)},
        "results": results,
    }
    
    report_path = Path(__file__).parent / "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nReport saved to: {report_path}")
    
    if failed > 0:
        print("\n⚠️  Some tests failed. Review the output above.")
        sys.exit(1)
    else:
        print("\n✓ All tests passed! BigQuery data is ready for Step 3.")
        sys.exit(0)


if __name__ == "__main__":
    main()
