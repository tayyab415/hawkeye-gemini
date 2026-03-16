"""Local backend tests for globe-control tools and event mapping."""

from __future__ import annotations

import inspect
import json
import os
import typing

import pytest

pytest.importorskip("google.adk")

import app.main as main_module
from app.hawkeye_agent.agent import root_agent
from app.hawkeye_agent.tools import globe_control
from app.main import (
    DIRECT_FALLBACK_SUPPRESS_TOOLS,
    GLOBE_CONTROL_TOOLS,
    _build_heartbeat_pong_payload,
    _handle_stream_control_message,
    _extract_screenshot_response_payload,
    _ensure_google_api_key_from_fallback,
    _handle_upstream_audio_frame,
    _is_likely_heavy_analysis_request,
    _match_agent_globe_confirmation,
    _normalize_route_context_update,
    _normalize_video_message_payload,
    _map_tool_result_to_events,
    _match_direct_globe_command,
    _suppress_pending_globe_fallback_for_matching_tool,
)


class _DummyMapsService:
    def __init__(self, lat: float = -6.123, lng: float = 106.789, fail: bool = False):
        self.lat = lat
        self.lng = lng
        self.fail = fail

    def geocode(self, _place_name: str) -> dict:
        if self.fail:
            raise RuntimeError("geocode failure")
        return {"lat": self.lat, "lng": self.lng}


class _DummyLiveRequestQueue:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send_activity_start(self) -> None:
        self.calls.append("activity_start")

    def send_activity_end(self) -> None:
        self.calls.append("activity_end")

    def send_audio_stream_end(self) -> None:
        self.calls.append("audio_stream_end")


class _DummyLiveRequestQueueNoStreamEnd:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send_activity_start(self) -> None:
        self.calls.append("activity_start")

    def send_activity_end(self) -> None:
        self.calls.append("activity_end")


class _DummyAudioQueue:
    def __init__(self) -> None:
        self.frames: list[typing.Any] = []

    def send_realtime(self, blob: typing.Any) -> None:
        self.frames.append(blob)


class _DummyWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)


@pytest.fixture(autouse=True)
def _reset_maps_service_cache():
    original = globe_control._maps_service
    globe_control._maps_service = None
    try:
        yield
    finally:
        globe_control._maps_service = original


def test_tool_signatures_use_scalar_inputs_only():
    tool_funcs = [
        globe_control.fly_to_location,
        globe_control.set_camera_mode,
        globe_control.toggle_data_layer,
        globe_control.deploy_entity,
        globe_control.move_entity,
        globe_control.add_measurement,
        globe_control.set_atmosphere,
        globe_control.capture_current_view,
        globe_control.add_threat_rings,
    ]

    allowed_annotations = {str, int, float, bool}

    for func in tool_funcs:
        sig = inspect.signature(func)
        type_hints = typing.get_type_hints(func)
        for param in sig.parameters.values():
            annotation = type_hints.get(param.name, param.annotation)
            assert (
                annotation in allowed_annotations
            ), f"{func.__name__}.{param.name} uses non-scalar annotation: {annotation!r}"


def test_fly_to_location_uses_geocoded_coordinates():
    globe_control._maps_service = _DummyMapsService(lat=-6.2, lng=106.81)
    result = globe_control.fly_to_location("Kampung Melayu")
    assert result["action"] == "fly_to"
    assert result["lat"] == pytest.approx(-6.2)
    assert result["lng"] == pytest.approx(106.81)
    assert result["location_name"] == "Kampung Melayu"


def test_fly_to_location_falls_back_on_geocode_error():
    globe_control._maps_service = _DummyMapsService(fail=True)
    result = globe_control.fly_to_location("Unknown")
    assert result["action"] == "fly_to"
    assert result["lat"] == pytest.approx(-6.225)
    assert result["lng"] == pytest.approx(106.855)


def test_set_camera_mode_normalizes_and_includes_target_coordinates():
    globe_control._maps_service = _DummyMapsService(lat=-6.31, lng=106.77)
    result = globe_control.set_camera_mode("orbit", "University of Indonesia")
    assert result["action"] == "camera_mode"
    assert result["mode"] == "ORBIT"
    assert result["lat"] == pytest.approx(-6.31)
    assert result["lng"] == pytest.approx(106.77)


def test_add_measurement_returns_expected_keys():
    globe_control._maps_service = _DummyMapsService(lat=-6.2, lng=106.8)
    result = globe_control.add_measurement("A", "B")
    assert result["action"] == "add_measurement"
    assert result["line_id"].startswith("measure_")
    assert isinstance(result["label"], str) and result["label"].endswith(" km")
    for key in ("lat1", "lng1", "lat2", "lng2"):
        assert key in result


