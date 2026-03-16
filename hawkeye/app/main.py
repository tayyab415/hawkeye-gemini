"""
HawkEye ADK Bidi-Streaming Backend
Following the Google ADK bidi-demo pattern for WebSocket-based Live API communication.

Architecture:
- FastAPI WebSocket at /ws/{user_id}/{session_id}
- Upstream task: browser → LiveRequestQueue (audio/video/text)
- Downstream task: runner.run_live() → browser (audio + events)
- Proactive monitoring: Firestore water-level polling → agent alert injection
- ADK handles session resumption and context window compression
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Path as FastAPIPath, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event
from google.genai import types
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct as ProtoStruct

# Import the root agent from agent module
from app.hawkeye_agent.agent import root_agent
from app.hawkeye_agent.tools.globe_control import (
    add_measurement,
    add_threat_rings,
    capture_current_view,
    deploy_entity,
    fly_to_location,
    move_entity,
    set_atmosphere,
    set_camera_mode,
    toggle_data_layer,
)
from app.hawkeye_agent.tools.coordinator import generate_evacuation_route
from app.hawkeye_agent.tools.analyst import (
    get_earth_engine_service,
    get_flood_extent,
    get_flood_hotspots,
    get_last_flood_extent_full,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent


def _load_runtime_environment() -> None:
    """Load .env files so local runs pick up required API keys."""
    for env_path in (WORKSPACE_ROOT / ".env", PROJECT_ROOT / ".env"):
        if env_path.exists():
            loaded = load_dotenv(dotenv_path=env_path, override=False)
            logger.info("[ENV] %s from %s", "Loaded" if loaded else "Read", env_path)


LIVE_MODEL_UNAVAILABLE_HINT = (
    "Set GOOGLE_API_KEY or GCP_API_KEY in .env and restart backend "
    "to enable voice conversation."
)
LIVE_MODEL_UNAVAILABLE_GENERAL_MESSAGE = (
    f"Live model unavailable. {LIVE_MODEL_UNAVAILABLE_HINT}"
)
LIVE_MODEL_UNAVAILABLE_AUDIO_MESSAGE = (
    "Voice input unavailable because live model is disabled. "
    f"{LIVE_MODEL_UNAVAILABLE_HINT}"
)


def _ensure_google_api_key_from_fallback(*, log_context: str) -> bool:
    """Ensure GOOGLE_API_KEY exists, falling back to GCP_API_KEY when available."""
    if os.getenv("GOOGLE_API_KEY"):
        return True

    gcp_api_key = os.getenv("GCP_API_KEY")
    if not gcp_api_key:
        return False

    os.environ["GOOGLE_API_KEY"] = gcp_api_key
    logger.info("Configured GOOGLE_API_KEY from GCP_API_KEY (%s)", log_context)
    return True


def _build_live_model_unavailable_error_payload(
    *, source: str = "general"
) -> dict[str, str]:
    normalized_source = source.strip().lower() if isinstance(source, str) else "general"
    if normalized_source == "audio":
        message = LIVE_MODEL_UNAVAILABLE_AUDIO_MESSAGE
    else:
        message = LIVE_MODEL_UNAVAILABLE_GENERAL_MESSAGE
    return {
        "type": "error",
        "message": message,
    }


async def _emit_live_model_unavailable_error_once(
    *,
    websocket: WebSocket,
    emitted_channels: set[str],
    channel: str,
    metrics: _WebSocketSessionMetrics | None = None,
) -> bool:
    normalized_channel = (
        channel.strip().lower() if isinstance(channel, str) else "general"
    ) or "general"
    if normalized_channel in emitted_channels:
        return False

    payload = _build_live_model_unavailable_error_payload(source=normalized_channel)
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as exc:
        logger.debug(
            "[WS] Failed to emit live-model-unavailable notice (%s): %s",
            normalized_channel,
            exc,
        )
        return False

    emitted_channels.add(normalized_channel)
    if metrics is not None:
        metrics.mark_downstream_payload("text")
        metrics.mark_downstream_event(f"live_model_unavailable_{normalized_channel}")
    return True


async def _handle_upstream_audio_frame(
    *,
    audio_bytes: bytes,
    live_model_enabled: bool,
    live_request_queue: LiveRequestQueue,
    websocket: WebSocket,
    live_model_notice_channels: set[str],
    metrics: _WebSocketSessionMetrics | None = None,
) -> None:
    """Route inbound PCM audio to Gemini Live, or drop with a one-time explicit error."""
    if live_model_enabled:
        live_request_queue.send_realtime(
            types.Blob(
                mime_type="audio/pcm;rate=16000",
                data=audio_bytes,
            )
        )
        if metrics is not None:
            metrics.mark_upstream_activity("audio_frame")
        return

    if metrics is not None:
        metrics.record_upstream_drop("audio_frame_live_model_disabled")

    await _emit_live_model_unavailable_error_once(
        websocket=websocket,
        emitted_channels=live_model_notice_channels,
        channel="audio",
        metrics=metrics,
    )


# Global services (initialized in lifespan)
session_service: InMemorySessionService | None = None
artifact_service: InMemoryArtifactService | None = None
runner: Runner | None = None

# ─────────────────────────────────────────────────────────────────────
# In-memory cache for large GeoJSON payloads served via REST
# ─────────────────────────────────────────────────────────────────────
_geojson_cache: dict[str, Any] = {}
MAX_INLINE_PAYLOAD_BYTES = 50_000  # 50 KB threshold
MAX_LIVE_IMAGE_BYTES = 7 * 1024 * 1024
LIVE_CONTEXT_TRIGGER_TOKENS = 50_000
LIVE_CONTEXT_TARGET_TOKENS = 25_000
EE_TILE_MAX_ZOOM = 18
EE_TILE_LAYER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
EE_TILE_CACHE_CONTROL = "public, max-age=3600, immutable"
EE_TILE_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2ioAAAAASUVORK5CYII="
)
EE_TILE_PLACEHOLDER_ETAG = f'"{hashlib.sha256(EE_TILE_PLACEHOLDER_PNG).hexdigest()}"'
_output_transcription_buffers: dict[str, str] = {}
_pending_globe_fallbacks: dict[str, tuple[str, dict[str, Any], str]] = {}
_usage_telemetry_snapshots: dict[str, tuple[Any, ...]] = {}
_analysis_ack_last_sent_at: dict[str, float] = {}
_session_route_contexts: dict[str, dict[str, Any]] = {}

LIVE_SESSION_STRATEGY_ENV_VAR = "HAWKEYE_LIVE_SESSION_STRATEGY"
LIVE_SESSION_RETRY_ON_STALE_ENV_VAR = "HAWKEYE_LIVE_SESSION_RETRY_ON_STALE"
LIVE_SESSION_STRATEGY_RESUME_PREFER = "resume_prefer"
LIVE_SESSION_STRATEGY_FRESH = "fresh"
_STALE_LIVE_SESSION_ERROR_MARKERS = (
    "resumption handle",
    "session resumption",
    "resume token",
    "session not found",
    "unknown session",
    "session expired",
)

# ── Connection singleton per session ──────────────────────────────────
# Prevents concurrent runner.run_live() calls for the same session_id,
# which causes 1007 "invalid argument" errors from the Gemini Live API.
_active_connections: dict[str, asyncio.Event] = {}
_active_connections_lock = asyncio.Lock()

# Track proactive alerts at session level (survives WS reconnections)
_proactive_alerts_sent: set[str] = set()
_globe_tool_calls_this_turn: set[str] = set()

GLOBE_CONTROL_TOOLS = {
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
DIRECT_FALLBACK_SUPPRESS_TOOLS = GLOBE_CONTROL_TOOLS | {
    "generate_evacuation_route",
    "get_flood_hotspots",
    "get_flood_extent",
}

LONG_RUNNING_LIVE_TOOLS = {
    "analyze_frame",
    "compare_frames",
    "query_historical_floods",
    "get_flood_hotspots",
    "get_infrastructure_vulnerability",
    "get_flood_cascade_risk",
    "get_flood_extent",
    "get_infrastructure_at_risk",
    "get_population_at_risk",
    "compute_cascade",
    "evaluate_route_safety",
    "generate_risk_projection",
    "generate_evacuation_route",
    "send_emergency_alert",
    "generate_incident_summary",
}
FAST_ANALYSIS_ACK_COOLDOWN_SECONDS = 2.0
FAST_ANALYSIS_ACK_MIN_WORDS = 4
FAST_ANALYSIS_ACK_VERB_MARKERS = (
    "analy",
    "assess",
    "evaluate",
    "compare",
    "summar",
    "forecast",
    "project",
    "compute",
    "simulate",
    "estimate",
    "investigate",
)
FAST_ANALYSIS_ACK_DOMAIN_MARKERS = (
    "flood",
    "water",
    "cascade",
    "risk",
    "hotspot",
    "infrastructure",
    "population",
    "evacuation",
    "route",
    "incident",
    "trend",
    "historical",
    "vulnerability",
)
NONBLOCKING_TOOL_EVENTS_ENV_VAR = "HAWKEYE_ENABLE_NONBLOCKING_TOOL_EVENTS"
UPSTREAM_AUDIO_DUMP_ENV_VAR = "HAWKEYE_DUMP_UPSTREAM_AUDIO"
UPSTREAM_AUDIO_DUMP_PATH_ENV_VAR = "HAWKEYE_DUMP_UPSTREAM_AUDIO_PATH"
UPSTREAM_AUDIO_DUMP_MAX_BYTES_ENV_VAR = "HAWKEYE_DUMP_UPSTREAM_AUDIO_MAX_BYTES"
UPSTREAM_AUDIO_INFO_INTERVAL_ENV_VAR = "HAWKEYE_UPSTREAM_AUDIO_INFO_INTERVAL"
PROACTIVE_WATER_LEVEL_TIMEOUT_ENV_VAR = "HAWKEYE_PROACTIVE_WATER_LEVEL_TIMEOUT_S"
_pending_long_running_tools: dict[str, dict[str, list[dict[str, Any]]]] = {}

_DIRECT_GLOBE_TOOL_FUNCS = {
    "fly_to_location": fly_to_location,
    "set_camera_mode": set_camera_mode,
    "toggle_data_layer": toggle_data_layer,
    "deploy_entity": deploy_entity,
    "move_entity": move_entity,
    "add_measurement": add_measurement,
    "set_atmosphere": set_atmosphere,
    "capture_current_view": capture_current_view,
    "add_threat_rings": add_threat_rings,
    "generate_evacuation_route": generate_evacuation_route,
    "get_flood_hotspots": get_flood_hotspots,
    "get_flood_extent": get_flood_extent,
}
DEFAULT_EVACUATION_ROUTE_ORIGIN = "Kampung Melayu"
DEFAULT_EVACUATION_ROUTE_DESTINATION = "Gelora Bung Karno Stadium"
_CLIENT_TURN_CONTROL_MESSAGE_TYPES = frozenset(
    {
        "turn_start",
        "turn_end",
        "turn_complete",
        "response.cancel",
        "response.create",
        "input_audio_buffer.commit",
        "input_audio_buffer.clear",
    }
)


class _WebSocketSessionMetrics:
    """Lightweight, per-session diagnostics for websocket reliability and latency."""

    def __init__(self, *, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.connected_at_ms = int(time.time() * 1000)
        self.connected_monotonic = time.monotonic()
        self.outcome = "in_progress"

        self.counters: Counter[str] = Counter()
        self.drop_reasons: Counter[str] = Counter()
        self.invalid_reasons: Counter[str] = Counter()
        self.lifecycle_outcomes: Counter[str] = Counter()
        self.duration_stats: dict[str, dict[str, int]] = {}
        self.tool_chain_by_tool: dict[str, dict[str, int]] = {}
        self.lifecycle_status: dict[str, Any] = {}

        self.first_upstream_monotonic: float | None = None
        self.first_upstream_kind: str | None = None
        self.first_downstream_event_monotonic: float | None = None
        self.first_downstream_event_source: str | None = None
        self.first_downstream_payload_monotonic: float | None = None
        self.first_downstream_payload_kind: str | None = None
        self.first_downstream_audio_monotonic: float | None = None
        self.first_downstream_text_monotonic: float | None = None
        self._summary_emitted = False

    def increment(self, counter_name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        self.counters[counter_name] += amount

    def observe_duration_ms(self, duration_name: str, duration_ms: float) -> None:
        clamped_ms = max(0, int(duration_ms))
        stats = self.duration_stats.setdefault(
            duration_name,
            {"count": 0, "total_ms": 0, "min_ms": clamped_ms, "max_ms": clamped_ms},
        )
        stats["count"] += 1
        stats["total_ms"] += clamped_ms
        if clamped_ms < stats["min_ms"]:
            stats["min_ms"] = clamped_ms
        if clamped_ms > stats["max_ms"]:
            stats["max_ms"] = clamped_ms

    def record_upstream_drop(self, reason: str) -> None:
        normalized_reason = (
            reason.strip().lower() if isinstance(reason, str) else "unknown"
        )
        self.increment("upstream_dropped_messages")
        self.drop_reasons[normalized_reason or "unknown"] += 1

    def record_upstream_invalid(self, reason: str, *, dropped: bool = True) -> None:
        normalized_reason = (
            reason.strip().lower() if isinstance(reason, str) else "unknown"
        )
        self.increment("upstream_invalid_messages")
        self.invalid_reasons[normalized_reason or "unknown"] += 1
        if dropped:
            self.record_upstream_drop(normalized_reason or "unknown")

    def mark_upstream_activity(self, kind: str) -> None:
        normalized_kind = kind.strip().lower() if isinstance(kind, str) else "unknown"
        self.increment("upstream_activity_messages")
        if self.first_upstream_monotonic is None:
            self.first_upstream_monotonic = time.monotonic()
            self.first_upstream_kind = normalized_kind or "unknown"

    def mark_downstream_event(self, source: str) -> None:
        normalized_source = (
            source.strip().lower() if isinstance(source, str) else "unknown"
        )
        self.increment("downstream_events_seen")
        if self.first_downstream_event_monotonic is None:
            self.first_downstream_event_monotonic = time.monotonic()
            self.first_downstream_event_source = normalized_source or "unknown"

    def mark_downstream_payload(self, payload_kind: str) -> None:
        normalized_kind = (
            payload_kind.strip().lower() if isinstance(payload_kind, str) else "unknown"
        )
        self.increment("downstream_payload_messages")
        now = time.monotonic()
        if self.first_downstream_payload_monotonic is None:
            self.first_downstream_payload_monotonic = now
            self.first_downstream_payload_kind = normalized_kind or "unknown"

        if normalized_kind == "audio":
            self.increment("downstream_audio_chunks")
            if self.first_downstream_audio_monotonic is None:
                self.first_downstream_audio_monotonic = now
        else:
            self.increment("downstream_text_messages")
            if self.first_downstream_text_monotonic is None:
                self.first_downstream_text_monotonic = now

    def record_lifecycle_status(self, lifecycle_status: dict[str, Any]) -> None:
        self.lifecycle_status = (
            dict(lifecycle_status) if isinstance(lifecycle_status, dict) else {}
        )
        decision = self.lifecycle_status.get("decision")
        if isinstance(decision, str) and decision.strip():
            self.lifecycle_outcomes[decision.strip().lower()] += 1

    def record_tool_chain_duration(
        self,
        *,
        tool_name: str,
        duration_ms: float,
        status: str,
    ) -> None:
        normalized_tool = (
            tool_name.strip().lower() if isinstance(tool_name, str) else "unknown"
        ) or "unknown"
        normalized_status = (
            status.strip().lower() if isinstance(status, str) else "unknown"
        ) or "unknown"

        self.observe_duration_ms("tool_chain_duration_ms", duration_ms)
        self.increment("tool_chain_events")
        if normalized_status == "error":
            self.increment("tool_chain_errors")

        clamped_ms = max(0, int(duration_ms))
        tool_stats = self.tool_chain_by_tool.setdefault(
            normalized_tool,
            {"count": 0, "total_ms": 0, "max_ms": 0, "error_count": 0},
        )
        tool_stats["count"] += 1
        tool_stats["total_ms"] += clamped_ms
        if clamped_ms > tool_stats["max_ms"]:
            tool_stats["max_ms"] = clamped_ms
        if normalized_status == "error":
            tool_stats["error_count"] += 1

    def set_outcome(self, outcome: str, *, override: bool = False) -> None:
        normalized_outcome = (
            outcome.strip().lower() if isinstance(outcome, str) else "unknown"
        ) or "unknown"
        if override or self.outcome == "in_progress":
            self.outcome = normalized_outcome

    @staticmethod
    def _diff_ms(start: float | None, end: float | None) -> int | None:
        if start is None or end is None or end < start:
            return None
        return max(0, int((end - start) * 1000))

    def _serialize_duration_stats(self) -> dict[str, dict[str, int]]:
        serialized: dict[str, dict[str, int]] = {}
        for name, stats in self.duration_stats.items():
            count = stats.get("count", 0)
            total_ms = stats.get("total_ms", 0)
            serialized[name] = {
                "count": count,
                "total_ms": total_ms,
                "avg_ms": int(total_ms / count) if count else 0,
                "min_ms": stats.get("min_ms", 0),
                "max_ms": stats.get("max_ms", 0),
            }
        return serialized

    def _serialize_tool_stats(self) -> dict[str, dict[str, int]]:
        serialized: dict[str, dict[str, int]] = {}
        for tool_name, stats in self.tool_chain_by_tool.items():
            count = stats.get("count", 0)
            total_ms = stats.get("total_ms", 0)
            serialized[tool_name] = {
                "count": count,
                "total_ms": total_ms,
                "avg_ms": int(total_ms / count) if count else 0,
                "max_ms": stats.get("max_ms", 0),
                "error_count": stats.get("error_count", 0),
            }
        return serialized

    def _build_summary(self) -> dict[str, Any]:
        session_duration_ms = max(
            0,
            int((time.monotonic() - self.connected_monotonic) * 1000),
        )
        return {
            "metric": "websocket_session_diagnostics",
            "user_id": self.user_id,
            "session_id": self.session_id,
            "connected_at_ms": self.connected_at_ms,
            "session_duration_ms": session_duration_ms,
            "outcome": self.outcome,
            "session_lifecycle": {
                "strategy": self.lifecycle_status.get("strategy"),
                "decision": self.lifecycle_status.get("decision"),
                "reason": self.lifecycle_status.get("reason"),
                "reused_existing_session": self.lifecycle_status.get(
                    "reused_existing_session"
                ),
                "fallback_to_fresh": self.lifecycle_status.get("fallback_to_fresh"),
                "recovery_attempted": self.lifecycle_status.get("recovery_attempted"),
            },
            "counters": dict(self.counters),
            "drop_reasons": dict(self.drop_reasons),
            "invalid_reasons": dict(self.invalid_reasons),
            "lifecycle_outcomes": dict(self.lifecycle_outcomes),
            "durations_ms": self._serialize_duration_stats(),
            "tool_chain_by_tool": self._serialize_tool_stats(),
            "latency_proxies_ms": {
                "first_upstream_from_connect": self._diff_ms(
                    self.connected_monotonic, self.first_upstream_monotonic
                ),
                "first_downstream_event_from_connect": self._diff_ms(
                    self.connected_monotonic, self.first_downstream_event_monotonic
                ),
                "first_downstream_payload_from_connect": self._diff_ms(
                    self.connected_monotonic, self.first_downstream_payload_monotonic
                ),
                "first_response_after_first_upstream": self._diff_ms(
                    self.first_upstream_monotonic, self.first_downstream_event_monotonic
                ),
                "first_payload_after_first_upstream": self._diff_ms(
                    self.first_upstream_monotonic,
                    self.first_downstream_payload_monotonic,
                ),
                "first_audio_after_first_upstream": self._diff_ms(
                    self.first_upstream_monotonic, self.first_downstream_audio_monotonic
                ),
                "first_text_after_first_upstream": self._diff_ms(
                    self.first_upstream_monotonic, self.first_downstream_text_monotonic
                ),
            },
            "first_markers": {
                "upstream_kind": self.first_upstream_kind,
                "downstream_event_source": self.first_downstream_event_source,
                "downstream_payload_kind": self.first_downstream_payload_kind,
            },
        }

    def emit_summary(self) -> None:
        if self._summary_emitted:
            return
        self._summary_emitted = True
        try:
            summary = self._build_summary()
            logger.info(
                "[WS METRICS] %s",
                json.dumps(summary, default=str, sort_keys=True, separators=(",", ":")),
            )
        except Exception as exc:
            logger.warning("[WS METRICS] Failed to emit summary: %s", exc)


def _normalize_client_message_type(message_data: dict[str, Any]) -> str | None:
    raw_type = message_data.get("type")
    if not isinstance(raw_type, str):
        return None
    normalized = raw_type.strip().lower()
    return normalized if normalized else None


def _build_heartbeat_pong_payload(
    message_data: dict[str, Any],
) -> dict[str, Any] | None:
    msg_type = _normalize_client_message_type(message_data)
    heartbeat_signal = None
    if msg_type == "heartbeat":
        raw_signal = message_data.get("event", message_data.get("signal"))
        if isinstance(raw_signal, str):
            heartbeat_signal = raw_signal.strip().lower()

    if msg_type != "ping" and heartbeat_signal != "ping":
        return None

    payload: dict[str, Any] = {
        "type": "pong",
        "timestamp": int(time.time() * 1000),
    }
    source_timestamp = message_data.get("timestamp")
    if isinstance(source_timestamp, (int, float)) and not isinstance(
        source_timestamp, bool
    ):
        payload["echo_timestamp"] = int(source_timestamp)
    return payload


def _is_heartbeat_ack_message(message_data: dict[str, Any]) -> bool:
    msg_type = _normalize_client_message_type(message_data)
    if msg_type == "pong":
        return True
    if msg_type != "heartbeat":
        return False

    raw_signal = message_data.get("event", message_data.get("signal"))
    if not isinstance(raw_signal, str):
        return False
    return raw_signal.strip().lower() == "pong"


def _merge_output_transcription_chunk(buffered_text: str, incoming_text: str) -> str:
    if not incoming_text:
        return buffered_text
    if not buffered_text:
        return incoming_text

    if incoming_text == buffered_text:
        return buffered_text
    if incoming_text.startswith(buffered_text):
        return incoming_text
    if buffered_text.startswith(incoming_text):
        return buffered_text
    if incoming_text.endswith(buffered_text):
        return incoming_text
    if buffered_text.endswith(incoming_text):
        return buffered_text

    max_overlap = min(len(buffered_text), len(incoming_text))
    for overlap_length in range(max_overlap, 0, -1):
        if buffered_text.endswith(incoming_text[:overlap_length]):
            return buffered_text + incoming_text[overlap_length:]

    return buffered_text + incoming_text


def _append_output_transcription(session_id: str, text: str) -> None:
    if not text:
        return
    existing_text = _output_transcription_buffers.get(session_id, "")
    _output_transcription_buffers[session_id] = _merge_output_transcription_chunk(
        existing_text, text
    )


def _clear_output_transcription(session_id: str) -> None:
    _output_transcription_buffers.pop(session_id, None)


def _default_live_session_state() -> dict[str, Any]:
    return {
        "operational_mode": "ALERT",
        "water_level_m": 0.0,
        "active_threats": [],
    }


def _normalize_live_session_strategy(raw_value: str | None) -> str:
    if raw_value is None:
        return LIVE_SESSION_STRATEGY_RESUME_PREFER

    normalized = raw_value.strip().lower()
    aliases = {
        "auto": LIVE_SESSION_STRATEGY_RESUME_PREFER,
        "default": LIVE_SESSION_STRATEGY_RESUME_PREFER,
        "resume": LIVE_SESSION_STRATEGY_RESUME_PREFER,
        "reuse": LIVE_SESSION_STRATEGY_RESUME_PREFER,
        LIVE_SESSION_STRATEGY_RESUME_PREFER: LIVE_SESSION_STRATEGY_RESUME_PREFER,
        "fresh": LIVE_SESSION_STRATEGY_FRESH,
        "reset": LIVE_SESSION_STRATEGY_FRESH,
        "new": LIVE_SESSION_STRATEGY_FRESH,
        LIVE_SESSION_STRATEGY_FRESH: LIVE_SESSION_STRATEGY_FRESH,
    }
    resolved = aliases.get(normalized)
    if resolved is not None:
        return resolved

    logger.warning(
        "[SESSION LIFECYCLE] Unknown %s value '%s'. Using default '%s'.",
        LIVE_SESSION_STRATEGY_ENV_VAR,
        raw_value,
        LIVE_SESSION_STRATEGY_RESUME_PREFER,
    )
    return LIVE_SESSION_STRATEGY_RESUME_PREFER


def _read_live_session_strategy() -> str:
    return _normalize_live_session_strategy(os.getenv(LIVE_SESSION_STRATEGY_ENV_VAR))


def _should_retry_stale_live_session() -> bool:
    raw_value = os.getenv(LIVE_SESSION_RETRY_ON_STALE_ENV_VAR, "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _build_session_lifecycle_status(
    *,
    strategy: str,
    decision: str,
    reason: str,
    reused_existing_session: bool,
    previous_session_found: bool,
    fallback_to_fresh: bool,
    recovery_attempted: bool,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "decision": decision,
        "reason": reason,
        "reused_existing_session": reused_existing_session,
        "previous_session_found": previous_session_found,
        "fallback_to_fresh": fallback_to_fresh,
        "recovery_attempted": recovery_attempted,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _ensure_live_session_state_shape(session: Any) -> dict[str, Any]:
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(session, "state", state)

    defaults = _default_live_session_state()
    if not isinstance(state.get("operational_mode"), str):
        state["operational_mode"] = defaults["operational_mode"]
    if not isinstance(state.get("water_level_m"), (int, float)):
        state["water_level_m"] = defaults["water_level_m"]
    if not isinstance(state.get("active_threats"), list):
        state["active_threats"] = list(defaults["active_threats"])
    return state


def _attach_session_lifecycle_status(
    session: Any, lifecycle_status: dict[str, Any]
) -> dict[str, Any]:
    state = _ensure_live_session_state_shape(session)
    state["session_lifecycle"] = lifecycle_status
    return state


def _log_session_lifecycle(
    *, user_id: str, session_id: str, lifecycle_status: dict[str, Any]
) -> None:
    logger.info(
        (
            "[SESSION LIFECYCLE] user=%s session=%s strategy=%s decision=%s "
            "reason=%s reused=%s fallback_to_fresh=%s recovery_attempted=%s"
        ),
        user_id,
        session_id,
        lifecycle_status.get("strategy"),
        lifecycle_status.get("decision"),
        lifecycle_status.get("reason"),
        lifecycle_status.get("reused_existing_session"),
        lifecycle_status.get("fallback_to_fresh"),
        lifecycle_status.get("recovery_attempted"),
    )


async def _create_fresh_live_session(
    *,
    service: InMemorySessionService,
    user_id: str,
    session_id: str,
    strategy: str,
    reason: str,
    previous_session_found: bool,
    fallback_to_fresh: bool,
    recovery_attempted: bool,
    delete_existing_session: bool = True,
) -> tuple[Any, dict[str, Any]]:
    if previous_session_found and delete_existing_session:
        await service.delete_session(
            app_name="hawkeye",
            user_id=user_id,
            session_id=session_id,
        )

    session = await service.create_session(
        app_name="hawkeye",
        user_id=user_id,
        session_id=session_id,
        state=_default_live_session_state(),
    )
    lifecycle_status = _build_session_lifecycle_status(
        strategy=strategy,
        decision="created_fresh",
        reason=reason,
        reused_existing_session=False,
        previous_session_found=previous_session_found,
        fallback_to_fresh=fallback_to_fresh,
        recovery_attempted=recovery_attempted,
    )
    _attach_session_lifecycle_status(session, lifecycle_status)
    return session, lifecycle_status


async def _prepare_live_session_with_service(
    *,
    service: InMemorySessionService,
    user_id: str,
    session_id: str,
    strategy: str | None = None,
    recovery_attempted: bool = False,
) -> tuple[Any, dict[str, Any]]:
    resolved_strategy = (
        _normalize_live_session_strategy(strategy)
        if strategy
        else _read_live_session_strategy()
    )

    try:
        existing_session = await service.get_session(
            app_name="hawkeye",
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as exc:
        logger.warning(
            "[SESSION LIFECYCLE] Failed to load existing session %s: %s. Falling back to fresh session.",
            session_id,
            exc,
        )
        return await _create_fresh_live_session(
            service=service,
            user_id=user_id,
            session_id=session_id,
            strategy=resolved_strategy,
            reason="resume_lookup_failed",
            previous_session_found=False,
            fallback_to_fresh=True,
            recovery_attempted=recovery_attempted,
            delete_existing_session=False,
        )

    previous_session_found = existing_session is not None
    if resolved_strategy == LIVE_SESSION_STRATEGY_FRESH:
        return await _create_fresh_live_session(
            service=service,
            user_id=user_id,
            session_id=session_id,
            strategy=resolved_strategy,
            reason="strategy_forced_fresh",
            previous_session_found=previous_session_found,
            fallback_to_fresh=previous_session_found,
            recovery_attempted=recovery_attempted,
            delete_existing_session=previous_session_found,
        )

    if existing_session is None:
        return await _create_fresh_live_session(
            service=service,
            user_id=user_id,
            session_id=session_id,
            strategy=resolved_strategy,
            reason="no_existing_session",
            previous_session_found=False,
            fallback_to_fresh=False,
            recovery_attempted=recovery_attempted,
            delete_existing_session=False,
        )

    try:
        lifecycle_status = _build_session_lifecycle_status(
            strategy=resolved_strategy,
            decision="reused_existing",
            reason="existing_session_reused",
            reused_existing_session=True,
            previous_session_found=True,
            fallback_to_fresh=False,
            recovery_attempted=recovery_attempted,
        )
        _attach_session_lifecycle_status(existing_session, lifecycle_status)
        return existing_session, lifecycle_status
    except Exception as exc:
        logger.warning(
            "[SESSION LIFECYCLE] Failed to reuse existing session %s: %s. Falling back to fresh session.",
            session_id,
            exc,
        )
        return await _create_fresh_live_session(
            service=service,
            user_id=user_id,
            session_id=session_id,
            strategy=resolved_strategy,
            reason="reuse_preparation_failed",
            previous_session_found=True,
            fallback_to_fresh=True,
            recovery_attempted=recovery_attempted,
            delete_existing_session=True,
        )


async def _prepare_live_session(
    *,
    user_id: str,
    session_id: str,
    strategy: str | None = None,
    recovery_attempted: bool = False,
) -> tuple[Any, dict[str, Any]]:
    if session_service is None:
        raise RuntimeError("Session service is not initialized")

    return await _prepare_live_session_with_service(
        service=session_service,
        user_id=user_id,
        session_id=session_id,
        strategy=strategy,
        recovery_attempted=recovery_attempted,
    )


def _is_stale_live_session_error(error: Exception) -> bool:
    message = str(error).lower()
    if "1007" in message and "invalid argument" in message:
        return True
    return any(marker in message for marker in _STALE_LIVE_SESSION_ERROR_MARKERS)


async def _emit_session_lifecycle_status(
    websocket: WebSocket,
    session: Any,
    lifecycle_status: dict[str, Any],
) -> None:
    state = getattr(session, "state", {})
    if not isinstance(state, dict):
        state = {}

    payload = {
        "type": "status_update",
        "mode": state.get("operational_mode"),
        "water_level_m": state.get("water_level_m"),
        "session_lifecycle": lifecycle_status,
    }
    try:
        await websocket.send_text(json.dumps(payload, default=str))
    except Exception as exc:
        logger.debug("[SESSION LIFECYCLE] Failed to emit status_update: %s", exc)


async def _emit_buffered_output_transcription(
    websocket: WebSocket,
    session_id: str,
    metrics: _WebSocketSessionMetrics | None = None,
) -> bool:
    buffered_text = _output_transcription_buffers.get(session_id, "")
    if not buffered_text:
        _clear_output_transcription(session_id)
        return False

    transcript_text = buffered_text.strip()
    _clear_output_transcription(session_id)
    if not transcript_text:
        return False

    await websocket.send_text(
        json.dumps(
            {
                "type": "transcript",
                "speaker": "agent",
                "text": transcript_text,
            }
        )
    )
    if metrics is not None:
        metrics.mark_downstream_payload("text")
    return True


def _clean_location(raw_value: str) -> str:
    return raw_value.strip().strip("?.!,")


def _normalize_route_context_update(message_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message_data, dict):
        return None

    lat_raw = message_data.get("lat")
    lng_raw = message_data.get("lng")
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None

    source_raw = message_data.get("source")
    source = (
        source_raw.strip().lower()
        if isinstance(source_raw, str) and source_raw.strip()
        else "unknown"
    )

    context: dict[str, Any] = {
        "lat": lat,
        "lng": lng,
        "source": source,
        "timestamp": int(time.time() * 1000),
    }

    label_raw = message_data.get("label")
    if isinstance(label_raw, str) and label_raw.strip():
        context["label"] = label_raw.strip()[:120]

    radius_raw = message_data.get("radius_km")
    try:
        if radius_raw is not None:
            radius_km = float(radius_raw)
            if radius_km > 0:
                context["radius_km"] = round(radius_km, 2)
    except (TypeError, ValueError):
        pass

    return context


def _route_origin_args_from_context(
    route_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(route_context, dict):
        return None

    lat_raw = route_context.get("lat")
    lng_raw = route_context.get("lng")
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None

    context_args: dict[str, Any] = {
        "origin_lat": lat,
        "origin_lng": lng,
    }
    label_raw = route_context.get("label")
    if isinstance(label_raw, str) and label_raw.strip():
        context_args["origin_label"] = label_raw.strip()[:120]

    radius_raw = route_context.get("radius_km")
    try:
        if radius_raw is not None:
            radius_km = float(radius_raw)
            if radius_km > 0:
                context_args["origin_radius_km"] = round(radius_km, 2)
    except (TypeError, ValueError):
        pass

    return context_args


def _is_safe_ee_tile_layer_id(layer_id: str) -> bool:
    return bool(EE_TILE_LAYER_ID_PATTERN.fullmatch(layer_id))


def _is_analytics_request(text_lower: str) -> bool:
    """
    Identify analytical/data questions that should go through Analyst tools,
    not direct globe-navigation shortcuts.
    """
    analytics_patterns = (
        "flood extent",
        "historical pattern",
        "historical flood",
        "water rises",
        "water rise",
        "cascade",
        "at risk",
        "incident summary",
        "nearest shelter",
        "route evacuation",
        "evacuation route",
    )
    return any(pattern in text_lower for pattern in analytics_patterns)


def _is_likely_heavy_analysis_request(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False

    if _is_analytics_request(normalized):
        return True

    words = re.findall(r"[a-z0-9]+", normalized)
    if len(words) < FAST_ANALYSIS_ACK_MIN_WORDS:
        return False

    has_analysis_verb = any(
        marker in normalized for marker in FAST_ANALYSIS_ACK_VERB_MARKERS
    )
    has_domain_term = any(
        marker in normalized for marker in FAST_ANALYSIS_ACK_DOMAIN_MARKERS
    )
    return has_analysis_verb and has_domain_term


async def _emit_fast_analysis_ack(
    websocket: WebSocket,
    session_id: str,
    metrics: _WebSocketSessionMetrics | None = None,
) -> bool:
    now = time.monotonic()
    last_sent_at = _analysis_ack_last_sent_at.get(session_id)
    if (
        isinstance(last_sent_at, (int, float))
        and (now - last_sent_at) < FAST_ANALYSIS_ACK_COOLDOWN_SECONDS
    ):
        return False

    timestamp = int(time.time() * 1000)
    ack_message = "Acknowledged. Running analytical checks now."
    ack_events = (
        {
            "type": "incident_log_entry",
            "severity": "low",
            "category": "analysis_ack",
            "message": ack_message,
            "text": ack_message,
            "timestamp": timestamp,
        },
        {
            "type": "transcript",
            "speaker": "system",
            "text": "Understood. I’m analyzing this now and will report shortly.",
            "timestamp": timestamp,
        },
    )

    for event in ack_events:
        await websocket.send_text(json.dumps(event))
        if metrics is not None:
            metrics.mark_downstream_payload("text")

    _analysis_ack_last_sent_at[session_id] = now
    if metrics is not None:
        metrics.mark_downstream_event("analysis_fast_ack")
    return True


def _matches_population_density_layer_toggle(lower_text: str, *, enabled: bool) -> bool:
    population_pattern = r"\b(population(?:\s+density)?|density)\b"
    click_pattern = r"\b(click|clicking|press|pressing|tap|tapping)\b"
    if enabled:
        patterns = (
            rf"\b(show|display|turn on|turning on|enable|enabling|activate|activating)\b.*{population_pattern}",
            rf"{click_pattern}.*{population_pattern}.*\b(button|on|enable|enabling|activate|activating)\b",
            rf"{click_pattern}.*\b(on|enable|enabling|activate|activating)\b.*{population_pattern}",
        )
    else:
        patterns = (
            rf"\b(hide|hiding|turn off|turning off|disable|disabling)\b.*{population_pattern}",
            rf"{click_pattern}.*{population_pattern}.*\b(off|disable|disabling|hide|hiding)\b",
            rf"{click_pattern}.*\b(off|disable|disabling|hide|hiding)\b.*{population_pattern}",
        )
    return any(re.search(pattern, lower_text) for pattern in patterns)


def _extract_evacuation_origin_hint(normalized_text: str) -> str:
    if not isinstance(normalized_text, str):
        return ""

    hint_patterns = (
        r"\bif\s+(?:you|we|i)(?:'re| are)\s+in\s+(.+?)(?:(?:\s+\b(?:to|towards|toward|for|with|and|then|please)\b)|[?.!,]|$)",
        r"\b(?:in|at|near|around)\s+(.+?)(?:(?:\s+\b(?:to|towards|toward|for|with|and|then|please)\b)|[?.!,]|$)",
    )
    excluded_tokens = {
        "here",
        "there",
        "this area",
        "the area",
        "this region",
        "the region",
        "this city",
        "the city",
    }

    for pattern in hint_patterns:
        match = re.search(pattern, normalized_text, re.I)
        if not match or not match.group(1):
            continue
        candidate = _clean_location(match.group(1))
        if candidate and candidate.lower() not in excluded_tokens:
            return candidate
    return ""


def _match_direct_globe_command(
    text: str,
    route_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str] | None:
    """
    Parse direct globe-navigation commands that should bypass sub-agent routing.
    """
    normalized = text.strip()
    lower = normalized.lower()
    route_context_args = _route_origin_args_from_context(route_context)

    # Flood intelligence shortcuts for demo reliability.
    if re.search(
        r"\b(?:major|top|key)\s+(?:flood\s+)?locations?\b",
        lower,
    ) or re.search(r"\bflood hotspots?\b", lower):
        return (
            "get_flood_hotspots",
            {},
            "Identifying major flood hotspot locations now...",
        )
    if re.search(
        r"\b(?:flood (?:situation|problem|problems)|current flooding|current flood)\b",
        lower,
    ):
        return (
            "get_flood_extent",
            {},
            "Running live flood-extent analysis now...",
        )

    # Layer controls
    if re.search(
        r"\b(show|display|turn on|enable)\b.*\bflood(?:\s+(?:extent|layer|overlay))?\b",
        lower,
    ):
        return (
            "toggle_data_layer",
            {"layer_name": "FLOOD_EXTENT", "enabled": True},
            "Displaying flood-extent overlay...",
        )
    if re.search(r"\b(show|display)\b.*\b(hospitals?|infrastructure)\b", lower):
        return (
            "toggle_data_layer",
            {"layer_name": "INFRASTRUCTURE", "enabled": True},
            "Activating infrastructure overlay...",
        )
    if re.search(r"\b(hide|turn off|disable)\b.*\bflood\b", lower):
        return (
            "toggle_data_layer",
            {"layer_name": "FLOOD_EXTENT", "enabled": False},
            "Turning off flood overlay...",
        )
    if _matches_population_density_layer_toggle(lower, enabled=False):
        return (
            "toggle_data_layer",
            {"layer_name": "POPULATION_DENSITY", "enabled": False},
            "Turning off population-density overlay...",
        )
    if _matches_population_density_layer_toggle(lower, enabled=True):
        return (
            "toggle_data_layer",
            {"layer_name": "POPULATION_DENSITY", "enabled": True},
            "Activating population-density overlay...",
        )

    # Evacuation routing fallback for text-only runs when live LLM is unavailable.
    route_match = re.search(
        r"^(?:show me\s+)?(?:route evacuation|evacuation route|evac route|evacuation plan|evac plan|route)\s+from\s+(.+?)\s+to\s+(.+)$",
        normalized,
        re.I,
    )
    if route_match:
        origin_name = _clean_location(route_match.group(1))
        destination_name = _clean_location(route_match.group(2))
        if origin_name and destination_name:
            safety_bypass_requested = bool(
                re.search(r"\b(?:without|ignore|skip)\s+flood\b", lower)
            )
            narration = f"Generating evacuation route from {origin_name} to {destination_name}..."
            if safety_bypass_requested:
                narration += " Flood safety checks remain enabled."
            return (
                "generate_evacuation_route",
                {
                    "origin_name": origin_name,
                    "destination_name": destination_name,
                    "avoid_flood": True,
                },
                narration,
            )

    generic_route_request = re.search(
        r"\b(?:route evacuation|evacuation route|evac route|evacuation plan|evac plan)\b",
        lower,
    )
    if generic_route_request:
        from_match = re.search(r"\bfrom\s+(.+?)(?:\s+to\b|$)", normalized, re.I)
        to_match = re.search(r"\bto\s+(.+)$", normalized, re.I)
        has_explicit_origin = bool(from_match and from_match.group(1))
        has_explicit_destination = bool(to_match and to_match.group(1))
        origin_hint = (
            ""
            if has_explicit_origin
            else _extract_evacuation_origin_hint(normalized)
        )
        if origin_hint:
            has_explicit_origin = True
        origin_name = (
            _clean_location(from_match.group(1))
            if has_explicit_origin and from_match and from_match.group(1)
            else (origin_hint or DEFAULT_EVACUATION_ROUTE_ORIGIN)
        )
        destination_name = (
            _clean_location(to_match.group(1))
            if has_explicit_destination and to_match and to_match.group(1)
            else DEFAULT_EVACUATION_ROUTE_DESTINATION
        )
        if has_explicit_origin and not has_explicit_destination:
            destination_name = ""

        if origin_name.lower() == destination_name.lower():
            destination_name = DEFAULT_EVACUATION_ROUTE_DESTINATION

        safety_bypass_requested = bool(
            re.search(r"\b(?:without|ignore|skip)\s+flood\b", lower)
        )
        using_defaults = not has_explicit_origin and not has_explicit_destination
        destination_requests_nearest = destination_name.strip().lower() in {
            "nearest shelter",
            "nearest safe shelter",
            "safe shelter",
            "evacuation shelter",
        }
        use_dynamic_destination = (
            (using_defaults and route_context_args is not None)
            or destination_requests_nearest
            or (has_explicit_origin and not has_explicit_destination)
        )
        tool_args: dict[str, Any] = {
            "origin_name": origin_name,
            "avoid_flood": True,
        }
        if destination_name:
            tool_args["destination_name"] = destination_name
        if route_context_args is not None and not has_explicit_origin:
            tool_args.update(route_context_args)
            if "origin_label" in route_context_args:
                origin_name = route_context_args["origin_label"]
        if use_dynamic_destination:
            tool_args["destination_mode"] = "nearest_safe_shelters"
            tool_args["max_alternates"] = 3

        if use_dynamic_destination:
            if has_explicit_origin and origin_name:
                narration = (
                    f"Generating dynamic evacuation plan from {origin_name} "
                    "to nearby safe shelters..."
                )
            else:
                narration = (
                    "Generating dynamic evacuation plan from current tactical position "
                    "to nearest safe shelters..."
                )
        elif not using_defaults:
            narration = (
                f"Generating evacuation route from {origin_name} to {destination_name}..."
            )
        else:
            narration = (
                f"Generating evacuation route from {origin_name} to {destination_name}. "
                "Say 'evacuation route from A to B' to customize."
            )
        if safety_bypass_requested:
            narration += " Flood safety checks remain enabled."
        return (
            "generate_evacuation_route",
            tool_args,
            narration,
        )

    # Camera modes
    if "orbit" in lower or "circle around" in lower:
        target = ""
        target_match = re.search(r"(?:orbit|circle around)\s+(.+)$", normalized, re.I)
        if target_match:
            candidate = _clean_location(target_match.group(1))
            if candidate.lower() not in {
                "",
                "this area",
                "the area",
                "here",
                "mode",
                "orbit mode",
                "camera mode",
                "the orbit mode",
            }:
                target = candidate
        return (
            "set_camera_mode",
            {"mode": "orbit", "target_location": target},
            "Switching to orbit camera mode...",
        )
    if "bird's eye" in lower or "bird eye" in lower or "top down" in lower:
        return (
            "set_camera_mode",
            {"mode": "bird_eye", "target_location": ""},
            "Switching to bird-eye camera mode...",
        )
    if re.fullmatch(r".*\boverview\b.*", lower):
        return (
            "set_camera_mode",
            {"mode": "overview", "target_location": ""},
            "Switching to overview camera mode...",
        )
    if "street level" in lower or "ground view" in lower:
        location_match = re.search(
            r"(?:street level|ground view)(?:.*\b(?:at|in|to)\b\s+(.+))?$",
            normalized,
            re.I,
        )
        location = (
            _clean_location(location_match.group(1))
            if location_match and location_match.group(1)
            else ""
        )
        return (
            "set_camera_mode",
            {"mode": "street_level", "target_location": location},
            "Switching to street-level camera mode...",
        )

    # Entity deployment
    deploy_match = re.search(
        r"deploy (?:a |an )?(helicopter|boat|command post|command_post)(?:\s+(?:to|at)\s+(.+))?$",
        normalized,
        re.I,
    )
    if deploy_match:
        entity_type = deploy_match.group(1).lower().replace(" ", "_")
        location = (
            _clean_location(deploy_match.group(2))
            if deploy_match.group(2)
            else "Kampung Melayu"
        )
        return (
            "deploy_entity",
            {"entity_type": entity_type, "location_name": location},
            f"Deploying {entity_type.replace('_', ' ')} to {location}...",
        )

    # Atmosphere
    if "night vision" in lower:
        return (
            "set_atmosphere",
            {"mode": "night_vision"},
            "Switching to night-vision mode...",
        )
    if "tactical view" in lower:
        return (
            "set_atmosphere",
            {"mode": "tactical"},
            "Switching to tactical atmosphere mode...",
        )
    if "normal view" in lower:
        return (
            "set_atmosphere",
            {"mode": "normal"},
            "Restoring normal atmosphere mode...",
        )

    # Measurement
    measure_match = re.search(r"how far is (.+?) from (.+)$", normalized, re.I)
    if not measure_match:
        measure_match = re.search(r"distance between (.+?) and (.+)$", normalized, re.I)
    if measure_match:
        from_location = _clean_location(measure_match.group(1))
        to_location = _clean_location(measure_match.group(2))
        return (
            "add_measurement",
            {"from_location": from_location, "to_location": to_location},
            f"Measuring distance from {from_location} to {to_location}...",
        )

    # Threat rings
    rings_match = re.search(r"(?:show\s+)?threat rings around (.+)$", normalized, re.I)
    if rings_match:
        center = _clean_location(rings_match.group(1))
        return (
            "add_threat_rings",
            {"center_location": center, "ring_radii_km": "1,2,3"},
            f"Showing threat rings around {center}...",
        )

    # Screenshot analysis trigger
    if "what am i looking at" in lower or "analyze the view" in lower:
        return (
            "capture_current_view",
            {},
            "Capturing current strategic view for analysis...",
        )

    # Location navigation (keep this after specific 'show me ...' layer rules)
    fly_match = re.match(
        r"^(?:fly(?: me)? to|show me|go to|zoom into|take me to)\s+(.+)$",
        normalized,
        re.I,
    )
    if fly_match:
        if _is_analytics_request(lower):
            return None
        location = _clean_location(fly_match.group(1))
        if location:
            return (
                "fly_to_location",
                {"location_name": location},
                f"Flying to {location}...",
            )

    return None


def _match_agent_globe_confirmation(
    text: str,
) -> tuple[str, dict[str, Any], str] | None:
    """
    Infer a direct globe action from the agent's spoken confirmation when the
    live model answered naturally but failed to emit the actual tool call.
    """
    normalized = text.strip()
    lower = normalized.lower()

    fly_match = re.search(r"\bflying to\s+(.+?)(?:[.!?,]|$)", normalized, re.I)
    if fly_match:
        location = _clean_location(fly_match.group(1))
        if location:
            return (
                "fly_to_location",
                {"location_name": location},
                f"agent_confirmation:{normalized}",
            )

    if re.search(r"\b(activating|switching to)\s+orbit(?:\s+camera)?\s+mode\b", lower):
        return (
            "set_camera_mode",
            {"mode": "orbit", "target_location": ""},
            f"agent_confirmation:{normalized}",
        )

    if re.search(
        r"\b(switching to|activating)\s+bird(?:'s)?[ -]?eye(?:\s+camera)?\s+mode\b",
        lower,
    ):
        return (
            "set_camera_mode",
            {"mode": "bird_eye", "target_location": ""},
            f"agent_confirmation:{normalized}",
        )

    if re.search(
        r"\b(switching to|activating)\s+overview\s+(?:camera\s+)?mode\b", lower
    ):
        return (
            "set_camera_mode",
            {"mode": "overview", "target_location": ""},
            f"agent_confirmation:{normalized}",
        )

    if re.search(
        r"\b(switching to|activating)\s+street[- ]level\s+(?:camera\s+)?mode\b", lower
    ):
        return (
            "set_camera_mode",
            {"mode": "street_level", "target_location": ""},
            f"agent_confirmation:{normalized}",
        )

    if re.search(r"\bactivating\s+infrastructure\s+overlay\b", lower):
        return (
            "toggle_data_layer",
            {"layer_name": "INFRASTRUCTURE", "enabled": True},
            f"agent_confirmation:{normalized}",
        )

    if re.search(r"\bturning off\s+flood\s+overlay\b", lower):
        return (
            "toggle_data_layer",
            {"layer_name": "FLOOD_EXTENT", "enabled": False},
            f"agent_confirmation:{normalized}",
        )

    if _matches_population_density_layer_toggle(lower, enabled=False):
        return (
            "toggle_data_layer",
            {"layer_name": "POPULATION_DENSITY", "enabled": False},
            f"agent_confirmation:{normalized}",
        )

    if _matches_population_density_layer_toggle(lower, enabled=True):
        return (
            "toggle_data_layer",
            {"layer_name": "POPULATION_DENSITY", "enabled": True},
            f"agent_confirmation:{normalized}",
        )

    if re.search(r"\b(switching to|activating)\s+night[- ]vision\s+mode\b", lower):
        return (
            "set_atmosphere",
            {"mode": "night_vision"},
            f"agent_confirmation:{normalized}",
        )

    if re.search(r"\brestoring\s+normal\s+atmosphere\s+mode\b", lower):
        return (
            "set_atmosphere",
            {"mode": "normal"},
            f"agent_confirmation:{normalized}",
        )

    return None


def _queue_pending_globe_fallback(
    session_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    source: str,
    *,
    overwrite: bool,
) -> None:
    if not overwrite and session_id in _pending_globe_fallbacks:
        return

    _pending_globe_fallbacks[session_id] = (tool_name, tool_args, source)
    logger.info(
        "[GLOBE FALLBACK] Queued %s args=%s source=%s",
        tool_name,
        tool_args,
        source,
    )


def _suppress_pending_globe_fallback_for_matching_tool(
    session_id: str, executed_tool_name: str
) -> bool:
    if executed_tool_name not in DIRECT_FALLBACK_SUPPRESS_TOOLS:
        return False
    pending = _pending_globe_fallbacks.get(session_id)
    if pending is None:
        return False
    pending_tool_name, _, _ = pending
    if pending_tool_name != executed_tool_name:
        return False
    _globe_tool_calls_this_turn.add(session_id)
    _pending_globe_fallbacks.pop(session_id, None)
    return True


def _clear_globe_turn_state(session_id: str) -> None:
    _pending_globe_fallbacks.pop(session_id, None)
    _globe_tool_calls_this_turn.discard(session_id)


def _clear_pending_long_running_tools(session_id: str) -> None:
    _pending_long_running_tools.pop(session_id, None)


def _clear_fast_ack_state(session_id: str) -> None:
    _analysis_ack_last_sent_at.pop(session_id, None)


def _nonblocking_tool_events_enabled() -> bool:
    raw_value = os.getenv(NONBLOCKING_TOOL_EVENTS_ENV_VAR)
    if raw_value is None:
        return True
    return _is_enabled_env_flag(raw_value)


def _is_long_running_live_tool(tool_name: str) -> bool:
    if not isinstance(tool_name, str):
        return False
    return tool_name in LONG_RUNNING_LIVE_TOOLS


def _extract_tool_call_id(func_call: Any) -> str | None:
    for field_name in ("id", "call_id", "tool_call_id"):
        candidate = getattr(func_call, field_name, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _register_pending_long_running_tool(
    session_id: str,
    tool_name: str,
    *,
    call_id: str | None = None,
) -> dict[str, Any]:
    resolved_call_id = (
        call_id
        if isinstance(call_id, str) and call_id.strip()
        else f"{tool_name}-{uuid.uuid4().hex[:10]}"
    )
    tracker = {
        "tool": tool_name,
        "call_id": resolved_call_id,
        "started_at_ms": int(time.time() * 1000),
        "started_monotonic": time.monotonic(),
    }
    session_trackers = _pending_long_running_tools.setdefault(session_id, {})
    tool_trackers = session_trackers.setdefault(tool_name, [])
    tool_trackers.append(tracker)
    return tracker


def _consume_pending_long_running_tool(
    session_id: str,
    tool_name: str,
) -> dict[str, Any] | None:
    session_trackers = _pending_long_running_tools.get(session_id)
    if not session_trackers:
        return None

    tool_trackers = session_trackers.get(tool_name)
    if not tool_trackers:
        return None

    tracker = tool_trackers.pop(0)
    if not tool_trackers:
        session_trackers.pop(tool_name, None)
    if not session_trackers:
        _pending_long_running_tools.pop(session_id, None)
    return tracker


def _build_tool_status_event(
    *,
    tool_name: str,
    state: str,
    call_id: str,
    message: str | None = None,
    error: str | None = None,
    started_at_ms: int | None = None,
    duration_ms: int | None = None,
    runtime_status: str | None = None,
) -> dict[str, Any]:
    event = {
        "type": "tool_status",
        "tool": tool_name,
        "state": state,
        "status": state,
        "call_id": call_id,
        "long_running": True,
        "timestamp": int(time.time() * 1000),
    }
    if isinstance(message, str) and message.strip():
        event["message"] = message.strip()
    if isinstance(error, str) and error.strip():
        event["error"] = error.strip()
    if isinstance(started_at_ms, int):
        event["started_at_ms"] = started_at_ms
    if isinstance(duration_ms, int):
        event["duration_ms"] = max(0, duration_ms)
    if isinstance(runtime_status, str) and runtime_status.strip():
        event["runtime_status"] = runtime_status.strip()
    return event


def _extract_tool_error_message(tool_result: dict[str, Any]) -> str | None:
    if not isinstance(tool_result, dict):
        return None

    direct_error = tool_result.get("error")
    if isinstance(direct_error, str) and direct_error.strip():
        return direct_error.strip()
    if isinstance(direct_error, dict):
        for key in ("message", "detail", "error", "code"):
            candidate = direct_error.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

    runtime_error = tool_result.get("runtime_error")
    if isinstance(runtime_error, str) and runtime_error.strip():
        return runtime_error.strip()
    if isinstance(runtime_error, dict):
        for key in ("message", "detail", "error", "code"):
            candidate = runtime_error.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _to_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"byte_length": len(value)}

    if isinstance(value, dict):
        return {str(key): _to_plain_data(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_plain_data(item) for item in value]

    try:
        if isinstance(value, ProtoStruct):
            return MessageToDict(value)
    except Exception:
        pass

    try:
        if hasattr(value, "DESCRIPTOR"):
            return MessageToDict(value)
    except Exception:
        pass

    try:
        if hasattr(value, "pb"):
            return MessageToDict(value.pb)
    except Exception:
        pass

    if hasattr(value, "keys"):
        try:
            return {str(key): _to_plain_data(value[key]) for key in value}
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return {
                key: _to_plain_data(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        except Exception:
            pass

    return str(value)


def _collect_dict_candidates(
    payload: Any,
    collector: list[dict[str, Any]],
    *,
    depth: int = 0,
    max_depth: int = 5,
) -> None:
    if payload is None or depth > max_depth:
        return

    if isinstance(payload, dict):
        collector.append(payload)
        for value in payload.values():
            _collect_dict_candidates(
                value,
                collector,
                depth=depth + 1,
                max_depth=max_depth,
            )
        return

    if isinstance(payload, list):
        for value in payload:
            _collect_dict_candidates(
                value,
                collector,
                depth=depth + 1,
                max_depth=max_depth,
            )


def _first_numeric_value(
    payload: dict[str, Any], keys: tuple[str, ...]
) -> float | None:
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, (int, float)):
            return float(candidate)
        if isinstance(candidate, str):
            trimmed = candidate.strip().replace(",", "")
            if not trimmed:
                continue
            try:
                return float(trimmed)
            except ValueError:
                continue
    return None


def _first_string_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _normalize_session_pressure(
    raw_pressure: str | None, utilization_ratio: float | None
) -> str | None:
    if isinstance(raw_pressure, str) and raw_pressure.strip():
        normalized = raw_pressure.strip().replace("-", "_").replace(" ", "_").upper()
        aliases = {
            "OK": "LOW",
            "NORMAL": "LOW",
            "SAFE": "LOW",
            "WARN": "MODERATE",
            "WARNING": "MODERATE",
            "ELEVATED": "MODERATE",
            "SEVERE": "HIGH",
        }
        return aliases.get(normalized, normalized)

    if isinstance(utilization_ratio, (int, float)):
        bounded = max(0.0, min(1.0, float(utilization_ratio)))
        if bounded >= 0.9:
            return "CRITICAL"
        if bounded >= 0.75:
            return "HIGH"
        if bounded >= 0.55:
            return "MODERATE"
        return "LOW"

    return None


def _extract_live_usage_update_event(event: Event) -> dict[str, Any] | None:
    candidate_dicts: list[dict[str, Any]] = []

    direct_sources = (
        getattr(event, "usage_metadata", None),
        getattr(event, "usage", None),
        getattr(event, "metadata", None),
    )
    for source in direct_sources:
        plain_source = _to_plain_data(source)
        _collect_dict_candidates(plain_source, candidate_dicts)

    root_plain = _to_plain_data(event)
    _collect_dict_candidates(root_plain, candidate_dicts, max_depth=6)

    if not candidate_dicts:
        return None

    metric_by_score: tuple[int, dict[str, Any], dict[str, Any]] | None = None
    for candidate in candidate_dicts:
        input_tokens = _first_numeric_value(
            candidate,
            (
                "prompt_token_count",
                "input_token_count",
                "input_tokens",
                "prompt_tokens",
                "request_token_count",
            ),
        )
        output_tokens = _first_numeric_value(
            candidate,
            (
                "candidates_token_count",
                "output_token_count",
                "output_tokens",
                "response_token_count",
                "completion_tokens",
            ),
        )
        total_tokens = _first_numeric_value(
            candidate,
            (
                "total_token_count",
                "total_tokens",
                "token_count",
                "token_total",
            ),
        )
        cached_tokens = _first_numeric_value(
            candidate,
            (
                "cached_content_token_count",
                "cache_read_token_count",
                "cached_tokens",
            ),
        )
        context_tokens = _first_numeric_value(
            candidate,
            (
                "context_window_token_count",
                "context_token_count",
                "context_tokens",
                "session_token_count",
                "memory_token_count",
            ),
        )
        utilization_ratio = _first_numeric_value(
            candidate,
            (
                "context_window_utilization",
                "context_utilization",
                "session_pressure_ratio",
                "pressure_ratio",
            ),
        )
        raw_pressure = _first_string_value(
            candidate,
            (
                "session_pressure",
                "context_window_pressure",
                "pressure",
                "status",
            ),
        )
        score = sum(
            value is not None
            for value in (
                input_tokens,
                output_tokens,
                total_tokens,
                cached_tokens,
                context_tokens,
                utilization_ratio,
                raw_pressure,
            )
        )
        if score <= 0:
            continue

        resolved_metrics = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "context_tokens": context_tokens,
            "utilization_ratio": utilization_ratio,
            "raw_pressure": raw_pressure,
            "trigger_tokens": _first_numeric_value(
                candidate,
                (
                    "trigger_tokens",
                    "context_trigger_tokens",
                    "max_context_tokens",
                ),
            ),
            "target_tokens": _first_numeric_value(
                candidate,
                (
                    "target_tokens",
                    "context_target_tokens",
                    "sliding_window_tokens",
                ),
            ),
            "source_hint": _first_string_value(
                candidate,
                ("source", "provider", "service", "component"),
            ),
        }
        if metric_by_score is None or score > metric_by_score[0]:
            metric_by_score = (score, candidate, resolved_metrics)

    if metric_by_score is None:
        return None

    _, _, metrics = metric_by_score
    input_tokens = metrics.get("input_tokens")
    output_tokens = metrics.get("output_tokens")
    total_tokens = metrics.get("total_tokens")
    cached_tokens = metrics.get("cached_tokens")
    context_tokens = metrics.get("context_tokens")
    utilization_ratio = metrics.get("utilization_ratio")
    trigger_tokens = metrics.get("trigger_tokens")
    target_tokens = metrics.get("target_tokens")

    if (
        total_tokens is None
        and isinstance(input_tokens, (int, float))
        and isinstance(output_tokens, (int, float))
    ):
        total_tokens = float(input_tokens) + float(output_tokens)

    if context_tokens is None:
        context_tokens = total_tokens if total_tokens is not None else input_tokens

    resolved_trigger_tokens = (
        int(trigger_tokens) if trigger_tokens else LIVE_CONTEXT_TRIGGER_TOKENS
    )
    resolved_target_tokens = (
        int(target_tokens) if target_tokens else LIVE_CONTEXT_TARGET_TOKENS
    )

    if utilization_ratio is None and isinstance(context_tokens, (int, float)):
        if resolved_trigger_tokens > 0:
            utilization_ratio = float(context_tokens) / float(resolved_trigger_tokens)

    pressure = _normalize_session_pressure(
        metrics.get("raw_pressure"),
        utilization_ratio,
    )

    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and cached_tokens is None
        and context_tokens is None
        and pressure is None
    ):
        return None

    usage_payload: dict[str, Any] = {}
    if input_tokens is not None:
        usage_payload["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        usage_payload["output_tokens"] = int(output_tokens)
    if total_tokens is not None:
        usage_payload["total_tokens"] = int(total_tokens)
    if cached_tokens is not None:
        usage_payload["cached_tokens"] = int(cached_tokens)

    context_payload: dict[str, Any] = {
        "trigger_tokens": resolved_trigger_tokens,
        "target_tokens": resolved_target_tokens,
    }
    if context_tokens is not None:
        context_payload["context_tokens"] = int(context_tokens)
    if utilization_ratio is not None:
        context_payload["utilization_ratio"] = round(
            max(0.0, min(1.0, float(utilization_ratio))), 4
        )

    session_health: dict[str, Any] = {}
    if pressure is not None:
        session_health["pressure"] = pressure
    if utilization_ratio is not None:
        session_health["pressure_score"] = round(
            max(0.0, min(1.0, float(utilization_ratio))), 4
        )
    source_hint = metrics.get("source_hint")
    if isinstance(source_hint, str) and source_hint.strip():
        session_health["source"] = source_hint.strip()

    return {
        "type": "usage_update",
        "usage": usage_payload,
        "context": context_payload,
        "session_health": session_health,
        "timestamp": int(time.time() * 1000),
    }


def _usage_event_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    session_health = (
        payload.get("session_health")
        if isinstance(payload.get("session_health"), dict)
        else {}
    )
    return (
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("total_tokens"),
        usage.get("cached_tokens"),
        context.get("context_tokens"),
        context.get("utilization_ratio"),
        session_health.get("pressure"),
    )


def _normalize_grounding_citation(
    raw_citation: Any,
    *,
    index: int,
) -> dict[str, Any] | None:
    title: str | None = None
    url: str | None = None
    source_label: str | None = None
    snippet: str | None = None

    if isinstance(raw_citation, str):
        trimmed = raw_citation.strip()
        if not trimmed:
            return None
        if trimmed.startswith(("http://", "https://")):
            url = trimmed
            title = trimmed
        else:
            title = trimmed
    elif isinstance(raw_citation, dict):
        title_candidate = _first_non_empty_value(
            raw_citation.get("title"),
            raw_citation.get("label"),
            raw_citation.get("name"),
            raw_citation.get("source_dataset"),
            raw_citation.get("source"),
        )
        if isinstance(title_candidate, str) and title_candidate.strip():
            title = title_candidate.strip()

        url_candidate = _first_non_empty_value(
            raw_citation.get("uri"),
            raw_citation.get("url"),
            raw_citation.get("link"),
            raw_citation.get("href"),
            raw_citation.get("source_url"),
        )
        if isinstance(url_candidate, str) and url_candidate.strip():
            normalized_url = url_candidate.strip()
            if normalized_url.startswith(("http://", "https://")):
                url = normalized_url

        source_candidate = _first_non_empty_value(
            raw_citation.get("source"),
            raw_citation.get("publisher"),
            raw_citation.get("domain"),
            raw_citation.get("source_dataset"),
        )
        if isinstance(source_candidate, str) and source_candidate.strip():
            source_label = source_candidate.strip()

        snippet_candidate = _first_non_empty_value(
            raw_citation.get("snippet"),
            raw_citation.get("description"),
            raw_citation.get("summary"),
            raw_citation.get("text"),
        )
        if isinstance(snippet_candidate, str) and snippet_candidate.strip():
            snippet = snippet_candidate.strip()
    else:
        return None

    if not title and url:
        title = url
    if not title and source_label:
        title = source_label
    if not title:
        title = f"Source {index + 1}"

    if url and not source_label:
        try:
            source_label = urlparse(url).netloc or None
        except Exception:
            source_label = None

    citation: dict[str, Any] = {
        "id": f"citation-{index + 1}",
        "title": title,
    }
    if url:
        citation["url"] = url
    if source_label:
        citation["source"] = source_label
    if snippet:
        citation["snippet"] = snippet
    return citation


def _collect_grounding_citations(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_candidates: list[Any] = []
    for key in (
        "sources",
        "citations",
        "references",
        "grounding_sources",
        "source_links",
    ):
        value = tool_result.get(key)
        if isinstance(value, list):
            raw_candidates.extend(value)

    for key in ("uri", "url", "link", "source_url"):
        candidate = tool_result.get(key)
        if isinstance(candidate, str) and candidate.strip():
            raw_candidates.append({"uri": candidate, "title": tool_result.get("title")})

    metadata = tool_result.get("metadata")
    if isinstance(metadata, dict):
        raw_candidates.append(metadata)

    provenance = tool_result.get("provenance")
    if isinstance(provenance, dict):
        raw_candidates.append(provenance)

    runtime_provenance = tool_result.get("runtime_provenance")
    if isinstance(runtime_provenance, dict):
        raw_candidates.append(runtime_provenance)

    ee_runtime = tool_result.get("ee_runtime")
    if isinstance(ee_runtime, dict):
        ee_provenance = ee_runtime.get("provenance")
        if isinstance(ee_provenance, dict):
            raw_candidates.append(ee_provenance)

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str, str | None]] = set()
    for index, candidate in enumerate(raw_candidates):
        citation = _normalize_grounding_citation(candidate, index=index)
        if citation is None:
            continue
        dedupe_key = (
            citation.get("url"),
            citation.get("title", ""),
            citation.get("source"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(citation)
        if len(normalized) >= 8:
            break
    return normalized


def _build_grounding_update_event(
    tool_name: str,
    tool_result: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(tool_result, dict):
        return None

    citations = _collect_grounding_citations(tool_result)
    if not citations:
        return None

    label = tool_name.replace("_", " ").strip().title() if tool_name else "Grounding"
    grounding_event: dict[str, Any] = {
        "type": "grounding_update",
        "tool": tool_name,
        "label": label,
        "grounded": bool(tool_result.get("grounded", True)),
        "source_count": len(citations),
        "citations": citations,
        "timestamp": int(time.time() * 1000),
    }

    query = tool_result.get("query")
    if isinstance(query, str) and query.strip():
        grounding_event["query"] = query.strip()

    search_queries = tool_result.get("search_queries")
    if isinstance(search_queries, list):
        normalized_search_queries = [
            item.strip()
            for item in search_queries
            if isinstance(item, str) and item.strip()
        ]
        if normalized_search_queries:
            grounding_event["search_queries"] = normalized_search_queries

    summary_candidate = _first_non_empty_value(
        tool_result.get("response"),
        tool_result.get("summary"),
        tool_result.get("description"),
    )
    if isinstance(summary_candidate, str) and summary_candidate.strip():
        grounding_event["summary"] = summary_candidate.strip()[:500]

    return grounding_event


async def _map_tool_result_to_events_nonblocking(
    tool_name: str,
    tool_result: dict[str, Any],
) -> list[dict[str, Any]]:
    if _is_long_running_live_tool(tool_name):
        return await asyncio.to_thread(
            _map_tool_result_to_events, tool_name, tool_result
        )
    return _map_tool_result_to_events(tool_name, tool_result)


async def _emit_interrupted_pending_tool_statuses(
    websocket: WebSocket,
    session_id: str,
) -> None:
    if not _nonblocking_tool_events_enabled():
        _clear_pending_long_running_tools(session_id)
        return

    session_trackers = _pending_long_running_tools.pop(session_id, None)
    if not session_trackers:
        return

    now_monotonic = time.monotonic()
    for tool_name, pending_items in session_trackers.items():
        for pending in pending_items:
            started_monotonic = pending.get("started_monotonic")
            duration_ms = None
            if isinstance(started_monotonic, (int, float)):
                duration_ms = int((now_monotonic - started_monotonic) * 1000)
            await websocket.send_text(
                json.dumps(
                    _build_tool_status_event(
                        tool_name=tool_name,
                        state="error",
                        call_id=str(
                            pending.get("call_id")
                            or f"{tool_name}-{uuid.uuid4().hex[:10]}"
                        ),
                        message=f"{tool_name} interrupted before completion",
                        error="Tool execution interrupted by user",
                        started_at_ms=pending.get("started_at_ms"),
                        duration_ms=duration_ms,
                    )
                )
            )


async def _execute_pending_globe_fallback(
    websocket: WebSocket, session_id: str
) -> None:
    pending = _pending_globe_fallbacks.get(session_id)
    if pending is None:
        _globe_tool_calls_this_turn.discard(session_id)
        return

    if session_id in _globe_tool_calls_this_turn:
        logger.info(
            "[GLOBE FALLBACK] Skipping pending fallback because a real globe tool call already executed"
        )
        _clear_globe_turn_state(session_id)
        return

    tool_name, tool_args, source = pending
    logger.info(
        "[GLOBE FALLBACK] Executing %s args=%s source=%s",
        tool_name,
        tool_args,
        source,
    )

    await websocket.send_text(
        json.dumps(
            {
                "type": "tool_call",
                "tool": tool_name,
                "args": tool_args,
            }
        )
    )

    try:
        tool_result = _execute_direct_globe_tool(tool_name, tool_args)
        for evt in _map_tool_result_to_events(tool_name, tool_result):
            await websocket.send_text(json.dumps(evt, default=str))
    except Exception as exc:
        logger.error(
            "[GLOBE FALLBACK] Failed to execute %s from %s: %s",
            tool_name,
            source,
            exc,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error",
                    "message": f"Failed to execute {tool_name}: {exc}",
                }
            )
        )
    finally:
        _clear_globe_turn_state(session_id)


def _execute_direct_globe_tool(
    tool_name: str, tool_args: dict[str, Any]
) -> dict[str, Any]:
    tool_fn = _DIRECT_GLOBE_TOOL_FUNCS.get(tool_name)
    if tool_fn is None:
        raise ValueError(f"Unsupported direct globe tool: {tool_name}")
    return tool_fn(**tool_args)


def _compress_screenshot_if_needed(image_bytes: bytes) -> bytes | None:
    """
    Keep screenshot payloads under Gemini Live's 7MB per-frame cap.

    Frontend should already send compact JPEGs, but this backend guardrail retries
    with 768x768 + JPEG quality=70 when needed.
    """
    if len(image_bytes) <= MAX_LIVE_IMAGE_BYTES:
        return image_bytes

    try:
        from io import BytesIO
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "[SCREENSHOT] Image too large (%s bytes) and Pillow is unavailable; skipping",
            len(image_bytes),
        )
        return None

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((768, 768))
            out = BytesIO()
            img.save(out, format="JPEG", quality=70, optimize=True)
            compressed = out.getvalue()
    except Exception as exc:
        logger.warning("[SCREENSHOT] Failed to recompress screenshot: %s", exc)
        return None

    if len(compressed) > MAX_LIVE_IMAGE_BYTES:
        logger.warning(
            "[SCREENSHOT] Recompressed image still too large (%s bytes), skipping",
            len(compressed),
        )
        return None

    logger.info(
        "[SCREENSHOT] Compressed screenshot from %s to %s bytes",
        len(image_bytes),
        len(compressed),
    )
    return compressed


def _strip_data_url_prefix(image_b64: str) -> str:
    normalized = image_b64.strip()
    if not normalized.startswith("data:"):
        return normalized

    _, _, payload = normalized.partition(",")
    return payload if payload else normalized


def _extract_screenshot_response_payload(
    message_data: dict[str, Any],
) -> tuple[str, str, str] | None:
    """
    Normalize screenshot response messages from canonical and legacy schemas.

    Canonical schema:
      {"type":"screenshot_response","request_id":"...","image_base64":"..."}

    Legacy schema (wrapped in text content):
      {"type":"text","content":"{\"type\":\"screenshot_response\",...}"}
    """

    def _extract_candidate(candidate: Any) -> tuple[str, str] | None:
        if not isinstance(candidate, dict):
            return None
        if candidate.get("type") != "screenshot_response":
            return None

        request_id_raw = candidate.get("request_id", candidate.get("requestId"))
        request_id = (
            request_id_raw.strip()
            if isinstance(request_id_raw, str) and request_id_raw.strip()
            else "unknown"
        )

        image_b64_raw = candidate.get("image_base64", candidate.get("imageBase64"))
        image_b64 = (
            _strip_data_url_prefix(image_b64_raw)
            if isinstance(image_b64_raw, str)
            else ""
        )
        return request_id, image_b64

    candidates: list[tuple[str, Any]] = [("top_level", message_data)]

    if message_data.get("type") == "text":
        for field_name in ("content", "text"):
            embedded = message_data.get(field_name)
            if isinstance(embedded, dict):
                candidates.append((f"text.{field_name}.dict", embedded))
                continue
            if isinstance(embedded, str):
                stripped = embedded.strip()
                if not stripped.startswith("{") or not stripped.endswith("}"):
                    continue
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                candidates.append((f"text.{field_name}.json", decoded))

    for source, candidate in candidates:
        extracted = _extract_candidate(candidate)
        if extracted is not None:
            request_id, image_b64 = extracted
            return request_id, image_b64, source

    return None


def _handle_screenshot_response_message(
    message_data: dict[str, Any],
    live_request_queue: LiveRequestQueue,
    metrics: _WebSocketSessionMetrics | None = None,
) -> bool:
    normalized = _extract_screenshot_response_payload(message_data)
    if normalized is None:
        return False

    if metrics is not None:
        metrics.increment("upstream_screenshot_messages")

    request_id, image_b64, source = normalized
    if not image_b64:
        if metrics is not None:
            metrics.record_upstream_invalid("screenshot_missing_image_base64")
        logger.warning(
            "[SCREENSHOT] Missing image_base64 for request_id=%s source=%s",
            request_id,
            source,
        )
        return True

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        if metrics is not None:
            metrics.record_upstream_invalid("screenshot_invalid_base64")
        logger.warning(
            "[SCREENSHOT] Invalid base64 payload for request_id=%s source=%s: %s",
            request_id,
            source,
            exc,
        )
        return True

    prepared_image = _compress_screenshot_if_needed(image_bytes)
    if prepared_image is None:
        if metrics is not None:
            metrics.record_upstream_drop("screenshot_rejected_after_compression")
        return True

    live_request_queue.send_realtime(
        types.Blob(
            mime_type="image/jpeg",
            data=prepared_image,
        )
    )
    live_request_queue.send_content(
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "I just captured the current 3D strategic view. "
                        "Analyze what you see — identify visible flood zones, "
                        "infrastructure, terrain features, and any threats visible "
                        "in the image. Describe the tactical situation."
                    )
                )
            ],
        )
    )
    if metrics is not None:
        metrics.mark_upstream_activity("screenshot_response")
        metrics.increment("upstream_screenshot_forwarded")
    logger.info(
        "[SCREENSHOT] Injected screenshot request_id=%s (%s bytes) source=%s",
        request_id,
        len(prepared_image),
        source,
    )
    return True


def _handle_stream_control_message(
    message_data: dict[str, Any],
    live_request_queue: LiveRequestQueue,
    metrics: _WebSocketSessionMetrics | None = None,
    *,
    allow_explicit_activity_control: bool = True,
) -> bool:
    msg_type = _normalize_client_message_type(message_data)
    counter_by_message_type = {
        "activity_start": "upstream_activity_start_messages",
        "activity_end": "upstream_activity_end_messages",
        "audio_stream_end": "upstream_audio_stream_end_messages",
    }

    if msg_type in counter_by_message_type and not allow_explicit_activity_control:
        logger.info(
            "Ignoring %s because automatic activity detection is enabled",
            msg_type,
        )
        if metrics is not None:
            metrics.increment(counter_by_message_type[msg_type])
            metrics.record_upstream_drop(
                f"{msg_type}_suppressed_auto_activity_detection"
            )
        return True

    if msg_type == "activity_start":
        send_activity_start = getattr(live_request_queue, "send_activity_start", None)
        if callable(send_activity_start):
            send_activity_start()
            logger.debug("Forwarded activity_start to LiveRequestQueue")
            if metrics is not None:
                metrics.mark_upstream_activity("activity_start")
        else:
            logger.debug(
                "LiveRequestQueue.send_activity_start unavailable; ignoring activity_start"
            )
            if metrics is not None:
                metrics.record_upstream_drop("activity_start_unavailable")
        if metrics is not None:
            metrics.increment("upstream_activity_start_messages")
        return True

    if msg_type == "activity_end":
        send_activity_end = getattr(live_request_queue, "send_activity_end", None)
        if callable(send_activity_end):
            send_activity_end()
            logger.debug("Forwarded activity_end to LiveRequestQueue")
            if metrics is not None:
                metrics.mark_upstream_activity("activity_end")
        else:
            logger.debug(
                "LiveRequestQueue.send_activity_end unavailable; ignoring activity_end"
            )
            if metrics is not None:
                metrics.record_upstream_drop("activity_end_unavailable")
        if metrics is not None:
            metrics.increment("upstream_activity_end_messages")
        return True

    if msg_type == "audio_stream_end":
        logger.info("Received audio_stream_end signal")
        send_audio_stream_end = getattr(
            live_request_queue, "send_audio_stream_end", None
        )
        if callable(send_audio_stream_end):
            send_audio_stream_end()
            if metrics is not None:
                metrics.mark_upstream_activity("audio_stream_end")
                metrics.increment("upstream_audio_stream_end_messages")
            return True

        send_activity_end = getattr(live_request_queue, "send_activity_end", None)
        if callable(send_activity_end):
            logger.info(
                "LiveRequestQueue.send_audio_stream_end unavailable; "
                "falling back to send_activity_end"
            )
            send_activity_end()
            if metrics is not None:
                metrics.mark_upstream_activity("audio_stream_end_fallback")
                metrics.increment("upstream_audio_stream_end_messages")
            return True

        logger.info(
            "LiveRequestQueue has no stream-end controls; dropping audio_stream_end message"
        )
        if metrics is not None:
            metrics.increment("upstream_audio_stream_end_messages")
            metrics.record_upstream_drop("audio_stream_end_unavailable")
        return True

    if msg_type in _CLIENT_TURN_CONTROL_MESSAGE_TYPES:
        if metrics is not None:
            metrics.increment("upstream_turn_control_noop_messages")
        logger.debug(
            "[UPSTREAM] Ignoring client turn-control message type=%s", msg_type
        )
        return True

    if msg_type == "client_content" and any(
        key in message_data
        for key in ("turn_complete", "turn_control", "turns", "response")
    ):
        if metrics is not None:
            metrics.increment("upstream_turn_control_noop_messages")
        logger.debug(
            "[UPSTREAM] Ignoring client_content turn-control message keys=%s",
            sorted(message_data.keys()),
        )
        return True

    if msg_type is None and any(
        key in message_data
        for key in ("turn_complete", "turn_control", "response.cancel", "response")
    ):
        if metrics is not None:
            metrics.increment("upstream_turn_control_noop_messages")
        logger.debug(
            "[UPSTREAM] Ignoring untyped turn-control payload keys=%s",
            sorted(message_data.keys()),
        )
        return True

    return False


def _normalize_video_message_payload(
    message_data: dict[str, Any],
    metrics: _WebSocketSessionMetrics | None = None,
) -> tuple[bytes, str, str, dict[str, Any]] | None:
    raw_data = message_data.get("data")
    if not isinstance(raw_data, str) or not raw_data.strip():
        if metrics is not None:
            metrics.record_upstream_invalid("video_empty_payload")
        logger.warning("[VIDEO] Dropping frame with empty payload")
        return None

    encoded_frame = _strip_data_url_prefix(raw_data)
    try:
        frame_bytes = base64.b64decode(encoded_frame, validate=True)
    except Exception as exc:
        if metrics is not None:
            metrics.record_upstream_invalid("video_invalid_base64")
        logger.warning("[VIDEO] Dropping frame with invalid base64 payload: %s", exc)
        return None

    if not frame_bytes:
        if metrics is not None:
            metrics.record_upstream_invalid("video_zero_byte_payload")
        logger.warning("[VIDEO] Dropping frame with zero-byte payload")
        return None

    prepared_frame = _compress_screenshot_if_needed(frame_bytes)
    if prepared_frame is None:
        if metrics is not None:
            metrics.record_upstream_drop("video_rejected_after_compression")
        return None

    raw_caption = message_data.get("caption")
    caption = (
        raw_caption.strip()
        if isinstance(raw_caption, str) and raw_caption.strip()
        else "Analyze this live frame."
    )

    raw_mime_type = message_data.get("mime_type")
    mime_type = (
        raw_mime_type.strip()
        if isinstance(raw_mime_type, str) and raw_mime_type.strip()
        else "image/jpeg"
    )
    if not mime_type.lower().startswith("image/"):
        mime_type = "image/jpeg"

    video_metadata: dict[str, Any] = {}
    for key in (
        "frame_id",
        "captured_at_ms",
        "cadence_fps",
        "source",
        "stream_id",
        "active_layers",
        "camera_mode",
    ):
        value = message_data.get(key)
        if isinstance(value, (str, int, float, bool)):
            video_metadata[key] = value

    ui_context_parts: list[str] = []
    caption_lower = caption.lower()
    active_layers = video_metadata.get("active_layers")
    if (
        isinstance(active_layers, str)
        and active_layers.strip()
        and "layers:" not in caption_lower
        and "active layers:" not in caption_lower
    ):
        ui_context_parts.append(f"active layers: {active_layers.strip()}")
    camera_mode = video_metadata.get("camera_mode")
    if (
        isinstance(camera_mode, str)
        and camera_mode.strip()
        and "camera:" not in caption_lower
        and "camera mode:" not in caption_lower
    ):
        ui_context_parts.append(f"camera mode: {camera_mode.strip()}")
    if ui_context_parts:
        caption = f"{caption} UI context: {'; '.join(ui_context_parts)}."

    return prepared_frame, caption, mime_type, video_metadata


def _is_enabled_env_flag(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw_value = os.getenv(name)
    if not isinstance(raw_value, str):
        resolved = default
    else:
        try:
            resolved = int(raw_value.strip())
        except (TypeError, ValueError):
            resolved = default

    if min_value is not None:
        return max(min_value, resolved)
    return resolved


def _get_env_float(
    name: str,
    default: float,
    *,
    min_value: float | None = None,
) -> float:
    raw_value = os.getenv(name)
    if not isinstance(raw_value, str):
        resolved = default
    else:
        try:
            resolved = float(raw_value.strip())
        except (TypeError, ValueError):
            resolved = default

    if min_value is not None:
        return max(min_value, resolved)
    return resolved


def _first_non_empty_value(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        elif value is not None:
            return value
    return None


def _normalize_runtime_confidence_label(label: Any) -> str:
    if not isinstance(label, str):
        return "UNKNOWN"
    normalized = label.strip().replace("-", "_").replace(" ", "_").upper()
    if not normalized:
        return "UNKNOWN"
    return normalized


def _normalize_ee_runtime_envelope(tool_result: dict[str, Any]) -> dict[str, Any]:
    metadata = tool_result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    ee_runtime_input = tool_result.get("ee_runtime")
    ee_runtime = dict(ee_runtime_input) if isinstance(ee_runtime_input, dict) else {}

    runtime_layers = ee_runtime.get("layers")
    if not isinstance(runtime_layers, list):
        fallback_layers = tool_result.get("runtime_layers")
        runtime_layers = fallback_layers if isinstance(fallback_layers, list) else []

    temporal_frames = ee_runtime.get("temporal_frames")
    if not isinstance(temporal_frames, dict):
        fallback_frames = tool_result.get("temporal_frames")
        temporal_frames = fallback_frames if isinstance(fallback_frames, dict) else {}

    temporal_playback = ee_runtime.get("temporal_playback")
    if not isinstance(temporal_playback, dict):
        fallback_playback = tool_result.get("temporal_playback")
        temporal_playback = (
            fallback_playback if isinstance(fallback_playback, dict) else {}
        )

    temporal_summary = ee_runtime.get("temporal_summary")
    if not isinstance(temporal_summary, dict):
        fallback_summary = tool_result.get("temporal_summary")
        temporal_summary = (
            fallback_summary if isinstance(fallback_summary, dict) else {}
        )

    multisensor_fusion = ee_runtime.get("multisensor_fusion")
    if not isinstance(multisensor_fusion, dict):
        fallback_fusion = tool_result.get("multisensor_fusion")
        multisensor_fusion = (
            fallback_fusion if isinstance(fallback_fusion, dict) else {}
        )

    runtime_provenance_input = ee_runtime.get("provenance")
    runtime_provenance = (
        runtime_provenance_input if isinstance(runtime_provenance_input, dict) else {}
    )
    resolved_runtime_provenance = {
        "source": _first_non_empty_value(
            runtime_provenance.get("source"), metadata.get("source"), "earth-engine"
        ),
        "source_dataset": _first_non_empty_value(
            runtime_provenance.get("source_dataset"),
            metadata.get("source_dataset"),
            metadata.get("source"),
        ),
        "source_dataset_detail": _first_non_empty_value(
            runtime_provenance.get("source_dataset_detail"),
            metadata.get("source_dataset_detail"),
            metadata.get("source_detail"),
        ),
        "method": _first_non_empty_value(
            runtime_provenance.get("method"),
            metadata.get("method"),
            "SAR change detection",
        ),
        "threshold_db": _first_non_empty_value(
            runtime_provenance.get("threshold_db"), metadata.get("threshold_db")
        ),
        "updated_at": _first_non_empty_value(
            runtime_provenance.get("updated_at"),
            metadata.get("generated_at"),
            metadata.get("computed_at"),
            metadata.get("updated_at"),
        ),
        "project_id": _first_non_empty_value(
            runtime_provenance.get("project_id"),
            metadata.get("project_id"),
            os.getenv("GCP_PROJECT_ID"),
        ),
        "generated_from": _first_non_empty_value(
            runtime_provenance.get("generated_from"), metadata.get("generated_from")
        ),
        "sidecar_path": _first_non_empty_value(
            runtime_provenance.get("sidecar_path"), metadata.get("sidecar_path")
        ),
    }
    for key, value in runtime_provenance.items():
        if _first_non_empty_value(value) is not None:
            resolved_runtime_provenance[key] = value

    runtime_confidence_input = ee_runtime.get("confidence")
    runtime_confidence = (
        runtime_confidence_input if isinstance(runtime_confidence_input, dict) else {}
    )
    resolved_confidence = {
        "label": _normalize_runtime_confidence_label(
            _first_non_empty_value(
                runtime_confidence.get("label"), metadata.get("confidence"), "UNKNOWN"
            )
        ),
        "score": _first_non_empty_value(
            runtime_confidence.get("score"), metadata.get("confidence_score")
        ),
        "source": _first_non_empty_value(
            runtime_confidence.get("source"), "analysis_metadata"
        ),
    }
    if isinstance(runtime_confidence.get("fusion_summary"), dict):
        resolved_confidence["fusion_summary"] = runtime_confidence.get("fusion_summary")
    elif isinstance(multisensor_fusion.get("aggregate_confidence"), dict):
        resolved_confidence["fusion_summary"] = multisensor_fusion.get(
            "aggregate_confidence"
        )

    runtime_error = ee_runtime.get("error")
    if runtime_error is not None and not isinstance(runtime_error, dict):
        runtime_error = {
            "code": "ee_runtime_descriptor_unavailable",
            "message": str(runtime_error),
        }

    runtime_status = ee_runtime.get("status")
    if not isinstance(runtime_status, str) or not runtime_status:
        runtime_status = "error" if isinstance(runtime_error, dict) else "fallback"

    runtime_mode = ee_runtime.get("runtime_mode")
    if not isinstance(runtime_mode, str) or not runtime_mode:
        runtime_mode = "error" if runtime_status == "error" else "fallback_descriptor"

    resolved_runtime = {
        **ee_runtime,
        "runtime_mode": runtime_mode,
        "status": runtime_status,
        "layers": runtime_layers,
        "temporal_frames": temporal_frames,
        "temporal_playback": temporal_playback,
        "temporal_summary": temporal_summary,
        "provenance": resolved_runtime_provenance,
        "confidence": resolved_confidence,
        "multisensor_fusion": multisensor_fusion,
        "error": runtime_error if isinstance(runtime_error, dict) else None,
    }

    area_sqkm = tool_result.get("area_sqkm")
    if not isinstance(area_sqkm, (int, float)) or isinstance(area_sqkm, bool):
        area_sqkm = 0.0

    growth_rate_pct = tool_result.get("growth_rate_pct")
    if not isinstance(growth_rate_pct, dict):
        growth_rate_pct = {
            "rate_pct_per_hour": 0.0,
            "source": "runtime_fallback",
        }

    return {
        "area_sqkm": area_sqkm,
        "growth_rate_pct": growth_rate_pct,
        "metadata": metadata,
        "ee_runtime": resolved_runtime,
        "runtime_layers": runtime_layers,
        "temporal_frames": temporal_frames,
        "temporal_playback": temporal_playback,
        "temporal_summary": temporal_summary,
        "multisensor_fusion": multisensor_fusion,
    }


def _build_ee_temporal_summary_payload(
    tool_result: dict[str, Any],
    ee_runtime: dict[str, Any],
    temporal_frames: dict[str, Any],
    temporal_summary: dict[str, Any],
) -> dict[str, Any]:
    frame_ids = temporal_summary.get("frame_ids")
    if not isinstance(frame_ids, list) or not frame_ids:
        frame_ids = [
            frame_id
            for frame_id in ("baseline", "event", "change")
            if isinstance(temporal_frames.get(frame_id), dict)
        ]

    latest_frame_id = temporal_summary.get("latest_frame_id")
    if not isinstance(latest_frame_id, str) or latest_frame_id not in temporal_frames:
        latest_frame_id = "change" if "change" in temporal_frames else None
    if latest_frame_id is None and frame_ids:
        latest_frame_id = frame_ids[-1]

    latest_frame = (
        temporal_frames.get(latest_frame_id, {})
        if isinstance(latest_frame_id, str)
        else {}
    )
    if not isinstance(latest_frame, dict):
        latest_frame = {}

    first_frame_id = frame_ids[0] if frame_ids else None
    first_frame = (
        temporal_frames.get(first_frame_id, {})
        if isinstance(first_frame_id, str)
        else {}
    )
    if not isinstance(first_frame, dict):
        first_frame = {}

    runtime_provenance = ee_runtime.get("provenance")
    if not isinstance(runtime_provenance, dict):
        runtime_provenance = {}

    runtime_confidence = ee_runtime.get("confidence")
    if not isinstance(runtime_confidence, dict):
        runtime_confidence = {}

    growth_rate = tool_result.get("growth_rate_pct")
    rate_pct_per_hour = None
    if isinstance(growth_rate, dict):
        rate_pct_per_hour = growth_rate.get("rate_pct_per_hour")

    return {
        "project_id": runtime_provenance.get("project_id")
        or os.getenv("GCP_PROJECT_ID"),
        "runtime_mode": ee_runtime.get("runtime_mode"),
        "source": runtime_provenance.get("source"),
        "confidence_label": runtime_confidence.get("label"),
        "confidence_score": runtime_confidence.get("score"),
        "area_sqkm": tool_result.get("area_sqkm"),
        "growth_rate_pct_per_hour": rate_pct_per_hour,
        "frame_count": temporal_summary.get("frame_count") or len(frame_ids),
        "frame_ids": frame_ids,
        "latest_frame_id": latest_frame.get("frame_id")
        or latest_frame.get("id")
        or latest_frame_id,
        "latest_frame_timestamp": temporal_summary.get("latest_frame_timestamp")
        or latest_frame.get("timestamp"),
        "start_timestamp": temporal_summary.get("start_timestamp")
        or first_frame.get("start_timestamp"),
        "end_timestamp": temporal_summary.get("end_timestamp")
        or latest_frame.get("end_timestamp")
        or latest_frame.get("timestamp"),
        "updated_at": runtime_provenance.get("updated_at"),
    }


def _sync_ee_temporal_summary_to_bigquery(
    summary_payload: dict[str, Any],
) -> dict[str, Any]:
    if not _is_enabled_env_flag(os.getenv("HAWKEYE_ENABLE_EE_TEMPORAL_BQ_SYNC")):
        return {
            "status": "disabled",
            "mode": "env_flag_off",
        }

    project_id = os.getenv("GCP_PROJECT_ID", "").strip()
    if not project_id:
        return {
            "status": "skipped",
            "mode": "missing_project_id",
        }

    try:
        from app.services.bigquery_service import GroundsourceService

        service = GroundsourceService(project_id=project_id)
        return service.sync_ee_temporal_summary(summary_payload)
    except Exception as exc:
        logger.warning("[EE TEMPORAL SYNC] BigQuery sync unavailable: %s", exc)
        return {
            "status": "error",
            "mode": "initialization_error",
            "error": str(exc),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool name → WebSocket event type mapping
# ─────────────────────────────────────────────────────────────────────


def _normalize_incident_log_event_payload(event: dict[str, Any]) -> None:
    message = event.get("message")
    text = event.get("text")

    normalized_message: str | None = None
    for candidate in (message, text):
        if isinstance(candidate, str) and candidate.strip():
            normalized_message = candidate.strip()
            break

    if normalized_message is None:
        return

    event["message"] = normalized_message
    event["text"] = normalized_message


def _normalize_feed_update_event_payload(event: dict[str, Any]) -> None:
    data_payload = event.get("data")
    normalized_data = dict(data_payload) if isinstance(data_payload, dict) else {}

    data_image = normalized_data.get("image")
    image_value: str | None = None
    if isinstance(data_image, str) and data_image:
        image_value = data_image
    else:
        top_level_image = event.get("image")
        if isinstance(top_level_image, str) and top_level_image:
            image_value = top_level_image

    if image_value is not None:
        event["image"] = image_value
        normalized_data["image"] = image_value

    if normalized_data:
        event["data"] = normalized_data


def _normalize_live_event_payload(event: dict[str, Any]) -> None:
    event_type = event.get("type")
    if event_type == "incident_log_entry":
        _normalize_incident_log_event_payload(event)
    elif event_type == "feed_update":
        _normalize_feed_update_event_payload(event)


def _is_route_marked_unsafe(
    safety_rating: str | None,
    route_safety: dict[str, Any] | None,
    route_risk_handling: dict[str, Any] | None,
) -> bool:
    if isinstance(safety_rating, str) and safety_rating.strip().upper() == "UNSAFE":
        return True

    if isinstance(route_safety, dict) and bool(
        route_safety.get("intersects_avoid_area")
    ):
        return True

    if not isinstance(route_risk_handling, dict):
        return False

    if route_risk_handling.get("safe_route_found") is False:
        return True

    status = route_risk_handling.get("status")
    if isinstance(status, str) and status.strip().lower() == "unsafe_route_selected":
        return True

    return False


def _map_tool_result_to_events(tool_name: str, tool_result: dict) -> list[dict]:
    """
    Given the name of a tool and its result dict, produce a list of
    WebSocket JSON events to emit to the frontend.

    Returns an empty list if the tool doesn't need special UI treatment.
    """
    events: list[dict] = []

    if tool_name in GLOBE_CONTROL_TOOLS:
        if not isinstance(tool_result, dict):
            logger.warning(
                "[GLOBE CONTROL] Unexpected non-dict tool result for %s: %s",
                tool_name,
                type(tool_result),
            )
            return events
        events.append({"type": "map_update", **tool_result})
        logger.info(
            "[GLOBE CONTROL] Mapped %s -> map_update action=%s",
            tool_name,
            tool_result.get("action"),
        )
        return events

    if tool_name == "get_flood_extent":
        # The tool now returns a lightweight summary to avoid crashing the
        # Gemini Live API.  Retrieve the full cached payload (with GeoJSON,
        # runtime layers, temporal frames, etc.) for building UI events.
        full = get_last_flood_extent_full()
        if full is not None:
            logger.info("[FLOOD EXTENT] Using cached full payload for UI events")
            tool_result = full
        else:
            logger.warning(
                "[FLOOD EXTENT] No cached full payload — using model summary"
            )

        ee_envelope = _normalize_ee_runtime_envelope(tool_result)
        ee_runtime = ee_envelope["ee_runtime"]
        runtime_layers = ee_envelope["runtime_layers"]
        temporal_frames = ee_envelope["temporal_frames"]
        temporal_playback = ee_envelope["temporal_playback"]
        temporal_summary = ee_envelope["temporal_summary"]
        multisensor_fusion = ee_envelope["multisensor_fusion"]
        runtime_provenance = ee_runtime.get("provenance", {})

        ee_temporal_summary = _build_ee_temporal_summary_payload(
            ee_envelope,
            ee_runtime,
            temporal_frames,
            temporal_summary,
        )
        ee_temporal_sync = _sync_ee_temporal_summary_to_bigquery(ee_temporal_summary)

        geojson = tool_result.get("geojson")
        if geojson:
            map_evt = {
                "type": "map_update",
                "action": "add_overlay",
                "layer": "flood_extent",
                "layerType": "flood",
                "label": "Flood Extent — Ciliwung Basin",
                "style": {
                    "fillColor": "rgba(0, 100, 255, 0.35)",
                    "strokeColor": "#0064ff",
                    "strokeWidth": 2,
                },
                "area_sqkm": ee_envelope["area_sqkm"],
                "growth_rate_pct": ee_envelope["growth_rate_pct"],
                "runtime_layers": runtime_layers,
            }
            # Check payload size — serve via REST if too large
            geojson_str = (
                json.dumps(geojson) if not isinstance(geojson, str) else geojson
            )
            if len(geojson_str) > MAX_INLINE_PAYLOAD_BYTES:
                layer_id = f"flood_extent_{uuid.uuid4().hex[:8]}"
                _geojson_cache[layer_id] = geojson
                map_evt["url"] = f"/api/geojson/{layer_id}"
            else:
                map_evt["geojson"] = geojson
            events.append(map_evt)

        # Also emit an ee_update for Earth Engine panel metrics
        live_analysis_task = tool_result.get("live_analysis_task") or tool_result.get(
            "live_task"
        )
        runtime_task_id = ee_runtime.get("task_id")
        if not isinstance(runtime_task_id, str) or not runtime_task_id.strip():
            runtime_task_id = None
            if isinstance(live_analysis_task, dict):
                task_id_candidate = live_analysis_task.get("task_id")
                if isinstance(task_id_candidate, str) and task_id_candidate.strip():
                    runtime_task_id = task_id_candidate.strip()
        else:
            runtime_task_id = runtime_task_id.strip()

        live_tile_status = ee_runtime.get("live_tile_status")
        if not isinstance(live_tile_status, dict):
            live_tile_status = None

        events.append(
            {
                "type": "ee_update",
                "area_sqkm": ee_envelope["area_sqkm"],
                "growth_rate_pct": ee_envelope["growth_rate_pct"],
                "metadata": ee_envelope["metadata"],
                "ee_runtime": ee_runtime,
                "runtime_layers": runtime_layers,
                "temporal_frames": temporal_frames,
                "temporal_playback": temporal_playback,
                "temporal_summary": temporal_summary,
                "multisensor_fusion": multisensor_fusion,
                "temporal_sync": ee_temporal_sync,
                "temporal_sync_summary": ee_temporal_summary,
                "runtime_provenance": runtime_provenance,
                "runtime_confidence": ee_runtime.get("confidence"),
                "runtime_error": ee_runtime.get("error"),
                "runtime_status": ee_runtime.get("status"),
                "runtime_mode": ee_runtime.get("runtime_mode"),
                "runtime_task_id": runtime_task_id,
                "live_tile_status": live_tile_status,
                "runtime_state": {
                    "status": ee_runtime.get("status"),
                    "mode": ee_runtime.get("runtime_mode"),
                    "error": ee_runtime.get("error"),
                    "task_id": runtime_task_id,
                    "live_tile_status": live_tile_status,
                    "task": live_analysis_task
                    if isinstance(live_analysis_task, dict)
                    else None,
                },
                "live_analysis_task": live_analysis_task
                if isinstance(live_analysis_task, dict)
                else None,
            }
        )

    elif tool_name == "get_flood_hotspots":
        hotspots = tool_result.get("hotspots", [])
        if hotspots:
            max_count = max(
                (int(hotspot.get("flood_count", 0) or 0) for hotspot in hotspots),
                default=0,
            )
            hotspot_markers = []
            for i, hotspot in enumerate(hotspots):
                lat = hotspot.get("grid_lat")
                lng = hotspot.get("grid_lng")
                if lat is None or lng is None:
                    continue

                flood_count = int(hotspot.get("flood_count", 0) or 0)
                intensity = flood_count / max_count if max_count else 0.0
                r = int(255 * intensity)
                hotspot_markers.append(
                    {
                        "id": f"hotspot_{i}",
                        "lat": lat,
                        "lng": lng,
                        "label": f"{flood_count} floods",
                        "type": "hotspot",
                        "color": f"#{r:02x}3030",
                    }
                )

            if hotspot_markers:
                events.append(
                    {
                        "type": "map_update",
                        "action": "add_markers",
                        "layer": "flood_hotspots",
                        "layerType": "marker",
                        "label": "Flood Hotspots",
                        "markers": hotspot_markers,
                    }
                )
                events.append(
                    {
                        "type": "map_update",
                        "action": "fly_to",
                        "lat": hotspot_markers[0]["lat"],
                        "lng": hotspot_markers[0]["lng"],
                        "altitude": 8000,
                        "location_name": "Worst flood hotspot",
                    }
                )

    elif tool_name == "get_infrastructure_vulnerability":
        ranking = tool_result.get("ranking", [])
        vuln_markers = []
        for i, facility in enumerate(ranking[:10]):
            lat = facility.get("latitude")
            lng = facility.get("longitude")
            if lat is None or lng is None:
                continue

            flood_exposure_count = int(facility.get("flood_exposure_count", 0) or 0)
            vuln_markers.append(
                {
                    "id": f"vuln_{i}",
                    "lat": lat,
                    "lng": lng,
                    "label": (
                        f"{facility.get('name', 'Unknown')}: "
                        f"{flood_exposure_count}x exposed"
                    ),
                    "type": str(facility.get("type", "infrastructure")),
                    "color": "#ff0000" if flood_exposure_count > 20 else "#ff8800",
                }
            )

        if vuln_markers:
            events.append(
                {
                    "type": "map_update",
                    "action": "add_markers",
                    "layer": "infrastructure_vulnerability",
                    "layerType": "marker",
                    "label": "Infrastructure Vulnerability",
                    "markers": vuln_markers,
                }
            )

    elif tool_name == "compute_cascade":
        # 1) Status update with population + water level
        first_order = tool_result.get("first_order", {})
        fourth_order = tool_result.get("fourth_order", {})
        events.append(
            {
                "type": "status_update",
                "population": first_order.get("population_at_risk", 0),
                "waterLevel": first_order.get("water_level_delta_m"),
                "hospitals_at_risk": tool_result.get("second_order", {}).get(
                    "hospitals_at_risk", 0
                ),
                "power_stations_at_risk": tool_result.get("third_order", {}).get(
                    "power_stations_at_risk", 0
                ),
                "children_under_5": fourth_order.get("children_under_5", 0),
                "elderly_over_65": fourth_order.get("elderly_over_65", 0),
            }
        )

        top_level_population = tool_result.get("population_at_risk", 0)
        if top_level_population:
            events.append(
                {
                    "type": "status_update",
                    "population": top_level_population,
                    "waterLevel": tool_result.get("water_level", None),
                }
            )

        # 2) Incident log entry with the cascade summary
        events.append(
            {
                "type": "incident_log_entry",
                "severity": "high",
                "category": "cascade_analysis",
                "text": tool_result.get("summary", "Cascade analysis complete"),
                "recommendation": tool_result.get("recommendation", ""),
            }
        )

        # 3) Map update overlay markers for newly-at-risk infrastructure
        second_order = tool_result.get("second_order", {})
        new_hospitals = second_order.get("newly_isolated_hospitals", [])
        hospitals_at_risk = tool_result.get("hospitals_at_risk", [])
        newly_isolated_names: set[str] = set()
        for new_hospital in new_hospitals:
            name = (
                new_hospital.get("name")
                if isinstance(new_hospital, dict)
                else new_hospital
            )
            if isinstance(name, str) and name.strip():
                newly_isolated_names.add(name.strip().casefold())

        newly_isolated_markers = []
        cascade_hospital_markers = []
        for i, hospital in enumerate(hospitals_at_risk):
            if not isinstance(hospital, dict):
                continue
            try:
                lat = float(hospital.get("latitude"))
                lng = float(hospital.get("longitude"))
            except (TypeError, ValueError):
                continue
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                continue
            hospital_name = str(hospital.get("name", "Hospital")).strip() or "Hospital"
            cascade_hospital_markers.append(
                {
                    "id": f"cascade_hosp_{i}",
                    "lat": lat,
                    "lng": lng,
                    "label": f"⚠ {hospital_name} — AT RISK",
                    "type": "hospital",
                    "color": "#ff0000",
                }
            )
            if hospital_name.casefold() in newly_isolated_names:
                newly_isolated_markers.append(
                    {
                        "id": f"cascade_new_hosp_{i}",
                        "lat": lat,
                        "lng": lng,
                        "label": f"⚠ {hospital_name} — NEWLY ISOLATED",
                        "type": "hospital",
                        "color": "#ff4444",
                    }
                )
        if newly_isolated_markers:
            events.append(
                {
                    "type": "map_update",
                    "action": "add_markers",
                    "layer": "cascade_infrastructure",
                    "layerType": "marker",
                    "label": "Newly At-Risk Infrastructure",
                    "markers": newly_isolated_markers,
                }
            )
        if cascade_hospital_markers:
            events.append(
                {
                    "type": "map_update",
                    "action": "add_markers",
                    "layer": "cascade_hospitals_at_risk",
                    "layerType": "marker",
                    "label": "Hospitals At Risk",
                    "markers": cascade_hospital_markers,
                }
            )

    elif tool_name == "get_infrastructure_at_risk":
        # Emit markers for all infrastructure at risk
        markers = []
        for infra_type in (
            "hospitals",
            "schools",
            "shelters",
            "power_stations",
            "water_treatment",
        ):
            items = tool_result.get(infra_type, [])
            color_map = {
                "hospitals": "#ff4444",
                "schools": "#ff8800",
                "shelters": "#00cc44",
                "power_stations": "#ffcc00",
                "water_treatment": "#00aaff",
            }
            for item in items:
                markers.append(
                    {
                        "lat": item.get("latitude"),
                        "lng": item.get("longitude"),
                        "label": item.get("name", "Unknown"),
                        "type": infra_type,
                        "color": color_map.get(infra_type, "#ffffff"),
                    }
                )
        if markers:
            events.append(
                {
                    "type": "map_update",
                    "action": "add_markers",
                    "layer": "infrastructure_at_risk",
                    "layerType": "marker",
                    "label": f"Infrastructure At Risk ({tool_result.get('total_at_risk', 0)} facilities)",
                    "markers": markers,
                }
            )

    elif tool_name == "generate_evacuation_route":
        route_geojson = tool_result.get("route_geojson")
        route_safety = (
            tool_result.get("route_safety")
            if isinstance(tool_result.get("route_safety"), dict)
            else None
        )
        route_risk_handling = (
            tool_result.get("route_risk_handling")
            if isinstance(tool_result.get("route_risk_handling"), dict)
            else None
        )
        safety_rating = tool_result.get("safety_rating")
        if safety_rating is None and route_safety is not None:
            safety_rating = route_safety.get("safety_rating")
        if isinstance(safety_rating, str):
            safety_rating = safety_rating.strip().upper() or "UNKNOWN"
        elif safety_rating is not None:
            safety_rating = str(safety_rating).strip().upper() or "UNKNOWN"

        route_is_unsafe = _is_route_marked_unsafe(
            safety_rating=safety_rating,
            route_safety=route_safety,
            route_risk_handling=route_risk_handling,
        )

        if route_geojson and not tool_result.get("error"):
            route_style: dict[str, Any] = {
                "strokeColor": "#00ff88",
                "strokeWidth": 4,
                "dashPattern": [10, 5],
            }
            route_label = "Evacuation Route"
            if route_is_unsafe:
                route_style = {
                    "strokeColor": "#ff3b30",
                    "strokeWidth": 5,
                    "dashPattern": [6, 3],
                }
                route_label = "Evacuation Route — UNSAFE"
            elif safety_rating == "CAUTION":
                route_style = {
                    "strokeColor": "#ff9900",
                    "strokeWidth": 4,
                    "dashPattern": [8, 4],
                }
                route_label = "Evacuation Route — CAUTION"

            route_options_payload: list[dict[str, Any]] = []
            route_options_raw = tool_result.get("route_options")
            if isinstance(route_options_raw, list):
                for idx, option in enumerate(route_options_raw[:4]):
                    if not isinstance(option, dict):
                        continue
                    destination_payload = (
                        option.get("destination")
                        if isinstance(option.get("destination"), dict)
                        else {}
                    )
                    option_evt: dict[str, Any] = {
                        "index": idx,
                        "distance_m": option.get("distance_m"),
                        "duration_minutes": option.get("duration_minutes"),
                        "safety_rating": option.get("safety_rating"),
                        "direct_distance_m": option.get("direct_distance_m"),
                        "destination": destination_payload,
                    }
                    option_route_safety = option.get("route_safety")
                    option_route_risk = option.get("route_risk_handling")
                    if isinstance(option_route_safety, dict):
                        option_evt["route_safety"] = option_route_safety
                    if isinstance(option_route_risk, dict):
                        option_evt["route_risk_handling"] = option_route_risk

                    option_geojson = option.get("route_geojson")
                    if option_geojson:
                        option_json_str = (
                            json.dumps(option_geojson)
                            if not isinstance(option_geojson, str)
                            else option_geojson
                        )
                        if len(option_json_str) > MAX_INLINE_PAYLOAD_BYTES:
                            option_layer_id = f"evac_route_opt_{uuid.uuid4().hex[:8]}"
                            _geojson_cache[option_layer_id] = option_geojson
                            option_evt["route_url"] = f"/api/geojson/{option_layer_id}"
                        else:
                            option_evt["route_geojson"] = option_geojson
                    route_options_payload.append(option_evt)

            evacuation_zone_payload = None
            evacuation_zone_geojson = tool_result.get("evacuation_zone_geojson")
            if evacuation_zone_geojson:
                evacuation_zone_payload = {}
                evacuation_zone_json = (
                    json.dumps(evacuation_zone_geojson)
                    if not isinstance(evacuation_zone_geojson, str)
                    else evacuation_zone_geojson
                )
                if len(evacuation_zone_json) > MAX_INLINE_PAYLOAD_BYTES:
                    zone_layer_id = f"evac_zone_{uuid.uuid4().hex[:8]}"
                    _geojson_cache[zone_layer_id] = evacuation_zone_geojson
                    evacuation_zone_payload["url"] = f"/api/geojson/{zone_layer_id}"
                else:
                    evacuation_zone_payload["geojson"] = evacuation_zone_geojson

            route_evt = {
                "type": "map_update",
                "action": "add_overlay",
                "layer": "evacuation_route",
                "layerType": "route",
                "label": route_label,
                "style": route_style,
                "distance_m": tool_result.get("distance_m"),
                "duration_minutes": tool_result.get("duration_minutes"),
                "safety_rating": safety_rating,
            }
            if route_safety is not None:
                route_evt["route_safety"] = route_safety
            if route_risk_handling is not None:
                route_evt["route_risk_handling"] = route_risk_handling
            if isinstance(tool_result.get("origin"), dict):
                route_evt["origin"] = tool_result["origin"]
            if isinstance(tool_result.get("destination"), dict):
                route_evt["destination"] = tool_result["destination"]
            if route_options_payload:
                route_evt["route_options"] = route_options_payload
            if isinstance(evacuation_zone_payload, dict):
                if "geojson" in evacuation_zone_payload:
                    route_evt["evacuation_zone_geojson"] = evacuation_zone_payload[
                        "geojson"
                    ]
                if "url" in evacuation_zone_payload:
                    route_evt["evacuation_zone_url"] = evacuation_zone_payload["url"]
                route_evt["evacuation_zone_radius_m"] = tool_result.get(
                    "evacuation_zone_radius_m"
                )
            if tool_result.get("destination_mode"):
                route_evt["destination_mode"] = tool_result.get("destination_mode")
            if tool_result.get("alternate_count") is not None:
                route_evt["alternate_count"] = tool_result.get("alternate_count")
            if tool_result.get("candidate_shelter_count") is not None:
                route_evt["candidate_shelter_count"] = tool_result.get(
                    "candidate_shelter_count"
                )

            route_json_str = (
                json.dumps(route_geojson)
                if not isinstance(route_geojson, str)
                else route_geojson
            )
            if len(route_json_str) > MAX_INLINE_PAYLOAD_BYTES:
                layer_id = f"evac_route_{uuid.uuid4().hex[:8]}"
                _geojson_cache[layer_id] = route_geojson
                route_evt["url"] = f"/api/geojson/{layer_id}"
            else:
                route_evt["geojson"] = route_geojson
            events.append(route_evt)

            if route_is_unsafe:
                intersection_pct = (
                    route_safety.get("intersection_pct")
                    if isinstance(route_safety, dict)
                    else None
                )
                warning_message = (
                    "Evacuation route intersects active flood zones. "
                    "Treat this route as unsafe and request an alternative."
                )
                if isinstance(intersection_pct, (int, float)):
                    warning_message = (
                        "Evacuation route is unsafe "
                        f"({intersection_pct:.1f}% intersects flood zones). "
                        "Request an alternative route."
                    )
                events.append(
                    {
                        "type": "incident_log_entry",
                        "severity": "high",
                        "category": "route_safety",
                        "message": warning_message,
                        "text": warning_message,
                        "safety_rating": safety_rating,
                    }
                )
        else:
            error_message = tool_result.get("error")
            if isinstance(error_message, str) and error_message.strip():
                message = f"Evacuation route unavailable: {error_message.strip()}"
            else:
                message = "Evacuation route unavailable: no route geometry returned."
            events.append({"type": "error", "message": message})

    elif tool_name == "generate_risk_projection":
        image_b64 = tool_result.get("projection_image_base64")
        events.append(
            {
                "type": "feed_update",
                "mode": "PREDICTION",
                "image": image_b64,
                "scenario": tool_result.get("scenario"),
                "confidence": tool_result.get("confidence"),
                "confidence_color": tool_result.get("confidence_color"),
                "description": tool_result.get("description"),
            }
        )

    elif tool_name == "send_emergency_alert":
        events.append(
            {
                "type": "incident_log_entry",
                "severity": "high",
                "category": "emergency_alert",
                "text": f"Emergency alert sent to {tool_result.get('recipient', 'N/A')}: {tool_result.get('subject', '')}",
                "delivery_method": tool_result.get("delivery_method"),
                "timestamp": tool_result.get("timestamp"),
            }
        )

    elif tool_name == "generate_incident_summary":
        events.append(
            {
                "type": "incident_log_entry",
                "severity": "info",
                "category": "incident_summary",
                "text": tool_result.get("summary_text", "Summary unavailable"),
                "event_count": tool_result.get("event_count", 0),
            }
        )

    elif tool_name == "log_incident":
        events.append(
            {
                "type": "incident_log_entry",
                "severity": tool_result.get("severity", "info"),
                "category": "incident_log",
                "text": f"Incident logged: {tool_result.get('event_type', 'unknown')}",
                "timestamp": tool_result.get("timestamp"),
            }
        )

    elif tool_name == "query_historical_floods":
        freq = tool_result.get("frequency", {})
        events.append(
            {
                "type": "ee_update",
                "historical_events": freq.get("total_events"),
                "avg_duration_days": freq.get("avg_duration_days"),
                "max_duration_days": freq.get("max_duration_days"),
                "summary": tool_result.get("summary"),
            }
        )
        query_lat = tool_result.get("query_lat")
        query_lng = tool_result.get("query_lng")
        if query_lat is not None and query_lng is not None:
            events.append(
                {
                    "type": "map_update",
                    "action": "fly_to",
                    "lat": query_lat,
                    "lng": query_lng,
                    "altitude": 5000,
                    "location_name": "Query area",
                }
            )

    elif tool_name == "evaluate_route_safety":
        # Emit danger zone markers if route is unsafe
        if not tool_result.get("is_safe", True):
            danger_markers = []
            for dz in tool_result.get("danger_zones", []):
                danger_markers.append(
                    {
                        "lat": dz.get("lat"),
                        "lng": dz.get("lng"),
                        "label": dz.get("reason", "Danger Zone"),
                        "type": "danger",
                        "color": "#ff0000",
                    }
                )
            if danger_markers:
                events.append(
                    {
                        "type": "map_update",
                        "action": "add_markers",
                        "layer": "route_danger_zones",
                        "layerType": "danger",
                        "label": f"Route Danger Zones (Safety: {tool_result.get('safety_rating', 'UNKNOWN')})",
                        "markers": danger_markers,
                    }
                )

    grounding_update = _build_grounding_update_event(tool_name, tool_result)
    if grounding_update is not None:
        events.append(grounding_update)

    for event in events:
        if isinstance(event, dict):
            _normalize_live_event_payload(event)

    return events


# ─────────────────────────────────────────────────────────────────────
# App Lifespan
# ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize ADK services on startup."""
    global session_service, artifact_service, runner

    logger.info("Initializing ADK services...")
    _load_runtime_environment()

    _ensure_google_api_key_from_fallback(log_context="app startup")

    # Initialize session and artifact services
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()

    # Create the runner with our root agent
    runner = Runner(
        agent=root_agent,
        app_name="hawkeye",
        session_service=session_service,
        artifact_service=artifact_service,
    )

    logger.info(f"ADK Runner initialized with agent: {root_agent.name}")

    yield

    # Cleanup on shutdown
    logger.info("Shutting down ADK services...")


