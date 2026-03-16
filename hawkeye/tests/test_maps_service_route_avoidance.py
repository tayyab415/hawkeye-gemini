"""Unit tests for MapsService avoid-area routing behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.services.maps_service import MapsService


UNSAFE_ROUTE = [[-1.0, 0.0], [2.0, 0.0]]
SAFE_ROUTE = [[-1.0, 1.5], [2.0, 1.5]]
DETOUR_SAFE_ROUTE = [
    [-1.0, 1.5],
    [-0.5, 2.2],
    [0.0, 1.0],
    [0.5, 2.2],
    [1.0, 1.0],
    [2.0, 1.5],
]
AVOID_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[-0.2, -0.2], [0.8, -0.2], [0.8, 0.2], [-0.2, 0.2], [-0.2, -0.2]]
    ],
}


def _route_result(polyline_id: str, distance_m: int, duration_s: int) -> dict:
    return {
        "legs": [
            {
                "distance": {"value": distance_m, "text": f"{distance_m / 1000:.1f} km"},
                "duration": {"value": duration_s, "text": f"{duration_s // 60} mins"},
            }
        ],
        "overview_polyline": {"points": polyline_id},
    }


class TestMapsServiceRouteAvoidance:
    def test_selects_safer_route_when_avoid_geojson_provided(self) -> None:
        mock_client = Mock()
        mock_client.directions.return_value = [
            _route_result("unsafe", 900, 120),
            _route_result("safe", 1600, 260),
        ]
        decoded = {"unsafe": UNSAFE_ROUTE, "safe": SAFE_ROUTE}

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
                avoid_geojson=AVOID_GEOJSON,
            )

        assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
        assert result["safety_rating"] == "SAFE"
        assert result["route_safety"]["candidate_count"] == 2
        assert result["route_safety"]["selected_candidate_index"] == 1
        assert result["route_safety"]["intersects_avoid_area"] is False
        assert result["route_geojson"]["properties"]["route_safety"]["avoidance_applied"]
        assert result["route_geojson"]["properties"]["safety_rating"] == "SAFE"
        assert result["route_risk_handling"]["status"] == "safe_route_selected"
        assert result["route_risk_handling"]["safe_route_found"] is True

    def test_evaluates_each_candidate_when_avoid_geojson_provided(self) -> None:
        mock_client = Mock()
        mock_client.directions.return_value = [
            _route_result("unsafe", 900, 120),
            _route_result("safe", 1600, 260),
        ]
        decoded = {"unsafe": UNSAFE_ROUTE, "safe": SAFE_ROUTE}
        safety_evaluations = [
            {
                "safety_rating": "UNSAFE",
                "intersects_avoid_area": True,
                "intersection_pct": 60.0,
                "min_distance_to_avoid_m": 0.0,
            },
            {
                "safety_rating": "SAFE",
                "intersects_avoid_area": False,
                "intersection_pct": 0.0,
                "min_distance_to_avoid_m": 140.0,
            },
        ]

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
            patch(
                "app.services.maps_service._evaluate_route_safety",
                side_effect=safety_evaluations,
            ) as mock_eval,
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
                avoid_geojson=AVOID_GEOJSON,
            )

        assert mock_eval.call_count == 2
        assert result["route_safety"]["selected_candidate_index"] == 1
        assert result["route_safety"]["safety_rating"] == "SAFE"

    def test_preserves_first_route_when_avoid_geojson_absent(self) -> None:
        mock_client = Mock()
        mock_client.directions.return_value = [
            _route_result("unsafe", 900, 120),
            _route_result("safe", 1600, 260),
        ]
        decoded = {"unsafe": UNSAFE_ROUTE, "safe": SAFE_ROUTE}

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
            )

        assert result["route_geojson"]["geometry"]["coordinates"] == UNSAFE_ROUTE
        assert "route_safety" not in result
        assert "safety_rating" not in result

    def test_raises_for_invalid_avoid_geojson(self) -> None:
        with patch("app.services.maps_service.googlemaps.Client", return_value=Mock()):
            service = MapsService(api_key="fake")
            with pytest.raises(ValueError, match="avoid_geojson"):
                service.get_evacuation_route(
                    origin_latlng=(-6.22, 106.85),
                    destination_latlng=(-6.21, 106.88),
                    avoid_geojson="not valid json",
                )

    def test_prefers_less_detour_when_safety_scores_are_equal(self) -> None:
        mock_client = Mock()
        mock_client.directions.return_value = [
            _route_result("detour", 1800, 280),
            _route_result("direct", 1600, 260),
        ]
        decoded = {"detour": DETOUR_SAFE_ROUTE, "direct": SAFE_ROUTE}

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
                avoid_geojson=AVOID_GEOJSON,
            )

        assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
        assert result["route_safety"]["selected_candidate_index"] == 1
        assert result["route_quality"]["detour_ratio"] < 1.1

    def test_attempts_detour_waypoints_when_initial_routes_are_unsafe(self) -> None:
        mock_client = Mock()
        mock_client.directions.side_effect = [
            [_route_result("unsafe_0", 900, 120)],
            [_route_result("unsafe_1", 950, 140)],
            [_route_result("safe_detour", 1800, 320)],
            [],
            [],
        ]
        decoded = {
            "unsafe_0": UNSAFE_ROUTE,
            "unsafe_1": UNSAFE_ROUTE,
            "safe_detour": SAFE_ROUTE,
        }

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
                avoid_geojson=AVOID_GEOJSON,
            )

        assert mock_client.directions.call_count == 5
        assert result["route_geojson"]["geometry"]["coordinates"] == SAFE_ROUTE
        assert result["route_source"] == "detour_waypoint"
        assert result["route_safety"]["selected_route_source"] == "detour_waypoint"
        assert result["route_risk_handling"]["detour_search_attempted"] is True
        assert result["route_risk_handling"]["detour_candidates_tested"] == 2
        assert result["route_risk_handling"]["safe_route_found"] is True
        assert result["route_risk_handling"]["status"] == "safe_route_selected"

    def test_reports_unsafe_status_when_no_safe_candidate_is_found(self) -> None:
        mock_client = Mock()
        mock_client.directions.side_effect = [
            [_route_result("unsafe_0", 900, 120)],
            [_route_result("unsafe_1", 950, 140)],
            [_route_result("unsafe_2", 1200, 180)],
            [],
            [],
        ]
        decoded = {
            "unsafe_0": UNSAFE_ROUTE,
            "unsafe_1": UNSAFE_ROUTE,
            "unsafe_2": UNSAFE_ROUTE,
        }

        with (
            patch("app.services.maps_service.googlemaps.Client", return_value=mock_client),
            patch("app.services.maps_service._decode_polyline", side_effect=decoded.get),
        ):
            service = MapsService(api_key="fake")
            result = service.get_evacuation_route(
                origin_latlng=(-6.22, 106.85),
                destination_latlng=(-6.21, 106.88),
                avoid_geojson=AVOID_GEOJSON,
            )

        assert result["route_safety"]["intersects_avoid_area"] is True
        assert result["route_risk_handling"]["detour_search_attempted"] is True
        assert result["route_risk_handling"]["safe_route_found"] is False
        assert result["route_risk_handling"]["status"] == "unsafe_route_selected"
