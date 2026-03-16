"""
Analyst Agent Tools — Track 3B
Intelligence analysis, historical patterns, cascade computation, and route safety.
Uses BigQuery GroundsourceService for data queries.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from typing import Any

from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform
import pyproj

from google import genai
from google.genai import types

# Import the services
from app.services.bigquery_service import GroundsourceService
from app.services.earth_engine_service import EarthEngineService
from app.services.maps_service import MapsService

logger = logging.getLogger(__name__)

# Lazy-initialized services
_groundsource: GroundsourceService | None = None

# Cache for heavy tool payloads that exceed Gemini Live API frame limits.
# Tool functions store full results here; main.py retrieves them for UI events.
_last_flood_extent_full: dict[str, Any] | None = None
_earth_engine: EarthEngineService | None = None
_maps_service: MapsService | None = None
_genai_client: genai.Client | None = None


def _get_groundsource() -> GroundsourceService:
    global _groundsource
    if _groundsource is None:
        project_id = os.getenv("GCP_PROJECT_ID", "")
        _groundsource = GroundsourceService(project_id=project_id)
    return _groundsource


def _get_earth_engine() -> EarthEngineService:
    global _earth_engine
    if _earth_engine is None:
        project_id = os.getenv("GCP_PROJECT_ID", "")
        _earth_engine = EarthEngineService(project_id=project_id)
    return _earth_engine


def get_earth_engine_service() -> EarthEngineService:
    """Expose the shared EarthEngineService instance for HTTP orchestration routes."""
    return _get_earth_engine()


def _get_maps_service() -> MapsService:
    global _maps_service
    if _maps_service is None:
        api_key = os.getenv("GCP_MAPS_API_KEY", "")
        _maps_service = MapsService(api_key=api_key)
    return _maps_service


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        api_key = os.getenv("GCP_API_KEY", "")
        _genai_client = genai.Client(api_key=api_key)
    return _genai_client


def _resolve_flood_geojson(
    flood_geojson: str | dict[str, Any] | None,
) -> str | dict[str, Any]:
    """Resolve a flood_geojson argument, falling back to the cached extent.

    Since ``get_flood_extent()`` now returns a lightweight summary (to avoid
    exceeding Gemini Live API frame limits), the model may pass an empty or
    placeholder string for tools that need the actual GeoJSON geometry.  This
    helper transparently falls back to the cached full payload so downstream
    tools always receive valid GeoJSON.
    """
    # If the caller provided something that looks usable, return it as-is.
    if isinstance(flood_geojson, dict) and flood_geojson:
        return flood_geojson
    if isinstance(flood_geojson, str) and flood_geojson.strip().startswith("{"):
        return flood_geojson

    # Fall back to the module-level cache populated by get_flood_extent().
    cached = _last_flood_extent_full
    if cached and isinstance(cached.get("geojson"), dict):
        logger.info("[ANALYST] flood_geojson resolved from cached flood extent")
        return cached["geojson"]

    raise ValueError(
        "No flood GeoJSON available — call get_flood_extent first to populate "
        "the flood extent cache."
    )


def _normalize_geojson_geometry(geojson_input: str | dict[str, Any]) -> str:
    """
    Normalize GeoJSON to a geometry JSON string for BigQuery ST_GEOGFROMGEOJSON.

    BigQuery expects a raw Geometry object, not Feature/FeatureCollection wrappers.
    """
    data = (
        json.loads(geojson_input) if isinstance(geojson_input, str) else geojson_input
    )
    if not isinstance(data, dict):
        raise ValueError("GeoJSON input must be a dict or JSON object string")

    geojson_type = data.get("type")
    geometry: dict[str, Any] | None
    if geojson_type == "Feature":
        geometry = data.get("geometry")
    elif geojson_type == "FeatureCollection":
        features = data.get("features", [])
        first_feature = features[0] if features else None
        geometry = (
            first_feature.get("geometry") if isinstance(first_feature, dict) else None
        )
    else:
        geometry = data

    if not isinstance(geometry, dict) or "type" not in geometry:
        raise ValueError("GeoJSON geometry is missing or invalid")

    return json.dumps(geometry)


def _parse_geojson_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    """Parse GeoJSON payloads defensively from JSON strings, dicts, or Python-literal strings."""
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise ValueError(f"Unsupported GeoJSON payload type: {type(payload)}")

    candidates = [payload.strip()]
    trimmed = payload.strip()
    if "{" in trimmed and "}" in trimmed:
        start = trimmed.find("{")
        end = trimmed.rfind("}") + 1
        if 0 <= start < end <= len(trimmed):
            extracted = trimmed[start:end]
            if extracted not in candidates:
                candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass

    raise ValueError("Unable to parse GeoJSON payload")


# ─────────────────────────────────────────────────────────────────────
# Tool 1: query_historical_floods (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────


def query_historical_floods(lat: float, lng: float, radius_km: float) -> dict:
    """
    Query Groundsource database for historical flood events near a location.

    Args:
        lat: Latitude (-6.225 for Jakarta center)
        lng: Longitude (106.845 for Jakarta center)
        radius_km: Search radius in kilometers

    Returns:
        Historical flood statistics and pattern matches
    """
    logger.info(
        f"Analyst: Querying historical floods at ({lat}, {lng}), radius={radius_km}km"
    )
    logger.info(
        f"[ANALYST TOOL] query_historical_floods called with lat={lat}, lng={lng}, radius_km={radius_km}"
    )

    try:
        # Query BigQuery for flood frequency
        logger.info(
            "[ANALYST TOOL] query_historical_floods — calling get_flood_frequency"
        )
        frequency = _get_groundsource().get_flood_frequency(lat, lng, radius_km)
        logger.info(
            f"[ANALYST TOOL] query_historical_floods — BigQuery returned: {frequency.get('total_events', 0)} events"
        )

        # Get monthly pattern
        logger.info(
            "[ANALYST TOOL] query_historical_floods — calling get_monthly_frequency"
        )
        monthly = _get_groundsource().get_monthly_frequency()
        logger.info(
            f"[ANALYST TOOL] query_historical_floods — BigQuery returned: {len(monthly) if isinstance(monthly, list) else 'N/A'} monthly records"
        )

        # Get yearly trend
        logger.info("[ANALYST TOOL] query_historical_floods — calling get_yearly_trend")
        yearly = _get_groundsource().get_yearly_trend()
        logger.info(
            f"[ANALYST TOOL] query_historical_floods — BigQuery returned: {len(yearly) if isinstance(yearly, list) else 'N/A'} yearly records"
        )

        # Find pattern matches based on average stats
        avg_area = frequency.get("avg_area_sqkm", 10.0) or 10.0
        avg_duration = int(frequency.get("avg_duration_days", 3) or 3)
        logger.info(
            f"[ANALYST TOOL] query_historical_floods — calling find_pattern_match(area={avg_area}, duration={avg_duration})"
        )
        patterns = _get_groundsource().find_pattern_match(avg_area, avg_duration)
        logger.info(
            f"[ANALYST TOOL] query_historical_floods — BigQuery returned: {len(patterns) if isinstance(patterns, list) else 'N/A'} pattern matches"
        )

        return {
            "query_lat": lat,
            "query_lng": lng,
            "frequency": frequency,
            "monthly_pattern": monthly,
            "yearly_trend": yearly,
            "closest_historical_matches": patterns,
            "summary": (
                f"This area has experienced {frequency.get('total_events', 0)} flood events "
                f"since 2000 according to Google's Groundsource database. "
                f"Average duration: {frequency.get('avg_duration_days', 'unknown')} days. "
                f"Worst case: {frequency.get('max_duration_days', 'unknown')} days."
            ),
        }
    except Exception as e:
        logger.error(f"[ANALYST TOOL] query_historical_floods — ERROR: {e}")
        return {
            "error": str(e),
            "summary": "Unable to retrieve historical flood data at this time.",
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 2: get_flood_hotspots
# ─────────────────────────────────────────────────────────────────────


def get_flood_hotspots() -> dict:
    """Get top Jakarta flood hotspot areas based on historical events."""
    logger.info("[ANALYST TOOL] get_flood_hotspots called")

    try:
        hotspots = _get_groundsource().get_flood_hotspots()
        hotspot_count = len(hotspots)
        logger.info(
            "[ANALYST TOOL] get_flood_hotspots — Groundsource returned: "
            f"{hotspot_count} hotspots"
        )

        top_flood_count = hotspots[0].get("flood_count", 0) if hotspots else 0
        return {
            "hotspot_count": hotspot_count,
            "hotspots": hotspots,
            "summary": (
                f"Identified {hotspot_count} flood hotspot areas. "
                f"Top area has {top_flood_count} historical events."
            ),
        }
    except Exception as e:
        logger.error(f"[ANALYST TOOL] get_flood_hotspots — ERROR: {e}")
        return {
            "hotspot_count": 0,
            "hotspots": [],
            "summary": "Identified 0 flood hotspot areas. Top area has 0 historical events.",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 3: get_infrastructure_vulnerability
# ─────────────────────────────────────────────────────────────────────


def get_infrastructure_vulnerability() -> dict:
    """Get ranked infrastructure vulnerability based on historical flood exposure."""
    logger.info("[ANALYST TOOL] get_infrastructure_vulnerability called")

    try:
        ranking = _get_groundsource().get_infrastructure_exposure_ranking()
        total_ranked = len(ranking)
        logger.info(
            "[ANALYST TOOL] get_infrastructure_vulnerability — Groundsource returned: "
            f"{total_ranked} facilities"
        )

        hospitals = [
            facility
            for facility in ranking
            if str(facility.get("type", "")).lower() == "hospital"
        ]
        schools = [
            facility
            for facility in ranking
            if str(facility.get("type", "")).lower() == "school"
        ]

        most_vulnerable_hospital = hospitals[0] if hospitals else None
        most_vulnerable_school = schools[0] if schools else None

        if ranking:
            top = ranking[0]
            summary = (
                f"Top vulnerable facility is {top.get('name', 'unknown')} "
                f"({top.get('type', 'unknown')}) with "
                f"{top.get('flood_exposure_count', 0)} flood exposures."
            )
        else:
            summary = "No data"

        return {
            "total_ranked": total_ranked,
            "most_vulnerable_hospital": most_vulnerable_hospital,
            "most_vulnerable_school": most_vulnerable_school,
            "ranking": ranking,
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"[ANALYST TOOL] get_infrastructure_vulnerability — ERROR: {e}")
        return {
            "total_ranked": 0,
            "most_vulnerable_hospital": None,
            "most_vulnerable_school": None,
            "ranking": [],
            "summary": "No data",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 4: get_flood_cascade_risk
# ─────────────────────────────────────────────────────────────────────


def get_flood_cascade_risk() -> dict:
    """Estimate flood cascade probability based on temporal event clusters."""
    logger.info("[ANALYST TOOL] get_flood_cascade_risk called")

    try:
        clusters = _get_groundsource().get_flood_temporal_clusters()
        logger.info(
            "[ANALYST TOOL] get_flood_cascade_risk — Groundsource returned: "
            f"{len(clusters)} cluster types"
        )

        cascading_cluster = next(
            (
                cluster
                for cluster in clusters
                if "cascading" in str(cluster.get("cluster_type", "")).lower()
            ),
            None,
        )

        total_events = sum(
            int(cluster.get("event_count", 0) or 0) for cluster in clusters
        )
        cascading_events = (
            int(cascading_cluster.get("event_count", 0) or 0)
            if cascading_cluster
            else 0
        )
        cascade_probability_pct = (
            round((cascading_events / total_events) * 100, 1) if total_events else 0.0
        )

        return {
            "clusters": clusters,
            "cascade_probability_pct": cascade_probability_pct,
            "summary": (
                f"{cascade_probability_pct}% of floods are followed by another event "
                "within 48 hours. Prepare for secondary surge."
            ),
        }
    except Exception as e:
        logger.error(f"[ANALYST TOOL] get_flood_cascade_risk — ERROR: {e}")
        return {
            "clusters": [],
            "cascade_probability_pct": 0.0,
            "summary": (
                "0.0% of floods are followed by another event within 48 hours. "
                "Prepare for secondary surge."
            ),
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 5: get_flood_extent (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────


def get_last_flood_extent_full() -> dict[str, Any] | None:
    """Return the full cached payload from the last get_flood_extent call.

    main.py uses this to build map overlays / EE update events that need
    the heavy GeoJSON + temporal frames that are too large to send back
    through the Gemini Live API function_response.
    """
    return _last_flood_extent_full


def _summarize_geojson(geojson: Any) -> dict[str, Any]:
    """Return a lightweight summary of a GeoJSON FeatureCollection."""
    if not isinstance(geojson, dict):
        return {"available": False}
    features = geojson.get("features", [])
    return {
        "available": True,
        "feature_count": len(features),
        "type": geojson.get("type", "unknown"),
    }


def get_flood_extent() -> dict:
    """
    Get current flood extent from Earth Engine analysis.

    Returns a lightweight summary suitable for the Gemini Live API.
    The full payload (including GeoJSON) is cached and retrievable
    via ``get_last_flood_extent_full()``.
    """
    global _last_flood_extent_full
    logger.info("Analyst: Getting flood extent")

    try:
        earth_engine = _get_earth_engine()
        geojson = earth_engine.get_flood_extent_geojson()
        runtime_payload = earth_engine.get_flood_extent_runtime_payload()
        full_response = {
            "geojson": geojson,
            "area_sqkm": runtime_payload.get("area_sqkm"),
            "growth_rate_pct": runtime_payload.get("growth_rate_pct"),
            "metadata": runtime_payload.get("metadata"),
            "runtime_layers": runtime_payload.get("runtime_layers", []),
            "temporal_frames": runtime_payload.get("temporal_frames", {}),
            "temporal_playback": runtime_payload.get("temporal_playback", {}),
            "temporal_summary": runtime_payload.get("temporal_summary", {}),
            "multisensor_fusion": runtime_payload.get("multisensor_fusion", {}),
            "ee_runtime": runtime_payload.get("ee_runtime"),
        }
        get_live_task_status = getattr(
            earth_engine, "get_latest_live_analysis_task_status", None
        )
        get_live_task_result = getattr(
            earth_engine, "get_live_analysis_task_result", None
        )
        if callable(get_live_task_status):
            live_task = get_live_task_status()
            if isinstance(live_task, dict):
                task_id = live_task.get("task_id")
                if (
                    isinstance(task_id, str)
                    and task_id
                    and callable(get_live_task_result)
                ):
                    live_result = get_live_task_result(task_id)
                    if live_result is not None:
                        runtime_candidate = (
                            live_result.get("runtime_payload", live_result)
                            if isinstance(live_result, dict)
                            else None
                        )
                        if isinstance(runtime_candidate, dict) and isinstance(
                            runtime_candidate.get("ee_runtime"), dict
                        ):
                            runtime_payload = runtime_candidate
                            full_response.update(
                                {
                                    "area_sqkm": runtime_payload.get("area_sqkm"),
                                    "growth_rate_pct": runtime_payload.get(
                                        "growth_rate_pct"
                                    ),
                                    "metadata": runtime_payload.get("metadata"),
                                    "runtime_layers": runtime_payload.get(
                                        "runtime_layers", []
                                    ),
                                    "temporal_frames": runtime_payload.get(
                                        "temporal_frames", {}
                                    ),
                                    "temporal_playback": runtime_payload.get(
                                        "temporal_playback", {}
                                    ),
                                    "temporal_summary": runtime_payload.get(
                                        "temporal_summary", {}
                                    ),
                                    "multisensor_fusion": runtime_payload.get(
                                        "multisensor_fusion", {}
                                    ),
                                    "ee_runtime": runtime_payload.get("ee_runtime"),
                                }
                            )
                        live_task = {**live_task, "result": live_result}
                full_response["live_analysis_task"] = live_task
                full_response["live_task"] = live_task

        # Cache the full payload for main.py to retrieve for UI events.
        _last_flood_extent_full = full_response
        logger.info(
            "[FLOOD EXTENT] Cached full payload (%d bytes), returning summary to model",
            len(json.dumps(full_response, default=str)),
        )

        # Return only a lightweight summary to the model so it doesn't
        # exceed the Gemini Live API WebSocket frame limit.
        ee_runtime = full_response.get("ee_runtime") or {}
        return {
            "geojson_summary": _summarize_geojson(geojson),
            "area_sqkm": full_response.get("area_sqkm"),
            "growth_rate_pct": full_response.get("growth_rate_pct"),
            "metadata": full_response.get("metadata"),
            "runtime_status": ee_runtime.get("status"),
            "runtime_mode": ee_runtime.get("runtime_mode"),
            "_full_payload_cached": True,
        }
    except Exception as e:
        logger.error(f"Error getting flood extent: {e}")
        runtime_error = {
            "code": "get_flood_extent_failed",
            "message": str(e),
        }
        return {
            "error": str(e),
            "geojson": None,
            "area_sqkm": 0,
            "growth_rate_pct": {"rate_pct_per_hour": 0.0, "source": "error"},
            "metadata": {},
            "runtime_layers": [],
            "temporal_frames": {},
            "temporal_playback": {},
            "temporal_summary": {},
            "multisensor_fusion": {},
            "ee_runtime": {
                "runtime_mode": "error",
                "status": "error",
                "layers": [],
                "temporal_frames": {},
                "temporal_playback": {},
                "temporal_summary": {},
                "multisensor_fusion": {},
                "provenance": {"project_id": os.getenv("GCP_PROJECT_ID", "")},
                "confidence": {
                    "label": "UNKNOWN",
                    "score": None,
                    "source": "runtime_error",
                },
                "error": runtime_error,
            },
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 3: get_infrastructure_at_risk (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────


def get_infrastructure_at_risk(flood_geojson: str) -> dict:
    """
    Query infrastructure inside the current flood zone.

    Args:
        flood_geojson: Flood extent as GeoJSON string

    Returns:
        Hospitals, schools, shelters, power stations at risk
    """
    logger.info("Analyst: Getting infrastructure at risk")

    try:
        flood_geojson = _resolve_flood_geojson(flood_geojson)  # type: ignore[assignment]
        normalized_geojson = _normalize_geojson_geometry(flood_geojson)
        logger.info(
            "[ANALYST TOOL] get_infrastructure_at_risk called with normalized geometry "
            f"({len(normalized_geojson)} chars)"
        )
        logger.info(
            "[ANALYST TOOL] get_infrastructure_at_risk — calling BigQuery get_infrastructure_at_risk"
        )
        result = _get_groundsource().get_infrastructure_at_risk(normalized_geojson)
        logger.info(
            f"[ANALYST TOOL] get_infrastructure_at_risk — BigQuery returned: "
            f"{result.get('total_at_risk', 0)} facilities "
            f"(hospitals={len(result.get('hospitals', []))}, "
            f"schools={len(result.get('schools', []))}, "
            f"shelters={len(result.get('shelters', []))}, "
            f"power={len(result.get('power_stations', []))})"
        )
        return result
    except Exception as e:
        logger.error(f"[ANALYST TOOL] get_infrastructure_at_risk — ERROR: {e}")
        return {
            "total_at_risk": 0,
            "hospitals": [],
            "schools": [],
            "shelters": [],
            "power_stations": [],
            "water_treatment": [],
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 4: get_population_at_risk (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────


def get_population_at_risk(flood_geojson: str) -> dict:
    """
    Estimate population demographics at risk in the flood zone.

    Args:
        flood_geojson: Flood extent as GeoJSON string

    Returns:
        Population breakdown with vulnerable group estimates
    """
    logger.info("Analyst: Computing population at risk")

    try:
        # Get flood area
        area_sqkm = _get_earth_engine().get_flood_area_sqkm()

        # Jakarta population density ~15,000/km² in affected areas
        estimated_density = 15000  # people per km²
        total_population = int(area_sqkm * estimated_density)

        # Jakarta demographic ratios
        children_under_5 = int(total_population * 0.085)
        elderly_over_65 = int(total_population * 0.057)

        return {
            "total": total_population,
            "children_under_5": children_under_5,
            "elderly_over_65": elderly_over_65,
            "estimated_hospital_patients": None,
            "population_density_per_km2": estimated_density,
            "confidence": "medium",
            "caveats": [
                "Based on average population density",
                "Actual numbers may vary by neighborhood",
            ],
        }
    except Exception as e:
        logger.error(f"Error computing population: {e}")
        return {
            "total": 0,
            "children_under_5": 0,
            "elderly_over_65": 0,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 5: compute_cascade (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────


def compute_cascade(flood_geojson: str, water_level_delta_m: float) -> dict:
    """
    Compute multi-order consequence cascade for a flood scenario.

    Args:
        flood_geojson: Current flood extent as GeoJSON string
        water_level_delta_m: Water level rise to simulate (e.g., 2.0 for +2 meters)

    Returns:
        Structured 4-order cascade with population, infrastructure, and humanitarian impact
    """
    logger.info(f"Analyst: Computing cascade for +{water_level_delta_m}m water rise")
    logger.info(
        f"[ANALYST TOOL] compute_cascade called with water_level_delta_m={water_level_delta_m}"
    )

    try:
        flood_geojson = _resolve_flood_geojson(flood_geojson)  # type: ignore[assignment]
        normalized_geojson = _normalize_geojson_geometry(flood_geojson)

        # Get current infrastructure at risk
        logger.info(
            "[ANALYST TOOL] compute_cascade — calling BigQuery get_infrastructure_at_risk (current)"
        )
        current_infra = _get_groundsource().get_infrastructure_at_risk(
            normalized_geojson
        )
        logger.info(
            f"[ANALYST TOOL] compute_cascade — BigQuery returned: {current_infra.get('total_at_risk', 0)} currently at risk"
        )

        # Calculate buffer (rough heuristic: 500m per meter of water rise)
        buffer_m = int(water_level_delta_m * 500)

        # Get newly at-risk infrastructure at expanded level
        logger.info(
            f"[ANALYST TOOL] compute_cascade — calling BigQuery get_infrastructure_at_expanded_level (buffer={buffer_m}m)"
        )
        new_infra = _get_groundsource().get_infrastructure_at_expanded_level(
            normalized_geojson, buffer_m
        )
        logger.info(
            f"[ANALYST TOOL] compute_cascade — BigQuery returned: {new_infra.get('total_at_risk', 0)} newly at risk"
        )

        # Get population estimates
        pop_data = get_population_at_risk(normalized_geojson)
        total_population = pop_data.get("total", 0)
        children_under_5 = pop_data.get("children_under_5", 0)
        elderly_over_65 = pop_data.get("elderly_over_65", 0)
        current_hospitals = current_infra.get("hospitals", [])
        new_hospitals = new_infra.get("hospitals", [])
        hospitals_at_risk = [
            {
                "name": hospital.get("name", "Unknown Hospital"),
                "latitude": hospital.get("latitude"),
                "longitude": hospital.get("longitude"),
            }
            for hospital in current_hospitals + new_hospitals
            if hospital.get("latitude") is not None
            and hospital.get("longitude") is not None
        ]

        # Totals
        total_hospitals = len(current_hospitals) + len(new_hospitals)
        total_schools = len(current_infra.get("schools", [])) + len(
            new_infra.get("schools", [])
        )
        total_power = len(current_infra.get("power_stations", [])) + len(
            new_infra.get("power_stations", [])
        )

        # Build cascade
        cascade = {
            "first_order": {
                "description": "Direct flood impact",
                "population_at_risk": total_population,
                "flood_area_expanded": True,
                "water_level_delta_m": water_level_delta_m,
            },
            "second_order": {
                "description": "Infrastructure isolation",
                "hospitals_at_risk": total_hospitals,
                "hospital_names": [
                    h.get("name", "Unknown Hospital")
                    for h in current_hospitals + new_hospitals
                ],
                "schools_at_risk": total_schools,
                "newly_isolated_hospitals": [h.get("name", "") for h in new_hospitals],
            },
            "third_order": {
                "description": "Power and utilities cascade",
                "power_stations_at_risk": total_power,
                "power_station_names": [
                    p["name"]
                    for p in current_infra.get("power_stations", [])
                    + new_infra.get("power_stations", [])
                ],
                "estimated_residents_without_power": total_power * 80000,  # Heuristic
            },
            "fourth_order": {
                "description": "Humanitarian impact",
                "children_under_5": children_under_5,
                "elderly_over_65": elderly_over_65,
                "hospital_patients_needing_evac": total_hospitals * 120,  # Heuristic
            },
            "summary": (
                f"At +{water_level_delta_m}m, population at risk reaches {total_population:,}. "
                f"This includes approximately {children_under_5:,} children under 5 "
                f"and {elderly_over_65:,} elderly over 65. "
                f"{total_hospitals} hospitals are in the flood zone. "
                f"{total_power} power substations at risk, potentially affecting "
                f"{total_power * 80000:,} residents."
            ),
            "recommendation": (
                "Consider preemptive evacuation of vulnerable populations. "
                "Coordinate with hospitals for patient transfers. "
                "Alert power utility for potential grid impact."
            ),
            "population_at_risk": total_population,
            "water_level": water_level_delta_m,
            "hospitals_at_risk": hospitals_at_risk,
        }

        return cascade

    except Exception as e:
        logger.error(f"[ANALYST TOOL] compute_cascade — ERROR: {e}")
        return {
            "error": str(e),
            "summary": "Unable to compute cascade at this time.",
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 6: evaluate_route_safety  *** NOW REAL ***
# Uses Shapely spatial intersection to check route vs flood polygon
# ─────────────────────────────────────────────────────────────────────


def evaluate_route_safety(route_geojson: str, flood_geojson: str) -> dict:
    """
    Evaluate if an evacuation route passes through flood zones using
    real spatial intersection (Shapely).

    Args:
        route_geojson: Route as GeoJSON LineString or Feature
        flood_geojson: Flood extent as GeoJSON Polygon or Feature

    Returns:
        Safety assessment with danger zones and alternatives
    """
    logger.info("Analyst: Evaluating route safety with spatial intersection")

    try:
        # Resolve flood GeoJSON from cache if the model passed a placeholder
        flood_geojson = _resolve_flood_geojson(flood_geojson)  # type: ignore[assignment]

        # Parse GeoJSON inputs
        route_data = _parse_geojson_payload(route_geojson)
        flood_data = _parse_geojson_payload(flood_geojson)

        # Extract geometry (handle both Feature and raw geometry)
        route_geom_data = route_data.get("geometry", route_data)
        flood_geom_data = flood_data.get("geometry", flood_data)

        route_shape = shape(route_geom_data)
        flood_shape = shape(flood_geom_data)

        # Compute intersection
        intersection = route_shape.intersection(flood_shape)

        # Calculate intersection percentage (as fraction of route length)
        if route_shape.length > 0:
            intersection_pct = round(
                (intersection.length / route_shape.length) * 100, 1
            )
        else:
            intersection_pct = 0.0

        is_safe = intersection.is_empty
        has_intersection = not intersection.is_empty

        # Determine safety rating
        if intersection_pct == 0:
            safety_rating = "SAFE"
        elif intersection_pct < 10:
            safety_rating = "CAUTION"
        else:
            safety_rating = "UNSAFE"

        # Extract danger zone coordinates
        danger_zones = []
        if has_intersection:
            # Get centroid(s) of intersection segments for danger zone markers
            if intersection.geom_type == "MultiLineString":
                for segment in intersection.geoms:
                    centroid = segment.centroid
                    danger_zones.append(
                        {
                            "lat": centroid.y,
                            "lng": centroid.x,
                            "length_m": _estimate_length_meters(segment),
                            "reason": "Route passes through flooded area",
                            "severity": "HIGH" if segment.length > 0.005 else "MEDIUM",
                        }
                    )
            elif intersection.geom_type == "LineString":
                centroid = intersection.centroid
                danger_zones.append(
                    {
                        "lat": centroid.y,
                        "lng": centroid.x,
                        "length_m": _estimate_length_meters(intersection),
                        "reason": "Route passes through flooded area",
                        "severity": "HIGH",
                    }
                )
            elif intersection.geom_type == "Point":
                danger_zones.append(
                    {
                        "lat": intersection.y,
                        "lng": intersection.x,
                        "length_m": 0,
                        "reason": "Route touches flood zone boundary",
                        "severity": "MEDIUM",
                    }
                )

        # Build response
        result = {
            "is_safe": is_safe,
            "safety_rating": safety_rating,
            "intersection_pct": intersection_pct,
            "danger_zones": danger_zones,
            "confidence": 0.92,
        }

        # If unsafe, try to suggest an alternative route
        if not is_safe:
            result["recommendation"] = (
                f"DO NOT USE THIS ROUTE — {intersection_pct}% passes through "
                f"active flood zone. Request alternative via Coordinator agent."
            )
            # Attempt to find an alternative route via Maps API
            try:
                alt = _suggest_alternative_route(route_geom_data, flood_shape)
                if alt:
                    result["alternative_route"] = alt
            except Exception as alt_err:
                logger.warning(f"Could not generate alternative route: {alt_err}")
                result["alternative_route"] = {
                    "description": "Use elevated roadways or ring roads to bypass flood zone",
                    "safety_rating": "UNKNOWN",
                }
        else:
            result["recommendation"] = (
                "Route is clear of flood zones — safe to proceed."
            )

        return result

    except Exception as e:
        logger.error(f"Error evaluating route safety: {e}")
        return {
            "is_safe": False,
            "safety_rating": "UNKNOWN",
            "danger_zones": [],
            "intersection_pct": 0,
            "recommendation": f"Unable to evaluate route safety: {e}",
            "confidence": 0.0,
            "error": str(e),
        }


def _estimate_length_meters(geom) -> float:
    """Estimate geometry length in meters using UTM projection for Jakarta."""
    try:
        project_to_meters = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:32748", always_xy=True
        ).transform
        geom_m = transform(project_to_meters, geom)
        return round(geom_m.length, 0)
    except Exception:
        # Fallback: rough approximation (1 degree ≈ 111km at equator)
        return round(geom.length * 111_000, 0)


def _suggest_alternative_route(route_geom_data: dict, flood_shape) -> dict | None:
    """Try to suggest a route that avoids the flood zone."""
    # Extract start and end points from the route
    coords = route_geom_data.get("coordinates", [])
    if len(coords) < 2:
        return None

    start = coords[0]  # [lng, lat]
    end = coords[-1]

    try:
        route_result = _get_maps_service().get_evacuation_route(
            origin_latlng=(start[1], start[0]),
            destination_latlng=(end[1], end[0]),
            avoid_geojson=mapping(flood_shape),
        )

        if route_result and route_result.get("route_geojson"):
            # Keep backward-compatible fallback if route service did not include a rating.
            fallback_rating = "UNKNOWN"
            alt_geom = route_result["route_geojson"].get("geometry", {})
            if alt_geom:
                alt_shape = shape(alt_geom)
                alt_intersection = alt_shape.intersection(flood_shape)
                fallback_rating = "SAFE" if alt_intersection.is_empty else "CAUTION"
            safety_rating = route_result.get("safety_rating") or fallback_rating

            alternative: dict[str, Any] = {
                "route_geojson": route_result["route_geojson"],
                "distance_m": route_result.get("distance_m"),
                "duration_s": route_result.get("duration_s"),
                "safety_rating": safety_rating,
                "description": (
                    "Alternative route via flood-constrained Google Maps Directions API"
                ),
            }
            route_safety = route_result.get("route_safety")
            if route_safety is not None:
                alternative["route_safety"] = route_safety
            route_risk_handling = route_result.get("route_risk_handling")
            if route_risk_handling is not None:
                alternative["route_risk_handling"] = route_risk_handling
            return alternative
    except Exception as e:
        logger.warning(f"Alternative route lookup failed: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────
# Tool 7: get_search_grounding  *** NEW ***
# Uses Google Search grounding for real-time information
# ─────────────────────────────────────────────────────────────────────


def get_search_grounding(query: str) -> dict:
    """
    Search for current real-time information using Google Search grounding.

    Uses Gemini with Google Search tool to retrieve grounded, cited answers
    about current events, regulations, weather, or any live data.

    Args:
        query: Search query (e.g., "Current Jakarta flood conditions March 2026")

    Returns:
        Grounded response text with source citations
    """
    logger.info(f"Analyst: Search grounding for: {query[:60]}...")

    try:
        grounding_tool = types.Tool(google_search=types.GoogleSearch())

        response = _get_genai_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[grounding_tool],
                temperature=0.3,
            ),
        )

        result = {
            "response": response.text,
            "query": query,
            "grounded": True,
        }

        # Extract grounding metadata if available
        if response.candidates and response.candidates[0].grounding_metadata:
            metadata = response.candidates[0].grounding_metadata
            result["search_queries"] = getattr(metadata, "web_search_queries", [])

            sources = []
            for chunk in getattr(metadata, "grounding_chunks", []):
                if hasattr(chunk, "web"):
                    sources.append(
                        {
                            "title": getattr(chunk.web, "title", ""),
                            "uri": getattr(chunk.web, "uri", ""),
                        }
                    )
            result["sources"] = sources

        return result

    except Exception as e:
        logger.error(f"Search grounding failed: {e}")
        return {
            "response": f"Search grounding unavailable: {e}",
            "query": query,
            "grounded": False,
            "error": str(e),
        }