app = FastAPI(
    title="HawkEye Backend",
    description="ADK-based bidi-streaming backend for HawkEye disaster response agent",
    lifespan=lifespan,
)

# CORS middleware for frontend connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────
# REST Endpoints
# ─────────────────────────────────────────────────────────────────────


def _get_earth_engine_runtime_service():
    return get_earth_engine_service()


@app.get("/health")
def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "agent": root_agent.name if root_agent else None,
        "runner_initialized": runner is not None,
    }


@app.get("/api/geojson/{layer_id}")
def get_geojson(layer_id: str):
    """
    Serve large GeoJSON payloads that were too big for inline WebSocket.
    The frontend fetches this URL after receiving a map_update with a 'url' field.
    """
    data = _geojson_cache.get(layer_id)
    if data is None:
        return JSONResponse(status_code=404, content={"error": "Layer not found"})
    return JSONResponse(content=data)


@app.get("/api/earth-engine/tiles/offline/{layer_id}/{z}/{x}/{y}.png")
def get_offline_earth_engine_tile(
    layer_id: str,
    z: int = FastAPIPath(..., ge=0, le=EE_TILE_MAX_ZOOM),
    x: int = FastAPIPath(..., ge=0),
    y: int = FastAPIPath(..., ge=0),
):
    """Serve deterministic placeholder PNG tiles for offline Earth Engine layers."""
    if not _is_safe_ee_tile_layer_id(layer_id):
        return JSONResponse(status_code=400, content={"error": "Invalid layer id"})

    max_tile_index = (1 << z) - 1
    if x > max_tile_index or y > max_tile_index:
        return JSONResponse(
            status_code=400,
            content={"error": "Tile coordinates are out of range for zoom level"},
        )

    return Response(
        content=EE_TILE_PLACEHOLDER_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": EE_TILE_CACHE_CONTROL,
            "ETag": EE_TILE_PLACEHOLDER_ETAG,
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/earth-engine/tiles/live/{tile_handle}/{z}/{x}/{y}.png")
def get_live_earth_engine_tile(
    tile_handle: str,
    z: int = FastAPIPath(..., ge=0, le=EE_TILE_MAX_ZOOM),
    x: int = FastAPIPath(..., ge=0),
    y: int = FastAPIPath(..., ge=0),
):
    """Proxy live Earth Engine map tiles resolved from runtime task descriptors."""
    if not _is_safe_ee_tile_layer_id(tile_handle):
        return JSONResponse(status_code=400, content={"error": "Invalid tile handle"})

    max_tile_index = (1 << z) - 1
    if x > max_tile_index or y > max_tile_index:
        return JSONResponse(
            status_code=400,
            content={"error": "Tile coordinates are out of range for zoom level"},
        )

    try:
        service = _get_earth_engine_runtime_service()
        tile_result = service.fetch_live_tile(tile_handle, z=z, x=x, y=y)
    except Exception as exc:
        logger.warning(
            "[EE LIVE TILE] Failed to resolve tile handle %s: %s", tile_handle, exc
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Failed to resolve live tile descriptor",
                "detail": str(exc),
            },
        )

    status = tile_result.get("status")
    if status == "not_found":
        return JSONResponse(
            status_code=404,
            content={
                "error": tile_result.get("error", {}).get(
                    "message", "Live tile not found"
                ),
            },
        )
    if status != "ok":
        return JSONResponse(
            status_code=int(tile_result.get("http_status") or 502),
            content={
                "error": tile_result.get("error", {}).get(
                    "message", "Live tile fetch failed"
                ),
            },
        )

    tile_content = tile_result.get("content")
    if not isinstance(tile_content, bytes):
        return JSONResponse(
            status_code=502,
            content={"error": "Live tile fetch returned invalid content payload"},
        )

    response_headers = {
        "Cache-Control": tile_result.get("cache_control", "public, max-age=120"),
        "X-Content-Type-Options": "nosniff",
    }
    etag = tile_result.get("etag")
    if isinstance(etag, str) and etag:
        response_headers["ETag"] = etag

    return Response(
        content=tile_content,
        media_type=tile_result.get("content_type", "image/png"),
        headers=response_headers,
    )