def test_add_threat_rings_parses_csv_and_drops_invalid_tokens():
    globe_control._maps_service = _DummyMapsService(lat=-6.21, lng=106.85)
    result = globe_control.add_threat_rings("Center", "1, 2.5, bad, 4")
    assert result["action"] == "add_threat_rings"
    assert result["lat"] == pytest.approx(-6.21)
    assert result["lng"] == pytest.approx(106.85)
    assert result["rings"] == [1.0, 2.5, 4.0]


def test_map_tool_result_passthrough_for_globe_control_tools():
    tool_result = {"action": "set_atmosphere", "mode": "night"}
    events = _map_tool_result_to_events("set_atmosphere", tool_result)
    assert len(events) == 1
    assert events[0]["type"] == "map_update"
    assert events[0]["action"] == "set_atmosphere"
    assert events[0]["mode"] == "night"


def test_capture_current_view_emits_capture_screenshot_map_update():
    tool_result = globe_control.capture_current_view()
    events = _map_tool_result_to_events("capture_current_view", tool_result)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "map_update"
    assert event["action"] == "capture_screenshot"
    assert isinstance(event["request_id"], str)
    assert event["request_id"].startswith("screenshot_")


def test_extract_screenshot_payload_accepts_canonical_message():
    payload = _extract_screenshot_response_payload(
        {
            "type": "screenshot_response",
            "request_id": "req-1",
            "image_base64": "data:image/jpeg;base64,QUJDRA==",
        }
    )
    assert payload == ("req-1", "QUJDRA==", "top_level")


def test_extract_screenshot_payload_accepts_legacy_text_wrapped_message():
    payload = _extract_screenshot_response_payload(
        {
            "type": "text",
            "content": (
                '{"type":"screenshot_response","requestId":"legacy-2","imageBase64":"QUJDRA=="}'
            ),
        }
    )
    assert payload == ("legacy-2", "QUJDRA==", "text.content.json")


def test_stream_control_handler_routes_activity_start():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message({"type": "activity_start"}, queue)
    assert handled is True
    assert queue.calls == ["activity_start"]


def test_stream_control_handler_routes_activity_end():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message({"type": "activity_end"}, queue)
    assert handled is True
    assert queue.calls == ["activity_end"]


def test_stream_control_handler_routes_audio_stream_end():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message({"type": "audio_stream_end"}, queue)
    assert handled is True
    assert queue.calls == ["audio_stream_end"]


def test_stream_control_handler_falls_back_when_stream_end_unavailable():
    queue = _DummyLiveRequestQueueNoStreamEnd()
    handled = _handle_stream_control_message({"type": "audio_stream_end"}, queue)
    assert handled is True
    assert queue.calls == ["activity_end"]


def test_stream_control_handler_suppresses_audio_stream_end_when_explicit_controls_disabled():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message(
        {"type": "audio_stream_end"},
        queue,
        allow_explicit_activity_control=False,
    )
    assert handled is True
    assert queue.calls == []


def test_stream_control_handler_suppresses_activity_end_when_explicit_controls_disabled():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message(
        {"type": "activity_end"},
        queue,
        allow_explicit_activity_control=False,
    )
    assert handled is True
    assert queue.calls == []


def test_stream_control_handler_ignores_unknown_message_type():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message({"type": "mode_change"}, queue)
    assert handled is False
    assert queue.calls == []


def test_stream_control_handler_tolerates_response_cancel_turn_control():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message({"type": "response.cancel"}, queue)
    assert handled is True
    assert queue.calls == []


def test_stream_control_handler_tolerates_client_content_turn_control_shape():
    queue = _DummyLiveRequestQueue()
    handled = _handle_stream_control_message(
        {
            "type": "client_content",
            "turn_complete": True,
            "response": {"cancel": True},
        },
        queue,
    )
    assert handled is True
    assert queue.calls == []


def test_ensure_google_api_key_fallback_uses_gcp_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GCP_API_KEY", "test-gcp-key")

    assert _ensure_google_api_key_from_fallback(log_context="test") is True
    assert os.getenv("GOOGLE_API_KEY") == "test-gcp-key"


def test_ensure_google_api_key_fallback_returns_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GCP_API_KEY", raising=False)

    assert _ensure_google_api_key_from_fallback(log_context="test") is False


