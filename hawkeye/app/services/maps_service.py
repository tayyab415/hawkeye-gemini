"""Google Maps Platform service — geocoding, routing, places, elevation."""

from __future__ import annotations

import json
import math
from typing import Any

import googlemaps
from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def _decode_polyline(encoded: str) -> list[list[float]]:
    """Decode a Google-encoded polyline into [[lng, lat], ...] for GeoJSON."""
    coords: list[list[float]] = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = 0
            result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append([lng / 1e5, lat / 1e5])
    return coords


def _parse_avoid_geometry(avoid_geojson: str | dict[str, Any]) -> BaseGeometry:
    """Parse avoid-area GeoJSON payload into a Shapely geometry."""
    if isinstance(avoid_geojson, str):
        try:
            payload = json.loads(avoid_geojson)
        except json.JSONDecodeError as exc:
            raise ValueError("avoid_geojson must be valid JSON") from exc
    elif isinstance(avoid_geojson, dict):
        payload = avoid_geojson
    else:
        raise ValueError("avoid_geojson must be a JSON string or GeoJSON object")

    try:
        geo_type = payload.get("type")
        if geo_type == "FeatureCollection":
            features = payload.get("features")
            if not isinstance(features, list) or not features:
                raise ValueError("avoid_geojson FeatureCollection has no features")

            geometries: list[BaseGeometry] = []
            for feature in features:
                if not isinstance(feature, dict):
                    continue
                feature_geometry = feature.get("geometry")
                if isinstance(feature_geometry, dict):
                    geometries.append(shape(feature_geometry))

            if not geometries:
                raise ValueError(
                    "avoid_geojson FeatureCollection has no valid geometries"
                )
            avoid_geometry = unary_union(geometries)
        elif geo_type == "Feature":
            feature_geometry = payload.get("geometry")
            if not isinstance(feature_geometry, dict):
                raise ValueError("avoid_geojson Feature has no geometry")
            avoid_geometry = shape(feature_geometry)
        else:
            avoid_geometry = shape(payload)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"avoid_geojson is not valid GeoJSON: {exc}") from exc

    if avoid_geometry.is_empty:
        raise ValueError("avoid_geojson geometry is empty")
    return avoid_geometry


def _compute_risk_cost_score(
    intersects_avoid_area: bool,
    intersection_pct: float,
    min_distance_to_avoid_m: float | None,
) -> float:
    """Compute route risk cost used for candidate selection (lower is safer)."""
    overlap_penalty = 1_000_000.0 if intersects_avoid_area else 0.0
    intersection_penalty = max(intersection_pct, 0.0) * 10_000.0
    proximity_threshold_m = 300.0
    if min_distance_to_avoid_m is None:
        proximity_penalty = proximity_threshold_m
    else:
        proximity_penalty = max(0.0, proximity_threshold_m - min_distance_to_avoid_m)
    return round(overlap_penalty + intersection_penalty + proximity_penalty, 2)


def _evaluate_route_safety(
    coords: list[list[float]], avoid_geometry: BaseGeometry
) -> dict[str, Any]:
    """Score route safety against an avoid geometry."""
    if len(coords) < 2:
        return {
            "safety_rating": "UNKNOWN",
            "intersects_avoid_area": False,
            "intersection_pct": 0.0,
            "min_distance_to_avoid_m": None,
            "risk_cost_score": _compute_risk_cost_score(False, 0.0, None),
        }

    route_line = LineString(coords)
    intersection = route_line.intersection(avoid_geometry)
    intersects_avoid_area = route_line.intersects(avoid_geometry)
    route_length = route_line.length
    intersection_pct = (
        round((intersection.length / route_length) * 100, 2) if route_length > 0 else 0.0
    )
    min_distance_to_avoid_m = round(route_line.distance(avoid_geometry) * 111_000, 1)
    risk_cost_score = _compute_risk_cost_score(
        intersects_avoid_area=intersects_avoid_area,
        intersection_pct=intersection_pct,
        min_distance_to_avoid_m=min_distance_to_avoid_m,
    )

    if not intersects_avoid_area:
        safety_rating = "SAFE"
    elif intersection_pct <= 10:
        safety_rating = "CAUTION"
    else:
        safety_rating = "UNSAFE"

    return {
        "safety_rating": safety_rating,
        "intersects_avoid_area": intersects_avoid_area,
        "intersection_pct": intersection_pct,
        "min_distance_to_avoid_m": min_distance_to_avoid_m,
        "risk_cost_score": risk_cost_score,
    }


