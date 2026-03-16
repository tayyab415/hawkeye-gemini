"""Direct BigQuery inspection for HawkEye GroundsourceService data flow validation.

Run from `hawkeye/`:
    source .venv/bin/activate
    export GOOGLE_APPLICATION_CREDENTIALS=credentials/hawkeye-runtime-key.json
    python tests/inspect_bigquery.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.bigquery_service import GroundsourceService


ROOT = Path(__file__).resolve().parent.parent
FLOOD_GEOJSON_PATH = ROOT / "data" / "geojson" / "flood_extent.geojson"
DEFAULT_CREDENTIALS = ROOT / "credentials" / "hawkeye-runtime-key.json"
DEFAULT_PROJECT_ID = "gen-lang-client-0261050164"


def _normalize_geojson_geometry(data: dict[str, Any]) -> str:
    geojson_type = data.get("type")
    if geojson_type == "Feature":
        geometry = data.get("geometry")
    elif geojson_type == "FeatureCollection":
        features = data.get("features") or []
        geometry = features[0].get("geometry") if features and isinstance(features[0], dict) else None
    else:
        geometry = data

    if not isinstance(geometry, dict) or "type" not in geometry:
        raise ValueError("Flood GeoJSON does not contain a valid geometry object")
    return json.dumps(geometry)


def _timed(name: str, fn: Callable[[], Any], timings: list[tuple[str, float]]) -> Any:
    start = time.time()
    result = fn()
    elapsed = time.time() - start
    timings.append((name, elapsed))
    print(f"\n### {name}")
    print(f"- Time: `{elapsed:.2f}s`")
    return result


def _run_worst_case_date_query(
    client: bigquery.Client, flood_lat: float, flood_lng: float, radius_km: float
) -> dict[str, Any]:
    sql = """
    SELECT start_date, end_date, duration_days, area_sqkm
    FROM hawkeye.groundsource_jakarta
    WHERE ST_DWITHIN(geometry, ST_GEOGPOINT(@lng, @lat), @radius_m)
    ORDER BY duration_days DESC, start_date DESC
    LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("lng", "FLOAT64", flood_lng),
        bigquery.ScalarQueryParameter("lat", "FLOAT64", flood_lat),
        bigquery.ScalarQueryParameter("radius_m", "INT64", int(radius_km * 1000)),
    ]
    rows = client.query(
        sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
    ).result()
    for row in rows:
        return dict(row)
    return {}


def _run_cascade_expansion_sql(
    client: bigquery.Client, flood_geojson: str, buffer_meters: int
) -> dict[str, Any]:
    sql = """
    WITH original_flood AS (
      SELECT ST_GEOGFROMGEOJSON(@flood_geojson, make_valid => TRUE) AS geometry
    ),
    expanded_flood AS (
      SELECT ST_BUFFER(geometry, @buffer_meters) AS geometry
      FROM original_flood
    ),
    currently_at_risk AS (
      SELECT i.id
      FROM hawkeye.infrastructure i, original_flood f
      WHERE ST_INTERSECTS(i.location, f.geometry)
    ),
    additional_points AS (
      SELECT COUNT(*) AS additional_count
      FROM hawkeye.infrastructure i, expanded_flood ef
      WHERE ST_INTERSECTS(i.location, ef.geometry)
        AND i.id NOT IN (SELECT id FROM currently_at_risk)
    )
    SELECT
      ROUND(ST_AREA((SELECT geometry FROM original_flood)) / 1000000, 2) AS original_area_sqkm,
      ROUND(ST_AREA((SELECT geometry FROM expanded_flood)) / 1000000, 2) AS expanded_area_sqkm,
      (SELECT additional_count FROM additional_points) AS additional_infrastructure_points
    """
    params = [
        bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson),
        bigquery.ScalarQueryParameter("buffer_meters", "INT64", buffer_meters),
    ]
    rows = client.query(
        sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
    ).result()
    for row in rows:
        return dict(row)
    return {}


def _trend_direction(yearly: list[dict[str, Any]]) -> str:
    if len(yearly) < 2:
        return "insufficient_data"
    tail = yearly[-5:] if len(yearly) >= 5 else yearly
    first = float(tail[0].get("flood_count", 0) or 0)
    last = float(tail[-1].get("flood_count", 0) or 0)
    if last > first:
        return "increasing"
    if last < first:
        return "decreasing"
    return "flat"


