"""
Test 02: Data Quality & Integrity
==================================
Validates data in the BigQuery tables is meaningful and consistent.
Matches Phase 2.4 verification queries from HawkEye_BigQuery_Specification.md.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).resolve().parent.parent / "credentials" / "hawkeye-runtime-key.json"),
)
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "gen-lang-client-0261050164")

from google.cloud import bigquery

client = bigquery.Client(project=PROJECT_ID)
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


def run_query(sql):
    return [dict(row) for row in client.query(sql).result()]


print("=" * 70)
print("TEST 02: DATA QUALITY & INTEGRITY")
print("=" * 70)

# ---------- 1. Groundsource row counts ----------
print("\n1. Groundsource row counts")
try:
    raw_count = run_query(f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.hawkeye.groundsource_raw`")[0]["cnt"]
    opt_count = run_query(f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.hawkeye.groundsource`")[0]["cnt"]
    jak_count = run_query(f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.hawkeye.groundsource_jakarta`")[0]["cnt"]

    print(f"  Raw: {raw_count:,} | Optimized: {opt_count:,} | Jakarta: {jak_count:,}")

    check(f"Raw table has data ({raw_count:,})", raw_count > 0)
    check(f"Optimized table has data ({opt_count:,})", opt_count > 0)
    check("Optimized <= Raw (some rows dropped for invalid geometry)",
          opt_count <= raw_count, f"{opt_count} > {raw_count}")
    check(f"Jakarta view has data ({jak_count:,})", jak_count > 0)
    check("Jakarta << Global (spatial filter working)",
          jak_count < opt_count, f"Jakarta={jak_count} vs Global={opt_count}")
except Exception as e:
    check("Row counts", False, str(e))

# ---------- 2. Date range sanity ----------
print("\n2. Date range sanity (Groundsource)")
try:
    result = run_query(f"""
        SELECT MIN(start_date) AS earliest, MAX(start_date) AS latest,
               COUNT(DISTINCT EXTRACT(YEAR FROM start_date)) AS distinct_years
        FROM `{PROJECT_ID}.hawkeye.groundsource`
    """)[0]
    print(f"  Range: {result['earliest']} to {result['latest']} ({result['distinct_years']} years)")
    check("Earliest date before 2010", str(result["earliest"]) < "2010-01-01")
    check("Latest date after 2020", str(result["latest"]) > "2020-01-01")
    check("Spans multiple years", result["distinct_years"] > 5)
except Exception as e:
    check("Date range", False, str(e))

# ---------- 3. Duration and area sanity ----------
print("\n3. Duration and area statistics (Jakarta)")
try:
    result = run_query(f"""
        SELECT
            ROUND(AVG(duration_days), 1) AS avg_dur,
            MAX(duration_days) AS max_dur,
            MIN(duration_days) AS min_dur,
            ROUND(AVG(area_sqkm), 2) AS avg_area,
            MAX(area_sqkm) AS max_area,
            COUNTIF(duration_days < 0) AS negative_durations,
            COUNTIF(area_sqkm < 0) AS negative_areas,
            COUNTIF(duration_days IS NULL) AS null_durations,
            COUNTIF(area_sqkm IS NULL) AS null_areas
        FROM `{PROJECT_ID}.hawkeye.groundsource_jakarta`
    """)[0]
    print(f"  Duration: avg={result['avg_dur']}d, range=[{result['min_dur']}, {result['max_dur']}]d")
    print(f"  Area: avg={result['avg_area']} km², max={result['max_area']} km²")

    check("No negative durations", result["negative_durations"] == 0,
          f"{result['negative_durations']} negative")
    check("No negative areas", result["negative_areas"] == 0,
          f"{result['negative_areas']} negative")
    check("No null durations", result["null_durations"] == 0,
          f"{result['null_durations']} null")
    check("No null areas", result["null_areas"] == 0,
          f"{result['null_areas']} null")
    check("Average duration is reasonable (0-365 days)",
          0 < result["avg_dur"] < 365, f"avg_dur={result['avg_dur']}")
except Exception as e:
    check("Duration/area stats", False, str(e))

# ---------- 4. Infrastructure type distribution ----------
print("\n4. Infrastructure type distribution")
try:
    results = run_query(f"""
        SELECT type, COUNT(*) AS count
        FROM `{PROJECT_ID}.hawkeye.infrastructure`
        GROUP BY type
        ORDER BY count DESC
    """)
    for r in results:
        print(f"  {r['type']}: {r['count']}")

    types = {r["type"] for r in results}
    check("Has hospital type", "hospital" in types)
    check("Has school type", "school" in types)
    check("Has shelter type", "shelter" in types)
    check("Has power_station type", "power_station" in types)

    total = sum(r["count"] for r in results)
    check(f"Total infrastructure records: {total}", total > 10,
          f"only {total} records")
except Exception as e:
    check("Infrastructure types", False, str(e))

# ---------- 5. Infrastructure coordinates in Jakarta ----------
print("\n5. Infrastructure coordinates within Jakarta bounds?")
try:
    result = run_query(f"""
        SELECT
            MIN(latitude) AS min_lat, MAX(latitude) AS max_lat,
            MIN(longitude) AS min_lng, MAX(longitude) AS max_lng,
            COUNTIF(latitude < -7.0 OR latitude > -5.5) AS out_of_lat,
            COUNTIF(longitude < 106.0 OR longitude > 107.5) AS out_of_lng
        FROM `{PROJECT_ID}.hawkeye.infrastructure`
    """)[0]
    print(f"  Lat range: [{result['min_lat']:.4f}, {result['max_lat']:.4f}]")
    print(f"  Lng range: [{result['min_lng']:.4f}, {result['max_lng']:.4f}]")

    check("All latitudes in Jakarta range (-7.0 to -5.5)",
          result["out_of_lat"] == 0, f"{result['out_of_lat']} out of range")
    check("All longitudes in Jakarta range (106.0 to 107.5)",
          result["out_of_lng"] == 0, f"{result['out_of_lng']} out of range")
except Exception as e:
    check("Infrastructure coordinates", False, str(e))

# ---------- 6. GEOGRAPHY column integrity ----------
print("\n6. GEOGRAPHY column integrity")
try:
    result = run_query(f"""
        SELECT
            COUNTIF(geometry IS NULL) AS null_geom,
            COUNTIF(ST_ISEMPTY(geometry)) AS empty_geom,
            COUNT(*) AS total
        FROM `{PROJECT_ID}.hawkeye.groundsource_jakarta`
    """)[0]
    check("No NULL geometries in Jakarta view",
          result["null_geom"] == 0, f"{result['null_geom']} null")
    check("No empty geometries in Jakarta view",
          result["empty_geom"] == 0, f"{result['empty_geom']} empty")
except Exception as e:
    check("Geography integrity", False, str(e))

try:
    result = run_query(f"""
        SELECT
            COUNTIF(location IS NULL) AS null_loc,
            COUNT(*) AS total
        FROM `{PROJECT_ID}.hawkeye.infrastructure`
    """)[0]
    check("No NULL locations in infrastructure",
          result["null_loc"] == 0, f"{result['null_loc']} null")
except Exception as e:
    check("Infrastructure location integrity", False, str(e))

# ---------- Summary ----------
print("\n" + "=" * 70)
total = passed + failed
print(f"RESULT: {passed}/{total} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed > 0 else 0)