@pytest.mark.asyncio
async def test_handle_upstream_audio_frame_queues_audio_when_live_model_enabled() -> None:
    queue = _DummyAudioQueue()
    websocket = _DummyWebSocket()
    notice_channels: set[str] = set()

    await _handle_upstream_audio_frame(
        audio_bytes=b"\x01\x02\x03\x04",
        live_model_enabled=True,
        live_request_queue=queue,
        websocket=websocket,
        live_model_notice_channels=notice_channels,
    )

    assert len(queue.frames) == 1
    assert getattr(queue.frames[0], "mime_type", None) == "audio/pcm;rate=16000"
    assert getattr(queue.frames[0], "data", None) == b"\x01\x02\x03\x04"
    assert websocket.sent_text == []
    assert notice_channels == set()


@pytest.mark.asyncio
async def test_handle_upstream_audio_frame_drops_and_notifies_once_when_live_model_disabled() -> None:
    queue = _DummyAudioQueue()
    websocket = _DummyWebSocket()
    notice_channels: set[str] = set()

    await _handle_upstream_audio_frame(
        audio_bytes=b"\x05\x06\x07\x08",
        live_model_enabled=False,
        live_request_queue=queue,
        websocket=websocket,
        live_model_notice_channels=notice_channels,
    )
    await _handle_upstream_audio_frame(
        audio_bytes=b"\x09\x0A\x0B\x0C",
        live_model_enabled=False,
        live_request_queue=queue,
        websocket=websocket,
        live_model_notice_channels=notice_channels,
    )

    assert queue.frames == []
    assert notice_channels == {"audio"}
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "error"
    assert "Voice input unavailable" in payload["message"]


def test_build_heartbeat_pong_payload_from_ping():
    payload = _build_heartbeat_pong_payload({"type": "ping", "timestamp": 12345})
    assert payload is not None
    assert payload["type"] == "pong"
    assert isinstance(payload["timestamp"], int)
    assert payload["echo_timestamp"] == 12345


def test_normalize_video_payload_accepts_base64_jpeg():
    normalized = _normalize_video_message_payload(
        {
            "type": "video",
            "data": "QUJDRA==",
            "caption": "Analyze latest frame",
            "frame_id": "frame-1",
            "cadence_fps": 1.0,
            "active_layers": "FLOOD_EXTENT, POPULATION_DENSITY",
            "camera_mode": "orbit",
        }
    )
    assert normalized is not None
    frame_bytes, caption, mime_type, metadata = normalized
    assert frame_bytes == b"ABCD"
    assert caption.startswith("Analyze latest frame")
    assert "active layers: FLOOD_EXTENT, POPULATION_DENSITY" in caption
    assert "camera mode: orbit" in caption
    assert mime_type == "image/jpeg"
    assert metadata["frame_id"] == "frame-1"
    assert metadata["cadence_fps"] == 1.0
    assert metadata["active_layers"] == "FLOOD_EXTENT, POPULATION_DENSITY"
    assert metadata["camera_mode"] == "orbit"


def test_normalize_video_payload_rejects_invalid_base64():
    normalized = _normalize_video_message_payload(
        {
            "type": "video",
            "data": "@@not-base64@@",
        }
    )
    assert normalized is None


def test_normalize_route_context_update_accepts_valid_payload():
    normalized = _normalize_route_context_update(
        {
            "type": "context_update",
            "lat": -6.2244,
            "lng": 106.8562,
            "source": "click",
            "label": "Selected map point",
            "radius_km": 3.5,
        }
    )
    assert normalized is not None
    assert normalized["lat"] == pytest.approx(-6.2244)
    assert normalized["lng"] == pytest.approx(106.8562)
    assert normalized["source"] == "click"
    assert normalized["label"] == "Selected map point"
    assert normalized["radius_km"] == pytest.approx(3.5)
    assert isinstance(normalized["timestamp"], int)


def test_normalize_route_context_update_rejects_invalid_coordinates():
    assert _normalize_route_context_update({"lat": "bad", "lng": 106.8}) is None
    assert _normalize_route_context_update({"lat": -120.0, "lng": 106.8}) is None
    assert _normalize_route_context_update({"lat": -6.2, "lng": 190.0}) is None


