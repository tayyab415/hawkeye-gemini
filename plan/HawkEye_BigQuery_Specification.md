# HAWK EYE — BigQuery Geospatial Intelligence Specification

## Purpose

This document is the single source of truth for everything BigQuery in Hawk Eye. Any coding agent working on Step 0 (data loading), Track 1C (service layer), Track 2C (data validation), or Track 3B (Analyst agent) should read this document first.

---

## What BigQuery Does in Hawk Eye

BigQuery is the intelligence backbone. It holds two datasets:

1. **Groundsource** — 2.6 million historical flash flood events worldwide (from Google's open-source release, March 12, 2026). This is what makes Hawk Eye's historical pattern matching possible. When the agent says "this area has flooded 47 times since 2000," that number comes from BigQuery.

2. **Jakarta Infrastructure** — hospitals, schools, shelters, power stations, water treatment plants extracted from OpenStreetMap. This is what makes the cascade possible. When the agent says "3 hospitals lose road access at +2 meters," that comes from a spatial join in BigQuery.

Both datasets are queried using BigQuery's geospatial SQL functions — spatial intersections, distance calculations, containment checks — all running server-side at Google scale.

---

## Phase 1: Data Ingestion (Step 0)

### 1.1 Groundsource Parquet Load

Download from Zenodo: https://doi.org/10.5281/zenodo.18647054

File: ~636 MB parquet, 2,646,302 rows.

Columns in the parquet (from AlphaSignal analysis):
- `geometry` — spatial boundary of the reported flood location (WGS 84 / EPSG:4326). Can be a complex polygon (administrative district) or a buffered point (street intersection).
- `start_date` — first day with documented evidence of ongoing flood (YYYY-MM-DD string)
- `end_date` — last consecutive day with documented evidence (YYYY-MM-DD string)

Upload to Cloud Storage first, then load:

```bash
# Upload to Cloud Storage
gsutil cp groundsource.parquet gs://{BUCKET}/data/groundsource.parquet

# Load into BigQuery raw table
bq load \
  --source_format=PARQUET \
  hawkeye.groundsource_raw \
  gs://{BUCKET}/data/groundsource.parquet
```

### 1.2 Verify the GEOGRAPHY Column

After loading, check whether BigQuery auto-detected the geometry column as GEOGRAPHY type (it does this for GeoParquet files):

```sql
SELECT column_name, data_type
FROM hawkeye.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'groundsource_raw'
  AND column_name = 'geometry';
```

**If data_type = 'GEOGRAPHY':** Great, proceed to Phase 2.

**If data_type = 'STRING' (WKT or GeoJSON text):** You need to cast it. Determine the format first:

```sql
-- Check the first few values to see if it's WKT or GeoJSON
SELECT geometry FROM hawkeye.groundsource_raw LIMIT 5;
```

If it looks like `POLYGON((...))` → it's WKT, use `ST_GEOGFROMTEXT`.
If it looks like `{"type":"Polygon","coordinates":[...]}` → it's GeoJSON, use `ST_GEOGFROMGEOJSON`.

**If data_type = 'BYTES' or 'RECORD' (nested struct):** The parquet may store geometry as a nested struct with coordinates. In this case, you'll need to extract and construct the GEOGRAPHY manually. Check the schema:

```sql
SELECT * FROM hawkeye.groundsource_raw LIMIT 1;
```

And inspect the actual structure before writing the conversion.

### 1.3 Jakarta Infrastructure Load

Extract from OpenStreetMap Overpass API. Run this Python script once:

```python
# data/load_infrastructure.py
import requests
import json
from google.cloud import bigquery

OVERPASS_URL = "http://overpass-api.de/api/interpreter"

# Query Jakarta infrastructure within ~20km radius
queries = {
    "hospital": '[out:json];node["amenity"="hospital"](around:20000,-6.2,106.85);out body;',
    "school": '[out:json];node["amenity"="school"](around:20000,-6.2,106.85);out body;',
    "shelter": '[out:json];(node["amenity"="shelter"](around:20000,-6.2,106.85);node["building"="civic"](around:20000,-6.2,106.85););out body;',
    "power_station": '[out:json];node["power"="substation"](around:20000,-6.2,106.85);out body;',
    "water_treatment": '[out:json];node["man_made"="water_works"](around:20000,-6.2,106.85);out body;',
}

rows = []
for infra_type, query in queries.items():
    response = requests.get(OVERPASS_URL, params={"data": query})
    data = response.json()
    for element in data.get("elements", []):
        rows.append({
            "id": str(element["id"]),
            "name": element.get("tags", {}).get("name", f"Unknown {infra_type}"),
            "type": infra_type,
            "latitude": element["lat"],
            "longitude": element["lon"],
            "capacity": element.get("tags", {}).get("capacity", None),
            "metadata": json.dumps(element.get("tags", {})),
        })

# Load into BigQuery
client = bigquery.Client()
table_id = "hawkeye.infrastructure_raw"

schema = [
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("type", "STRING"),
    bigquery.SchemaField("latitude", "FLOAT64"),
    bigquery.SchemaField("longitude", "FLOAT64"),
    bigquery.SchemaField("capacity", "STRING"),
    bigquery.SchemaField("metadata", "STRING"),
]

job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
job = client.load_table_from_json(rows, table_id, job_config=job_config)
job.result()
print(f"Loaded {len(rows)} infrastructure records")
```

---

## Phase 2: Schema Optimization (Step 0, after load)

### 2.1 Create Optimized Groundsource Table

The raw table has string dates and potentially non-GEOGRAPHY geometry. Create the production table with proper types, clustering, and computed columns:

```sql
-- If geometry IS already GEOGRAPHY type:
CREATE OR REPLACE TABLE hawkeye.groundsource
CLUSTER BY geometry AS
SELECT
  geometry,
  SAFE.PARSE_DATE('%Y-%m-%d', start_date) AS start_date,
  SAFE.PARSE_DATE('%Y-%m-%d', end_date) AS end_date,
  DATE_DIFF(
    SAFE.PARSE_DATE('%Y-%m-%d', end_date),
    SAFE.PARSE_DATE('%Y-%m-%d', start_date),
    DAY
  ) AS duration_days,
  ST_AREA(geometry) / 1000000 AS area_sqkm
FROM hawkeye.groundsource_raw
WHERE SAFE.PARSE_DATE('%Y-%m-%d', start_date) IS NOT NULL
  AND SAFE.PARSE_DATE('%Y-%m-%d', end_date) IS NOT NULL;
```

```sql
-- If geometry is STRING (WKT):
CREATE OR REPLACE TABLE hawkeye.groundsource
CLUSTER BY geometry AS
SELECT
  ST_GEOGFROMTEXT(geometry, make_valid => TRUE) AS geometry,
  SAFE.PARSE_DATE('%Y-%m-%d', start_date) AS start_date,
  SAFE.PARSE_DATE('%Y-%m-%d', end_date) AS end_date,
  DATE_DIFF(
    SAFE.PARSE_DATE('%Y-%m-%d', end_date),
    SAFE.PARSE_DATE('%Y-%m-%d', start_date),
    DAY
  ) AS duration_days,
  ST_AREA(ST_GEOGFROMTEXT(geometry, make_valid => TRUE)) / 1000000 AS area_sqkm
FROM hawkeye.groundsource_raw
WHERE SAFE.PARSE_DATE('%Y-%m-%d', start_date) IS NOT NULL
  AND SAFE.PARSE_DATE('%Y-%m-%d', end_date) IS NOT NULL
  AND SAFE.ST_GEOGFROMTEXT(geometry, make_valid => TRUE) IS NOT NULL;
```

```sql
-- If geometry is STRING (GeoJSON):
CREATE OR REPLACE TABLE hawkeye.groundsource
CLUSTER BY geometry AS
SELECT
  ST_GEOGFROMGEOJSON(geometry, make_valid => TRUE) AS geometry,
  SAFE.PARSE_DATE('%Y-%m-%d', start_date) AS start_date,
  SAFE.PARSE_DATE('%Y-%m-%d', end_date) AS end_date,
  DATE_DIFF(
    SAFE.PARSE_DATE('%Y-%m-%d', end_date),
    SAFE.PARSE_DATE('%Y-%m-%d', start_date),
    DAY
  ) AS duration_days,
  ST_AREA(ST_GEOGFROMGEOJSON(geometry, make_valid => TRUE)) / 1000000 AS area_sqkm
FROM hawkeye.groundsource_raw
WHERE SAFE.PARSE_DATE('%Y-%m-%d', start_date) IS NOT NULL
  AND SAFE.PARSE_DATE('%Y-%m-%d', end_date) IS NOT NULL
  AND SAFE.ST_GEOGFROMGEOJSON(geometry, make_valid => TRUE) IS NOT NULL;
```

Use SAFE prefix to skip invalid rows rather than failing the entire job. The 18% error rate in Groundsource means some rows will have bad geometry.

### 2.2 Create Jakarta Materialized View

This avoids scanning 2.6M global rows for every Jakarta query. Jakarta metro area is roughly within 50km of the city center:

```sql
CREATE MATERIALIZED VIEW hawkeye.groundsource_jakarta AS
SELECT *
FROM hawkeye.groundsource
WHERE ST_DWITHIN(
  geometry,
  ST_GEOGPOINT(106.845, -6.225),  -- Jakarta center (lng, lat — BigQuery uses lng first!)
  50000  -- 50km radius in meters
);
```

Verify:

```sql
SELECT COUNT(*) AS jakarta_flood_count FROM hawkeye.groundsource_jakarta;
-- Should return hundreds to low thousands of events
```

If the count is very low (under 50), try expanding the radius to 100km or check if the geometry data for Indonesia is sparse.

### 2.3 Create Optimized Infrastructure Table

Convert lat/lng to GEOGRAPHY and add elevation data:

```sql
CREATE OR REPLACE TABLE hawkeye.infrastructure
CLUSTER BY location AS
SELECT
  id,
  name,
  type,
  ST_GEOGPOINT(longitude, latitude) AS location,  -- lng first!
  latitude,
  longitude,
  CAST(capacity AS INT64) AS capacity,
  metadata
FROM hawkeye.infrastructure_raw;
```

### 2.4 Verification Queries (Run All, Confirm Results)

```sql
-- Count total Groundsource records
SELECT COUNT(*) AS total FROM hawkeye.groundsource;
-- Expected: ~2.2-2.6M (some dropped due to invalid geometry)

-- Count Jakarta records
SELECT COUNT(*) AS jakarta_total FROM hawkeye.groundsource_jakarta;
-- Expected: hundreds to thousands

-- Count infrastructure by type
SELECT type, COUNT(*) AS count
FROM hawkeye.infrastructure
GROUP BY type
ORDER BY count DESC;
-- Expected: hospitals (50-200), schools (hundreds), etc.

-- Test spatial query: floods near Kampung Melayu
SELECT start_date, end_date, duration_days, area_sqkm
FROM hawkeye.groundsource_jakarta
WHERE ST_DWITHIN(geometry, ST_GEOGPOINT(106.855, -6.225), 5000)
ORDER BY start_date DESC
LIMIT 10;
-- Expected: actual flood events with dates

-- Test infrastructure spatial query
SELECT name, type, capacity
FROM hawkeye.infrastructure
WHERE ST_DWITHIN(location, ST_GEOGPOINT(106.855, -6.225), 5000)
ORDER BY type;
-- Expected: hospitals, schools near Kampung Melayu
```

---

## Phase 3: Query Design (Track 1C — GroundsourceService)

These are the exact SQL queries that the service layer methods execute. Each query is parameterized — the Python service class substitutes parameters using BigQuery's query parameter binding.

### Query 1: Historical Floods Intersecting a Polygon

Used when: Commander asks "Show me the flood extent" and the Analyst cross-references with history.

```sql
SELECT
  start_date,
  end_date,
  duration_days,
  area_sqkm,
  ST_ASGEOJSON(geometry) AS geometry_geojson,
  ROUND(ST_AREA(ST_INTERSECTION(geometry, @flood_polygon)) / 1000000, 2) AS overlap_sqkm
FROM hawkeye.groundsource_jakarta
WHERE ST_INTERSECTS(geometry, @flood_polygon)
ORDER BY start_date DESC
LIMIT 50;
```

Parameter: `@flood_polygon` — GEOGRAPHY from the current flood extent GeoJSON.

Python binding:
```python
job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("flood_polygon", "GEOGRAPHY", flood_geojson_string)
    ]
)
```

### Query 2: Flood Frequency for a Location

Used when: Agent proactively reports "This basin has flooded X times since 2000."

```sql
SELECT
  COUNT(*) AS total_events,
  ROUND(AVG(duration_days), 1) AS avg_duration_days,
  MAX(duration_days) AS max_duration_days,
  MIN(start_date) AS earliest_event,
  MAX(start_date) AS latest_event,
  ROUND(AVG(area_sqkm), 2) AS avg_area_sqkm,
  MAX(area_sqkm) AS max_area_sqkm
FROM hawkeye.groundsource_jakarta
WHERE ST_DWITHIN(geometry, ST_GEOGPOINT(@lng, @lat), @radius_m);
```

Parameters: `@lng` FLOAT64, `@lat` FLOAT64, `@radius_m` INT64 (in meters).

### Query 3: Infrastructure at Risk (Spatial Join with Flood Polygon)

Used when: The cascade engine determines which hospitals, schools, and power stations are inside the flood zone.

```sql
SELECT
  name,
  type,
  capacity,
  ST_ASGEOJSON(location) AS location_geojson,
  latitude,
  longitude,
  ROUND(ST_DISTANCE(location, ST_BOUNDARY(@flood_polygon)), 0) AS distance_to_flood_edge_m
FROM hawkeye.infrastructure
WHERE ST_INTERSECTS(location, @flood_polygon)
ORDER BY type, distance_to_flood_edge_m;
```

Parameter: `@flood_polygon` — GEOGRAPHY.

### Query 4: Infrastructure at Risk at Expanded Water Level

Used when: Commander asks "What if water rises 2 more meters?" The cascade expands the flood zone using elevation.

This query approximates the expanded flood zone by buffering the existing polygon. A more precise approach would use elevation data, but for hackathon scope, a buffer serves as a reasonable proxy.

```sql
-- Buffer the flood polygon by delta_meters as rough expansion proxy
-- Then find infrastructure inside the expanded zone
WITH expanded_flood AS (
  SELECT ST_BUFFER(@flood_polygon, @buffer_meters) AS expanded_geometry
)
SELECT
  i.name,
  i.type,
  i.capacity,
  i.latitude,
  i.longitude,
  ST_ASGEOJSON(i.location) AS location_geojson,
  'expanded_zone' AS risk_zone
FROM hawkeye.infrastructure i, expanded_flood ef
WHERE ST_INTERSECTS(i.location, ef.expanded_geometry)

EXCEPT DISTINCT

-- Subtract infrastructure already in the original flood zone
SELECT
  name, type, capacity, latitude, longitude,
  ST_ASGEOJSON(location) AS location_geojson,
  'expanded_zone' AS risk_zone
FROM hawkeye.infrastructure
WHERE ST_INTERSECTS(location, @flood_polygon);
```

Parameters: `@flood_polygon` GEOGRAPHY, `@buffer_meters` INT64 (e.g., 500m per meter of water rise — a rough heuristic).

This returns ONLY the newly-at-risk infrastructure (not already in the original flood zone), which is exactly what the cascade narration needs: "At +2 meters, these ADDITIONAL hospitals/schools are now at risk..."

### Query 5: Pattern Match — Find Historical Event Similar to Current Conditions

Used when: Agent says "The current rate of rise matches the January 2020 event."

```sql
SELECT
  start_date,
  end_date,
  duration_days,
  area_sqkm,
  ST_ASGEOJSON(geometry) AS geometry_geojson,
  ABS(area_sqkm - @current_area_sqkm) AS area_diff,
  ABS(duration_days - @current_duration_estimate) AS duration_diff
FROM hawkeye.groundsource_jakarta
WHERE duration_days BETWEEN @min_duration AND @max_duration
  AND area_sqkm BETWEEN @current_area_sqkm * 0.5 AND @current_area_sqkm * 2.0
ORDER BY
  ABS(area_sqkm - @current_area_sqkm) +
  ABS(duration_days - @current_duration_estimate) ASC
LIMIT 5;
```

Parameters: `@current_area_sqkm` FLOAT64, `@current_duration_estimate` INT64, `@min_duration` INT64, `@max_duration` INT64.

### Query 6: Temporal Analysis — Monthly Flood Frequency

Used when: Agent provides seasonal context — "Jakarta floods are 3x more frequent during November-February."

```sql
SELECT
  EXTRACT(MONTH FROM start_date) AS month,
  COUNT(*) AS flood_count,
  ROUND(AVG(duration_days), 1) AS avg_duration
FROM hawkeye.groundsource_jakarta
GROUP BY month
ORDER BY month;
```

No parameters. Cache this result on service startup since it never changes.

### Query 7: Year-over-Year Trend

Used when: Agent provides trend context — "Flooding frequency has increased 40% in the last 5 years."

```sql
SELECT
  EXTRACT(YEAR FROM start_date) AS year,
  COUNT(*) AS flood_count,
  ROUND(AVG(area_sqkm), 2) AS avg_area
FROM hawkeye.groundsource_jakarta
GROUP BY year
ORDER BY year;
```

No parameters. Also cacheable.

---

## Phase 4: Python Service Layer (Track 1C)

### GroundsourceService Class

```python
# app/services/bigquery_service.py

from google.cloud import bigquery
import json

class GroundsourceService:
    def __init__(self, project_id: str):
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id
        # Cache static queries on init
        self._monthly_frequency = None
        self._yearly_trend = None

    def _run_query(self, sql: str, params: list = None) -> list:
        job_config = bigquery.QueryJobConfig(
            query_parameters=params or []
        )
        results = self.client.query(sql, job_config=job_config)
        return [dict(row) for row in results]

    def query_floods_intersecting_polygon(self, flood_geojson: str) -> list:
        """Query 1: Historical floods overlapping with given flood polygon."""
        sql = """
        SELECT start_date, end_date, duration_days, area_sqkm,
               ST_ASGEOJSON(geometry) AS geometry_geojson,
               ROUND(ST_AREA(ST_INTERSECTION(geometry,
                 ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE)
               )) / 1000000, 2) AS overlap_sqkm
        FROM hawkeye.groundsource_jakarta
        WHERE ST_INTERSECTS(geometry,
          ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE))
        ORDER BY start_date DESC
        LIMIT 50
        """
        params = [bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson)]
        return self._run_query(sql, params)

    def get_flood_frequency(self, lat: float, lng: float, radius_km: float) -> dict:
        """Query 2: Flood frequency stats for a location."""
        sql = """
        SELECT COUNT(*) AS total_events,
               ROUND(AVG(duration_days), 1) AS avg_duration_days,
               MAX(duration_days) AS max_duration_days,
               MIN(start_date) AS earliest_event,
               MAX(start_date) AS latest_event,
               ROUND(AVG(area_sqkm), 2) AS avg_area_sqkm,
               MAX(area_sqkm) AS max_area_sqkm
        FROM hawkeye.groundsource_jakarta
        WHERE ST_DWITHIN(geometry, ST_GEOGPOINT(@lng, @lat), @radius_m)
        """
        params = [
            bigquery.ScalarQueryParameter("lng", "FLOAT64", lng),
            bigquery.ScalarQueryParameter("lat", "FLOAT64", lat),
            bigquery.ScalarQueryParameter("radius_m", "INT64", int(radius_km * 1000)),
        ]
        results = self._run_query(sql, params)
        return results[0] if results else {}

    def get_infrastructure_at_risk(self, flood_geojson: str) -> dict:
        """Query 3: Infrastructure inside the flood polygon."""
        sql = """
        SELECT name, type, capacity, latitude, longitude,
               ST_ASGEOJSON(location) AS location_geojson,
               ROUND(ST_DISTANCE(location, ST_BOUNDARY(
                 ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE)
               )), 0) AS distance_to_flood_edge_m
        FROM hawkeye.infrastructure
        WHERE ST_INTERSECTS(location,
          ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE))
        ORDER BY type, distance_to_flood_edge_m
        """
        params = [bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson)]
        results = self._run_query(sql, params)

        # Group by type for the cascade
        grouped = {}
        for r in results:
            t = r["type"]
            if t not in grouped:
                grouped[t] = []
            grouped[t].append(r)

        return {
            "total_at_risk": len(results),
            "hospitals": grouped.get("hospital", []),
            "schools": grouped.get("school", []),
            "shelters": grouped.get("shelter", []),
            "power_stations": grouped.get("power_station", []),
            "water_treatment": grouped.get("water_treatment", []),
        }

    def get_infrastructure_at_expanded_level(
        self, flood_geojson: str, buffer_meters: int
    ) -> dict:
        """Query 4: NEWLY at-risk infrastructure when water rises."""
        sql = """
        WITH expanded_flood AS (
          SELECT ST_BUFFER(
            ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE),
            @buffer_meters
          ) AS expanded_geometry
        ),
        currently_at_risk AS (
          SELECT id FROM hawkeye.infrastructure
          WHERE ST_INTERSECTS(location,
            ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE))
        )
        SELECT i.name, i.type, i.capacity, i.latitude, i.longitude,
               ST_ASGEOJSON(i.location) AS location_geojson
        FROM hawkeye.infrastructure i, expanded_flood ef
        WHERE ST_INTERSECTS(i.location, ef.expanded_geometry)
          AND i.id NOT IN (SELECT id FROM currently_at_risk)
        ORDER BY i.type
        """
        params = [
            bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson),
            bigquery.ScalarQueryParameter("buffer_meters", "INT64", buffer_meters),
        ]
        results = self._run_query(sql, params)

        grouped = {}
        for r in results:
            t = r["type"]
            if t not in grouped:
                grouped[t] = []
            grouped[t].append(r)

        return {
            "newly_at_risk": len(results),
            "hospitals": grouped.get("hospital", []),
            "schools": grouped.get("school", []),
            "shelters": grouped.get("shelter", []),
            "power_stations": grouped.get("power_station", []),
            "water_treatment": grouped.get("water_treatment", []),
        }

    def find_pattern_match(
        self, current_area_sqkm: float, duration_estimate_days: int
    ) -> list:
        """Query 5: Find historical events similar to current conditions."""
        sql = """
        SELECT start_date, end_date, duration_days, area_sqkm,
               ST_ASGEOJSON(geometry) AS geometry_geojson
        FROM hawkeye.groundsource_jakarta
        WHERE duration_days BETWEEN @min_dur AND @max_dur
          AND area_sqkm BETWEEN @min_area AND @max_area
        ORDER BY
          ABS(area_sqkm - @target_area) +
          ABS(duration_days - @target_dur) ASC
        LIMIT 5
        """
        params = [
            bigquery.ScalarQueryParameter("min_dur", "INT64", max(1, duration_estimate_days - 5)),
            bigquery.ScalarQueryParameter("max_dur", "INT64", duration_estimate_days + 10),
            bigquery.ScalarQueryParameter("min_area", "FLOAT64", current_area_sqkm * 0.3),
            bigquery.ScalarQueryParameter("max_area", "FLOAT64", current_area_sqkm * 3.0),
            bigquery.ScalarQueryParameter("target_area", "FLOAT64", current_area_sqkm),
            bigquery.ScalarQueryParameter("target_dur", "INT64", duration_estimate_days),
        ]
        return self._run_query(sql, params)

    def get_monthly_frequency(self) -> list:
        """Query 6: Monthly flood frequency (cached)."""
        if self._monthly_frequency is None:
            sql = """
            SELECT EXTRACT(MONTH FROM start_date) AS month,
                   COUNT(*) AS flood_count,
                   ROUND(AVG(duration_days), 1) AS avg_duration
            FROM hawkeye.groundsource_jakarta
            GROUP BY month ORDER BY month
            """
            self._monthly_frequency = self._run_query(sql)
        return self._monthly_frequency

    def get_yearly_trend(self) -> list:
        """Query 7: Year-over-year trend (cached)."""
        if self._yearly_trend is None:
            sql = """
            SELECT EXTRACT(YEAR FROM start_date) AS year,
                   COUNT(*) AS flood_count,
                   ROUND(AVG(area_sqkm), 2) AS avg_area
            FROM hawkeye.groundsource_jakarta
            GROUP BY year ORDER BY year
            """
            self._yearly_trend = self._run_query(sql)
        return self._yearly_trend
```

---

## Phase 5: Agent Tool Integration (Track 3B)

The Analyst sub-agent wraps these service methods as ADK tools. Each tool function:
1. Calls the GroundsourceService method
2. Formats the result for voice narration
3. Returns structured data that the downstream WebSocket task can also emit as UI updates

Example tool function:

```python
# app/hawkeye_agent/tools/analyst.py

from app.services.bigquery_service import GroundsourceService

gs = GroundsourceService(project_id="your-project-id")

def query_historical_floods(lat: float, lng: float, radius_km: float) -> dict:
    """
    Query Groundsource database for historical flood events near a location.
    Returns flood frequency statistics and closest pattern matches.
    """
    frequency = gs.get_flood_frequency(lat, lng, radius_km)
    pattern = gs.find_pattern_match(
        current_area_sqkm=frequency.get("avg_area_sqkm", 10),
        duration_estimate_days=frequency.get("avg_duration_days", 3)
    )
    monthly = gs.get_monthly_frequency()

    return {
        "frequency": frequency,
        "closest_historical_matches": pattern,
        "monthly_pattern": monthly,
        "summary": (
            f"This area has experienced {frequency.get('total_events', 0)} flood events "
            f"since 2000 according to Google's Groundsource database. "
            f"Average duration: {frequency.get('avg_duration_days', 'unknown')} days. "
            f"Worst case: {frequency.get('max_duration_days', 'unknown')} days."
        )
    }


def compute_cascade(flood_geojson: str, water_level_delta_m: float) -> dict:
    """
    Compute multi-order consequence cascade for a flood scenario.
    Returns infrastructure at risk, population breakdown, and narration.
    """
    # Current state
    current_infra = gs.get_infrastructure_at_risk(flood_geojson)

    # Expanded state (rough heuristic: 500m buffer per meter of water rise)
    buffer_m = int(water_level_delta_m * 500)
    new_infra = gs.get_infrastructure_at_expanded_level(flood_geojson, buffer_m)

    # Demographics (Jakarta averages)
    # Children under 5: ~8.5%, Elderly over 65: ~5.7%
    total_population = 128000  # This would come from Earth Engine population overlay
    children_under_5 = int(total_population * 0.085)
    elderly_over_65 = int(total_population * 0.057)

    total_hospitals = len(current_infra["hospitals"]) + len(new_infra["hospitals"])
    total_schools = len(current_infra["schools"]) + len(new_infra["schools"])
    total_power = len(current_infra["power_stations"]) + len(new_infra["power_stations"])

    return {
        "first_order": {
            "description": "Direct flood impact",
            "population_at_risk": total_population,
            "flood_area_expanded": True,
        },
        "second_order": {
            "description": "Infrastructure isolation",
            "hospitals_at_risk": total_hospitals,
            "hospital_names": [h["name"] for h in current_infra["hospitals"] + new_infra["hospitals"]],
            "schools_at_risk": total_schools,
            "newly_isolated_hospitals": [h["name"] for h in new_infra["hospitals"]],
        },
        "third_order": {
            "description": "Power and utilities cascade",
            "power_stations_at_risk": total_power,
            "power_station_names": [p["name"] for p in current_infra["power_stations"] + new_infra["power_stations"]],
            "estimated_residents_without_power": total_power * 80000,  # rough estimate per substation
        },
        "fourth_order": {
            "description": "Humanitarian impact",
            "children_under_5": children_under_5,
            "elderly_over_65": elderly_over_65,
            "hospital_patients_needing_evac": total_hospitals * 120,  # rough estimate per hospital
        },
        "summary": (
            f"At +{water_level_delta_m}m, population at risk reaches {total_population:,}. "
            f"This includes approximately {children_under_5:,} children under 5 "
            f"and {elderly_over_65:,} elderly over 65. "
            f"{total_hospitals} hospitals are in the flood zone. "
            f"{total_power} power substations at risk, potentially affecting "
            f"{total_power * 80000:,} residents."
        )
    }
```

---

## Performance Notes

- BigQuery geospatial queries with ST_DWITHIN use spatial indexing and are fast (1-3 seconds for the Jakarta materialized view).
- The clustering on the geometry column in the optimized table helps with queries that filter by location.
- The materialized view (groundsource_jakarta) is the biggest performance win — it reduces the scan from 2.6M rows to a few thousand.
- Cache static queries (monthly frequency, yearly trend) on service startup. They never change.
- BigQuery charges per bytes scanned. The materialized view also saves cost.

---

## Common Pitfalls

1. **Longitude/latitude order:** BigQuery uses `ST_GEOGPOINT(longitude, latitude)` — longitude FIRST. Jakarta is `ST_GEOGPOINT(106.845, -6.225)`, not `ST_GEOGPOINT(-6.225, 106.845)`. Getting this wrong puts your queries in the middle of the ocean.

2. **GeoParquet detection:** If BigQuery doesn't auto-detect the GEOGRAPHY column, you need the manual cast step. Check the schema BEFORE writing any queries.

3. **Invalid geometry:** 18% of Groundsource records have errors. Using `SAFE.ST_GEOGFROMGEOJSON()` and `make_valid => TRUE` handles most of these, but the optimized table creation step filters out rows that can't be parsed at all.

4. **Buffer as flood expansion proxy:** Using `ST_BUFFER` to simulate "what if water rises 2 meters" is a simplification. Real flood modeling uses elevation-based inundation. For the hackathon, this is acceptable — state it as an approximation in the demo.

5. **Query parameter types:** BigQuery is strict about parameter types. GEOGRAPHY parameters need special handling — pass the GeoJSON as a STRING parameter and use `ST_GEOGFROMGEOJSON()` inside the SQL, not as a GEOGRAPHY parameter directly.

---

## Verification Checklist (Run Before Moving to Step 1)

After completing all Phase 1 and Phase 2 steps, run each of these and confirm results:

- [ ] `hawkeye.groundsource` table exists with GEOGRAPHY column and ~2.2M+ rows
- [ ] `hawkeye.groundsource_jakarta` materialized view exists with 100+ rows
- [ ] `hawkeye.infrastructure` table exists with 50+ rows across hospital/school/shelter/power types
- [ ] Query 2 (flood frequency for Kampung Melayu) returns non-zero total_events
- [ ] Query 3 (infrastructure at risk) returns hospitals and schools when given a Jakarta polygon
- [ ] Query 5 (pattern match) returns at least 1 historical event
- [ ] Query 6 (monthly frequency) shows higher counts in Nov-Feb (monsoon season)

If any of these fail, debug before proceeding. The entire Analyst agent depends on these queries working correctly.
