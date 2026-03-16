"""Tests for the agent tool functions — Step 3 verification.

These tests verify that the tool implementations work correctly.
Some tests require GCP API keys and hit real services (marked with
appropriate skips).  Local-only tests always pass.
"""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path

import pytest

# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geojson"


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.environ.get("GCP_API_KEY")
    if not key:
        pytest.skip("GCP_API_KEY not set")
    return key


@pytest.fixture(scope="session")
def flood_geojson_str() -> str:
    path = _DATA_DIR / "flood_extent.geojson"
    if not path.exists():
        pytest.skip("flood_extent.geojson not found")
    with open(path) as f:
        data = json.load(f)
    return json.dumps(data["geometry"])


@pytest.fixture()
def sample_jpeg_base64() -> str:
    """Create a minimal valid JPEG (1x1 red pixel) for testing."""
    # Minimal JPEG: 1x1 red pixel
    # This is a valid JPEG file with the correct SOI/EOI markers
    jpeg_bytes = bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
        0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
        0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
        0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
        0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
        0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
        0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
        0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
        0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
        0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
        0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
        0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00,
        0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
        0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
        0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
        0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
        0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A,
        0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35,
        0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
        0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00,
        0x3F, 0x00, 0x7B, 0x94, 0x11, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0xFF, 0xD9,
    ])
    return base64.b64encode(jpeg_bytes).decode("utf-8")


# ────────────────────────────────────────────────────────────────────
# Confidence Decay (always passes — pure math)
# ────────────────────────────────────────────────────────────────────

class TestConfidenceDecay:
    def test_zero_hours(self):
        from app.hawkeye_agent.tools.predictor import compute_confidence_decay
        r = compute_confidence_decay(0)
        assert r["confidence_pct"] == 95
        assert r["color"] == "green"

    def test_high_hours_decays(self):
        from app.hawkeye_agent.tools.predictor import compute_confidence_decay
        r = compute_confidence_decay(48)
        assert r["confidence_pct"] < 5
        assert r["color"] == "red"

    def test_monotonically_decreasing(self):
        from app.hawkeye_agent.tools.predictor import compute_confidence_decay
        prev = 100
        for h in range(0, 50, 5):
            r = compute_confidence_decay(h)
            assert r["confidence_pct"] <= prev
            prev = r["confidence_pct"]

    def test_has_required_fields(self):
        from app.hawkeye_agent.tools.predictor import compute_confidence_decay
        r = compute_confidence_decay(12)
        for key in ("hours_ahead", "confidence_pct", "color", "recommendation", "model"):
            assert key in r


# ────────────────────────────────────────────────────────────────────
# Route Safety Evaluation (local — uses Shapely, no GCP)
# ────────────────────────────────────────────────────────────────────

class TestRouteSafety:
    def test_unsafe_route_through_flood(self, flood_geojson_str):
        from app.hawkeye_agent.tools.analyst import evaluate_route_safety
        route = json.dumps({
            "type": "LineString",
            "coordinates": [
                [106.830, -6.220],
                [106.860, -6.220],
                [106.880, -6.220],
            ],
        })
        r = evaluate_route_safety(route, flood_geojson_str)
        assert r["is_safe"] is False
        assert r["safety_rating"] in ("UNSAFE", "CAUTION")
        assert r["intersection_pct"] > 0
        assert len(r["danger_zones"]) > 0

    def test_safe_route_outside_flood(self, flood_geojson_str):
        from app.hawkeye_agent.tools.analyst import evaluate_route_safety
        route = json.dumps({
            "type": "LineString",
            "coordinates": [
                [106.800, -6.180],
                [106.810, -6.175],
                [106.820, -6.170],
            ],
        })
        r = evaluate_route_safety(route, flood_geojson_str)
        assert r["is_safe"] is True
        assert r["safety_rating"] == "SAFE"
        assert r["intersection_pct"] == 0
        assert len(r["danger_zones"]) == 0

    def test_handles_feature_wrapper(self, flood_geojson_str):
        from app.hawkeye_agent.tools.analyst import evaluate_route_safety
        route_feature = json.dumps({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[106.830, -6.220], [106.860, -6.220]],
            },
            "properties": {},
        })
        flood_feature = json.dumps({
            "type": "Feature",
            "geometry": json.loads(flood_geojson_str),
            "properties": {},
        })
        r = evaluate_route_safety(route_feature, flood_feature)
        assert "is_safe" in r
        assert "safety_rating" in r

    def test_error_handling(self):
        from app.hawkeye_agent.tools.analyst import evaluate_route_safety
        r = evaluate_route_safety("not valid json", "also bad")
        assert r["safety_rating"] == "UNKNOWN"
        assert "error" in r