def test_map_tool_result_emits_runtime_ee_fields_for_flood_extent():
    tool_result = {
        "geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [106.84, -6.22],
                        [106.86, -6.22],
                        [106.86, -6.24],
                        [106.84, -6.24],
                        [106.84, -6.22],
                    ]
                ],
            },
        },
        "area_sqkm": 1.56,
        "growth_rate_pct": {"rate_pct_per_hour": 12.0},
        "metadata": {"source": "earth-engine", "confidence": "MEDIUM"},
        "ee_runtime": {
            "runtime_mode": "fallback_descriptor",
            "layers": [
                {
                    "id": "ee_change_detection",
                    "name": "EE Flood Change Detection",
                    "type": "raster",
                }
            ],
            "temporal_frames": {
                "baseline": {"id": "baseline"},
                "event": {"id": "event"},
                "change": {"id": "change"},
            },
            "temporal_playback": {
                "ordered_frame_ids": ["baseline", "event", "change"],
                "default_frame_id": "change",
                "frames": [],
                "progression_frames": [],
            },
            "temporal_summary": {
                "frame_count": 3,
                "frame_ids": ["baseline", "event", "change"],
                "latest_frame_id": "change",
                "latest_frame_timestamp": "2026-03-14T08:21:17Z",
            },
            "provenance": {"source": "earth-engine"},
            "confidence": {"label": "MEDIUM"},
            "error": None,
        },
    }

    events = _map_tool_result_to_events("get_flood_extent", tool_result)
    ee_events = [event for event in events if event.get("type") == "ee_update"]
    assert len(ee_events) == 1
    ee_event = ee_events[0]

    assert ee_event["area_sqkm"] == tool_result["area_sqkm"]
    assert ee_event["growth_rate_pct"] == tool_result["growth_rate_pct"]
    assert ee_event["metadata"] == tool_result["metadata"]
    assert ee_event["runtime_layers"] == tool_result["ee_runtime"]["layers"]
    assert ee_event["temporal_frames"] == tool_result["ee_runtime"]["temporal_frames"]
    assert ee_event["temporal_playback"] == tool_result["ee_runtime"]["temporal_playback"]
    assert ee_event["temporal_summary"] == tool_result["ee_runtime"]["temporal_summary"]
    assert ee_event["multisensor_fusion"] == ee_event["ee_runtime"]["multisensor_fusion"]
    assert ee_event["ee_runtime"]["runtime_mode"] == "fallback_descriptor"
    assert ee_event["ee_runtime"]["status"] == "fallback"
    assert isinstance(ee_event["ee_runtime"]["provenance"], dict)
    assert "source" in ee_event["ee_runtime"]["provenance"]
    assert isinstance(ee_event["ee_runtime"]["confidence"], dict)
    assert "label" in ee_event["ee_runtime"]["confidence"]
    assert ee_event["temporal_sync"]["status"] == "disabled"
    assert ee_event["runtime_provenance"] == ee_event["ee_runtime"]["provenance"]
    assert ee_event["runtime_confidence"] == ee_event["ee_runtime"]["confidence"]
    assert ee_event["runtime_status"] == "fallback"
    assert ee_event["runtime_mode"] == "fallback_descriptor"
    assert isinstance(ee_event["runtime_state"], dict)
    assert ee_event["runtime_state"]["status"] == "fallback"
    assert ee_event["runtime_state"]["mode"] == "fallback_descriptor"
    assert ee_event["runtime_task_id"] is None
    assert ee_event["live_tile_status"] is None
    assert ee_event["runtime_state"]["task_id"] is None
    assert ee_event["runtime_state"]["live_tile_status"] is None
    assert ee_event["live_analysis_task"] is None
    assert ee_event["temporal_sync_summary"]["latest_frame_id"] == "change"
    assert ee_event["temporal_sync_summary"]["frame_count"] == 3