@app.post("/api/earth-engine/live-analysis")
def run_live_earth_engine_analysis(request_payload: dict[str, Any] | None = None):
    """Submit and execute a live EE runtime analysis task."""
    service = _get_earth_engine_runtime_service()
    result = service.run_live_analysis_task(
        request=request_payload if isinstance(request_payload, dict) else {}
    )
    return result


@app.get("/api/earth-engine/live-analysis/latest")
def get_latest_live_earth_engine_analysis():
    """Return latest live EE task status (and result if available)."""
    service = _get_earth_engine_runtime_service()
    status = service.get_latest_live_analysis_task_status()
    if not isinstance(status, dict):
        return JSONResponse(
            status_code=404,
            content={"error": "No live analysis task has been submitted."},
        )

    task_id = status.get("task_id")
    if isinstance(task_id, str) and task_id:
        result = service.get_live_analysis_task_result(task_id)
        if result is not None:
            status = {**status, "result": result}
    return status


@app.get("/api/earth-engine/live-analysis/{task_id}")
def get_live_earth_engine_analysis_status(task_id: str):
    """Return status for a specific live EE analysis task."""
    service = _get_earth_engine_runtime_service()
    status = service.get_live_analysis_task_status(task_id)
    error_payload = status.get("error")
    if (
        isinstance(error_payload, dict)
        and error_payload.get("code") == "live_analysis_task_not_found"
    ):
        return JSONResponse(status_code=404, content=status)
    return status


