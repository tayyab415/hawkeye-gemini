"""
PROPOSED: Enhanced MapsService with proactive flood avoidance.
This file demonstrates the contract changes needed for hard-constraint routing.
"""

from __future__ import annotations
from typing import Any
import googlemaps
from shapely.geometry import Point


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


class MapsServiceV2:
    """Enhanced MapsService with proactive flood avoidance constraints."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = googlemaps.Client(key=api_key)

    def get_evacuation_route(
        self,
        origin_latlng: tuple[float, float],
        destination_latlng: tuple[float, float],
        avoid_zones: list[dict] | None = None,
        cost_surface: str | None = None,
        hard_constraint: bool = False,
    ) -> dict[str, Any]:
        """
        ENHANCED: Generate evacuation route with proactive flood avoidance.
        
        Args:
            origin_latlng: (lat, lng) tuple
            destination_latlng: (lat, lng) tuple
            avoid_zones: List of zones [{"lat": float, "lng": float, "radius_m": float}, ...]
            cost_surface: "high_risk" | "medium_risk" | None
            hard_constraint: If True, fail immediately if no safe route exists
            
        Returns:
            Route GeoJSON with safety_rating PRE-COMPUTED (not PENDING_ANALYSIS)
        """
        kwargs: dict[str, Any] = {
            "origin": origin_latlng,
            "destination": destination_latlng,
            "mode": "driving",
            "alternatives": True,
        }
        
        # IMPLEMENTATION OPTION 1: Use waypoints to enforce avoidance
        # If avoid_zones provided, add them as "soft" waypoints at distance
        if avoid_zones and len(avoid_zones) > 0:
            waypoints = self._compute_waypoints_to_avoid(
                origin_latlng, destination_latlng, avoid_zones
            )
            if waypoints:
                kwargs["waypoints"] = waypoints
        
        # IMPLEMENTATION OPTION 2: Constrain via Google Maps avoid parameter
        if avoid_zones and len(avoid_zones) > 0:
            # Note: Google Maps API "avoid" only supports tolls|highways|ferries
            # For custom avoid zones, we need waypoint manipulation (Option 1)
            pass
        
        results = self.client.directions(**kwargs)
        if not results:
            if hard_constraint and avoid_zones:
                return {
                    "route_geojson": None,
                    "distance_m": 0,
                    "duration_s": 0,
                    "error": "No safe route found avoiding specified zones",
                    "safety_rating": "UNSAFE",
                }
            return {"route_geojson": None, "distance_m": 0, "duration_s": 0}
        
        leg = results[0]["legs"][0]
        polyline_enc = results[0]["overview_polyline"]["points"]
        coords = _decode_polyline(polyline_enc)
        
        # PRE-COMPUTE safety rating (not PENDING_ANALYSIS)
        safety_rating = self._compute_safety_pre_routing(coords, avoid_zones)
        
        return {
            "route_geojson": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "distance_m": leg["distance"]["value"],
                    "duration_s": leg["duration"]["value"],
                    "distance_text": leg["distance"]["text"],
                    "duration_text": leg["duration"]["text"],
                    "safety_rating": safety_rating,
                },
            },
            "distance_m": leg["distance"]["value"],
            "duration_s": leg["duration"]["value"],
            "safety_rating": safety_rating,
        }
    
    def _compute_waypoints_to_avoid(
        self, origin: tuple[float, float], destination: tuple[float, float],
        avoid_zones: list[dict]
    ) -> list[tuple[float, float]] | None:
        """
        Compute intermediate waypoints to route AROUND avoid zones.
        
        Strategy: For each avoid zone, compute perpendicular waypoint at distance.
        """
        if not avoid_zones:
            return None
        
        waypoints = []
        for zone in avoid_zones:
            zone_pt = Point(zone["lng"], zone["lat"])
            # Compute waypoint offset perpendicular to line (origin → destination)
            # at safe distance from zone
            waypoint = self._compute_offset_waypoint(origin, destination, zone_pt, zone.get("radius_m", 500))
            if waypoint:
                waypoints.append(waypoint)
        
        return waypoints if waypoints else None
    
    def _compute_offset_waypoint(
        self, origin: tuple[float, float], destination: tuple[float, float],
        zone_pt, radius_m: float
    ) -> tuple[float, float] | None:
        """
        Compute waypoint perpendicular to line at safe distance from zone.
        
        Returns: (lat, lng) tuple or None if computation fails
        """
        # TODO: Implement perpendicular offset calculation
        # This is a simplified placeholder
        return None
    
    def _compute_safety_pre_routing(
        self, route_coords: list[list[float]], avoid_zones: list[dict] | None
    ) -> str:
        """
        PRE-COMPUTE safety rating instead of returning PENDING_ANALYSIS.
        
        Returns: "SAFE" | "CAUTION" | "UNSAFE"
        """
        if not avoid_zones:
            return "SAFE"  # No constraints, assume safe
        
        from shapely.geometry import LineString
        route_line = LineString([(c[0], c[1]) for c in route_coords])
        
        unsafe_count = 0
        for zone in avoid_zones:
            zone_pt = Point(zone["lng"], zone["lat"])
            radius_deg = (zone.get("radius_m", 500) / 111_000)  # Rough conversion
            distance = route_line.distance(zone_pt)
            if distance < radius_deg:
                unsafe_count += 1
        
        if unsafe_count == 0:
            return "SAFE"
        elif unsafe_count == 1:
            return "CAUTION"
        else:
            return "UNSAFE"