def _route_priority_key(
    candidate: dict[str, Any],
) -> tuple[float, bool, float, bool, float, int, int]:
    """Sort key that prioritizes safer routes then shorter travel time."""
    route_safety = candidate["route_safety"]
    route_quality = candidate.get("route_quality") or {}
    risk_cost_score = route_safety.get("risk_cost_score", float("inf"))
    return (
        risk_cost_score,
        route_safety["intersects_avoid_area"],
        route_safety["intersection_pct"],
        not route_quality.get("is_simple", True),
        route_quality.get("detour_ratio", float("inf")),
        candidate["duration_s"],
        candidate["distance_m"],
    )


def _compute_detour_waypoints(
    avoid_geometry: BaseGeometry, margin_m: float = 350.0
) -> list[tuple[float, float]]:
    """Generate waypoint candidates around avoid geometry bounds."""
    min_lng, min_lat, max_lng, max_lat = avoid_geometry.bounds
    center_lng = (min_lng + max_lng) / 2
    center_lat = (min_lat + max_lat) / 2

    lat_margin = margin_m / 111_000
    cos_lat = max(math.cos(math.radians(center_lat)), 0.2)
    lng_margin = margin_m / (111_000 * cos_lat)

    lat_offset = max((max_lat - min_lat) / 2 + lat_margin, lat_margin)
    lng_offset = max((max_lng - min_lng) / 2 + lng_margin, lng_margin)

    return [
        (center_lat + lat_offset, center_lng),
        (center_lat - lat_offset, center_lng),
        (center_lat, center_lng + lng_offset),
        (center_lat, center_lng - lng_offset),
    ]


