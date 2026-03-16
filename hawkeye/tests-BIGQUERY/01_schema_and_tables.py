"""
Test 01: Schema & Table Verification
=====================================
Confirms all BigQuery tables/views exist with correct schemas,
matching Phase 1 + Phase 2 of HawkEye_BigQuery_Specification.md.
"""

import os
import sys
from pathlib import Path

# Project setup
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


print("=" * 70)
print("TEST 01: SCHEMA & TABLE VERIFICATION")
print("=" * 70)

# ---------- 1. Dataset exists ----------
print("\n1. Dataset 'hawkeye' exists?")
try:
    ds = client.get_dataset(f"{PROJECT_ID}.hawkeye")
    check("Dataset hawkeye exists", True)
    check(f"Location: {ds.location}", True)
except Exception as e:
    check("Dataset hawkeye exists", False, str(e))

# ---------- 2. Tables exist ----------
print("\n2. Required tables/views exist?")
expected_tables = {
    "groundsource_raw": "TABLE",
    "groundsource": "TABLE",
    "groundsource_jakarta": ("TABLE", "MATERIALIZED_VIEW", "VIEW"),
    "infrastructure_raw": "TABLE",
    "infrastructure": "TABLE",
}

actual_tables = {}
for table in client.list_tables(f"{PROJECT_ID}.hawkeye"):
    actual_tables[table.table_id] = table.table_type

for tname, expected_type in expected_tables.items():
    exists = tname in actual_tables
    if isinstance(expected_type, tuple):
        type_ok = actual_tables.get(tname) in expected_type
        check(f"{tname} exists (type={actual_tables.get(tname, 'MISSING')})", exists and type_ok)
    else:
        type_ok = actual_tables.get(tname) == expected_type
        check(f"{tname} exists (type={actual_tables.get(tname, 'MISSING')})", exists and type_ok)

# Report any extra tables
extras = set(actual_tables.keys()) - set(expected_tables.keys())
if extras:
    print(f"\n  ℹ️  Extra tables in hawkeye dataset: {extras}")

# ---------- 3. Groundsource schema ----------
print("\n3. Groundsource optimized table schema?")
try:
    table = client.get_table(f"{PROJECT_ID}.hawkeye.groundsource")
    schema_map = {f.name: f.field_type for f in table.schema}

    check("geometry column exists", "geometry" in schema_map)
    check("geometry is GEOGRAPHY", schema_map.get("geometry") == "GEOGRAPHY",
          f"got {schema_map.get('geometry')}")
    check("start_date column exists", "start_date" in schema_map)
    check("start_date is DATE", schema_map.get("start_date") == "DATE",
          f"got {schema_map.get('start_date')}")
    check("end_date column exists", "end_date" in schema_map)
    check("end_date is DATE", schema_map.get("end_date") == "DATE",
          f"got {schema_map.get('end_date')}")
    check("duration_days column exists", "duration_days" in schema_map)
    check("duration_days is INTEGER/INT64",
          schema_map.get("duration_days") in ("INTEGER", "INT64"),
          f"got {schema_map.get('duration_days')}")
    check("area_sqkm column exists", "area_sqkm" in schema_map)
    check("area_sqkm is FLOAT/FLOAT64",
          schema_map.get("area_sqkm") in ("FLOAT", "FLOAT64"),
          f"got {schema_map.get('area_sqkm')}")
    check(f"Total rows: {table.num_rows:,}", table.num_rows > 0)

    print(f"\n  Schema: {schema_map}")
except Exception as e:
    check("Groundsource schema check", False, str(e))

# ---------- 4. Infrastructure schema ----------
print("\n4. Infrastructure table schema?")
try:
    table = client.get_table(f"{PROJECT_ID}.hawkeye.infrastructure")
    schema_map = {f.name: f.field_type for f in table.schema}

    check("location column exists", "location" in schema_map)
    check("location is GEOGRAPHY", schema_map.get("location") == "GEOGRAPHY",
          f"got {schema_map.get('location')}")
    check("name column exists", "name" in schema_map)
    check("type column exists", "type" in schema_map)
    check("latitude column exists", "latitude" in schema_map)
    check("longitude column exists", "longitude" in schema_map)
    check("capacity column exists", "capacity" in schema_map)
    check(f"Total rows: {table.num_rows:,}", table.num_rows > 0)

    print(f"\n  Schema: {schema_map}")
except Exception as e:
    check("Infrastructure schema check", False, str(e))

# ---------- 5. Jakarta view row count ----------
print("\n5. Jakarta materialized view row count?")
try:
    query = f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.hawkeye.groundsource_jakarta`"
    result = list(client.query(query).result())
    cnt = result[0].cnt
    check(f"groundsource_jakarta has {cnt:,} rows", cnt > 0)
    check("Row count >= 50 (meaningful data)", cnt >= 50,
          f"only {cnt} rows — may need larger radius")
except Exception as e:
    check("Jakarta view count", False, str(e))

# ---------- Summary ----------
print("\n" + "=" * 70)
total = passed + failed
print(f"RESULT: {passed}/{total} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed > 0 else 0)