def test_map_tool_result_propagates_temporal_sync_and_runtime_task_state(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        main_module,
        "_sync_ee_temporal_summary_to_bigquery",
        lambda _payload: {
            "status": "persisted",
            "mode": "bigquery",
            "table": "hawkeye.ee_temporal_summary",
        },
    )

    live_task = {
        "task_id": "ee_live_task_0042",
        "state": "complete",
        "result_available": True,
    }
    tool_result = {
        "geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [106.84, -6.22],
                        [106.86, -6.22],
                        [106.86, -6.24],
                        [106.84, -6.24],
                        [106.84, -6.22],
                    ]
                ],
            },
        },
        "area_sqkm": 2.4,
        "growth_rate_pct": {"rate_pct_per_hour": 9.4},
        "metadata": {"source": "earth-engine", "confidence": "HIGH"},
        "live_analysis_task": live_task,
        "ee_runtime": {
            "runtime_mode": "live_earth_engine_tiles",
            "status": "live",
            "task_id": "ee_live_task_0042",
            "live_tile_status": {
                "status": "live",
                "enabled": True,
                "available_layer_count": 5,
                "layer_ids": [
                    "ee_baseline_backscatter",
                    "ee_event_backscatter",
                    "ee_change_detection",
                    "ee_multisensor_fusion",
                    "ee_fused_flood_likelihood",
                ],
            },
            "layers": [
                {
                    "id": "ee_change_detection",
                    "name": "EE Flood Change Detection",
                    "type": "raster",
                }
            ],
            "temporal_frames": {
                "baseline": {"id": "baseline", "frame_id": "baseline"},
                "event": {"id": "event", "frame_id": "event"},
                "change": {"id": "change", "frame_id": "change"},
            },
            "temporal_playback": {
                "ordered_frame_ids": ["baseline", "event", "change"],
                "default_frame_id": "change",
                "frames": [],
                "progression_frames": [],
            },
            "temporal_summary": {
                "frame_count": 3,
                "frame_ids": ["baseline", "event", "change"],
                "latest_frame_id": "change",
                "latest_frame_timestamp": "2026-03-14T08:21:17Z",
            },
            "provenance": {"source": "earth-engine"},
            "confidence": {"label": "HIGH"},
            "error": None,
        },
    }

    events = _map_tool_result_to_events("get_flood_extent", tool_result)
    ee_events = [event for event in events if event.get("type") == "ee_update"]
    assert len(ee_events) == 1
    ee_event = ee_events[0]

    assert ee_event["temporal_sync"]["status"] == "persisted"
    assert ee_event["temporal_sync"]["mode"] == "bigquery"
    assert ee_event["runtime_task_id"] == "ee_live_task_0042"
    assert ee_event["live_tile_status"] == tool_result["ee_runtime"]["live_tile_status"]
    assert ee_event["live_analysis_task"] == live_task
    assert ee_event["runtime_state"]["status"] == "live"
    assert ee_event["runtime_state"]["mode"] == "live_earth_engine_tiles"
    assert ee_event["runtime_state"]["task_id"] == "ee_live_task_0042"
    assert ee_event["runtime_state"]["live_tile_status"]["status"] == "live"
    assert ee_event["runtime_state"]["task"] == live_task


def test_root_agent_has_direct_globe_tools_and_model():
    assert root_agent.model == "gemini-2.5-flash-native-audio-latest"
    tool_names = [tool.name for tool in (root_agent.tools or [])]
    expected = {
        "fly_to_location",
        "set_camera_mode",
        "toggle_data_layer",
        "deploy_entity",
        "move_entity",
        "add_measurement",
        "set_atmosphere",
        "capture_current_view",
        "add_threat_rings",
    }
    assert set(tool_names) == expected
    assert GLOBE_CONTROL_TOOLS == expected


def test_direct_command_match_prioritizes_layer_toggles_over_fly_to():
    tool_name, tool_args, _ = _match_direct_globe_command("Show me the hospitals")
    assert tool_name == "toggle_data_layer"
    assert tool_args == {"layer_name": "INFRASTRUCTURE", "enabled": True}


def test_direct_command_match_fly_to_location():
    tool_name, tool_args, _ = _match_direct_globe_command("Fly to Kampung Melayu")
    assert tool_name == "fly_to_location"
    assert tool_args == {"location_name": "Kampung Melayu"}


def test_direct_command_match_fly_me_to_location():
    tool_name, tool_args, _ = _match_direct_globe_command("Fly me to Jakarta")
    assert tool_name == "fly_to_location"
    assert tool_args == {"location_name": "Jakarta"}


def test_direct_command_match_skips_analytics_requests():
    assert _match_direct_globe_command("Analyze flood extent around Jakarta.") is None


def test_heavy_analysis_request_matcher_detects_analytical_prompts():
    assert (
        _is_likely_heavy_analysis_request(
            "Can you analyze flood extent and cascade risk around Kampung Melayu?"
        )
        is True
    )


def test_heavy_analysis_request_matcher_skips_trivial_prompts():
    assert _is_likely_heavy_analysis_request("Thanks") is False
    assert _is_likely_heavy_analysis_request("Fly to Monas") is False


def test_direct_command_match_night_vision():
    tool_name, tool_args, _ = _match_direct_globe_command("Switch to night vision")
    assert tool_name == "set_atmosphere"
    assert tool_args == {"mode": "night_vision"}


def test_direct_command_match_measurement():
    tool_name, tool_args, _ = _match_direct_globe_command(
        "How far is Kampung Melayu from University of Indonesia"
    )
    assert tool_name == "add_measurement"
    assert tool_args == {
        "from_location": "Kampung Melayu",
        "to_location": "University of Indonesia",
    }


