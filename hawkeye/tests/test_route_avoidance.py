"""
Focused tests for proactive flood-constrained routing behavior.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from app.hawkeye_agent.tools.analyst import evaluate_route_safety
from app.hawkeye_agent.tools.coordinator import generate_evacuation_route
from app.services.maps_service import MapsService

FLOOD_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [106.84, -6.23],
            [106.87, -6.23],
            [106.87, -6.21],
            [106.84, -6.21],
            [106.84, -6.23],
        ]
    ],
}


def _build_route_result(safety_rating: str | None = "SAFE") -> dict:
    result: dict = {
        "route_geojson": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [106.855, -6.225],
                    [106.865, -6.235],
                ],
            },
            "properties": {"distance_m": 5000, "duration_s": 300},
        },
        "distance_m": 5000,
        "duration_s": 300,
    }
    if safety_rating is not None:
        result["safety_rating"] = safety_rating
        result["route_safety"] = {
            "avoidance_applied": True,
            "candidate_count": 2,
            "selected_candidate_index": 1,
            "intersects_avoid_area": safety_rating != "SAFE",
            "intersection_pct": 0.0 if safety_rating == "SAFE" else 15.0,
            "min_distance_to_avoid_m": 120.0,
            "safety_rating": safety_rating,
        }
    return result


def _build_maps_service(route_result: dict) -> MagicMock:
    mock_service = MagicMock()
    mock_service.geocode.side_effect = [
        {
            "lat": -6.225,
            "lng": 106.855,
            "formatted_address": "Kampung Melayu",
        },
        {
            "lat": -6.215,
            "lng": 106.88,
            "formatted_address": "Gelora Bung Karno Stadium",
        },
    ]
    mock_service.get_evacuation_route.return_value = route_result
    return mock_service


def _build_route_variant(
    *,
    safety_rating: str,
    duration_s: int,
    distance_m: int,
) -> dict:
    result = _build_route_result(safety_rating)
    result["duration_s"] = duration_s
    result["distance_m"] = distance_m
    route_geojson = result.get("route_geojson")
    if isinstance(route_geojson, dict):
        properties = route_geojson.get("properties")
        if isinstance(properties, dict):
            properties["duration_s"] = duration_s
            properties["distance_m"] = distance_m
    return result


@pytest.fixture
def flood_geojson_str() -> str:
    return json.dumps(FLOOD_GEOMETRY)


class TestAvoidGeojsonNotDeadCode:
    def test_avoid_geojson_parameter_exists_and_is_used(self) -> None:
        sig = inspect.signature(MapsService.get_evacuation_route)
        assert "avoid_geojson" in sig.parameters

        source = inspect.getsource(MapsService.get_evacuation_route)
        usage_count = source.count("avoid_geojson")
        assert usage_count > 1


class TestCoordinatorFetchesFloodBeforeRouting:
    def test_generate_evacuation_route_fetches_flood_constraints(self) -> None:
        mock_service = _build_maps_service(_build_route_result("SAFE"))

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch(
                "app.hawkeye_agent.tools.analyst.get_flood_extent",
                return_value={"geojson": FLOOD_GEOMETRY},
            ) as mock_get_flood,
        ):
            result = generate_evacuation_route(
                origin_name="Kampung Melayu",
                destination_name="Gelora Bung Karno Stadium",
                avoid_flood=True,
            )

        mock_get_flood.assert_called_once()
        call_kwargs = mock_service.get_evacuation_route.call_args.kwargs
        assert call_kwargs["avoid_geojson"] == FLOOD_GEOMETRY
        assert result["flood_constraints_applied"] is True
        assert result["safety_rating"] == "SAFE"
        assert result["safety_rating"] != "PENDING_ANALYSIS"

    def test_generate_evacuation_route_falls_back_if_flood_unavailable(self) -> None:
        mock_service = _build_maps_service(_build_route_result(safety_rating=None))

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch(
                "app.hawkeye_agent.tools.analyst.get_flood_extent",
                return_value={"geojson": None, "error": "service unavailable"},
            ),
        ):
            result = generate_evacuation_route(
                origin_name="Kampung Melayu",
                destination_name="Gelora Bung Karno Stadium",
                avoid_flood=True,
            )

        call_kwargs = mock_service.get_evacuation_route.call_args.kwargs
        assert "avoid_geojson" not in call_kwargs
        assert result["flood_constraints_applied"] is False
        assert result["safety_rating"] == "UNKNOWN"
        assert result["safety_rating"] != "PENDING_ANALYSIS"

    def test_generate_evacuation_route_skip_flood_lookup_when_disabled(self) -> None:
        mock_service = _build_maps_service(_build_route_result("SAFE"))

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch("app.hawkeye_agent.tools.analyst.get_flood_extent") as mock_get_flood,
        ):
            result = generate_evacuation_route(
                origin_name="Kampung Melayu",
                destination_name="Gelora Bung Karno Stadium",
                avoid_flood=False,
            )

        mock_get_flood.assert_not_called()
        call_kwargs = mock_service.get_evacuation_route.call_args.kwargs
        assert "avoid_geojson" not in call_kwargs
        assert "flood_constraints_applied" not in result
        assert result["safety_rating"] == "SAFE"

    def test_generate_evacuation_route_passthrough_route_risk_metadata(self) -> None:
        route_result = _build_route_result("CAUTION")
        route_result["route_risk_handling"] = {
            "avoidance_requested": True,
            "constraints_applied": False,
            "status": "safety_unavailable",
            "message": "Safety model unavailable; route generated without risk scoring.",
        }
        mock_service = _build_maps_service(route_result)

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch(
                "app.hawkeye_agent.tools.analyst.get_flood_extent",
                return_value={"geojson": FLOOD_GEOMETRY},
            ),
        ):
            result = generate_evacuation_route(
                origin_name="Kampung Melayu",
                destination_name="Gelora Bung Karno Stadium",
                avoid_flood=True,
            )

        call_kwargs = mock_service.get_evacuation_route.call_args.kwargs
        assert call_kwargs["avoid_geojson"] == FLOOD_GEOMETRY
        assert result["route_safety"] == route_result["route_safety"]
        assert result["route_risk_handling"] == route_result["route_risk_handling"]
        assert result["flood_constraints_applied"] is False


class TestSafetyRatingPreComputed:
    @pytest.mark.parametrize("rating", ["SAFE", "CAUTION", "UNSAFE", "UNKNOWN"])
    def test_generate_evacuation_route_returns_computed_rating(self, rating: str) -> None:
        mock_service = _build_maps_service(_build_route_result(rating))

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch(
                "app.hawkeye_agent.tools.analyst.get_flood_extent",
                return_value={"geojson": FLOOD_GEOMETRY},
            ),
        ):
            result = generate_evacuation_route(
                origin_name="Kampung Melayu",
                destination_name="Gelora Bung Karno Stadium",
                avoid_flood=True,
            )

        assert result["safety_rating"] == rating
        assert result["safety_rating"] != "PENDING_ANALYSIS"


class TestDynamicShelterRouting:
    def test_generate_evacuation_route_ranks_nearest_safe_shelters(self) -> None:
        mock_service = MagicMock()
        mock_service.find_nearby_shelters.return_value = [
            {
                "name": "Shelter A",
                "lat": -6.214,
                "lng": 106.872,
                "address": "Address A",
                "place_id": "A",
            },
            {
                "name": "Shelter B",
                "lat": -6.219,
                "lng": 106.881,
                "address": "Address B",
                "place_id": "B",
            },
            {
                "name": "Shelter C",
                "lat": -6.222,
                "lng": 106.889,
                "address": "Address C",
                "place_id": "C",
            },
        ]
        mock_service.get_evacuation_route.side_effect = [
            _build_route_variant(safety_rating="CAUTION", duration_s=540, distance_m=7100),
            _build_route_variant(safety_rating="SAFE", duration_s=570, distance_m=7600),
            _build_route_variant(safety_rating="UNSAFE", duration_s=480, distance_m=6900),
        ]

        with (
            patch(
                "app.hawkeye_agent.tools.coordinator._get_maps_service",
                return_value=mock_service,
            ),
            patch(
                "app.hawkeye_agent.tools.analyst.get_flood_extent",
                return_value={"geojson": FLOOD_GEOMETRY},
            ),
        ):
            result = generate_evacuation_route(
                origin_name="",
                destination_name="",
                avoid_flood=True,
                origin_lat=-6.225,
                origin_lng=106.855,
                origin_label="Selected map point",
                destination_mode="nearest_safe_shelters",
            )

        assert result["destination_mode"] == "nearest_safe_shelters"
        assert result["origin"]["formatted_address"] == "Selected map point"
        assert result["candidate_shelter_count"] == 3
        assert result["alternate_count"] == 2
        assert result["safety_rating"] == "SAFE"
        assert len(result["route_options"]) == 3
        assert result["route_options"][0]["safety_rating"] == "SAFE"
        assert result["route_options"][1]["safety_rating"] == "CAUTION"
        assert result["route_options"][2]["safety_rating"] == "UNSAFE"
        assert "evacuation_zone_geojson" in result
        mock_service.find_nearby_shelters.assert_called_once()
        assert mock_service.get_evacuation_route.call_count == 3


class TestEvaluateRouteSafetyWithConstraints:
    def test_route_intersecting_flood_marked_unsafe(self, flood_geojson_str: str) -> None:
        route_geojson = json.dumps(
            {
                "type": "LineString",
                "coordinates": [
                    [106.83, -6.22],
                    [106.88, -6.22],
                ],
            }
        )

        result = evaluate_route_safety(route_geojson, flood_geojson_str)

        assert result["is_safe"] is False
        assert result["safety_rating"] == "UNSAFE"
        assert result["intersection_pct"] > 0
        assert result["danger_zones"]

    def test_route_outside_flood_marked_safe(self, flood_geojson_str: str) -> None:
        route_geojson = json.dumps(
            {
                "type": "LineString",
                "coordinates": [
                    [106.79, -6.19],
                    [106.80, -6.185],
                ],
            }
        )

        result = evaluate_route_safety(route_geojson, flood_geojson_str)

        assert result["is_safe"] is True
        assert result["safety_rating"] == "SAFE"
        assert result["intersection_pct"] == 0
        assert not result["danger_zones"]
