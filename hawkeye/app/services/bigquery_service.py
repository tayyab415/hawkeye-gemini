"""BigQuery GroundsourceService — all SQL from HawkEye_BigQuery_Specification.md Phase 3+4."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from google.cloud import bigquery

logger = logging.getLogger(__name__)


class GroundsourceService:
    def __init__(
        self,
        project_id: str,
        client: bigquery.Client | None = None,
        ee_temporal_summary_hook: Callable[[dict[str, Any]], dict[str, Any] | None]
        | None = None,
    ):
        self.client = client or bigquery.Client(project=project_id)
        self.project_id = project_id
        self._monthly_frequency: list[dict] | None = None
        self._yearly_trend: list[dict] | None = None
        self._ee_temporal_summary_hook = ee_temporal_summary_hook
        self._latest_ee_temporal_summary: dict[str, Any] | None = None

    def _run_query(self, sql: str, params: list | None = None) -> list[dict]:
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        results = self.client.query(sql, job_config=job_config)
        return [dict(row) for row in results]

    def _normalize_ee_temporal_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        frame_ids = summary.get("frame_ids")
        if not isinstance(frame_ids, list):
            frame_ids = []

        growth_rate = summary.get("growth_rate_pct_per_hour")
        if growth_rate is None:
            growth = summary.get("growth_rate_pct")
            if isinstance(growth, dict):
                growth_rate = growth.get("rate_pct_per_hour")

        normalized = {
            "project_id": summary.get("project_id") or self.project_id,
            "runtime_mode": summary.get("runtime_mode"),
            "source": summary.get("source"),
            "confidence_label": summary.get("confidence_label"),
            "confidence_score": summary.get("confidence_score"),
            "area_sqkm": summary.get("area_sqkm"),
            "growth_rate_pct_per_hour": growth_rate,
            "frame_count": summary.get("frame_count") or len(frame_ids),
            "frame_ids": [str(frame_id) for frame_id in frame_ids if frame_id],
            "latest_frame_id": summary.get("latest_frame_id"),
            "latest_frame_timestamp": summary.get("latest_frame_timestamp"),
            "start_timestamp": summary.get("start_timestamp"),
            "end_timestamp": summary.get("end_timestamp"),
            "updated_at": summary.get("updated_at"),
        }
        return normalized

    def sync_ee_temporal_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        """
        Lightweight hook for EE temporal summary persistence/forwarding.

        Behavior is deterministic in local/test environments:
        - If no hook/table is configured, returns dry-run metadata (no side effects).
        - If a hook is provided, forwards normalized payload to the hook.
        - If env var HAWKEYE_EE_TEMPORAL_SUMMARY_TABLE is set, attempts BigQuery insert.
        """
        normalized = self._normalize_ee_temporal_summary(summary)
        self._latest_ee_temporal_summary = dict(normalized)

        if self._ee_temporal_summary_hook is not None:
            try:
                hook_result = self._ee_temporal_summary_hook(dict(normalized))
                return {
                    "status": "forwarded",
                    "mode": "hook",
                    "record": normalized,
                    "hook_result": hook_result,
                }
            except Exception as exc:
                logger.warning("[BIGQUERY] EE temporal summary hook failed: %s", exc)
                return {
                    "status": "error",
                    "mode": "hook",
                    "record": normalized,
                    "error": str(exc),
                }

        table_name = os.getenv("HAWKEYE_EE_TEMPORAL_SUMMARY_TABLE", "").strip()
        if not table_name:
            return {
                "status": "dry_run",
                "mode": "local",
                "record": normalized,
            }

        try:
            errors = self.client.insert_rows_json(table_name, [normalized])
            if errors:
                logger.warning(
                    "[BIGQUERY] EE temporal summary insert returned errors: %s", errors
                )
                return {
                    "status": "error",
                    "mode": "bigquery",
                    "table": table_name,
                    "record": normalized,
                    "errors": errors,
                }
            return {
                "status": "persisted",
                "mode": "bigquery",
                "table": table_name,
                "record": normalized,
            }
        except Exception as exc:
            logger.warning("[BIGQUERY] EE temporal summary insert failed: %s", exc)
            return {
                "status": "error",
                "mode": "bigquery",
                "table": table_name,
                "record": normalized,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Query 1: Historical floods overlapping with given flood polygon
    # ------------------------------------------------------------------
    def query_floods_intersecting_polygon(self, flood_geojson: str) -> list[dict]:
        logger.info(
            "[BIGQUERY] query_floods_intersecting_polygon called — executing spatial intersection query"
        )
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
        params = [
            bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson),
        ]
        results = self._run_query(sql, params)
        logger.info(
            f"[BIGQUERY] query_floods_intersecting_polygon returned {len(results)} rows"
        )
        return results

    # ------------------------------------------------------------------
    # Query 2: Flood frequency stats for a location
    # ------------------------------------------------------------------
    def get_flood_frequency(self, lat: float, lng: float, radius_km: float) -> dict:
        logger.info(
            f"[BIGQUERY] get_flood_frequency called — lat={lat}, lng={lng}, radius={radius_km}km"
        )
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
        row = results[0] if results else {}
        logger.info(
            f"[BIGQUERY] get_flood_frequency returned {row.get('total_events', 0)} total events"
        )
        return row

    # ------------------------------------------------------------------
    # Query 3: Infrastructure inside the flood polygon, grouped by type
    # ------------------------------------------------------------------
    def get_infrastructure_at_risk(self, flood_geojson: str) -> dict:
        logger.info(
            "[BIGQUERY] get_infrastructure_at_risk called — executing spatial query"
        )
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
        params = [
            bigquery.ScalarQueryParameter("flood_geojson", "STRING", flood_geojson),
        ]
        results = self._run_query(sql, params)
        logger.info(
            f"[BIGQUERY] get_infrastructure_at_risk returned {len(results)} rows"
        )

        grouped: dict[str, list[dict]] = {}
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

    # ------------------------------------------------------------------
    # Query 4: NEWLY at-risk infrastructure when water rises
    # ------------------------------------------------------------------
    def get_infrastructure_at_expanded_level(
        self, flood_geojson: str, buffer_meters: int
    ) -> dict:
        logger.info(
            f"[BIGQUERY] get_infrastructure_at_expanded_level called — buffer={buffer_meters}m"
        )
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
        logger.info(
            f"[BIGQUERY] get_infrastructure_at_expanded_level returned {len(results)} newly at-risk rows"
        )

        grouped: dict[str, list[dict]] = {}
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

    # ------------------------------------------------------------------
    # Query 5: Find historical events similar to current conditions
    # ------------------------------------------------------------------
    def find_pattern_match(
        self, current_area_sqkm: float, duration_estimate_days: int
    ) -> list[dict]:
        logger.info(
            f"[BIGQUERY] find_pattern_match called — area={current_area_sqkm} sqkm, duration={duration_estimate_days} days"
        )
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
            bigquery.ScalarQueryParameter(
                "min_dur", "INT64", max(1, duration_estimate_days - 5)
            ),
            bigquery.ScalarQueryParameter(
                "max_dur", "INT64", duration_estimate_days + 10
            ),
            bigquery.ScalarQueryParameter(
                "min_area", "FLOAT64", current_area_sqkm * 0.3
            ),
            bigquery.ScalarQueryParameter(
                "max_area", "FLOAT64", current_area_sqkm * 3.0
            ),
            bigquery.ScalarQueryParameter("target_area", "FLOAT64", current_area_sqkm),
            bigquery.ScalarQueryParameter(
                "target_dur", "INT64", duration_estimate_days
            ),
        ]
        results = self._run_query(sql, params)
        logger.info(f"[BIGQUERY] find_pattern_match returned {len(results)} matches")
        return results

    # ------------------------------------------------------------------
    # Query 6: Monthly flood frequency (cached)
    # ------------------------------------------------------------------
    def get_monthly_frequency(self) -> list[dict]:
        if self._monthly_frequency is not None:
            logger.info("[BIGQUERY] get_monthly_frequency called (cached)")
            return self._monthly_frequency
        logger.info("[BIGQUERY] get_monthly_frequency called — executing fresh query")
        sql = """
        SELECT EXTRACT(MONTH FROM start_date) AS month,
               COUNT(*) AS flood_count,
               ROUND(AVG(duration_days), 1) AS avg_duration
        FROM hawkeye.groundsource_jakarta
        GROUP BY month ORDER BY month
        """
        self._monthly_frequency = self._run_query(sql)
        logger.info(
            f"[BIGQUERY] get_monthly_frequency returned {len(self._monthly_frequency)} months"
        )
        return self._monthly_frequency

    # ------------------------------------------------------------------
    # Query 7: Year-over-year trend (cached)
    # ------------------------------------------------------------------
    def get_yearly_trend(self) -> list[dict]:
        if self._yearly_trend is not None:
            logger.info("[BIGQUERY] get_yearly_trend called (cached)")
            return self._yearly_trend
        logger.info("[BIGQUERY] get_yearly_trend called — executing fresh query")
        sql = """
        SELECT EXTRACT(YEAR FROM start_date) AS year,
               COUNT(*) AS flood_count,
               ROUND(AVG(area_sqkm), 2) AS avg_area
        FROM hawkeye.groundsource_jakarta
        GROUP BY year ORDER BY year
        """
        self._yearly_trend = self._run_query(sql)
        logger.info(
            f"[BIGQUERY] get_yearly_trend returned {len(self._yearly_trend)} years"
        )
        return self._yearly_trend

    # ------------------------------------------------------------------
    # Query 8: Flood hotspot intelligence by grid cell
    # ------------------------------------------------------------------
    def get_flood_hotspots(self) -> list[dict]:
        logger.info("[BIGQUERY] get_flood_hotspots called")
        sql = """
        WITH grid_cells AS (
          SELECT
            ROUND(ST_Y(ST_CENTROID(geometry)), 2) AS grid_lat,
            ROUND(ST_X(ST_CENTROID(geometry)), 2) AS grid_lng,
            COUNT(*) AS flood_count,
            AVG(duration_days) AS avg_duration,
            AVG(area_sqkm) AS avg_area,
            MAX(area_sqkm) AS max_area
          FROM hawkeye.groundsource_jakarta
          GROUP BY grid_lat, grid_lng
          HAVING flood_count > 100
        )
        SELECT
          grid_lat,
          grid_lng,
          flood_count,
          ROUND(avg_duration, 1) AS avg_duration,
          ROUND(avg_area, 1) AS avg_area,
          ROUND(max_area, 1) AS max_area
        FROM grid_cells
        ORDER BY flood_count DESC
        LIMIT 15
        """
        results = self._run_query(sql)
        logger.info(f"[BIGQUERY] get_flood_hotspots returned {len(results)} rows")
        return results

    # ------------------------------------------------------------------
    # Query 9: Flood temporal cluster intelligence
    # ------------------------------------------------------------------
    def get_flood_temporal_clusters(self) -> list[dict]:
        logger.info("[BIGQUERY] get_flood_temporal_clusters called")
        sql = """
        WITH ordered_events AS (
          SELECT
            start_date,
            area_sqkm,
            duration_days,
            LAG(start_date) OVER (ORDER BY start_date) AS prev_date
          FROM hawkeye.groundsource_jakarta
          WHERE start_date >= '2020-01-01'
        ),
        clusters AS (
          SELECT
            start_date,
            area_sqkm,
            duration_days,
            DATE_DIFF(start_date, prev_date, DAY) AS days_since_previous
          FROM ordered_events
          WHERE prev_date IS NOT NULL
        )
        SELECT
          CASE
            WHEN days_since_previous <= 2 THEN 'cascading (0-2 days)'
            WHEN days_since_previous <= 7 THEN 'follow-up (3-7 days)'
            ELSE 'independent (8+ days)'
          END AS cluster_type,
          COUNT(*) AS event_count,
          ROUND(AVG(area_sqkm), 1) AS avg_area,
          ROUND(AVG(duration_days), 1) AS avg_duration
        FROM clusters
        GROUP BY cluster_type
        ORDER BY event_count DESC
        """
        results = self._run_query(sql)
        logger.info(
            f"[BIGQUERY] get_flood_temporal_clusters returned {len(results)} rows"
        )
        return results

    # ------------------------------------------------------------------
    # Query 10: Infrastructure exposure ranking by flood proximity
    # ------------------------------------------------------------------
    def get_infrastructure_exposure_ranking(self) -> list[dict]:
        logger.info("[BIGQUERY] get_infrastructure_exposure_ranking called")
        sql = """
        SELECT
          i.name,
          i.type,
          i.latitude,
          i.longitude,
          COUNT(g.start_date) AS flood_exposure_count,
          MAX(g.area_sqkm) AS worst_flood_area,
          MAX(g.duration_days) AS worst_flood_duration
        FROM hawkeye.infrastructure i
        JOIN hawkeye.groundsource_jakarta g
          ON ST_DWITHIN(i.location, ST_CENTROID(g.geometry), 1000)
        GROUP BY i.name, i.type, i.latitude, i.longitude
        ORDER BY flood_exposure_count DESC
        LIMIT 20
        """
        results = self._run_query(sql)
        logger.info(
            f"[BIGQUERY] get_infrastructure_exposure_ranking returned {len(results)} rows"
        )
        return results

    # ------------------------------------------------------------------
    # Query 11: Yearly flood progression (top events per year)
    # ------------------------------------------------------------------
    def get_yearly_flood_progression(self) -> list[dict]:
        logger.info("[BIGQUERY] get_yearly_flood_progression called")
        sql = """
        SELECT
          EXTRACT(YEAR FROM start_date) AS year,
          start_date,
          end_date,
          area_sqkm,
          duration_days,
          ST_ASGEOJSON(geometry) AS geojson
        FROM hawkeye.groundsource_jakarta
        WHERE area_sqkm > 5
          AND EXTRACT(YEAR FROM start_date) >= 2020
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY EXTRACT(YEAR FROM start_date)
          ORDER BY area_sqkm DESC
        ) <= 3
        ORDER BY start_date
        """
        results = self._run_query(sql)
        logger.info(
            f"[BIGQUERY] get_yearly_flood_progression returned {len(results)} rows"
        )
        return results