def test_direct_command_match_orbit_mode_without_geocoding_mode_word():
    tool_name, tool_args, _ = _match_direct_globe_command("Show me orbit mode")
    assert tool_name == "set_camera_mode"
    assert tool_args == {"mode": "orbit", "target_location": ""}


def test_direct_command_match_major_flood_locations():
    tool_name, tool_args, _ = _match_direct_globe_command(
        "What are the major flood locations right now?"
    )
    assert tool_name == "get_flood_hotspots"
    assert tool_args == {}


def test_direct_command_match_show_flood_layer():
    tool_name, tool_args, _ = _match_direct_globe_command("Turn on flood extent layer")
    assert tool_name == "toggle_data_layer"
    assert tool_args == {"layer_name": "FLOOD_EXTENT", "enabled": True}


@pytest.mark.parametrize(
    "command",
    [
        "Turn on population density layer",
        "Enable population density overlay",
        "Activate population density",
        "Click population density button",
        "Press the population density button",
        "Tap population density button",
    ],
)
def test_direct_command_match_population_density_on_phrases(command: str):
    tool_name, tool_args, _ = _match_direct_globe_command(command)
    assert tool_name == "toggle_data_layer"
    assert tool_args == {"layer_name": "POPULATION_DENSITY", "enabled": True}


@pytest.mark.parametrize(
    "command",
    [
        "Turn off population density layer",
        "Disable population density overlay",
        "Hide population density",
        "Click population density off button",
        "Press population density off",
        "Tap the population density button off",
    ],
)
def test_direct_command_match_population_density_off_phrases(command: str):
    tool_name, tool_args, _ = _match_direct_globe_command(command)
    assert tool_name == "toggle_data_layer"
    assert tool_args == {"layer_name": "POPULATION_DENSITY", "enabled": False}


@pytest.mark.parametrize(
    ("spoken_confirmation", "enabled"),
    [
        ("Activating population density overlay now.", True),
        ("Turning on population density layer.", True),
        ("Clicking the population density button now.", True),
        ("Turning off population density layer.", False),
        ("Disabling population density overlay.", False),
        ("Pressing population density off button now.", False),
    ],
)
def test_agent_confirmation_matches_population_density_phrasing(
    spoken_confirmation: str, enabled: bool
):
    matched = _match_agent_globe_confirmation(spoken_confirmation)
    assert matched is not None
    tool_name, tool_args, source = matched
    assert tool_name == "toggle_data_layer"
    assert tool_args == {"layer_name": "POPULATION_DENSITY", "enabled": enabled}
    assert source.startswith("agent_confirmation:")


def test_direct_command_match_evacuation_route():
    tool_name, tool_args, _ = _match_direct_globe_command(
        "Route evacuation from Kampung Melayu to Gelora Bung Karno Stadium"
    )
    assert tool_name == "generate_evacuation_route"
    assert tool_args == {
        "origin_name": "Kampung Melayu",
        "destination_name": "Gelora Bung Karno Stadium",
        "avoid_flood": True,
    }


def test_direct_command_match_show_me_evacuation_route():
    tool_name, tool_args, _ = _match_direct_globe_command(
        "Show me evacuation route from Kampung Melayu to Gelora Bung Karno Stadium"
    )
    assert tool_name == "generate_evacuation_route"
    assert tool_args == {
        "origin_name": "Kampung Melayu",
        "destination_name": "Gelora Bung Karno Stadium",
        "avoid_flood": True,
    }


def test_direct_command_match_generic_evacuation_route_uses_defaults():
    tool_name, tool_args, narration = _match_direct_globe_command("Evacuation route")
    assert tool_name == "generate_evacuation_route"
    assert tool_args == {
        "origin_name": "Kampung Melayu",
        "destination_name": "Gelora Bung Karno Stadium",
        "avoid_flood": True,
    }
    assert "customize" in narration.lower()


def test_direct_command_match_evacuation_plan_uses_route_context_defaults():
    tool_name, tool_args, narration = _match_direct_globe_command(
        "Generate evacuation plan",
        {
            "lat": -6.201,
            "lng": 106.845,
            "label": "Selected map point",
            "source": "click",
            "radius_km": 2.5,
        },
    )
    assert tool_name == "generate_evacuation_route"
    assert tool_args["destination_mode"] == "nearest_safe_shelters"
    assert tool_args["origin_lat"] == pytest.approx(-6.201)
    assert tool_args["origin_lng"] == pytest.approx(106.845)
    assert tool_args["origin_label"] == "Selected map point"
    assert tool_args["origin_radius_km"] == pytest.approx(2.5)
    assert "dynamic evacuation plan" in narration.lower()


