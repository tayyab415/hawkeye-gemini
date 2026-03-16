"""
Coordinator Agent Tools — Track 3D
Action execution: alerts (Gmail API), routes, incident logging, and reports.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

# Gmail API imports
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Import services
from app.services.firestore_service import IncidentService
from app.services.maps_service import MapsService

logger = logging.getLogger(__name__)

# Lazy-initialized services
_incident_service: IncidentService | None = None
_maps_service: MapsService | None = None
_gmail_service = None

DEFAULT_SHELTER_SEARCH_RADIUS_KM = 10.0
MAX_DYNAMIC_SHELTER_CANDIDATES = 8
MAX_DYNAMIC_ROUTE_OPTIONS = 4
DEFAULT_EVACUATION_ZONE_RADIUS_M = 600.0
NEAREST_SHELTER_DESTINATION_MODES = frozenset(
    {
        "nearest_safe_shelters",
        "nearest_shelter",
        "nearest",
        "dynamic_nearest_shelter",
    }
)
NEAREST_SHELTER_DESTINATION_TOKENS = (
    "nearest shelter",
    "nearest safe shelter",
    "safe shelter",
    "evacuation shelter",
)


def _get_incident_service() -> IncidentService:
    global _incident_service
    if _incident_service is None:
        project_id = os.getenv("GCP_PROJECT_ID", "")
        _incident_service = IncidentService(project_id)
    return _incident_service


def _get_maps_service() -> MapsService:
    global _maps_service
    if _maps_service is None:
        api_key = os.getenv("GCP_MAPS_API_KEY", "")
        _maps_service = MapsService(api_key)
    return _maps_service


def _get_gmail_service():
    """
    Initialize Gmail API service using service account with domain-wide delegation,
    or return None if credentials aren't available (graceful degradation).
    """
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    # Try multiple credential paths
    cred_paths = [
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "credentials", "service-account.json",
        ),
    ]

    for cred_path in cred_paths:
        if cred_path and os.path.exists(cred_path):
            try:
                scopes = ["https://www.googleapis.com/auth/gmail.send"]
                credentials = service_account.Credentials.from_service_account_file(
                    cred_path, scopes=scopes
                )

                # If domain-wide delegation is configured, impersonate the sender
                sender_email = os.getenv("HAWKEYE_SENDER_EMAIL", "")
                if sender_email:
                    credentials = credentials.with_subject(sender_email)

                _gmail_service = build("gmail", "v1", credentials=credentials)
                logger.info("Gmail API service initialized successfully")
                return _gmail_service
            except Exception as e:
                logger.warning(f"Gmail init failed with {cred_path}: {e}")
                continue

    logger.warning(
        "Gmail credentials not found — email sending will be logged but not sent. "
        "Set GOOGLE_APPLICATION_CREDENTIALS or place service-account.json in credentials/"
    )
    return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_current_flood_extent_geojson() -> dict[str, Any] | None:
    """
    Fetch current flood extent geometry via Analyst tooling.

    Returns None when flood data is unavailable so route generation can
    gracefully fall back to unconstrained routing.
    """
    try:
        from app.hawkeye_agent.tools.analyst import get_flood_extent
    except Exception as exc:
        logger.warning("Coordinator: Could not import flood extent tool: %s", exc)
        return None

    try:
        flood_result = get_flood_extent()
    except Exception as exc:
        logger.warning("Coordinator: Flood extent lookup failed: %s", exc)
        return None

    if not isinstance(flood_result, dict):
        logger.warning(
            "Coordinator: Unexpected flood extent payload type: %s",
            type(flood_result),
        )
        return None

    flood_geojson = flood_result.get("geojson")
    if flood_geojson is None and flood_result.get("error"):
        logger.warning(
            "Coordinator: Flood extent unavailable for proactive avoidance: %s",
            flood_result["error"],
        )
    return flood_geojson


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_valid_lat_lng(lat: float, lng: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _haversine_distance_m(
    start_lat: float, start_lng: float, end_lat: float, end_lng: float
) -> float:
    radius_m = 6_371_000.0
    start_lat_rad = math.radians(start_lat)
    end_lat_rad = math.radians(end_lat)
    lat_delta = math.radians(end_lat - start_lat)
    lng_delta = math.radians(end_lng - start_lng)
    haversine = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(start_lat_rad)
        * math.cos(end_lat_rad)
        * math.sin(lng_delta / 2) ** 2
    )
    arc = 2 * math.atan2(math.sqrt(haversine), math.sqrt(max(1 - haversine, 0)))
    return radius_m * arc


def _normalize_destination_mode(
    destination_mode: str | None, destination_name: str
) -> str | None:
    if isinstance(destination_mode, str) and destination_mode.strip():
        normalized = destination_mode.strip().lower().replace("-", "_")
        if normalized in NEAREST_SHELTER_DESTINATION_MODES:
            return "nearest_safe_shelters"

    destination_normalized = destination_name.strip().lower()
    if destination_normalized and any(
        token in destination_normalized for token in NEAREST_SHELTER_DESTINATION_TOKENS
    ):
        return "nearest_safe_shelters"
    return None


def _route_safety_rank(safety_rating: str | None) -> int:
    normalized = (safety_rating or "").strip().upper()
    ranks = {
        "SAFE": 0,
        "CAUTION": 1,
        "UNKNOWN": 2,
        "UNSAFE": 3,
    }
    return ranks.get(normalized, 2)


def _derive_route_safety_rating(route_result: dict[str, Any]) -> str:
    safety_rating = route_result.get("safety_rating")
    route_safety = route_result.get("route_safety")
    if safety_rating is None and isinstance(route_safety, dict):
        safety_rating = route_safety.get("safety_rating")
    if not isinstance(safety_rating, str):
        return "UNKNOWN"
    normalized = safety_rating.strip().upper()
    return normalized or "UNKNOWN"


def _derive_duration_minutes(route_result: dict[str, Any]) -> float | None:
    duration_s = _to_float(route_result.get("duration_s"))
    if duration_s is not None:
        return round(duration_s / 60.0, 2)
    duration_minutes = _to_float(route_result.get("duration_minutes"))
    if duration_minutes is None:
        return None
    return round(duration_minutes, 2)


def _compute_route_option_sort_key(option: dict[str, Any]) -> tuple[float, ...]:
    route_safety = (
        option.get("route_safety") if isinstance(option.get("route_safety"), dict) else {}
    )
    risk_cost_score = _to_float(route_safety.get("risk_cost_score"))
    if risk_cost_score is None:
        risk_cost_score = float("inf")

    duration_minutes = _to_float(option.get("duration_minutes"))
    if duration_minutes is None:
        duration_minutes = float("inf")

    distance_m = _to_float(option.get("distance_m"))
    if distance_m is None:
        distance_m = float("inf")

    direct_distance_m = _to_float(option.get("direct_distance_m"))
    if direct_distance_m is None:
        direct_distance_m = float("inf")

    return (
        float(_route_safety_rank(option.get("safety_rating"))),
        risk_cost_score,
        duration_minutes,
        distance_m,
        direct_distance_m,
    )


def _build_evacuation_zone_geojson(
    center_lat: float,
    center_lng: float,
    *,
    label: str,
    radius_m: float = DEFAULT_EVACUATION_ZONE_RADIUS_M,
    points: int = 24,
) -> dict[str, Any] | None:
    if not _is_valid_lat_lng(center_lat, center_lng):
        return None
    if radius_m <= 0:
        return None
    if points < 8:
        points = 8

    lat_radius = radius_m / 111_000.0
    cos_lat = max(math.cos(math.radians(center_lat)), 0.2)
    lng_radius = radius_m / (111_000.0 * cos_lat)

    ring: list[list[float]] = []
    for idx in range(points + 1):
        angle = 2 * math.pi * idx / points
        lat = center_lat + (lat_radius * math.sin(angle))
        lng = center_lng + (lng_radius * math.cos(angle))
        ring.append([round(lng, 6), round(lat, 6)])

    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [ring],
        },
        "properties": {
            "label": label,
            "zone_type": "evacuation_buffer",
            "radius_m": round(radius_m, 1),
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Tool 1: send_emergency_alert  *** NOW REAL ***
# ─────────────────────────────────────────────────────────────────────

def send_emergency_alert(subject: str, body: str, recipient_email: str) -> dict:
    """
    Send an emergency alert email to specified recipients.

    Uses Gmail API when credentials are available, otherwise logs the alert
    to Firestore with full details (graceful degradation for demo).

    Args:
        subject: Alert subject line
        body: Alert body text
        recipient_email: Primary recipient email address

    Returns:
        Send status and message ID
    """
    logger.info(f"Coordinator: Sending emergency alert to {recipient_email}")
    timestamp = _utcnow_iso()

    # Build the alert email body with HawkEye formatting
    formatted_body = (
        f"═══════════════════════════════════════════════\n"
        f"  HAWK EYE — EMERGENCY ALERT\n"
        f"═══════════════════════════════════════════════\n\n"
        f"Timestamp: {timestamp}\n"
        f"System: HawkEye AI Incident Command\n\n"
        f"{'─' * 47}\n\n"
        f"{body}\n\n"
        f"{'─' * 47}\n\n"
        f"This alert was generated by HawkEye, an AI-powered\n"
        f"disaster response command system. All alerts are logged\n"
        f"for audit purposes.\n\n"
        f"Do not reply to this message.\n"
    )

    alert_data = {
        "type": "emergency_alert",
        "subject": subject,
        "body": body,
        "recipient": recipient_email,
        "timestamp": timestamp,
    }

    # Attempt Gmail API send
    gmail = _get_gmail_service()
    sent_via_gmail = False
    gmail_message_id = None

    if gmail is not None:
        try:
            message = MIMEText(formatted_body)
            message["to"] = recipient_email
            message["subject"] = f"🚨 HAWK EYE ALERT — {subject}"

            sender = os.getenv("HAWKEYE_SENDER_EMAIL", "")
            if sender:
                message["from"] = sender

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            send_result = (
                gmail.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            gmail_message_id = send_result.get("id")
            sent_via_gmail = True
            logger.info(f"Email sent via Gmail API: {gmail_message_id}")
            alert_data["gmail_message_id"] = gmail_message_id
            alert_data["status"] = "sent"
        except Exception as e:
            logger.error(f"Gmail send failed: {e}")
            alert_data["status"] = "gmail_failed"
            alert_data["gmail_error"] = str(e)
    else:
        alert_data["status"] = "logged_only"
        logger.info("Gmail not available — alert logged to Firestore only")

    # Always log to Firestore (audit trail)
    try:
        _get_incident_service().log_event(
            event_type="emergency_alert",
            severity="high",
            data=alert_data,
        )
    except Exception as e:
        logger.error(f"Failed to log alert to Firestore: {e}")

    return {
        "sent": sent_via_gmail,
        "message_id": gmail_message_id or f"log-{datetime.now(timezone.utc).timestamp()}",
        "recipient": recipient_email,
        "subject": subject,
        "delivery_method": "gmail_api" if sent_via_gmail else "firestore_log",
        "timestamp": timestamp,
    }


# ─────────────────────────────────────────────────────────────────────
# Tool 2: generate_evacuation_route (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────

def generate_evacuation_route(
    origin_name: str = "",
    destination_name: str = "",
    avoid_flood: bool = True,
    origin_lat: float | None = None,
    origin_lng: float | None = None,
    origin_label: str = "",
    destination_mode: str = "",
    max_alternates: int = 3,
    origin_radius_km: float | None = None,
) -> dict:
    """
    Generate evacuation routing with optional context-aware dynamic shelter ranking.

    Args:
        origin_name: Starting location label (used when coordinates are absent).
        destination_name: Destination label (ignored for nearest-shelter mode).
        avoid_flood: Whether to avoid active flood geometry.
        origin_lat: Optional origin latitude from tactical context.
        origin_lng: Optional origin longitude from tactical context.
        origin_label: Optional readable origin label for context coordinates.
        destination_mode: Optional destination strategy selector.
        max_alternates: Number of alternate routes to include (top safety-ranked).
        origin_radius_km: Optional shelter search radius hint.

    Returns:
        Route payload containing primary route, optional alternates, and metadata.
    """
    origin_name = origin_name.strip() if isinstance(origin_name, str) else ""
    destination_name = (
        destination_name.strip() if isinstance(destination_name, str) else ""
    )
    origin_label = origin_label.strip() if isinstance(origin_label, str) else ""
    normalized_destination_mode = _normalize_destination_mode(
        destination_mode,
        destination_name,
    )

    try:
        requested_alternates = int(max_alternates)
    except (TypeError, ValueError):
        requested_alternates = 3
    requested_alternates = max(0, min(requested_alternates, MAX_DYNAMIC_ROUTE_OPTIONS - 1))
    max_route_options = requested_alternates + 1

    logger.info(
        "Coordinator: Generating route origin=%s destination=%s mode=%s avoid_flood=%s",
        origin_name or "<context>",
        destination_name or "<dynamic>",
        normalized_destination_mode or "explicit_destination",
        avoid_flood,
    )

    try:
        maps_service = _get_maps_service()

        origin_lat_value = _to_float(origin_lat)
        origin_lng_value = _to_float(origin_lng)
        if (
            origin_lat_value is not None
            and origin_lng_value is not None
            and _is_valid_lat_lng(origin_lat_value, origin_lng_value)
        ):
            origin = {
                "lat": origin_lat_value,
                "lng": origin_lng_value,
                "formatted_address": (
                    origin_label or origin_name or "Current tactical position"
                ),
            }
        elif origin_name:
            origin = maps_service.geocode(origin_name)
        else:
            return {
                "error": "Origin is required for evacuation planning.",
                "origin": origin_name,
                "destination": destination_name,
            }

        origin_latlng = (_to_float(origin.get("lat")), _to_float(origin.get("lng")))
        if (
            origin_latlng[0] is None
            or origin_latlng[1] is None
            or not _is_valid_lat_lng(origin_latlng[0], origin_latlng[1])
        ):
            return {
                "error": f"Could not resolve origin coordinates: {origin_name or origin_label or 'context'}",
                "origin": origin_name or origin_label,
                "destination": destination_name,
            }

        origin = {
            **origin,
            "lat": origin_latlng[0],
            "lng": origin_latlng[1],
            "formatted_address": (
                origin.get("formatted_address")
                or origin_label
                or origin_name
                or "Current tactical position"
            ),
        }

        route_kwargs_base: dict[str, Any] = {
            "origin_latlng": (origin["lat"], origin["lng"]),
        }
        flood_constraints_applied = False
        flood_geojson = None
        if avoid_flood:
            flood_geojson = _get_current_flood_extent_geojson()
            if flood_geojson is not None:
                route_kwargs_base["avoid_geojson"] = flood_geojson
                flood_constraints_applied = True
            else:
                logger.warning(
                    "Coordinator: Proceeding without flood avoidance constraints"
                )

        if normalized_destination_mode == "nearest_safe_shelters":
            radius_km = _to_float(origin_radius_km)
            if radius_km is None or radius_km <= 0:
                radius_km = DEFAULT_SHELTER_SEARCH_RADIUS_KM
            radius_km = max(2.0, min(radius_km, 25.0))

            try:
                nearby_shelters = maps_service.find_nearby_shelters(
                    origin["lat"],
                    origin["lng"],
                    radius_km,
                )
            except Exception as exc:
                logger.warning("Coordinator: Shelter lookup failed: %s", exc)
                nearby_shelters = []

            candidate_destinations: list[dict[str, Any]] = []
            seen_candidate_keys: set[str] = set()
            for shelter in nearby_shelters:
                if not isinstance(shelter, dict):
                    continue
                shelter_lat = _to_float(shelter.get("lat"))
                shelter_lng = _to_float(shelter.get("lng"))
                if (
                    shelter_lat is None
                    or shelter_lng is None
                    or not _is_valid_lat_lng(shelter_lat, shelter_lng)
                ):
                    continue
                candidate_name = (
                    str(shelter.get("name", "")).strip()
                    or str(shelter.get("address", "")).strip()
                    or "Evacuation Shelter"
                )
                candidate_key = f"{candidate_name.casefold()}:{shelter_lat:.5f}:{shelter_lng:.5f}"
                if candidate_key in seen_candidate_keys:
                    continue
                seen_candidate_keys.add(candidate_key)

                candidate_destinations.append(
                    {
                        "name": candidate_name,
                        "lat": shelter_lat,
                        "lng": shelter_lng,
                        "formatted_address": (
                            str(shelter.get("address", "")).strip() or candidate_name
                        ),
                        "place_id": shelter.get("place_id"),
                        "rating": shelter.get("rating"),
                        "direct_distance_m": round(
                            _haversine_distance_m(
                                origin["lat"],
                                origin["lng"],
                                shelter_lat,
                                shelter_lng,
                            ),
                            1,
                        ),
                    }
                )

            candidate_destinations.sort(
                key=lambda candidate: (
                    _to_float(candidate.get("direct_distance_m")) or float("inf"),
                    -(_to_float(candidate.get("rating")) or 0.0),
                )
            )
            candidate_destinations = candidate_destinations[
                :MAX_DYNAMIC_SHELTER_CANDIDATES
            ]

            route_options: list[dict[str, Any]] = []
            for candidate in candidate_destinations:
                route_kwargs = {
                    **route_kwargs_base,
                    "destination_latlng": (
                        candidate["lat"],
                        candidate["lng"],
                    ),
                }
                route_result = maps_service.get_evacuation_route(**route_kwargs)
                if not route_result.get("route_geojson"):
                    continue

                route_safety = (
                    route_result.get("route_safety")
                    if isinstance(route_result.get("route_safety"), dict)
                    else None
                )
                route_risk_handling = (
                    route_result.get("route_risk_handling")
                    if isinstance(route_result.get("route_risk_handling"), dict)
                    else None
                )
                option = {
                    "destination": {
                        "name": candidate["name"],
                        "lat": candidate["lat"],
                        "lng": candidate["lng"],
                        "formatted_address": candidate["formatted_address"],
                        "place_id": candidate.get("place_id"),
                        "rating": candidate.get("rating"),
                    },
                    "direct_distance_m": candidate["direct_distance_m"],
                    "route_geojson": route_result.get("route_geojson"),
                    "distance_m": route_result.get("distance_m"),
                    "duration_minutes": _derive_duration_minutes(route_result),
                    "safety_rating": _derive_route_safety_rating(route_result),
                }
                if route_safety is not None:
                    option["route_safety"] = route_safety
                if route_risk_handling is not None:
                    option["route_risk_handling"] = route_risk_handling
                route_options.append(option)

            if not route_options:
                return {
                    "error": (
                        "Could not generate routes to nearby shelters from current "
                        "tactical position."
                    ),
                    "origin": origin,
                    "destination_mode": "nearest_safe_shelters",
                    "candidate_shelter_count": len(candidate_destinations),
                }

            route_options.sort(key=_compute_route_option_sort_key)
            selected_options = route_options[:max_route_options]
            primary_option = selected_options[0]
            primary_destination = primary_option["destination"]
            primary_route_safety = (
                primary_option.get("route_safety")
                if isinstance(primary_option.get("route_safety"), dict)
                else None
            )
            primary_route_risk = (
                primary_option.get("route_risk_handling")
                if isinstance(primary_option.get("route_risk_handling"), dict)
                else None
            )

            response: dict[str, Any] = {
                "route_geojson": primary_option.get("route_geojson"),
                "distance_m": primary_option.get("distance_m"),
                "duration_minutes": primary_option.get("duration_minutes"),
                "origin": origin,
                "destination": {
                    "lat": primary_destination.get("lat"),
                    "lng": primary_destination.get("lng"),
                    "formatted_address": (
                        primary_destination.get("formatted_address")
                        or primary_destination.get("name")
                    ),
                    "name": primary_destination.get("name"),
                    "place_id": primary_destination.get("place_id"),
                    "rating": primary_destination.get("rating"),
                },
                "safety_rating": primary_option.get("safety_rating", "UNKNOWN"),
                "avoid_flood": avoid_flood,
                "destination_mode": "nearest_safe_shelters",
                "route_options": selected_options,
                "alternate_count": max(0, len(selected_options) - 1),
                "candidate_shelter_count": len(candidate_destinations),
                "selection_method": "safety_then_eta",
            }
            if primary_route_safety is not None:
                response["route_safety"] = primary_route_safety
            if primary_route_risk is not None:
                response["route_risk_handling"] = primary_route_risk
            if avoid_flood:
                if isinstance(primary_route_risk, dict):
                    response["flood_constraints_applied"] = bool(
                        primary_route_risk.get("constraints_applied")
                    )
                else:
                    response["flood_constraints_applied"] = flood_constraints_applied

            evacuation_zone_geojson = _build_evacuation_zone_geojson(
                center_lat=primary_destination["lat"],
                center_lng=primary_destination["lng"],
                label=(
                    f"Evacuation Assembly Zone — {primary_destination.get('name') or 'Primary Shelter'}"
                ),
            )
            if evacuation_zone_geojson is not None:
                response["evacuation_zone_geojson"] = evacuation_zone_geojson
                response["evacuation_zone_radius_m"] = DEFAULT_EVACUATION_ZONE_RADIUS_M
            return response

        if not destination_name:
            return {
                "error": (
                    "Destination is required unless destination_mode is set to nearest shelter."
                ),
                "origin": origin,
                "destination": destination_name,
            }

        destination = maps_service.geocode(destination_name)
        destination_lat = _to_float(destination.get("lat"))
        destination_lng = _to_float(destination.get("lng"))
        if (
            destination_lat is None
            or destination_lng is None
            or not _is_valid_lat_lng(destination_lat, destination_lng)
        ):
            return {
                "error": f"Could not geocode destination: {destination_name}",
                "origin": origin,
                "destination": destination_name,
            }

        destination = {
            **destination,
            "lat": destination_lat,
            "lng": destination_lng,
            "formatted_address": (
                destination.get("formatted_address") or destination_name
            ),
        }
        route_result = maps_service.get_evacuation_route(
            **route_kwargs_base,
            destination_latlng=(destination_lat, destination_lng),
        )
        route_safety = (
            route_result.get("route_safety")
            if isinstance(route_result.get("route_safety"), dict)
            else None
        )
        route_risk_handling = (
            route_result.get("route_risk_handling")
            if isinstance(route_result.get("route_risk_handling"), dict)
            else None
        )
        safety_rating = _derive_route_safety_rating(route_result)

        primary_option = {
            "destination": {
                "name": destination.get("formatted_address"),
                "lat": destination_lat,
                "lng": destination_lng,
                "formatted_address": destination.get("formatted_address"),
            },
            "route_geojson": route_result.get("route_geojson"),
            "distance_m": route_result.get("distance_m"),
            "duration_minutes": _derive_duration_minutes(route_result),
            "safety_rating": safety_rating,
            "direct_distance_m": round(
                _haversine_distance_m(
                    origin["lat"],
                    origin["lng"],
                    destination_lat,
                    destination_lng,
                ),
                1,
            ),
        }
        if route_safety is not None:
            primary_option["route_safety"] = route_safety
        if route_risk_handling is not None:
            primary_option["route_risk_handling"] = route_risk_handling

        response = {
            "route_geojson": route_result.get("route_geojson"),
            "distance_m": route_result.get("distance_m"),
            "duration_minutes": _derive_duration_minutes(route_result),
            "origin": origin,
            "destination": destination,
            "safety_rating": safety_rating,
            "avoid_flood": avoid_flood,
            "destination_mode": "explicit_destination",
            "route_options": [primary_option],
            "alternate_count": 0,
        }
        if route_safety is not None:
            response["route_safety"] = route_safety
        if route_risk_handling is not None:
            response["route_risk_handling"] = route_risk_handling
        if avoid_flood:
            if isinstance(route_risk_handling, dict):
                response["flood_constraints_applied"] = bool(
                    route_risk_handling.get("constraints_applied")
                )
            else:
                response["flood_constraints_applied"] = flood_constraints_applied

        evacuation_zone_geojson = _build_evacuation_zone_geojson(
            center_lat=destination_lat,
            center_lng=destination_lng,
            label=(
                f"Evacuation Assembly Zone — {destination.get('formatted_address', destination_name)}"
            ),
        )
        if evacuation_zone_geojson is not None:
            response["evacuation_zone_geojson"] = evacuation_zone_geojson
            response["evacuation_zone_radius_m"] = DEFAULT_EVACUATION_ZONE_RADIUS_M
        return response

    except Exception as e:
        logger.error(f"Error generating route: {e}")
        return {
            "error": str(e),
            "origin": origin_name or origin_label,
            "destination": destination_name,
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 3: log_incident (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────

def log_incident(
    description: str, severity: str, location: str | None = None
) -> dict:
    """
    Log an incident to the Firestore database.

    Args:
        description: Incident description
        severity: low | medium | high | critical
        location: Optional location dict with lat/lng

    Returns:
        Log entry confirmation
    """
    logger.info(f"Coordinator: Logging incident - {description[:50]}...")

    try:
        location_data: dict[str, Any] | None = None
        if location:
            try:
                parsed_location = json.loads(location)
                if isinstance(parsed_location, dict):
                    location_data = parsed_location
                else:
                    location_data = {"raw": parsed_location}
            except json.JSONDecodeError:
                location_data = {"raw": location}

        event_data = {
            "description": description,
            "location": location_data,
        }

        _get_incident_service().log_event(
            event_type="incident",
            severity=severity,
            data=event_data,
        )

        return {
            "logged": True,
            "event_type": "incident",
            "severity": severity,
            "timestamp": _utcnow_iso(),
        }

    except Exception as e:
        logger.error(f"Error logging incident: {e}")
        return {
            "logged": False,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 4: generate_incident_summary (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────

def generate_incident_summary() -> dict:
    """
    Generate a summary report of the current session.

    Returns:
        Formatted incident summary with timeline
    """
    logger.info("Coordinator: Generating incident summary")

    try:
        timeline = _get_incident_service().get_session_timeline()

        # Count events by type
        event_counts: dict[str, int] = {}
        for event in timeline:
            event_type = event.get("event_type", "unknown")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Format summary
        summary_text = (
            f"\nINCIDENT SUMMARY REPORT\n"
            f"Generated: {ts_now}\n\n"
            f"Total Events: {len(timeline)}\n"
            f"Event Breakdown: {json.dumps(event_counts, indent=2)}\n\n"
            f"TIMELINE:\n"
        )

        for event in timeline[-10:]:  # Last 10 events
            ts = event.get("timestamp", "")
            type_ = event.get("event_type", "")
            sev = event.get("severity", "")
            summary_text += f"\n[{ts}] {type_.upper()} ({sev})"
            if "data" in event and "description" in event["data"]:
                summary_text += f": {event['data']['description'][:60]}"

        return {
            "summary_text": summary_text,
            "event_count": len(timeline),
            "event_breakdown": event_counts,
            "timeline": timeline,
        }

    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        return {
            "error": str(e),
            "summary_text": "Error generating summary",
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 5: update_map (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────

def update_map(
    geojson: str, layer_type: str, label: str, style: str | None = None
) -> dict:
    """
    Update the 3D map with new geospatial data.

    This emits a map_update event to the frontend WebSocket.

    Args:
        geojson: GeoJSON data to display
        layer_type: flood | route | marker | zone
        label: Display label for the layer
        style: Optional styling parameters

    Returns:
        Update confirmation with layer ID
    """
    logger.info(f"Coordinator: Updating map with {layer_type} layer - {label}")

    style_data: dict[str, Any] = {}
    if style:
        try:
            parsed_style = json.loads(style)
            if isinstance(parsed_style, dict):
                style_data = parsed_style
        except json.JSONDecodeError:
            logger.warning("Coordinator: update_map style was not valid JSON")

    return {
        "updated": True,
        "layer_id": f"{layer_type}-{datetime.now(timezone.utc).timestamp()}",
        "layer_type": layer_type,
        "label": label,
        "style": style_data,
        "message": "Map update queued for WebSocket transmission",
    }