@app.get("/api/earth-engine/live-analysis/{task_id}/result")
def get_live_earth_engine_analysis_result(task_id: str):
    """Return runtime payload result for a completed live EE analysis task."""
    service = _get_earth_engine_runtime_service()
    result = service.get_live_analysis_task_result(task_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No completed result found for task '{task_id}'."},
        )
    return result


@app.post("/api/seed-water-level")
def seed_water_level(level: float = 4.1):
    """
    Seed Firestore with a water level for demo purposes.
    Call this before the demo to trigger the proactive alert.
    """
    try:
        from app.services.firestore_service import IncidentService

        project_id = os.getenv("GCP_PROJECT_ID", "")
        incident_svc = IncidentService(project_id)
        incident_svc.set_water_level(level)
        return {"status": "ok", "water_level_m": level}
    except Exception as e:
        logger.error(f"Failed to seed water level: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/infrastructure")
def get_infrastructure():
    """
    Return infrastructure facilities (hospitals, schools, shelters, power stations)
    from BigQuery via GroundsourceService.  The frontend uses this as a live-data
    upgrade over its bundled static fallback.
    """
    try:
        from app.services.bigquery_service import GroundsourceService

        project_id = os.getenv("GCP_PROJECT_ID", "")
        svc = GroundsourceService(project_id=project_id)

        # Use Jakarta metro bounding box as a broad GeoJSON polygon
        jakarta_bbox_geojson = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [106.65, -6.40],
                        [107.05, -6.40],
                        [107.05, -6.08],
                        [106.65, -6.08],
                        [106.65, -6.40],
                    ]
                ],
            }
        )
        logger.info(
            "[API] /api/infrastructure — querying BigQuery for Jakarta metro infrastructure"
        )
        result = svc.get_infrastructure_at_risk(jakarta_bbox_geojson)
        logger.info(
            f"[API] /api/infrastructure — returned {result.get('total_at_risk', 0)} facilities"
        )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"[API] /api/infrastructure — ERROR: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─────────────────────────────────────────────────────────────────────