def main() -> int:
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(DEFAULT_CREDENTIALS))
    project_id = os.getenv("GCP_PROJECT_ID", DEFAULT_PROJECT_ID)

    print("# BigQuery Inspection Results")
    print(f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`")
    print(f"- Project ID: `{project_id}`")
    print(
        f"- GOOGLE_APPLICATION_CREDENTIALS: `{os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')}`"
    )
    print(f"- Flood GeoJSON source: `{FLOOD_GEOJSON_PATH}`")

    if not FLOOD_GEOJSON_PATH.exists():
        print("\nERROR: flood extent file not found.")
        return 1

    with open(FLOOD_GEOJSON_PATH) as f:
        flood_feature = json.load(f)
    flood_geometry_json = _normalize_geojson_geometry(flood_feature)

    gs = GroundsourceService(project_id=project_id)
    timings: list[tuple[str, float]] = []

    # Completeness call for all GroundsourceService methods (Query 1 in service).
    intersecting = _timed(
        "query_floods_intersecting_polygon",
        lambda: gs.query_floods_intersecting_polygon(flood_geometry_json),
        timings,
    )
    print(f"- Intersecting historical floods: `{len(intersecting)}`")
    if intersecting:
        top = intersecting[0]
        print(
            "- Sample event: "
            f"`start_date={top.get('start_date')}`, "
            f"`duration_days={top.get('duration_days')}`, "
            f"`overlap_sqkm={top.get('overlap_sqkm')}`"
        )

    frequency = _timed(
        "get_flood_frequency(-6.225, 106.855, 10)",
        lambda: gs.get_flood_frequency(-6.225, 106.855, 10),
        timings,
    )
    worst_case = _timed(
        "raw_worst_case_event_date_query",
        lambda: _run_worst_case_date_query(gs.client, -6.225, 106.855, 10),
        timings,
    )
    print(f"- total_events: `{frequency.get('total_events')}`")
    print(f"- avg_duration_days: `{frequency.get('avg_duration_days')}`")
    print(
        "- worst_case_event_date: "
        f"`{worst_case.get('start_date')}` (duration_days={worst_case.get('duration_days')})"
    )

    infra = _timed(
        "get_infrastructure_at_risk(flood_geojson)",
        lambda: gs.get_infrastructure_at_risk(flood_geometry_json),
        timings,
    )
    hospital_names = [h.get("name", "Unknown") for h in infra.get("hospitals", [])]
    print(f"- hospitals_in_zone: `{len(hospital_names)}`")
    print(f"- hospital_names: {json.dumps(hospital_names, ensure_ascii=True)}")
    print(f"- schools_in_zone: `{len(infra.get('schools', []))}`")
    print(f"- shelters_in_zone: `{len(infra.get('shelters', []))}`")
    print(f"- power_stations_in_zone: `{len(infra.get('power_stations', []))}`")

    expanded = _timed(
        "get_infrastructure_at_expanded_level(flood_geojson, 1000)",
        lambda: gs.get_infrastructure_at_expanded_level(flood_geometry_json, 1000),
        timings,
    )
    new_hospital_names = [h.get("name", "Unknown") for h in expanded.get("hospitals", [])]
    print(f"- newly_at_risk_hospitals: `{len(new_hospital_names)}`")
    print(f"- newly_at_risk_hospital_names: {json.dumps(new_hospital_names, ensure_ascii=True)}")
    print(f"- total_newly_at_risk: `{expanded.get('newly_at_risk')}`")

    pattern = _timed(
        "find_pattern_match(15.0, 4)",
        lambda: gs.find_pattern_match(15.0, 4),
        timings,
    )
    print("- top_3_pattern_matches:")
    for idx, row in enumerate(pattern[:3], start=1):
        print(
            f"  {idx}. start_date={row.get('start_date')}, "
            f"end_date={row.get('end_date')}, area_sqkm={row.get('area_sqkm')}, "
            f"duration_days={row.get('duration_days')}"
        )

    monthly = _timed("get_monthly_frequency()", gs.get_monthly_frequency, timings)
    print("- monthly_flood_counts:")
    highest_month = None
    for row in monthly:
        month = int(row.get("month", 0) or 0)
        flood_count = int(row.get("flood_count", 0) or 0)
        print(f"  - month={month:02d}: flood_count={flood_count}")
        if highest_month is None or flood_count > int(highest_month.get("flood_count", 0) or 0):
            highest_month = row
    if highest_month:
        print(
            f"- highest_month: month={highest_month.get('month')}, "
            f"flood_count={highest_month.get('flood_count')}"
        )

    yearly = _timed("get_yearly_trend()", gs.get_yearly_trend, timings)
    tail = yearly[-5:] if len(yearly) >= 5 else yearly
    print("- last_5_years:")
    for row in tail:
        print(
            f"  - year={row.get('year')}: flood_count={row.get('flood_count')}, "
            f"avg_area={row.get('avg_area')}"
        )
    print(f"- trend_direction: `{_trend_direction(yearly)}`")

    expansion_sql = _timed(
        "raw_st_buffer_cascade_query",
        lambda: _run_cascade_expansion_sql(gs.client, flood_geometry_json, 1000),
        timings,
    )
    original_area = float(expansion_sql.get("original_area_sqkm") or 0.0)
    expanded_area = float(expansion_sql.get("expanded_area_sqkm") or 0.0)
    additional_points = int(expansion_sql.get("additional_infrastructure_points") or 0)
    area_ratio = (expanded_area / original_area) if original_area > 0 else 0.0
    print(f"- original_area_sqkm: `{original_area:.2f}`")
    print(f"- expanded_area_sqkm: `{expanded_area:.2f}`")
    print(f"- expanded_vs_original_ratio: `{area_ratio:.2f}x`")
    print(f"- additional_infrastructure_points_in_expanded_zone: `{additional_points}`")

    print("\n## Timing Summary")
    print("| Query | Time (s) | Under 3s? |")
    print("|---|---:|:---:|")
    for name, elapsed in timings:
        print(f"| `{name}` | {elapsed:.2f} | {'YES' if elapsed < 3 else 'NO'} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
