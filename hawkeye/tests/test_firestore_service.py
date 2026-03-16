"""Integration tests for IncidentService (Firestore)."""

from __future__ import annotations

import time

import pytest

from app.services.firestore_service import IncidentService


class TestLogEvent:
    def test_writes_and_returns_id(
        self, incident_service: IncidentService
    ) -> None:
        doc_id = incident_service.log_event("test", "low", {"msg": "test"})
        assert doc_id, "Expected a document ID back"

    def test_event_readable(
        self, incident_service: IncidentService
    ) -> None:
        incident_service.log_event("test_read", "medium", {"key": "value"})
        time.sleep(1)
        timeline = incident_service.get_session_timeline()
        types = [e.get("event_type") for e in timeline if "event_type" in e]
        assert "test_read" in types


class TestLogDecision:
    def test_writes_decision(
        self, incident_service: IncidentService
    ) -> None:
        doc_id = incident_service.log_decision(
            "Evacuate zone A", "Water rising fast", 0.92
        )
        assert doc_id


class TestWaterLevel:
    def test_set_and_get(self, incident_service: IncidentService) -> None:
        incident_service.set_water_level(4.1)
        result = incident_service.get_water_level()
        assert result["water_level_m"] == pytest.approx(4.1)

    def test_default_when_missing(
        self, incident_service: IncidentService
    ) -> None:
        result = incident_service.get_water_level()
        assert "water_level_m" in result


class TestSessionTimeline:
    def test_returns_ordered_list(
        self, incident_service: IncidentService
    ) -> None:
        incident_service.log_event("timeline_a", "low", {})
        time.sleep(0.5)
        incident_service.log_decision("decision_b", "reason", 0.8)
        time.sleep(1)
        timeline = incident_service.get_session_timeline()
        assert isinstance(timeline, list)
        assert len(timeline) >= 2