# Location-Based Analytics (camera-driven BigQuery pipeline)
# ─────────────────────────────────────────────────────────────────────

import math as _math
import threading as _threading
import pathlib as _pathlib

_JAKARTA_CENTER_LAT = -6.2
_JAKARTA_CENTER_LNG = 106.85
_JAKARTA_PROXIMITY_KM = 50.0
_location_analytics_lock = _threading.Lock()

# ── File-backed cache: survives server restarts / StatReload ──
_ANALYTICS_CACHE_PATH = (
    _pathlib.Path(__file__).resolve().parent.parent / "data" / "_analytics_cache.json"
)


def _load_analytics_cache_from_disk() -> dict:
    """Load previously cached analytics from disk if available."""
    try:
        if _ANALYTICS_CACHE_PATH.exists():
            with open(_ANALYTICS_CACHE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "jakarta" in data:
                logger.info(
                    "[CACHE] Loaded analytics from disk: %s", _ANALYTICS_CACHE_PATH
                )
                return data
    except Exception as exc:
        logger.warning("[CACHE] Failed to load disk cache: %s", exc)
    return {}


def _save_analytics_cache_to_disk(cache: dict) -> None:
    """Persist analytics cache to disk for survival across restarts."""
    try:
        _ANALYTICS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_ANALYTICS_CACHE_PATH, "w") as f:
            json.dump(cache, f)
        logger.info("[CACHE] Wrote analytics to disk: %s", _ANALYTICS_CACHE_PATH)
    except Exception as exc:
        logger.warning("[CACHE] Failed to write disk cache: %s", exc)