# ────────────────────────────────────────────────────────────────────
# Perception Tools (requires GCP_API_KEY)
# ────────────────────────────────────────────────────────────────────

class TestPerception:
    @pytest.mark.skipif(
        not os.environ.get("GCP_API_KEY"),
        reason="GCP_API_KEY not set",
    )
    def test_analyze_frame_returns_structured(self, sample_jpeg_base64):
        from app.hawkeye_agent.tools.perception import analyze_frame
        r = analyze_frame(sample_jpeg_base64)
        assert "threat_level" in r
        assert "damage_detected" in r
        assert "water_depth_estimate" in r
        assert "confidence" in r
        # Should NOT contain the mock description from the old stub
        assert "Street flooding visible in residential area" not in r.get("description", "")

    @pytest.mark.skipif(
        not os.environ.get("GCP_API_KEY"),
        reason="GCP_API_KEY not set",
    )
    def test_compare_frames_returns_structured(self, sample_jpeg_base64):
        from app.hawkeye_agent.tools.perception import compare_frames
        r = compare_frames(sample_jpeg_base64, sample_jpeg_base64, 15)
        assert "changes_detected" in r
        assert "threat_level_change" in r
        assert "confidence" in r

    def test_analyze_frame_error_handling(self):
        from app.hawkeye_agent.tools.perception import analyze_frame
        r = analyze_frame("not-valid-base64!!!")
        # Should return graceful fallback, not crash
        assert "threat_level" in r
        assert r["confidence"] == 0.0


# ────────────────────────────────────────────────────────────────────
# Predictor (requires GCP_API_KEY for image gen)
# ────────────────────────────────────────────────────────────────────

class TestPredictor:
    @pytest.mark.skipif(
        not os.environ.get("GCP_API_KEY"),
        reason="GCP_API_KEY not set",
    )
    def test_generate_risk_projection(self, sample_jpeg_base64):
        from app.hawkeye_agent.tools.predictor import generate_risk_projection
        r = generate_risk_projection(sample_jpeg_base64, "+2m water rise", 2.0)
        assert "scenario" in r
        assert "confidence" in r
        assert "caveats" in r
        assert isinstance(r["caveats"], list)

    def test_projection_error_handling(self):
        from app.hawkeye_agent.tools.predictor import generate_risk_projection
        r = generate_risk_projection("bad-base64", "test", 1.0)
        # Should return graceful fallback
        assert r["projection_generated"] is False
        assert "error" in r
        assert "confidence" in r  # Should still compute confidence


# ────────────────────────────────────────────────────────────────────
# Coordinator — send_emergency_alert
# ────────────────────────────────────────────────────────────────────

class TestCoordinator:
    def test_send_alert_graceful_without_gmail(self):
        """Without Gmail credentials, it should still work (log only)."""
        from app.hawkeye_agent.tools.coordinator import send_emergency_alert
        r = send_emergency_alert(
            subject="Test Alert",
            body="This is a test alert",
            recipient_email="test@example.com",
        )
        assert "sent" in r
        assert "message_id" in r
        assert "recipient" in r
        assert r["delivery_method"] in ("gmail_api", "firestore_log")

    def test_update_map(self):
        from app.hawkeye_agent.tools.coordinator import update_map
        r = update_map(
            geojson='{"type":"Point","coordinates":[106.85,-6.2]}',
            layer_type="marker",
            label="Test Point",
        )
        assert r["updated"] is True
        assert r["layer_type"] == "marker"
        assert "layer_id" in r


# ────────────────────────────────────────────────────────────────────
# Search Grounding (requires GCP_API_KEY)
# ────────────────────────────────────────────────────────────────────

class TestSearchGrounding:
    @pytest.mark.skipif(
        not os.environ.get("GCP_API_KEY"),
        reason="GCP_API_KEY not set",
    )
    def test_grounding_returns_response(self):
        from app.hawkeye_agent.tools.analyst import get_search_grounding
        r = get_search_grounding("What is Jakarta's population in 2025?")
        assert "response" in r
        assert r["grounded"] is True
        assert len(r["response"]) > 10