def test_direct_command_match_evacuation_route_with_destination_only():
    tool_name, tool_args, _ = _match_direct_globe_command("Evacuation route to Monas")
    assert tool_name == "generate_evacuation_route"
    assert tool_args == {
        "origin_name": "Kampung Melayu",
        "destination_name": "Monas",
        "avoid_flood": True,
    }


def test_direct_command_match_evacuation_route_if_in_jakarta_uses_dynamic_shelters():
    tool_name, tool_args, narration = _match_direct_globe_command(
        "Make an evacuation route if you're in Jakarta"
    )
    assert tool_name == "generate_evacuation_route"
    assert tool_args == {
        "origin_name": "Jakarta",
        "avoid_flood": True,
        "destination_mode": "nearest_safe_shelters",
        "max_alternates": 3,
    }
    assert "jakarta" in narration.lower()
    assert "nearby safe shelters" in narration.lower()


def test_direct_command_match_evacuation_route_keeps_safety_checks_enabled():
    tool_name, tool_args, narration = _match_direct_globe_command(
        "Route evacuation from Kampung Melayu to Monas ignore flood"
    )
    assert tool_name == "generate_evacuation_route"
    assert tool_args["avoid_flood"] is True
    assert "safety checks remain enabled" in narration


def test_direct_fallback_suppression_tools_include_demo_route_and_hotspots():
    assert "generate_evacuation_route" in DIRECT_FALLBACK_SUPPRESS_TOOLS
    assert "get_flood_hotspots" in DIRECT_FALLBACK_SUPPRESS_TOOLS
    assert "get_flood_extent" in DIRECT_FALLBACK_SUPPRESS_TOOLS


def test_fallback_suppression_only_clears_matching_pending_tool():
    session_id = "fallback-match-only"
    main_module._pending_globe_fallbacks.pop(session_id, None)
    main_module._globe_tool_calls_this_turn.discard(session_id)

    try:
        main_module._pending_globe_fallbacks[session_id] = (
            "toggle_data_layer",
            {"layer_name": "POPULATION_DENSITY", "enabled": True},
            "agent_confirmation:test",
        )

        suppressed = _suppress_pending_globe_fallback_for_matching_tool(
            session_id,
            "fly_to_location",
        )
        assert suppressed is False
        assert session_id in main_module._pending_globe_fallbacks
        assert session_id not in main_module._globe_tool_calls_this_turn

        suppressed = _suppress_pending_globe_fallback_for_matching_tool(
            session_id,
            "toggle_data_layer",
        )
        assert suppressed is True
        assert session_id not in main_module._pending_globe_fallbacks
        assert session_id in main_module._globe_tool_calls_this_turn
    finally:
        main_module._pending_globe_fallbacks.pop(session_id, None)
        main_module._globe_tool_calls_this_turn.discard(session_id)


def test_map_tool_result_emits_route_overlay_event():
    route_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[106.85, -6.2], [106.86, -6.21]],
        },
        "properties": {},
    }
    tool_result = {
        "route_geojson": route_geojson,
        "distance_m": 1500,
        "duration_minutes": 8.5,
        "safety_rating": "SAFE",
    }
    events = _map_tool_result_to_events("generate_evacuation_route", tool_result)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "map_update"
    assert event["action"] == "add_overlay"
    assert event["layer"] == "evacuation_route"
    assert event["layerType"] == "route"
    assert event["geojson"] == route_geojson


