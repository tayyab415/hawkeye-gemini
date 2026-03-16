"""Integration tests for GroundsourceService — the most critical service.

Every test hits real BigQuery tables loaded in Step 0.
"""

from __future__ import annotations

import pytest

from app.services.bigquery_service import GroundsourceService


class TestFloodFrequency:
    def test_returns_nonzero_events(
        self, bigquery_service: GroundsourceService
    ) -> None:
        result = bigquery_service.get_flood_frequency(-6.225, 106.855, 10)
        assert result["total_events"] > 0, "Expected flood events near Kampung Melayu"
        assert result["avg_duration_days"] > 0

    def test_has_all_stat_fields(
        self, bigquery_service: GroundsourceService
    ) -> None:
        result = bigquery_service.get_flood_frequency(-6.225, 106.855, 10)
        for key in (
            "total_events",
            "avg_duration_days",
            "max_duration_days",
            "earliest_event",
            "latest_event",
            "avg_area_sqkm",
            "max_area_sqkm",
        ):
            assert key in result, f"Missing key: {key}"


class TestInfrastructureAtRisk:
    def test_returns_grouped_results(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        result = bigquery_service.get_infrastructure_at_risk(jakarta_flood_geojson)
        assert "hospitals" in result
        assert "schools" in result
        assert "total_at_risk" in result
        assert result["total_at_risk"] >= 0

    def test_hospital_entries_have_names(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        result = bigquery_service.get_infrastructure_at_risk(jakarta_flood_geojson)
        for h in result["hospitals"]:
            assert "name" in h
            assert "latitude" in h
            assert "longitude" in h


class TestInfrastructureAtExpandedLevel:
    def test_returns_newly_at_risk(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        result = bigquery_service.get_infrastructure_at_expanded_level(
            jakarta_flood_geojson, 1000
        )
        assert "newly_at_risk" in result
        assert "hospitals" in result
        assert "schools" in result

    def test_expanded_differs_from_base(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        base = bigquery_service.get_infrastructure_at_risk(jakarta_flood_geojson)
        expanded = bigquery_service.get_infrastructure_at_expanded_level(
            jakarta_flood_geojson, 2000
        )
        base_names = {h["name"] for h in base.get("hospitals", [])}
        expanded_names = {h["name"] for h in expanded.get("hospitals", [])}
        assert expanded_names.isdisjoint(base_names), (
            "Expanded set should not contain items already in the base set"
        )


class TestPatternMatch:
    def test_returns_at_least_one(
        self, bigquery_service: GroundsourceService
    ) -> None:
        results = bigquery_service.find_pattern_match(15.0, 4)
        assert len(results) >= 1, "Expected at least 1 historical pattern match"

    def test_result_has_required_fields(
        self, bigquery_service: GroundsourceService
    ) -> None:
        results = bigquery_service.find_pattern_match(15.0, 4)
        if results:
            r = results[0]
            assert "start_date" in r
            assert "duration_days" in r
            assert "area_sqkm" in r


class TestMonthlyFrequency:
    def test_returns_12_months(
        self, bigquery_service: GroundsourceService
    ) -> None:
        results = bigquery_service.get_monthly_frequency()
        months = {r["month"] for r in results}
        assert len(months) == 12, f"Expected 12 months, got {len(months)}"

    def test_monsoon_months_higher(
        self, bigquery_service: GroundsourceService
    ) -> None:
        results = bigquery_service.get_monthly_frequency()
        by_month = {r["month"]: r["flood_count"] for r in results}
        monsoon_avg = sum(by_month.get(m, 0) for m in [11, 12, 1, 2]) / 4
        dry_avg = sum(by_month.get(m, 0) for m in [6, 7, 8, 9]) / 4
        assert monsoon_avg > dry_avg, (
            f"Monsoon avg ({monsoon_avg}) should exceed dry avg ({dry_avg})"
        )

    def test_caching(self, bigquery_service: GroundsourceService) -> None:
        r1 = bigquery_service.get_monthly_frequency()
        r2 = bigquery_service.get_monthly_frequency()
        assert r1 is r2, "Second call should return cached object"


class TestYearlyTrend:
    def test_returns_multiple_years(
        self, bigquery_service: GroundsourceService
    ) -> None:
        results = bigquery_service.get_yearly_trend()
        assert len(results) >= 2, "Expected data for multiple years"

    def test_caching(self, bigquery_service: GroundsourceService) -> None:
        r1 = bigquery_service.get_yearly_trend()
        r2 = bigquery_service.get_yearly_trend()
        assert r1 is r2


class TestFloodsIntersectingPolygon:
    def test_returns_list(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        results = bigquery_service.query_floods_intersecting_polygon(
            jakarta_flood_geojson
        )
        assert isinstance(results, list)

    def test_result_fields(
        self,
        bigquery_service: GroundsourceService,
        jakarta_flood_geojson: str,
    ) -> None:
        results = bigquery_service.query_floods_intersecting_polygon(
            jakarta_flood_geojson
        )
        if results:
            r = results[0]
            assert "start_date" in r
            assert "overlap_sqkm" in r
            assert "geometry_geojson" in r
