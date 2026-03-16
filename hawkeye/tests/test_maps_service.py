"""Integration tests for MapsService (Google Maps Platform).

Requires GCP_MAPS_API_KEY with Geocoding, Elevation, Places, and
Directions APIs enabled.
"""

from __future__ import annotations

import pytest

from app.services.maps_service import MapsService


class TestGeocode:
    def test_kampung_melayu(self, maps_service: MapsService) -> None:
        result = maps_service.geocode("Kampung Melayu Jakarta")
        assert result["lat"] is not None
        assert result["lng"] is not None
        assert abs(result["lat"] - (-6.225)) < 0.05, (
            f"Latitude {result['lat']} too far from -6.225"
        )
        assert abs(result["lng"] - 106.855) < 0.05, (
            f"Longitude {result['lng']} too far from 106.855"
        )

    def test_returns_formatted_address(self, maps_service: MapsService) -> None:
        result = maps_service.geocode("Kampung Melayu Jakarta")
        assert result["formatted_address"]


class TestElevation:
    def test_jakarta_elevation(self, maps_service: MapsService) -> None:
        elev = maps_service.get_elevation(-6.225, 106.855)
        assert 0 <= elev <= 50, f"Jakarta elevation {elev}m outside expected 0-50m"

    def test_batch_elevation(self, maps_service: MapsService) -> None:
        locations = [(-6.225, 106.855), (-6.200, 106.845)]
        results = maps_service.get_elevations_batch(locations)
        assert len(results) == 2
        for r in results:
            assert "elevation_m" in r