def _build_route_candidates(
    directions_results: list[dict[str, Any]],
    avoid_geometry: BaseGeometry | None,
    route_index_offset: int = 0,
    route_source: str = "maps_alternative",
    route_waypoint: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """Normalize Google Directions responses into scored route candidates."""
    candidates: list[dict[str, Any]] = []
    for idx, result in enumerate(directions_results):
        legs = result.get("legs") or []
        polyline = result.get("overview_polyline") or {}
        if not legs or "points" not in polyline:
            continue

        leg = legs[0]
        distance = leg.get("distance") or {}
        duration = leg.get("duration") or {}
        coords = _decode_polyline(polyline["points"])
        if not isinstance(coords, list):
            continue

        route_safety = (
            _evaluate_route_safety(coords, avoid_geometry)
            if avoid_geometry is not None
            else None
        )
        route_quality = _compute_route_quality(coords)
        candidate: dict[str, Any] = {
            "route_index": route_index_offset + idx,
            "route_source": route_source,
            "coords": coords,
            "distance_m": distance.get("value", 0),
            "duration_s": duration.get("value", 0),
            "distance_text": distance.get("text"),
            "duration_text": duration.get("text"),
            "route_safety": route_safety,
            "route_quality": route_quality,
        }
        if route_waypoint is not None:
            candidate["route_waypoint"] = {
                "lat": round(route_waypoint[0], 6),
                "lng": round(route_waypoint[1], 6),
            }
        candidates.append(candidate)
    return candidates


def _haversine_meters(
    start_lat: float, start_lng: float, end_lat: float, end_lng: float
) -> float:
    """Return great-circle distance in meters between two WGS84 points."""
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


def _compute_route_quality(coords: list[list[float]]) -> dict[str, Any]:
    """Compute geometry quality features used to avoid loopy detours."""
    if len(coords) < 2:
        return {
            "is_simple": True,
            "detour_ratio": 1.0,
            "route_length_m": 0.0,
            "direct_length_m": 0.0,
        }

    route_line = LineString(coords)
    route_length_m = route_line.length * 111_000

    start_lng, start_lat = coords[0]
    end_lng, end_lat = coords[-1]
    direct_length_m = _haversine_meters(start_lat, start_lng, end_lat, end_lng)
    if direct_length_m <= 1.0:
        detour_ratio = 1.0
    else:
        detour_ratio = route_length_m / direct_length_m

    return {
        "is_simple": bool(route_line.is_simple),
        "detour_ratio": round(detour_ratio, 3),
        "route_length_m": round(route_length_m, 1),
        "direct_length_m": round(direct_length_m, 1),
    }


class MapsService:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = googlemaps.Client(key=api_key)

    # ------------------------------------------------------------------
    # Geocoding
    # ------------------------------------------------------------------
    def geocode(self, place_name: str) -> dict[str, Any]:
        results = self.client.geocode(place_name)
        if not results:
            return {"lat": None, "lng": None, "formatted_address": None}
        loc = results[0]["geometry"]["location"]
        return {
            "lat": loc["lat"],
            "lng": loc["lng"],
            "formatted_address": results[0]["formatted_address"],
        }

    # ------------------------------------------------------------------
    # Evacuation routing
    # ------------------------------------------------------------------
    def get_evacuation_route(
        self,
        origin_latlng: tuple[float, float],
        destination_latlng: tuple[float, float],
        avoid_geojson: str | dict | None = None,
    ) -> dict[str, Any]:
        avoid_geometry: BaseGeometry | None = None
        if avoid_geojson is not None:
            try:
                avoid_geometry = _parse_avoid_geometry(avoid_geojson)
            except ValueError as exc:
                raise ValueError(f"Route risk handling failed: {exc}") from exc

        kwargs: dict[str, Any] = {
            "origin": origin_latlng,
            "destination": destination_latlng,
            "mode": "driving",
            "alternatives": True,
        }

        results = self.client.directions(**kwargs)
        if not results:
            response = {"route_geojson": None, "distance_m": 0, "duration_s": 0}
            if avoid_geometry is not None:
                response["safety_rating"] = "UNKNOWN"
                response["route_risk_handling"] = {
                    "avoidance_requested": True,
                    "constraints_applied": False,
                    "strategy": "risk_cost_with_detour_waypoints",
                    "candidate_count": 0,
                    "safe_candidate_count": 0,
                    "detour_search_attempted": False,
                    "detour_candidates_tested": 0,
                    "safe_route_found": False,
                    "status": "no_route_candidates",
                    "message": "No routes returned by provider for requested origin/destination.",
                }
            return response

        candidates = _build_route_candidates(
            directions_results=results,
            avoid_geometry=avoid_geometry,
            route_index_offset=0,
            route_source="maps_alternative",
        )

        if not candidates:
            response = {"route_geojson": None, "distance_m": 0, "duration_s": 0}
            if avoid_geometry is not None:
                response["safety_rating"] = "UNKNOWN"
                response["route_risk_handling"] = {
                    "avoidance_requested": True,
                    "constraints_applied": False,
                    "strategy": "risk_cost_with_detour_waypoints",
                    "candidate_count": 0,
                    "safe_candidate_count": 0,
                    "detour_search_attempted": False,
                    "detour_candidates_tested": 0,
                    "safe_route_found": False,
                    "status": "no_valid_route_candidates",
                    "message": (
                        "Provider returned route payloads, but none included "
                        "usable legs/polyline geometry."
                    ),
                }
            return response

        selected = candidates[0]
        scored_candidates: list[dict[str, Any]] = []
        detour_search_attempted = False
        detour_candidates_tested = 0
        if avoid_geometry is not None:
            scored_candidates = [
                candidate
                for candidate in candidates
                if candidate.get("route_safety") is not None
            ]

            safe_candidates = [
                candidate
                for candidate in scored_candidates
                if not candidate["route_safety"]["intersects_avoid_area"]
            ]
            if scored_candidates and not safe_candidates:
                detour_search_attempted = True
                for waypoint in _compute_detour_waypoints(avoid_geometry):
                    detour_kwargs: dict[str, Any] = {
                        "origin": origin_latlng,
                        "destination": destination_latlng,
                        "mode": "driving",
                        "alternatives": False,
                        "waypoints": [waypoint],
                    }
                    detour_results = self.client.directions(**detour_kwargs)
                    detour_candidates = _build_route_candidates(
                        directions_results=detour_results or [],
                        avoid_geometry=avoid_geometry,
                        route_index_offset=len(candidates),
                        route_source="detour_waypoint",
                        route_waypoint=waypoint,
                    )
                    if detour_candidates:
                        detour_candidates_tested += len(detour_candidates)
                        candidates.extend(detour_candidates)

                scored_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.get("route_safety") is not None
                ]

            if scored_candidates:
                selected = min(scored_candidates, key=_route_priority_key)

        route_properties: dict[str, Any] = {
            "distance_m": selected["distance_m"],
            "duration_s": selected["duration_s"],
            "distance_text": selected["distance_text"],
            "duration_text": selected["duration_text"],
            "route_quality": selected["route_quality"],
            "route_source": selected.get("route_source", "maps_alternative"),
        }
        response: dict[str, Any] = {
            "route_geojson": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": selected["coords"]},
                "properties": route_properties,
            },
            "distance_m": selected["distance_m"],
            "duration_s": selected["duration_s"],
            "route_quality": selected["route_quality"],
            "route_source": selected.get("route_source", "maps_alternative"),
        }

        if selected["route_safety"] is not None:
            safe_candidate_count = sum(
                1
                for candidate in scored_candidates
                if not candidate["route_safety"]["intersects_avoid_area"]
            )
            route_safety = {
                "avoidance_applied": True,
                "candidate_count": (
                    len(scored_candidates) if scored_candidates else len(candidates)
                ),
                "selected_candidate_index": selected["route_index"],
                "safe_candidate_count": safe_candidate_count,
                "selection_strategy": "risk_cost",
                "selected_route_source": selected.get("route_source", "maps_alternative"),
                **selected["route_safety"],
            }
            selected_waypoint = selected.get("route_waypoint")
            if selected_waypoint is not None:
                route_safety["selected_waypoint"] = selected_waypoint

            safe_route_found = not route_safety["intersects_avoid_area"]
            route_risk_handling = {
                "avoidance_requested": True,
                "constraints_applied": True,
                "strategy": "risk_cost_with_detour_waypoints",
                "candidate_count": route_safety["candidate_count"],
                "safe_candidate_count": safe_candidate_count,
                "detour_search_attempted": detour_search_attempted,
                "detour_candidates_tested": detour_candidates_tested,
                "selected_candidate_index": selected["route_index"],
                "selected_route_source": selected.get("route_source", "maps_alternative"),
                "safe_route_found": safe_route_found,
                "status": (
                    "safe_route_selected"
                    if safe_route_found
                    else "unsafe_route_selected"
                ),
            }
            if selected_waypoint is not None:
                route_risk_handling["selected_waypoint"] = selected_waypoint

            route_properties["route_safety"] = route_safety
            route_properties["safety_rating"] = route_safety["safety_rating"]
            route_properties["route_risk_handling"] = route_risk_handling
            response["route_safety"] = route_safety
            response["safety_rating"] = route_safety["safety_rating"]
            response["route_risk_handling"] = route_risk_handling
        elif avoid_geometry is not None:
            route_properties["safety_rating"] = "UNKNOWN"
            route_properties["route_risk_handling"] = {
                "avoidance_requested": True,
                "constraints_applied": False,
                "strategy": "risk_cost_with_detour_waypoints",
                "candidate_count": len(candidates),
                "safe_candidate_count": 0,
                "detour_search_attempted": detour_search_attempted,
                "detour_candidates_tested": detour_candidates_tested,
                "safe_route_found": False,
                "status": "safety_unavailable",
                "message": "Route returned but safety evaluation could not be computed.",
            }
            response["safety_rating"] = "UNKNOWN"
            response["route_risk_handling"] = route_properties["route_risk_handling"]

        return response

    # ------------------------------------------------------------------
    # Nearby shelters via Places API
    # ------------------------------------------------------------------
    def find_nearby_shelters(
        self, lat: float, lng: float, radius_km: float
    ) -> list[dict]:
        results = self.client.places_nearby(
            location=(lat, lng),
            radius=int(radius_km * 1000),
            keyword="evacuation shelter",
        )
        places = []
        for p in results.get("results", []):
            loc = p["geometry"]["location"]
            places.append(
                {
                    "name": p.get("name"),
                    "lat": loc["lat"],
                    "lng": loc["lng"],
                    "address": p.get("vicinity"),
                    "place_id": p.get("place_id"),
                    "rating": p.get("rating"),
                }
            )
        return places

    # ------------------------------------------------------------------
    # Elevation
    # ------------------------------------------------------------------
    def get_elevation(self, lat: float, lng: float) -> float:
        results = self.client.elevation((lat, lng))
        if results:
            return results[0]["elevation"]
        return 0.0

    def get_elevations_batch(
        self, locations: list[tuple[float, float]]
    ) -> list[dict[str, float]]:
        results = self.client.elevation(locations)
        return [
            {
                "lat": r["location"]["lat"],
                "lng": r["location"]["lng"],
                "elevation_m": r["elevation"],
            }
            for r in results
        ]
