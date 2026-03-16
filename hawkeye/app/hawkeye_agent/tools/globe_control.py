"""Globe control tools for immediate map navigation and tactical visualization."""

from __future__ import annotations

import logging
import math
import os
import uuid

logger = logging.getLogger(__name__)

# Lazy-loaded to avoid startup failures when credentials are unavailable.
_maps_service = None


def _get_maps_service():
    global _maps_service
    if _maps_service is None:
        from app.services.maps_service import MapsService

        _maps_service = MapsService(api_key=os.getenv("GCP_MAPS_API_KEY", ""))
    return _maps_service


def _geocode_or_default(location_name: str) -> tuple[float, float]:
    default_lat, default_lng = -6.225, 106.855
    try:
        maps = _get_maps_service()
        result = maps.geocode(location_name)
        lat = result.get("lat")
        lng = result.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            return float(lat), float(lng)
        logger.warning(
            "[GLOBE CONTROL] Geocode returned invalid lat/lng for '%s': %s",
            location_name,
            result,
        )
    except Exception as exc:
        logger.warning(
            "[GLOBE CONTROL] Geocoding failed for '%s': %s; using Jakarta default",
            location_name,
            exc,
        )
    return default_lat, default_lng


def _normalize_camera_mode(mode: str) -> str:
    mode_map = {
        "orbit": "ORBIT",
        "bird_eye": "BIRD_EYE",
        "street_level": "STREET_LEVEL",
        "overview": "OVERVIEW",
    }
    normalized = mode.strip().lower()
    return mode_map.get(normalized, mode.upper())


def _normalize_atmosphere_mode(mode: str) -> str:
    mode_map = {
        "normal": "clear",
        "tactical": "haze",
        "night_vision": "night",
        "thermal": "thermal",
    }
    normalized = mode.strip().lower()
    return mode_map.get(normalized, normalized)


def fly_to_location(location_name: str) -> dict:
    """
    Fly the camera to a named location.
    Examples: "Kampung Melayu", "University of Indonesia".
    """
    logger.info("[GLOBE CONTROL] fly_to_location: %s", location_name)
    lat, lng = _geocode_or_default(location_name)
    return {
        "action": "fly_to",
        "lat": lat,
        "lng": lng,
        "altitude": 3000,
        "duration": 3,
        "location_name": location_name,
    }


def set_camera_mode(mode: str, target_location: str = "") -> dict:
    """
    Change camera mode.
    Supported modes: orbit, bird_eye, street_level, overview.
    """
    logger.info(
        "[GLOBE CONTROL] set_camera_mode: mode=%s target=%s", mode, target_location
    )
    normalized_mode = _normalize_camera_mode(mode)
    result: dict = {"action": "camera_mode", "mode": normalized_mode}

    if target_location and normalized_mode in {"ORBIT", "STREET_LEVEL"}:
        lat, lng = _geocode_or_default(target_location)
        result["lat"] = lat
        result["lng"] = lng

    return result


def toggle_data_layer(layer_name: str, enabled: bool) -> dict:
    """Toggle a data layer in the strategic view."""
    logger.info("[GLOBE CONTROL] toggle_data_layer: %s=%s", layer_name, enabled)
    return {
        "action": "toggle_layer",
        "layer": layer_name,
        "layer_id": layer_name,
        "enabled": enabled,
    }


def deploy_entity(entity_type: str, location_name: str) -> dict:
    """Deploy an entity (helicopter, boat, command_post) to a geocoded location."""
    logger.info("[GLOBE CONTROL] deploy_entity: %s at %s", entity_type, location_name)
    lat, lng = _geocode_or_default(location_name)
    entity_type_norm = entity_type.strip().lower()
    entity_id = f"{entity_type_norm}_{uuid.uuid4().hex[:8]}"
    return {
        "action": "deploy_entity",
        "entity_type": entity_type_norm,
        "entity_id": entity_id,
        "lat": lat,
        "lng": lng,
        "label": f"{entity_type_norm} at {location_name}",
    }


def move_entity(entity_id: str, destination_name: str) -> dict:
    """Move an existing entity to a new geocoded destination."""
    logger.info("[GLOBE CONTROL] move_entity: %s -> %s", entity_id, destination_name)
    lat, lng = _geocode_or_default(destination_name)
    return {
        "action": "move_entity",
        "entity_id": entity_id,
        "lat": lat,
        "lng": lng,
        "duration": 3,
        "duration_ms": 3000,
    }


def add_measurement(from_location: str, to_location: str) -> dict:
    """Add a measurement line between two named locations."""
    logger.info("[GLOBE CONTROL] add_measurement: %s -> %s", from_location, to_location)
    from_lat, from_lng = _geocode_or_default(from_location)
    to_lat, to_lng = _geocode_or_default(to_location)

    earth_radius_km = 6371
    dlat = math.radians(to_lat - from_lat)
    dlng = math.radians(to_lng - from_lng)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(from_lat))
        * math.cos(math.radians(to_lat))
        * math.sin(dlng / 2) ** 2
    )
    distance_km = earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    line_id = f"measure_{uuid.uuid4().hex[:8]}"
    return {
        "action": "add_measurement",
        "id": line_id,
        "line_id": line_id,
        "from_lat": from_lat,
        "from_lng": from_lng,
        "to_lat": to_lat,
        "to_lng": to_lng,
        "lat1": from_lat,
        "lng1": from_lng,
        "lat2": to_lat,
        "lng2": to_lng,
        "label": f"{distance_km:.1f} km",
    }


def set_atmosphere(mode: str) -> dict:
    """Set strategic-view atmosphere mode."""
    logger.info("[GLOBE CONTROL] set_atmosphere: %s", mode)
    return {
        "action": "set_atmosphere",
        "mode": _normalize_atmosphere_mode(mode),
    }


def capture_current_view() -> dict:
    """Request a one-shot screenshot from the frontend strategic view."""
    request_id = f"screenshot_{uuid.uuid4().hex[:8]}"
    logger.info("[GLOBE CONTROL] capture_current_view: request_id=%s", request_id)
    return {
        "action": "capture_screenshot",
        "request_id": request_id,
    }


def add_threat_rings(center_location: str, ring_radii_km: str = "1,2,3") -> dict:
    """
    Add concentric threat rings around a location.
    ring_radii_km is a comma-separated list, e.g. "1,2,3".
    """
    logger.info(
        "[GLOBE CONTROL] add_threat_rings: center=%s radii=%s",
        center_location,
        ring_radii_km,
    )
    lat, lng = _geocode_or_default(center_location)

    rings: list[float] = []
    for token in ring_radii_km.split(","):
        stripped = token.strip()
        if not stripped:
            continue
        try:
            rings.append(float(stripped))
        except ValueError:
            logger.warning("[GLOBE CONTROL] Invalid ring radius token ignored: %s", token)

    if not rings:
        rings = [1.0, 2.0, 3.0]

    return {
        "action": "add_threat_rings",
        "lat": lat,
        "lng": lng,
        "rings": rings,
        "center_location": center_location,
    }