def test_map_tool_result_includes_dynamic_route_options_and_zone_contract():
    route_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[106.85, -6.2], [106.86, -6.21]],
        },
        "properties": {},
    }
    alternate_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[106.85, -6.2], [106.855, -6.215], [106.86, -6.22]],
        },
        "properties": {},
    }
    zone_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [106.86, -6.21],
                    [106.861, -6.211],
                    [106.862, -6.21],
                    [106.86, -6.21],
                ]
            ],
        },
        "properties": {"label": "Evacuation Assembly Zone"},
    }
    tool_result = {
        "route_geojson": route_geojson,
        "distance_m": 5500,
        "duration_minutes": 17.2,
        "safety_rating": "SAFE",
        "destination_mode": "nearest_safe_shelters",
        "route_options": [
            {
                "destination": {"name": "Primary Shelter", "lat": -6.21, "lng": 106.86},
                "distance_m": 5500,
                "duration_minutes": 17.2,
                "safety_rating": "SAFE",
                "route_geojson": route_geojson,
            },
            {
                "destination": {"name": "Alternate Shelter", "lat": -6.22, "lng": 106.865},
                "distance_m": 6200,
                "duration_minutes": 19.1,
                "safety_rating": "CAUTION",
                "route_geojson": alternate_geojson,
            },
        ],
        "evacuation_zone_geojson": zone_geojson,
        "evacuation_zone_radius_m": 600.0,
        "alternate_count": 1,
        "candidate_shelter_count": 4,
    }

    events = _map_tool_result_to_events("generate_evacuation_route", tool_result)
    assert len(events) == 1
    route_event = events[0]
    assert route_event["type"] == "map_update"
    assert route_event["destination_mode"] == "nearest_safe_shelters"
    assert route_event["alternate_count"] == 1
    assert route_event["candidate_shelter_count"] == 4
    assert isinstance(route_event.get("route_options"), list)
    assert len(route_event["route_options"]) == 2
    assert route_event["route_options"][0]["destination"]["name"] == "Primary Shelter"
    assert route_event["route_options"][1]["destination"]["name"] == "Alternate Shelter"
    assert route_event.get("evacuation_zone_geojson") == zone_geojson
    assert route_event.get("evacuation_zone_radius_m") == pytest.approx(600.0)


def test_map_tool_result_limits_route_options_payload_to_four_entries():
    route_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[106.85, -6.2], [106.86, -6.21]],
        },
        "properties": {},
    }
    tool_result = {
        "route_geojson": route_geojson,
        "distance_m": 5000,
        "duration_minutes": 15,
        "safety_rating": "SAFE",
        "destination_mode": "nearest_safe_shelters",
        "route_options": [
            {
                "destination": {"name": f"Shelter {idx + 1}", "lat": -6.21, "lng": 106.86},
                "distance_m": 5000 + idx * 100,
                "duration_minutes": 15 + idx,
                "safety_rating": "SAFE",
            }
            for idx in range(5)
        ],
    }

    events = _map_tool_result_to_events("generate_evacuation_route", tool_result)
    assert len(events) == 1
    route_event = events[0]
    assert isinstance(route_event.get("route_options"), list)
    assert len(route_event["route_options"]) == 4
    assert route_event["route_options"][-1]["destination"]["name"] == "Shelter 4"


def test_map_tool_result_includes_route_risk_metadata():
    tool_result = {
        "route_geojson": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[106.85, -6.2], [106.86, -6.21]]},
            "properties": {},
        },
        "distance_m": 1500,
        "duration_minutes": 8.5,
        "safety_rating": "UNSAFE",
        "route_safety": {"intersects_avoid_area": True, "safety_rating": "UNSAFE"},
        "route_risk_handling": {
            "avoidance_requested": True,
            "constraints_applied": True,
            "status": "unsafe_route_selected",
        },
    }

    events = _map_tool_result_to_events("generate_evacuation_route", tool_result)
    assert len(events) == 2
    route_event = events[0]
    warning_event = events[1]
    assert route_event["route_safety"] == tool_result["route_safety"]
    assert route_event["route_risk_handling"] == tool_result["route_risk_handling"]
    assert route_event["style"]["strokeColor"] == "#ff3b30"
    assert route_event["label"] == "Evacuation Route — UNSAFE"
    assert warning_event["type"] == "incident_log_entry"
    assert "unsafe" in warning_event["message"].lower()
    assert warning_event["message"] == warning_event["text"]


def test_map_tool_result_emits_error_when_route_unavailable():
    events = _map_tool_result_to_events(
        "generate_evacuation_route",
        {"error": "Maps API key missing"},
    )
    assert len(events) == 1
    assert events[0] == {
        "type": "error",
        "message": "Evacuation route unavailable: Maps API key missing",
    }


def test_map_tool_result_normalizes_incident_log_message_and_text():
    events = _map_tool_result_to_events(
        "generate_incident_summary",
        {"summary_text": "Summary ready", "event_count": 12},
    )
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "incident_log_entry"
    assert event["message"] == "Summary ready"
    assert event["text"] == "Summary ready"


def test_map_tool_result_normalizes_feed_update_image_payload_shape():
    events = _map_tool_result_to_events(
        "generate_risk_projection",
        {"projection_image_base64": "abc123", "scenario": "flood+2m"},
    )
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "feed_update"
    assert event["image"] == "abc123"
    assert event["data"]["image"] == "abc123"