_location_analytics_cache: dict = _load_analytics_cache_from_disk()


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two lat/lng points."""
    r = 6371.0
    d_lat = _math.radians(lat2 - lat1)
    d_lng = _math.radians(lng2 - lng1)
    a = (
        _math.sin(d_lat / 2) ** 2
        + _math.cos(_math.radians(lat1))
        * _math.cos(_math.radians(lat2))
        * _math.sin(d_lng / 2) ** 2
    )
    return r * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


def _build_jakarta_analytics() -> dict:
    """Query BigQuery for all chart-panel analytics for the Jakarta region.

    Returns a dict with keys: flood_analytics, cascade_analysis,
    vulnerability_ranking — matching the shapes expected by the frontend
    AnalyticsDashboard component.
    """
    from app.services.bigquery_service import GroundsourceService

    project_id = os.getenv("GCP_PROJECT_ID", "")
    svc = GroundsourceService(project_id=project_id)

    # Jakarta metro bounding box (same as /api/infrastructure)
    jakarta_bbox_geojson = json.dumps(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [106.65, -6.40],
                    [107.05, -6.40],
                    [107.05, -6.08],
                    [106.65, -6.08],
                    [106.65, -6.40],
                ]
            ],
        }
    )

    # 1) Flood analytics — monthly + yearly + summary
    monthly = svc.get_monthly_frequency()
    yearly = svc.get_yearly_trend()
    freq = svc.get_flood_frequency(
        lat=_JAKARTA_CENTER_LAT,
        lng=_JAKARTA_CENTER_LNG,
        radius_km=25.0,
    )

    flood_analytics = {
        "monthly_frequency": monthly,
        "yearly_trend": yearly,
        "total_events": freq.get("total_events", 0),
        "avg_duration_days": freq.get("avg_duration_days", 0),
    }

    # 2) Infrastructure at risk (current flood extent)
    infra_current = svc.get_infrastructure_at_risk(jakarta_bbox_geojson)
    current_hospitals = len(infra_current.get("hospitals", []))
    current_schools = len(infra_current.get("schools", []))
    current_power = len(infra_current.get("power_stations", []))
    current_shelters = len(infra_current.get("shelters", []))

    # 3) Infrastructure at expanded flood level (+500m buffer)
    infra_expanded = svc.get_infrastructure_at_expanded_level(
        jakarta_bbox_geojson, buffer_meters=500
    )
    expanded_hospitals = current_hospitals + len(infra_expanded.get("hospitals", []))
    expanded_schools = current_schools + len(infra_expanded.get("schools", []))
    expanded_power = current_power + len(infra_expanded.get("power_stations", []))
    expanded_shelters = current_shelters + len(infra_expanded.get("shelters", []))

    # Population heuristic — 47,000 base from demo status bar
    pop_at_risk = 47000

    cascade_analysis = {
        "first_order": {
            "description": "Direct flood inundation across Jakarta metro",
            "population_at_risk": pop_at_risk,
            "flood_area_expanded": True,
            "water_level_delta_m": 2.0,
        },
        "second_order": {
            "description": "Healthcare & education disruption",
            "hospitals_at_risk": current_hospitals,
            "hospital_names": [
                h.get("name", "Unknown") for h in infra_current.get("hospitals", [])[:5]
            ],
            "schools_at_risk": current_schools,
            "newly_isolated_hospitals": [
                h.get("name", "Unknown")
                for h in infra_expanded.get("hospitals", [])[:3]
            ],
        },
        "third_order": {
            "description": "Power grid & utility failures",
            "power_stations_at_risk": current_power,
            "power_station_names": [
                p.get("name", "Unknown")
                for p in infra_current.get("power_stations", [])[:5]
            ],
            "estimated_residents_without_power": current_power * 15000,
        },
        "fourth_order": {
            "description": "Vulnerable population exposure",
            "children_under_5": int(pop_at_risk * 0.08),
            "elderly_over_65": int(pop_at_risk * 0.06),
            "hospital_patients_needing_evac": current_hospitals * 120,
        },
        "infrastructure_current": {
            "hospitals": current_hospitals,
            "schools": current_schools,
            "power_stations": current_power,
            "shelters": current_shelters,
        },
        "infrastructure_expanded": {
            "hospitals": expanded_hospitals,
            "schools": expanded_schools,
            "power_stations": expanded_power,
            "shelters": expanded_shelters,
        },
        "population_at_risk": pop_at_risk,
    }

    # 4) Vulnerability ranking
    ranking_raw = svc.get_infrastructure_exposure_ranking()
    vulnerability_ranking = {
        "ranking": ranking_raw,
        "total_ranked": len(ranking_raw),
    }

    return {
        "flood_analytics": flood_analytics,
        "cascade_analysis": cascade_analysis,
        "vulnerability_ranking": vulnerability_ranking,
    }


@app.get("/api/location-analytics")
def get_location_analytics(lat: float = 0.0, lng: float = 0.0):
    """Return pre-computed BigQuery analytics for the camera's lat/lng.

    If the camera is within ~50 km of Jakarta, returns full analytics.
    Otherwise returns an empty payload so the frontend clears charts.
    Results are cached after the first successful query.
    """
    dist = _haversine_km(lat, lng, _JAKARTA_CENTER_LAT, _JAKARTA_CENTER_LNG)
    logger.info(
        "[API] /api/location-analytics — lat=%.4f lng=%.4f dist_jakarta=%.1fkm",
        lat,
        lng,
        dist,
    )

    if dist > _JAKARTA_PROXIMITY_KM:
        return JSONResponse(content={"region": None, "data": {}})

    # Return cached result if available (fast path, no lock needed)
    if "jakarta" in _location_analytics_cache:
        logger.info(
            "[API] /api/location-analytics — returning cached Jakarta analytics"
        )
        return JSONResponse(
            content={
                "region": "jakarta",
                "data": _location_analytics_cache["jakarta"],
            }
        )

    # Serialize BigQuery queries so concurrent requests don't duplicate work
    with _location_analytics_lock:
        # Double-check cache inside lock (another thread may have populated it)
        if "jakarta" in _location_analytics_cache:
            return JSONResponse(
                content={
                    "region": "jakarta",
                    "data": _location_analytics_cache["jakarta"],
                }
            )

        try:
            analytics = _build_jakarta_analytics()
            _location_analytics_cache["jakarta"] = analytics
            _save_analytics_cache_to_disk(_location_analytics_cache)
            logger.info(
                "[API] /api/location-analytics — built & cached Jakarta analytics"
            )
            return JSONResponse(content={"region": "jakarta", "data": analytics})
        except Exception as e:
            logger.error("[API] /api/location-analytics — ERROR: %s", e)
            return JSONResponse(status_code=500, content={"error": str(e)})


# ─────────────────────────────────────────────────────────────────────
# Point-Click Analytics (click-to-query on Cesium globe)
# ─────────────────────────────────────────────────────────────────────

_point_analytics_cache: dict[str, dict] = {}


def _point_cache_key(lat: float, lng: float) -> str:
    """Grid-cell key at ~1 km resolution for deduplication."""
    return f"{round(lat / 0.02) * 0.02:.4f},{round(lng / 0.02) * 0.02:.4f}"


def _build_point_bbox_geojson(lat: float, lng: float, radius_km: float) -> str:
    """Build a small square bounding-box GeoJSON polygon around a point."""
    # ~0.009 degrees latitude ≈ 1 km
    dlat = radius_km * 0.009
    # longitude degrees per km varies with latitude
    dlng = radius_km * 0.009 / max(_math.cos(_math.radians(lat)), 0.01)
    return json.dumps(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [lng - dlng, lat - dlat],
                    [lng + dlng, lat - dlat],
                    [lng + dlng, lat + dlat],
                    [lng - dlng, lat + dlat],
                    [lng - dlng, lat - dlat],
                ]
            ],
        }
    )


@app.get("/api/point-analytics")
def get_point_analytics(lat: float = 0.0, lng: float = 0.0, radius_km: float = 3.0):
    """Return point-specific BigQuery analytics for a clicked map location.

    Queries flood frequency within radius_km and infrastructure within a small
    bounding box.  Results are grid-cell cached (~1 km resolution) to avoid
    duplicate BigQuery queries for nearby clicks.
    """
    # Reject points far from Jakarta
    dist = _haversine_km(lat, lng, _JAKARTA_CENTER_LAT, _JAKARTA_CENTER_LNG)
    if dist > _JAKARTA_PROXIMITY_KM:
        return JSONResponse(
            content={
                "region": None,
                "point": {"lat": lat, "lng": lng, "radius_km": radius_km},
                "data": None,
            }
        )

    logger.info(
        "[API] /api/point-analytics — lat=%.4f lng=%.4f radius=%.1fkm",
        lat,
        lng,
        radius_km,
    )

    cache_key = _point_cache_key(lat, lng)

    # Fast-path: return cached result
    if cache_key in _point_analytics_cache:
        logger.info("[API] /api/point-analytics — cache hit key=%s", cache_key)
        return JSONResponse(
            content={
                "region": "jakarta",
                "point": {"lat": lat, "lng": lng, "radius_km": radius_km},
                "data": _point_analytics_cache[cache_key],
            }
        )

    try:
        from app.services.bigquery_service import GroundsourceService

        project_id = os.getenv("GCP_PROJECT_ID", "")
        svc = GroundsourceService(project_id=project_id)

        # 1) Flood frequency within radius
        flood_freq = svc.get_flood_frequency(lat=lat, lng=lng, radius_km=radius_km)

        # Sanitize date/datetime objects → ISO strings for JSON serialization
        import datetime as _dt

        for _k, _v in list(flood_freq.items()):
            if isinstance(_v, (_dt.date, _dt.datetime)):
                flood_freq[_k] = _v.isoformat()

        # 2) Infrastructure within small bbox
        bbox_geojson = _build_point_bbox_geojson(lat, lng, radius_km)
        infra = svc.get_infrastructure_at_risk(bbox_geojson)

        # Derive risk level from event count
        total_events = flood_freq.get("total_events", 0)
        if total_events >= 10:
            risk_level = "CRITICAL"
        elif total_events >= 5:
            risk_level = "HIGH"
        elif total_events >= 2:
            risk_level = "MODERATE"
        elif total_events >= 1:
            risk_level = "LOW"
        else:
            risk_level = "MINIMAL"

        result = {
            "flood_frequency": flood_freq,
            "infrastructure": {
                "total": infra.get("total_at_risk", 0),
                "hospitals": len(infra.get("hospitals", [])),
                "schools": len(infra.get("schools", [])),
                "power_stations": len(infra.get("power_stations", [])),
                "shelters": len(infra.get("shelters", [])),
                "hospital_names": [
                    h.get("name", "Unknown") for h in infra.get("hospitals", [])[:3]
                ],
                "school_names": [
                    s.get("name", "Unknown") for s in infra.get("schools", [])[:3]
                ],
            },
            "risk_level": risk_level,
        }

        _point_analytics_cache[cache_key] = result
        logger.info(
            "[API] /api/point-analytics — built result: %d events, %d infra, risk=%s",
            total_events,
            infra.get("total_at_risk", 0),
            risk_level,
        )
        return JSONResponse(
            content={
                "region": "jakarta",
                "point": {"lat": lat, "lng": lng, "radius_km": radius_km},
                "data": result,
            }
        )
    except Exception as e:
        logger.error("[API] /api/point-analytics — ERROR: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─────────────────────────────────────────────────────────────────────
# WebSocket Endpoint
# ─────────────────────────────────────────────────────────────────────


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, session_id: str):
    """
    WebSocket endpoint for bidi-streaming with Gemini Live API.

    Flow:
    1. Client connects with user_id and session_id
    2. Upstream task receives audio/video/text from browser → LiveRequestQueue
    3. Downstream task receives events from runner.run_live() → browser
    4. Proactive monitoring task checks Firestore water level → injects alerts
    5. All tasks run concurrently until disconnect
    """
    await websocket.accept()
    logger.info(f"WebSocket connected: user={user_id}, session={session_id}")
    session_metrics = _WebSocketSessionMetrics(user_id=user_id, session_id=session_id)
    session_metrics.increment("connections_opened")

    # Shared state for coordination
    disconnect_event = asyncio.Event()

    # ── Evict any existing connection for this session ──
    # Prevents concurrent runner.run_live() calls that cause 1007 errors.
    async with _active_connections_lock:
        old_disconnect = _active_connections.get(session_id)
        if old_disconnect is not None:
            logger.warning(
                "[SINGLETON] Evicting previous connection for session %s",
                session_id,
            )
            session_metrics.increment("session_reconnect_evictions")
            old_disconnect.set()
            # Brief yield to let the old tasks notice the event
            await asyncio.sleep(0.3)
        _active_connections[session_id] = disconnect_event

    _clear_globe_turn_state(session_id)
    _clear_pending_long_running_tools(session_id)

    try:
        session, lifecycle_status = await _prepare_live_session(
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as exc:
        session_metrics.increment("session_prepare_failures")
        session_metrics.set_outcome("session_prepare_failed", override=True)
        logger.error(
            "[SESSION LIFECYCLE] Failed to prepare session %s: %s",
            session_id,
            exc,
        )
        async with _active_connections_lock:
            if _active_connections.get(session_id) is disconnect_event:
                _active_connections.pop(session_id, None)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": "Failed to initialize live session.",
                    }
                )
            )
            session_metrics.mark_downstream_payload("text")
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
        session_metrics.emit_summary()
        return

    session_metrics.record_lifecycle_status(lifecycle_status)
    _log_session_lifecycle(
        user_id=user_id,
        session_id=session_id,
        lifecycle_status=lifecycle_status,
    )
    await _emit_session_lifecycle_status(websocket, session, lifecycle_status)

    # Create RunConfig for Live API
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],  # Native audio output
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
            )
        ),
        # Transcription configs required for native-audio models (per bidi-demo)
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Session resumption for handling 10-minute limits
        session_resumption=types.SessionResumptionConfig(),
        # Context window compression for theoretically infinite sessions
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=LIVE_CONTEXT_TRIGGER_TOKENS,
            sliding_window=types.SlidingWindow(
                target_tokens=LIVE_CONTEXT_TARGET_TOKENS
            ),
        ),
        # Real-time input config for open-mic voice turns.
        # Let Gemini detect end-of-turn from silence so the user does not need
        # to toggle the mic off just to receive a response.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=300,
                silence_duration_ms=800,
            ),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
        ),
        # Keep live event loop responsive while tools perform blocking I/O.
        tool_thread_pool_config=ToolThreadPoolConfig(max_workers=8),
    )

    # Create LiveRequestQueue for this connection
    live_request_queue = LiveRequestQueue()
    live_model_enabled = _ensure_google_api_key_from_fallback(
        log_context=f"ws connect {session_id}"
    )
    live_model_notice_channels: set[str] = set()
    allow_explicit_activity_control = (
        getattr(run_config.realtime_input_config, "automatic_activity_detection", None)
        is None
    )
    if not allow_explicit_activity_control:
        logger.info(
            "[UPSTREAM] Explicit stream-control messages are disabled because "
            "automatic activity detection is enabled in RunConfig."
        )
    if not live_model_enabled:
        session_metrics.increment("live_model_disabled_sessions")
        logger.warning(
            "GOOGLE_API_KEY is not set; live LLM responses are disabled for this connection. "
            "Direct globe control commands will still execute."
        )
        await _emit_live_model_unavailable_error_once(
            websocket=websocket,
            emitted_channels=live_model_notice_channels,
            channel="general",
            metrics=session_metrics,
        )

    if lifecycle_status.get("reused_existing_session"):
        proactive_alert_sent = session_id in _proactive_alerts_sent
    else:
        _proactive_alerts_sent.discard(session_id)
        proactive_alert_sent = False

    existing_route_context = None
    if isinstance(getattr(session, "state", None), dict):
        existing_route_context = _normalize_route_context_update(
            session.state.get("route_context", {})
        )
    if existing_route_context is not None:
        _session_route_contexts[session_id] = existing_route_context
    else:
        _session_route_contexts.pop(session_id, None)

    async def upstream_task():
        """
        Handle messages from browser → LiveRequestQueue.

        Message types:
        - Binary: PCM 16-bit 16kHz audio frames
        - Text JSON: {type: "text"/"video"/"mode_change"/"activity_*"/"audio_stream_end", ...}
        """
        # Diagnostic: optional upstream PCM dump for debugging.
        # Play with: ffplay -f s16le -ar 16000 -ac 1 <dump_path>
        dump_audio = _is_enabled_env_flag(os.getenv(UPSTREAM_AUDIO_DUMP_ENV_VAR))
        dump_audio_path = os.getenv(
            UPSTREAM_AUDIO_DUMP_PATH_ENV_VAR,
            "/tmp/hawkeye-upstream-audio.pcm",
        )
        dump_max_bytes = _get_env_int(
            UPSTREAM_AUDIO_DUMP_MAX_BYTES_ENV_VAR,
            16000 * 2 * 5,  # 5 seconds of 16kHz 16-bit mono
            min_value=1,
        )
        audio_info_interval = _get_env_int(
            UPSTREAM_AUDIO_INFO_INTERVAL_ENV_VAR,
            250,
            min_value=0,
        )
        dump_file = None
        dump_bytes_written = 0

        if dump_audio:
            logger.info(
                "[UPSTREAM] Audio dump enabled path=%s max_bytes=%s",
                dump_audio_path,
                dump_max_bytes,
            )

        try:
            audio_frame_count = 0
            while not disconnect_event.is_set():
                # Receive message from WebSocket
                message = await websocket.receive()

                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    session_metrics.increment("upstream_websocket_disconnect_frames")
                    session_metrics.set_outcome("upstream_websocket_disconnect")
                    logger.info(
                        f"WebSocket disconnected (upstream receive): {session_id}"
                    )
                    break

                if message.get("bytes") is not None:
                    # Binary audio data
                    audio_bytes = message["bytes"]
                    session_metrics.increment("upstream_audio_frames")
                    session_metrics.increment("upstream_audio_bytes", len(audio_bytes))
                    audio_frame_count += 1
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "[UPSTREAM] Binary audio frame #%s: %s bytes",
                            audio_frame_count,
                            len(audio_bytes),
                        )
                    elif (
                        audio_info_interval > 0
                        and audio_frame_count % audio_info_interval == 0
                    ):
                        logger.info(
                            "[UPSTREAM] Processed %s audio frames (latest=%s bytes)",
                            audio_frame_count,
                            len(audio_bytes),
                        )

                    # Diagnostic audio dump
                    if dump_audio and dump_bytes_written < dump_max_bytes:
                        if dump_file is None:
                            try:
                                dump_file = open(dump_audio_path, "wb")
                                logger.info(
                                    "[UPSTREAM] Dumping audio to %s",
                                    dump_audio_path,
                                )
                            except Exception as dump_err:
                                dump_audio = False
                                logger.warning(
                                    "[UPSTREAM] Unable to open dump file %s: %s",
                                    dump_audio_path,
                                    dump_err,
                                )
                        if dump_file is not None:
                            remaining = dump_max_bytes - dump_bytes_written
                            chunk = audio_bytes[:remaining]
                            dump_file.write(chunk)
                            dump_bytes_written += len(chunk)
                            if dump_bytes_written >= dump_max_bytes:
                                dump_file.close()
                                dump_file = None
                                logger.info(
                                    "[UPSTREAM] Audio dump complete (%s bytes)",
                                    dump_bytes_written,
                                )

                    # Send to LiveRequestQueue, or explicitly drop when live model
                    # is disabled so users get a clear one-time error.
                    await _handle_upstream_audio_frame(
                        audio_bytes=audio_bytes,
                        live_model_enabled=live_model_enabled,
                        live_request_queue=live_request_queue,
                        websocket=websocket,
                        live_model_notice_channels=live_model_notice_channels,
                        metrics=session_metrics,
                    )

                elif message.get("text") is not None:
                    session_metrics.increment("upstream_text_frames")
                    # Text message - parse JSON
                    try:
                        data = json.loads(message["text"])
                        session_metrics.increment("upstream_json_messages")
                        msg_type = _normalize_client_message_type(data)

                        heartbeat_pong_payload = _build_heartbeat_pong_payload(data)
                        if heartbeat_pong_payload is not None:
                            session_metrics.increment(
                                "upstream_heartbeat_ping_messages"
                            )
                            await websocket.send_text(
                                json.dumps(heartbeat_pong_payload)
                            )
                            session_metrics.mark_downstream_payload("text")
                            continue

                        if _is_heartbeat_ack_message(data):
                            session_metrics.increment(
                                "upstream_heartbeat_pong_messages"
                            )
                            continue

                        if _handle_screenshot_response_message(
                            data,
                            live_request_queue,
                            session_metrics,
                        ):
                            continue

                        if _handle_stream_control_message(
                            data,
                            live_request_queue,
                            session_metrics,
                            allow_explicit_activity_control=allow_explicit_activity_control,
                        ):
                            continue

                        if msg_type == "context_update":
                            context_update = _normalize_route_context_update(data)
                            if context_update is None:
                                session_metrics.record_upstream_invalid(
                                    "context_update_invalid",
                                    dropped=True,
                                )
                                logger.warning(
                                    "[CONTEXT] Ignoring invalid context_update payload keys=%s",
                                    sorted(data.keys()),
                                )
                                continue

                            _session_route_contexts[session_id] = context_update
                            if isinstance(getattr(session, "state", None), dict):
                                session.state["route_context"] = dict(context_update)
                            session_metrics.mark_upstream_activity("context_update")
                            session_metrics.increment("upstream_context_update_messages")
                            continue

                        # Support both frontend schema {"type":"text","content":"..."}
                        # and manual test schema {"text":"..."} for backend-only testing.
                        text_content: str | None = None
                        if msg_type == "text":
                            text_content = data.get("content")
                            if text_content is None:
                                text_content = data.get("text")
                        elif msg_type is None:
                            text_content = data.get("text") or data.get("content")

                        if isinstance(text_content, str) and text_content.strip():
                            session_metrics.mark_upstream_activity("text")
                            logger.info(f"Received text: {text_content[:80]}...")
                            direct_match = _match_direct_globe_command(
                                text_content,
                                _session_route_contexts.get(session_id),
                            )
                            if direct_match:
                                session_metrics.mark_downstream_event(
                                    "direct_globe_command"
                                )
                                tool_name, tool_args, narration = direct_match
                                logger.info(
                                    "[GLOBE CONTROL] Direct command matched: %s args=%s",
                                    tool_name,
                                    tool_args,
                                )

                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "transcript",
                                            "speaker": "agent",
                                            "text": narration,
                                        }
                                    )
                                )
                                session_metrics.mark_downstream_payload("text")
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "tool_call",
                                            "tool": tool_name,
                                            "args": tool_args,
                                        }
                                    )
                                )
                                session_metrics.mark_downstream_payload("text")

                                direct_tool_started = time.monotonic()
                                direct_tool_status = "complete"
                                try:
                                    tool_result = _execute_direct_globe_tool(
                                        tool_name, tool_args
                                    )
                                    for evt in _map_tool_result_to_events(
                                        tool_name, tool_result
                                    ):
                                        await websocket.send_text(
                                            json.dumps(evt, default=str)
                                        )
                                        session_metrics.mark_downstream_payload("text")
                                except Exception as exc:
                                    direct_tool_status = "error"
                                    logger.error(
                                        "[GLOBE CONTROL] Direct command failed (%s): %s",
                                        tool_name,
                                        exc,
                                    )
                                    await websocket.send_text(
                                        json.dumps(
                                            {
                                                "type": "error",
                                                "message": f"Failed to execute {tool_name}: {exc}",
                                            }
                                        )
                                    )
                                    session_metrics.mark_downstream_payload("text")
                                finally:
                                    session_metrics.record_tool_chain_duration(
                                        tool_name=tool_name,
                                        duration_ms=(
                                            time.monotonic() - direct_tool_started
                                        )
                                        * 1000,
                                        status=direct_tool_status,
                                    )

                                await websocket.send_text(
                                    json.dumps({"type": "turn_complete"})
                                )
                                session_metrics.mark_downstream_payload("text")
                                continue

                            if not live_model_enabled:
                                await _emit_live_model_unavailable_error_once(
                                    websocket=websocket,
                                    emitted_channels=live_model_notice_channels,
                                    channel="general",
                                    metrics=session_metrics,
                                )
                                await websocket.send_text(
                                    json.dumps({"type": "turn_complete"})
                                )
                                session_metrics.mark_downstream_payload("text")
                                continue

                            if _is_likely_heavy_analysis_request(text_content):
                                await _emit_fast_analysis_ack(
                                    websocket=websocket,
                                    session_id=session_id,
                                    metrics=session_metrics,
                                )

                            live_request_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part.from_text(text=text_content)],
                                )
                            )
                            session_metrics.increment("upstream_text_content_messages")

                        elif msg_type == "video":
                            normalized_video = _normalize_video_message_payload(
                                data,
                                session_metrics,
                            )
                            if normalized_video is None:
                                continue

                            (
                                video_data,
                                caption,
                                mime_type,
                                video_metadata,
                            ) = normalized_video
                            logger.debug(
                                "[VIDEO] Received frame: %s bytes metadata=%s",
                                len(video_data),
                                video_metadata if video_metadata else "{}",
                            )
                            live_request_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[
                                        types.Part.from_text(text=caption),
                                        types.Part.from_bytes(
                                            data=video_data,
                                            mime_type=mime_type,
                                        ),
                                    ],
                                )
                            )
                            session_metrics.mark_upstream_activity("video")
                            session_metrics.increment("upstream_video_messages")

                        elif msg_type == "mode_change":
                            # Operational mode change (SILENT/ALERT/BRIEF/ACTION)
                            mode = data.get("mode", "ALERT")
                            logger.info(f"Mode change requested: {mode}")

                            # Update session state
                            session.state["operational_mode"] = mode

                            # Inject context update into agent stream so it knows about the mode change
                            mode_descriptions = {
                                "SILENT": "Monitor silently. Only speak for CRITICAL threats or direct questions.",
                                "ALERT": "Standard monitoring. Report significant changes and respond to queries.",
                                "BRIEF": "Active engagement. Provide regular updates and detailed analysis.",
                                "ACTION": "Maximum responsiveness. Immediate alerts, proactive suggestions, constant readiness.",
                            }
                            mode_desc = mode_descriptions.get(
                                mode, "Standard monitoring."
                            )
                            live_request_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[
                                        types.Part.from_text(
                                            text=(
                                                f"[SYSTEM] Operational mode changed to {mode}. "
                                                f"Adjust your behavior: {mode_desc}"
                                            )
                                        )
                                    ],
                                )
                            )
                            session_metrics.mark_upstream_activity("mode_change")
                            session_metrics.increment("upstream_mode_change_messages")

                        else:
                            session_metrics.record_upstream_drop("unknown_message_type")
                            logger.warning(f"Unknown message type: {msg_type}")

                    except json.JSONDecodeError:
                        session_metrics.record_upstream_invalid(
                            "json_decode_error",
                            dropped=False,
                        )
                        raw_text = message.get("text", "")
                        if isinstance(raw_text, str) and raw_text.strip():
                            logger.info(
                                f"Received raw text message: {raw_text[:80]}..."
                            )
                            if not live_model_enabled:
                                await _emit_live_model_unavailable_error_once(
                                    websocket=websocket,
                                    emitted_channels=live_model_notice_channels,
                                    channel="general",
                                    metrics=session_metrics,
                                )
                                await websocket.send_text(
                                    json.dumps({"type": "turn_complete"})
                                )
                                session_metrics.mark_downstream_payload("text")
                                continue
                            if live_model_enabled and _is_likely_heavy_analysis_request(
                                raw_text
                            ):
                                await _emit_fast_analysis_ack(
                                    websocket=websocket,
                                    session_id=session_id,
                                    metrics=session_metrics,
                                )
                            live_request_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part.from_text(text=raw_text)],
                                )
                            )
                            session_metrics.mark_upstream_activity("raw_text_fallback")
                            session_metrics.increment(
                                "upstream_raw_text_fallback_messages"
                            )
                        else:
                            session_metrics.record_upstream_drop(
                                "empty_non_json_text_message"
                            )
                            logger.error("Failed to parse text message as JSON")
                else:
                    session_metrics.record_upstream_drop(
                        "unsupported_websocket_message_shape"
                    )
                    logger.debug(
                        "[UPSTREAM] Ignoring unsupported websocket payload keys=%s",
                        sorted(message.keys()),
                    )

        except WebSocketDisconnect:
            session_metrics.set_outcome("upstream_websocket_disconnect")
            logger.info(f"WebSocket disconnected (upstream): {session_id}")
        except Exception as e:
            session_metrics.increment("upstream_exceptions")
            session_metrics.set_outcome("upstream_error")
            logger.error(f"Upstream error: {e}")
        finally:
            if dump_file is not None:
                try:
                    dump_file.close()
                except Exception:
                    pass
            disconnect_event.set()

    async def downstream_task():
        """
        Handle events from runner.run_live() → browser.

        Event types:
        - Audio output (binary PCM 24kHz)
        - Text transcriptions
        - Tool calls and responses → emit structured UI events
        """
        nonlocal lifecycle_status, proactive_alert_sent, session
        stale_recovery_attempted = False

        try:
            if not live_model_enabled:
                while not disconnect_event.is_set():
                    await asyncio.sleep(1)
                return

            while not disconnect_event.is_set():
                try:
                    run_live_attempt_started = time.monotonic()
                    session_metrics.increment("downstream_run_live_attempts")
                    # Start the live session with runner
                    async for event in runner.run_live(
                        user_id=user_id,
                        session_id=session_id,
                        live_request_queue=live_request_queue,
                        run_config=run_config,
                    ):
                        if disconnect_event.is_set():
                            break

                        # Process the event
                        await process_event(
                            event,
                            websocket,
                            session_id,
                            metrics=session_metrics,
                        )
                    session_metrics.observe_duration_ms(
                        "run_live_attempt_duration_ms",
                        (time.monotonic() - run_live_attempt_started) * 1000,
                    )
                    break

                except Exception as e:
                    session_metrics.observe_duration_ms(
                        "run_live_attempt_duration_ms",
                        (time.monotonic() - run_live_attempt_started) * 1000,
                    )
                    if (
                        not stale_recovery_attempted
                        and _should_retry_stale_live_session()
                        and _is_stale_live_session_error(e)
                    ):
                        stale_recovery_attempted = True
                        session_metrics.increment("stale_session_recovery_attempts")
                        logger.warning(
                            "[SESSION LIFECYCLE] stale live-session failure detected for %s; "
                            "retrying once with fresh session",
                            session_id,
                        )
                        try:
                            session, lifecycle_status = await _prepare_live_session(
                                user_id=user_id,
                                session_id=session_id,
                                strategy=LIVE_SESSION_STRATEGY_FRESH,
                                recovery_attempted=True,
                            )
                            _proactive_alerts_sent.discard(session_id)
                            proactive_alert_sent = False
                            session_metrics.record_lifecycle_status(lifecycle_status)
                            session_metrics.increment(
                                "stale_session_recovery_successes"
                            )
                            _log_session_lifecycle(
                                user_id=user_id,
                                session_id=session_id,
                                lifecycle_status=lifecycle_status,
                            )
                            await _emit_session_lifecycle_status(
                                websocket,
                                session,
                                lifecycle_status,
                            )
                            continue
                        except Exception as recovery_exc:
                            logger.error(
                                "[SESSION LIFECYCLE] Failed stale-session recovery for %s: %s",
                                session_id,
                                recovery_exc,
                            )
                            session_metrics.increment("stale_session_recovery_failures")
                            session_metrics.set_outcome("stale_session_recovery_failed")

                    session_metrics.increment("downstream_exceptions")
                    session_metrics.set_outcome("downstream_error")
                    session_metrics.mark_downstream_event("downstream_error")
                    error_text = str(e)
                    normalized_error_text = error_text.lower()
                    if "service is currently unavailable" in normalized_error_text:
                        client_error_message = (
                            "Live model temporarily unavailable (Gemini Live API). "
                            "Please retry in a moment."
                        )
                    elif (
                        "explicit activity control is not supported"
                        in normalized_error_text
                    ):
                        client_error_message = (
                            "Live audio stream control mismatch detected. "
                            "Please retry after reconnecting."
                        )
                    else:
                        client_error_message = f"Live model error: {error_text}"
                    try:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": client_error_message,
                                }
                            )
                        )
                        session_metrics.mark_downstream_payload("text")
                    except Exception:
                        pass
                    logger.error(f"Downstream error: {e}")
                    break
        finally:
            disconnect_event.set()

    async def proactive_monitoring_task():
        """
        Background task that checks Firestore water level every 30 seconds.
        When level exceeds 4.0m, injects a proactive message into the
        LiveRequestQueue to trigger the agent to break silence.

        IMPORTANT: The initial alert is delayed 60 seconds to let the
        Gemini Live session fully establish before injecting content.
        The alert text is kept simple to avoid triggering a long tool chain
        that could monopolize the session and block audio output.
        """
        nonlocal proactive_alert_sent

        if user_id.startswith("test_"):
            logger.info("Proactive monitoring disabled for test session")
            return

        try:
            from app.services.firestore_service import IncidentService

            project_id = os.getenv("GCP_PROJECT_ID", "")
            incident_svc = IncidentService(project_id)
        except Exception as e:
            logger.warning(f"Proactive monitoring unavailable: {e}")
            return

        # Wait 60 seconds before first check to let the audio pipeline establish
        initial_delay = 60
        poll_timeout_s = _get_env_float(
            PROACTIVE_WATER_LEVEL_TIMEOUT_ENV_VAR,
            default=3.0,
            min_value=0.1,
        )
        logger.info(
            "[PROACTIVE] Delaying first water level check by %ss to let audio pipeline establish",
            initial_delay,
        )
        await asyncio.sleep(initial_delay)

        while not disconnect_event.is_set():
            try:
                if disconnect_event.is_set():
                    break

                # Check water level from Firestore
                try:
                    water_data = await asyncio.wait_for(
                        asyncio.to_thread(incident_svc.get_water_level),
                        timeout=poll_timeout_s,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[PROACTIVE] Firestore water-level poll timed out after %.1fs",
                        poll_timeout_s,
                    )
                    await asyncio.sleep(10)
                    continue
                water_level = water_data.get("water_level_m", 0.0)

                logger.debug(f"Proactive check: water_level={water_level}m")

                # Update session state
                session.state["water_level_m"] = water_level

                # Emit status update to frontend
                try:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "status_update",
                                "waterLevel": water_level,
                            }
                        )
                    )
                except Exception:
                    pass

                # Threshold alert: inject proactive message if above 4.0m.
                # Keep unsolicited alerts limited to ACTION mode so demos and guided
                # Q&A flows are not interrupted in ALERT/BRIEF/SILENT.
                if water_level > 4.0 and not proactive_alert_sent:
                    operational_mode = (
                        str(session.state.get("operational_mode", "ALERT"))
                        .strip()
                        .upper()
                    )
                    if operational_mode != "ACTION":
                        logger.info(
                            "[PROACTIVE] Threshold met (%.2fm) but mode=%s; skipping proactive alert injection",
                            water_level,
                            operational_mode,
                        )
                    else:
                        proactive_alert_sent = True
                        _proactive_alerts_sent.add(session_id)
                        logger.info(
                            f"Water level threshold exceeded: {water_level}m > 4.0m — "
                            "injecting proactive alert (simplified, no tool chain)"
                        )

                        # Send alert to frontend immediately
                        try:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "status_update",
                                        "waterLevel": water_level,
                                        "alert": True,
                                        "alertMessage": f"Water level critical: {water_level}m",
                                    }
                                )
                            )
                        except Exception:
                            pass

                        # Inject a SHORT alert into agent stream.
                        # DO NOT request "situation assessment" or "report flood extent"
                        # as that triggers a cascade of tool calls (get_flood_extent →
                        # get_infrastructure_at_risk → get_population_at_risk →
                        # compute_cascade) that takes 30+ seconds and blocks audio.
                        live_request_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part.from_text(
                                        text=(
                                            f"[SYSTEM ALERT] Water level has reached {water_level} meters, "
                                            f"exceeding the 4.0m critical threshold. "
                                            f"Acknowledge this alert briefly. Do NOT run any tools yet — "
                                            f"wait for the commander's instructions."
                                        )
                                    )
                                ],
                            )
                        )

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Proactive monitoring error: {e}")
                await asyncio.sleep(10)

    # Run all three tasks concurrently
    try:
        await asyncio.gather(
            upstream_task(),
            downstream_task(),
            proactive_monitoring_task(),
        )
    except Exception as e:
        session_metrics.increment("websocket_gather_exceptions")
        session_metrics.set_outcome("websocket_error")
        logger.error(f"WebSocket error: {e}")
    finally:
        session_metrics.set_outcome("connection_closed")
        session_metrics.emit_summary()
        # Cleanup
        logger.info(f"Closing WebSocket connection: {session_id}")
        _clear_output_transcription(session_id)
        _clear_pending_long_running_tools(session_id)
        _clear_fast_ack_state(session_id)
        _session_route_contexts.pop(session_id, None)
        _usage_telemetry_snapshots.pop(session_id, None)
        # Remove from active connections if this is still the registered one
        async with _active_connections_lock:
            if _active_connections.get(session_id) is disconnect_event:
                _active_connections.pop(session_id, None)
        live_request_queue.close()
        try:
            await websocket.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Event Processing
# ─────────────────────────────────────────────────────────────────────


async def process_event(
    event: Event,
    websocket: WebSocket,
    session_id: str,
    metrics: _WebSocketSessionMetrics | None = None,
):
    """
    Process an ADK event and send appropriate response to browser.

    Audio output path (per ADK internals):
      Audio data appears ONLY in event.content.parts[i].inline_data
      where inline_data.data is bytes and inline_data.mime_type starts with 'audio/'.
      ADK Event objects typically do not expose event.content.audio or
      event.server_content in production, but we still support those legacy
      shapes for test/local compatibility.

    Other paths:
      - Text content → transcript JSON
      - Function calls → tool_call JSON
      - Function responses → extract tool results → emit structured UI events
      - output_transcription → buffered transcript of spoken audio
      - turn_complete → turn_complete JSON
      - interrupted → interrupted JSON (barge-in)
    """
    try:
        if metrics is not None:
            metrics.mark_downstream_event("runner_event")
        event_content = getattr(event, "content", None)
        debug_enabled = logger.isEnabledFor(logging.DEBUG)
        sent_text_from_content = False
        is_partial_event = bool(getattr(event, "partial", False))
        has_output_transcription_update = False
        audio_sent_in_event = False

        if debug_enabled:
            # ── Diagnostic audit ──
            has_content = event_content is not None
            has_content_parts = bool(
                has_content and hasattr(event_content, "parts") and event_content.parts
            )
            has_turn_complete = bool(getattr(event, "turn_complete", False))
            has_interrupted = bool(getattr(event, "interrupted", False))
            has_output_transcription = (
                getattr(event, "output_transcription", None) is not None
            )
            has_input_transcription = (
                getattr(event, "input_transcription", None) is not None
            )
            has_server_content = getattr(event, "server_content", None) is not None
            has_content_audio = bool(
                has_content
                and getattr(getattr(event_content, "audio", None), "data", None)
            )

            logger.debug(
                "[DOWNSTREAM] Event: content=%s parts=%s content_audio=%s "
                "server_content=%s turn_complete=%s interrupted=%s output_tx=%s "
                "input_tx=%s partial=%s",
                has_content,
                has_content_parts,
                has_content_audio,
                has_server_content,
                has_turn_complete,
                has_interrupted,
                has_output_transcription,
                has_input_transcription,
                is_partial_event,
            )

        # ── Legacy compatibility: content.audio ──
        # Some tests/mock harnesses still emit `event.content.audio.data`.
        content_audio = getattr(event_content, "audio", None)
        content_audio_payload = (
            getattr(content_audio, "data", None) if content_audio is not None else None
        )
        if isinstance(content_audio_payload, (bytes, bytearray, memoryview)):
            audio_chunk = bytes(content_audio_payload)
            if audio_chunk:
                await websocket.send_bytes(audio_chunk)
                if metrics is not None:
                    metrics.mark_downstream_payload("audio")
                audio_sent_in_event = True
                logger.debug(
                    "[DOWNSTREAM] Sent legacy content.audio: %s bytes",
                    len(audio_chunk),
                )

        # ── Process content.parts (audio, text, function calls/responses) ──
        if event_content and hasattr(event_content, "parts") and event_content.parts:
            for idx, part in enumerate(event_content.parts):
                # ── Part inspection diagnostic ──
                if debug_enabled:
                    part_attrs = []
                    if getattr(part, "text", None):
                        part_attrs.append(f"text({len(part.text)}ch)")
                    if getattr(part, "inline_data", None):
                        idata = part.inline_data
                        dtype = type(getattr(idata, "data", None)).__name__
                        dlen = (
                            len(getattr(idata, "data", None) or b"")
                            if hasattr(getattr(idata, "data", None) or b"", "__len__")
                            else "?"
                        )
                        part_attrs.append(
                            f"inline_data(mime={getattr(idata, 'mime_type', '?')}, data_type={dtype}, len={dlen})"
                        )
                    if getattr(part, "function_call", None):
                        part_attrs.append(f"function_call({part.function_call.name})")
                    if getattr(part, "function_response", None):
                        part_attrs.append(
                            f"function_response({getattr(part.function_response, 'name', '?')})"
                        )
                    if getattr(part, "thought", False):
                        part_attrs.append("thought")
                    if not part_attrs:
                        part_attrs.append(f"empty(type={type(part).__name__})")
                    logger.debug(
                        "[DOWNSTREAM] Part[%s]: %s",
                        idx,
                        ", ".join(part_attrs),
                    )

                # ── Audio extraction from inline_data ──
                # This is the ONLY path where audio data appears on ADK Events.
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None:
                    inline_payload = getattr(inline_data, "data", None)
                    inline_mime = getattr(inline_data, "mime_type", None) or ""
                    is_audio_mime = inline_mime.lower().startswith("audio/")
                    decoded_payload: bytes | None = None

                    if (
                        isinstance(inline_payload, (bytes, bytearray, memoryview))
                        and inline_payload
                    ):
                        decoded_payload = bytes(inline_payload)

                    elif isinstance(inline_payload, str) and inline_payload:
                        # Fallback: base64-encoded audio (shouldn't happen with ADK
                        # Python SDK which auto-decodes, but handle it just in case)
                        try:
                            decoded_payload = base64.b64decode(inline_payload)
                        except Exception as b64_err:
                            logger.warning(
                                "[DOWNSTREAM] Failed to base64-decode inline_data: %s",
                                b64_err,
                            )

                    if decoded_payload:
                        # If mime is omitted in test/local stubs, still treat bytes as audio.
                        if is_audio_mime or not inline_mime:
                            await websocket.send_bytes(decoded_payload)
                            if metrics is not None:
                                metrics.mark_downstream_payload("audio")
                            audio_sent_in_event = True
                            logger.debug(
                                "[DOWNSTREAM] Sent audio: %s bytes (mime=%s)",
                                len(decoded_payload),
                                inline_mime or "<unspecified>",
                            )
                            # For audio chunks, skip further processing for this part.
                            continue

                        logger.debug(
                            "[DOWNSTREAM] Skipping non-audio inline_data payload: mime=%s",
                            inline_mime,
                        )

                # ── Text content ──
                if hasattr(part, "text") and part.text:
                    text = part.text
                    is_thought_part = bool(getattr(part, "thought", False))

                    if is_thought_part:
                        logger.debug("Skipping thought text part")
                    else:
                        # Try to parse as structured UI event
                        ui_event = None
                        try:
                            parsed = json.loads(text)
                            if isinstance(parsed, dict) and "type" in parsed:
                                ui_event = parsed
                        except (json.JSONDecodeError, TypeError):
                            pass

                        if ui_event:
                            await websocket.send_text(json.dumps(ui_event))
                            if metrics is not None:
                                metrics.mark_downstream_payload("text")
                            sent_text_from_content = True
                        else:
                            logger.debug(
                                "Suppressing non-JSON content.parts transcript text"
                            )
                            if not is_partial_event:
                                inferred_globe_action = _match_agent_globe_confirmation(
                                    text
                                )
                                if inferred_globe_action:
                                    tool_name, tool_args, source = inferred_globe_action
                                    _queue_pending_globe_fallback(
                                        session_id,
                                        tool_name,
                                        tool_args,
                                        source,
                                        overwrite=False,
                                    )

                # ── Function calls (tool invocations) ──
                if hasattr(part, "function_call") and part.function_call:
                    func_call = part.function_call
                    tool_name = func_call.name
                    logger.info(f"Tool call: {tool_name}")

                    _suppress_pending_globe_fallback_for_matching_tool(
                        session_id,
                        tool_name,
                    )

                    tool_args = dict(func_call.args) if func_call.args else {}
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "tool_call",
                                "tool": tool_name,
                                "args": tool_args,
                            }
                        )
                    )
                    if metrics is not None:
                        metrics.mark_downstream_payload("text")

                    if (
                        _nonblocking_tool_events_enabled()
                        and _is_long_running_live_tool(tool_name)
                    ):
                        pending_tracker = _register_pending_long_running_tool(
                            session_id=session_id,
                            tool_name=tool_name,
                            call_id=_extract_tool_call_id(func_call),
                        )
                        await websocket.send_text(
                            json.dumps(
                                _build_tool_status_event(
                                    tool_name=tool_name,
                                    state="pending",
                                    call_id=pending_tracker["call_id"],
                                    message=f"{tool_name} queued",
                                    started_at_ms=pending_tracker["started_at_ms"],
                                )
                            )
                        )
                        if metrics is not None:
                            metrics.mark_downstream_payload("text")

                # ── Function responses (tool results) ──
                if hasattr(part, "function_response") and part.function_response:
                    func_resp = part.function_response
                    tool_name = func_resp.name if hasattr(func_resp, "name") else ""
                    tool_result = {}
                    tool_chain_started = time.monotonic()
                    nonblocking_tool_tracking = (
                        _nonblocking_tool_events_enabled()
                        and _is_long_running_live_tool(tool_name)
                    )
                    pending_tracker: dict[str, Any] | None = None
                    if nonblocking_tool_tracking:
                        pending_tracker = _consume_pending_long_running_tool(
                            session_id,
                            tool_name,
                        )
                        if pending_tracker is None:
                            pending_tracker = {
                                "call_id": f"{tool_name}-{uuid.uuid4().hex[:10]}",
                                "started_at_ms": int(time.time() * 1000),
                            }
                        await websocket.send_text(
                            json.dumps(
                                _build_tool_status_event(
                                    tool_name=tool_name,
                                    state="running",
                                    call_id=str(pending_tracker.get("call_id")),
                                    message=f"{tool_name} running",
                                    started_at_ms=pending_tracker.get("started_at_ms"),
                                )
                            )
                        )
                        if metrics is not None:
                            metrics.mark_downstream_payload("text")

                    if hasattr(func_resp, "response") and func_resp.response:
                        raw_response = func_resp.response
                        if isinstance(raw_response, str):
                            try:
                                tool_result = json.loads(raw_response)
                            except (json.JSONDecodeError, TypeError):
                                tool_result = {"raw": raw_response}
                        else:
                            # Force-convert proto MapComposite / RepeatedComposite
                            # to plain Python dicts/lists so .get() and
                            # json.dumps() work reliably downstream.
                            try:
                                # If it's a protobuf Struct or Message, use
                                # MessageToDict for full recursive conversion.
                                if hasattr(raw_response, "DESCRIPTOR"):
                                    tool_result = MessageToDict(raw_response)
                                elif hasattr(raw_response, "pb"):
                                    # proto-plus wrapper — unwrap then convert
                                    tool_result = MessageToDict(raw_response.pb)
                                elif hasattr(raw_response, "keys"):
                                    # dict-like proto MapComposite — iterate keys
                                    # and recursively convert values
                                    tool_result = json.loads(
                                        json.dumps(
                                            {k: raw_response[k] for k in raw_response},
                                            default=str,
                                        )
                                    )
                                else:
                                    tool_result = (
                                        dict(raw_response)
                                        if hasattr(raw_response, "__iter__")
                                        else {"raw": str(raw_response)}
                                    )
                            except Exception:
                                tool_result = {"raw": str(raw_response)}

                    # ADK may wrap the real payload under a "result" key
                    if (
                        isinstance(tool_result, dict)
                        and "result" in tool_result
                        and isinstance(tool_result["result"], dict)
                        and len(tool_result) == 1
                    ):
                        tool_result = tool_result["result"]

                    logger.info(
                        "[TOOL RESULT] %s keys=%s",
                        tool_name,
                        list(tool_result.keys())
                        if isinstance(tool_result, dict)
                        else type(tool_result).__name__,
                    )

                    tool_processing_error: str | None = None
                    try:
                        ui_events = await _map_tool_result_to_events_nonblocking(
                            tool_name,
                            tool_result,
                        )
                        logger.info(
                            "[CHART] _map_tool_result_to_events(%s) produced %d events, types=%s",
                            tool_name,
                            len(ui_events),
                            [e.get("type") for e in ui_events],
                        )
                        for evt in ui_events:
                            try:
                                evt_json = json.dumps(evt, default=str)
                                await websocket.send_text(evt_json)
                                if metrics is not None:
                                    metrics.mark_downstream_payload("text")
                                logger.info(
                                    "[CHART] Sent event type=%s chart=%s to WebSocket (%d bytes)",
                                    evt.get("type"),
                                    evt.get("chart", "N/A"),
                                    len(evt_json),
                                )
                            except Exception as e:
                                logger.error(
                                    "[CHART] Failed to emit UI event type=%s: %s",
                                    evt.get("type"),
                                    e,
                                )
                    except Exception as e:
                        tool_processing_error = (
                            f"Failed to process {tool_name} result: {e}"
                        )
                        logger.error(
                            "[TOOL RESULT] Failed to map tool result for %s: %s",
                            tool_name,
                            e,
                            exc_info=True,
                        )
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": tool_processing_error,
                                }
                            )
                        )
                        if metrics is not None:
                            metrics.mark_downstream_payload("text")

                    tool_error_message = (
                        tool_processing_error
                        or _extract_tool_error_message(tool_result)
                    )

                    if nonblocking_tool_tracking and pending_tracker is not None:
                        runtime_status = None
                        if isinstance(tool_result, dict):
                            if isinstance(tool_result.get("runtime_status"), str):
                                runtime_status = tool_result.get("runtime_status")
                            elif isinstance(tool_result.get("ee_runtime"), dict):
                                ee_runtime_status = tool_result["ee_runtime"].get(
                                    "status"
                                )
                                if isinstance(ee_runtime_status, str):
                                    runtime_status = ee_runtime_status

                        completion_state = "error" if tool_error_message else "complete"
                        duration_ms = None
                        started_monotonic = pending_tracker.get("started_monotonic")
                        if isinstance(started_monotonic, (int, float)):
                            duration_ms = int(
                                (time.monotonic() - started_monotonic) * 1000
                            )

                        await websocket.send_text(
                            json.dumps(
                                _build_tool_status_event(
                                    tool_name=tool_name,
                                    state=completion_state,
                                    call_id=str(pending_tracker.get("call_id")),
                                    message=(
                                        f"{tool_name} failed"
                                        if tool_error_message
                                        else f"{tool_name} complete"
                                    ),
                                    error=tool_error_message,
                                    started_at_ms=pending_tracker.get("started_at_ms"),
                                    duration_ms=duration_ms,
                                    runtime_status=runtime_status,
                                )
                            )
                        )
                        if metrics is not None:
                            metrics.mark_downstream_payload("text")

                    if metrics is not None:
                        metrics.record_tool_chain_duration(
                            tool_name=tool_name,
                            duration_ms=(time.monotonic() - tool_chain_started) * 1000,
                            status="error" if tool_error_message else "complete",
                        )

        # ── Legacy compatibility: server_content.model_turn.parts ──
        # ADK currently decomposes these into `content.parts`, but test stubs and
        # older integrations may still send `server_content`.
        server_content = getattr(event, "server_content", None)
        model_turn = (
            getattr(server_content, "model_turn", None)
            if server_content is not None
            else None
        )
        model_parts = (
            getattr(model_turn, "parts", None) if model_turn is not None else None
        )
        if model_parts:
            for idx, part in enumerate(model_parts):
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None:
                    inline_payload = getattr(inline_data, "data", None)
                    inline_mime = getattr(inline_data, "mime_type", None) or ""
                    is_audio_mime = inline_mime.lower().startswith("audio/")
                    decoded_payload: bytes | None = None

                    if isinstance(inline_payload, (bytes, bytearray, memoryview)):
                        decoded_payload = bytes(inline_payload)
                    elif isinstance(inline_payload, str) and inline_payload:
                        try:
                            decoded_payload = base64.b64decode(inline_payload)
                        except Exception as b64_err:
                            logger.warning(
                                "[DOWNSTREAM] Failed to base64-decode server_content part[%s]: %s",
                                idx,
                                b64_err,
                            )

                    if decoded_payload:
                        if is_audio_mime or not inline_mime:
                            await websocket.send_bytes(decoded_payload)
                            if metrics is not None:
                                metrics.mark_downstream_payload("audio")
                            audio_sent_in_event = True
                            logger.debug(
                                "[DOWNSTREAM] Sent legacy server_content audio: %s bytes (mime=%s)",
                                len(decoded_payload),
                                inline_mime or "<unspecified>",
                            )
                            continue

                        logger.debug(
                            "[DOWNSTREAM] Skipping non-audio server_content inline_data: mime=%s",
                            inline_mime,
                        )

                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    if is_partial_event:
                        logger.debug(
                            "Skipping partial server_content transcript text part"
                        )
                    else:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "transcript",
                                    "speaker": "agent",
                                    "text": text.strip(),
                                }
                            )
                        )
                        if metrics is not None:
                            metrics.mark_downstream_payload("text")
                        sent_text_from_content = True

        # ── Handle output transcription stream ──
        output_transcription = getattr(event, "output_transcription", None)
        if output_transcription is not None:
            has_output_transcription_update = True
            transcription_text = getattr(output_transcription, "text", None)
            if isinstance(transcription_text, str) and transcription_text:
                _append_output_transcription(session_id, transcription_text)

            if bool(getattr(output_transcription, "finished", False)):
                if await _emit_buffered_output_transcription(
                    websocket,
                    session_id,
                    metrics,
                ):
                    sent_text_from_content = True
                logger.debug("Output transcription finished for session %s", session_id)

        # ── Handle input transcription (user's speech-to-text) ──
        input_transcription = getattr(event, "input_transcription", None)
        if input_transcription is not None:
            input_text = getattr(input_transcription, "text", None)
            is_finished = bool(getattr(input_transcription, "finished", False))
            if isinstance(input_text, str) and input_text.strip() and is_finished:
                direct_match = _match_direct_globe_command(
                    input_text.strip(),
                    _session_route_contexts.get(session_id),
                )
                if direct_match:
                    tool_name, tool_args, _ = direct_match
                    _queue_pending_globe_fallback(
                        session_id,
                        tool_name,
                        tool_args,
                        f"input_transcription:{input_text.strip()}",
                        overwrite=True,
                    )

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "transcript",
                            "speaker": "user",
                            "text": input_text.strip(),
                        }
                    )
                )
                if metrics is not None:
                    metrics.mark_downstream_payload("text")
                logger.info(
                    "[DOWNSTREAM] Input transcription: %s", input_text.strip()[:80]
                )

        usage_update = _extract_live_usage_update_event(event)
        if usage_update is not None:
            usage_signature = _usage_event_signature(usage_update)
            if _usage_telemetry_snapshots.get(session_id) != usage_signature:
                _usage_telemetry_snapshots[session_id] = usage_signature
                await websocket.send_text(json.dumps(usage_update))
                if metrics is not None:
                    metrics.mark_downstream_payload("text")

        # ── Handle turn completion ──
        if hasattr(event, "turn_complete") and event.turn_complete:
            await _execute_pending_globe_fallback(websocket, session_id)
            if await _emit_buffered_output_transcription(
                websocket,
                session_id,
                metrics,
            ):
                sent_text_from_content = True
            logger.info(
                "[DOWNSTREAM] Turn complete (audio_sent=%s)",
                audio_sent_in_event,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "turn_complete",
                    }
                )
            )
            if metrics is not None:
                metrics.mark_downstream_payload("text")

        # ── Handle interruptions (barge-in) ──
        if hasattr(event, "interrupted") and event.interrupted:
            await _emit_interrupted_pending_tool_statuses(websocket, session_id)
            _clear_globe_turn_state(session_id)
            _clear_output_transcription(session_id)
            logger.info("Response interrupted by user")
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "interrupted",
                    }
                )
            )
            if metrics is not None:
                metrics.mark_downstream_payload("text")

    except Exception as e:
        if metrics is not None:
            metrics.increment("process_event_exceptions")
        _clear_globe_turn_state(session_id)
        _clear_pending_long_running_tools(session_id)
        _usage_telemetry_snapshots.pop(session_id, None)
        logger.error(f"Error processing event: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Serve static frontend files (optional, frontend may deploy separately)
# ─────────────────────────────────────────────────────────────────────
_frontend_dist = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist")
)
if os.path.exists(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="static")
