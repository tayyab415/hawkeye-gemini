"""
Perception Agent Tools — Track 3A
Visual analysis for drone footage and satellite imagery using Gemini 2.5 Flash.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GCP_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client


_VISION_MODEL = "gemini-2.5-flash"


# ─────────────────────────────────────────────────────────────────────
# Tool 1: analyze_frame
# ─────────────────────────────────────────────────────────────────────

_ANALYZE_SYSTEM = (
    "You are a disaster response visual analysis specialist embedded in an "
    "incident command system. You receive aerial or drone imagery of flood "
    "disaster zones and must provide structured, actionable intelligence.\n\n"
    "Assess each image along these dimensions:\n"
    "1. Threat Level: LOW | MEDIUM | HIGH | CRITICAL\n"
    "2. Damage: none | minor | moderate | severe | catastrophic\n"
    "3. Estimated water depth: none | <0.5m | 0.5-1m | 1-2m | >2m\n"
    "4. Objects of interest with counts and confidence\n"
    "5. A concise scene description suitable for radio broadcast\n"
    "6. Specific tactical recommendations\n"
    "7. Whether this warrants escalation to the incident commander\n\n"
    "Respond ONLY with valid JSON matching the schema provided."
)

_ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "threat_level": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        },
        "damage_detected": {
            "type": "string",
            "enum": ["none", "minor", "moderate", "severe", "catastrophic"],
        },
        "water_depth_estimate": {
            "type": "string",
            "enum": ["none", "<0.5m", "0.5-1m", "1-2m", ">2m"],
        },
        "objects_of_interest": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "count": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": ["type", "confidence"],
            },
        },
        "description": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number"},
        "escalation_needed": {"type": "boolean"},
    },
    "required": [
        "threat_level",
        "damage_detected",
        "water_depth_estimate",
        "objects_of_interest",
        "description",
        "recommendations",
        "confidence",
        "escalation_needed",
    ],
}


def analyze_frame(frame_base64: str) -> dict:
    """
    Analyze a single video frame for disaster response indicators
    using Gemini 2.5 Flash vision.

    Args:
        frame_base64: Base64-encoded JPEG image

    Returns:
        Structured analysis with threat level, damage assessment, and recommendations
    """
    logger.info("Perception: Analyzing frame with Gemini 2.5 Flash")

    try:
        # Decode to validate
        image_bytes = base64.b64decode(frame_base64)
        logger.debug(f"Frame size: {len(image_bytes)} bytes")

        response = _get_client().models.generate_content(
            model=_VISION_MODEL,
            contents=[
                types.Part.from_text(
                    text=(
                        "Analyze this disaster/flood zone image. "
                        "Provide structured threat assessment."
                    )
                ),
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(
                system_instruction=_ANALYZE_SYSTEM,
                response_mime_type="application/json",
                response_schema=_ANALYZE_SCHEMA,
                temperature=0.2,
            ),
        )

        result = json.loads(response.text)
        logger.info(
            f"Perception result: threat={result.get('threat_level')}, "
            f"confidence={result.get('confidence')}"
        )
        return result

    except Exception as e:
        logger.error(f"Perception analysis failed: {e}")
        # Graceful fallback so the pipeline never hard-fails
        return {
            "threat_level": "MEDIUM",
            "damage_detected": "unknown",
            "water_depth_estimate": "unknown",
            "objects_of_interest": [],
            "description": f"Analysis unavailable: {e}",
            "recommendations": ["Retry analysis", "Manual visual inspection recommended"],
            "confidence": 0.0,
            "escalation_needed": False,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 2: compare_frames
# ─────────────────────────────────────────────────────────────────────

_COMPARE_SYSTEM = (
    "You are a disaster response visual analyst. You receive TWO sequential "
    "aerial frames of the same area taken minutes apart.\n\n"
    "Compare the frames and report:\n"
    "1. Whether significant changes occurred\n"
    "2. Summary of changes\n"
    "3. Whether the situation is ESCALATING, STABLE, or IMPROVING\n"
    "4. Whether escalation to the incident commander is needed\n"
    "5. Estimated water level change in meters\n"
    "6. Tactical recommendations\n\n"
    "Respond ONLY with valid JSON matching the schema provided."
)

_COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "changes_detected": {"type": "boolean"},
        "change_summary": {"type": "string"},
        "escalation_needed": {"type": "boolean"},
        "threat_level_change": {
            "type": "string",
            "enum": ["ESCALATING", "STABLE", "IMPROVING"],
        },
        "details": {
            "type": "object",
            "properties": {
                "water_level_change_m": {"type": "number"},
                "new_areas_affected": {"type": "integer"},
                "new_hazards": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "rate_of_change": {"type": "string"},
        "confidence": {"type": "number"},
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "changes_detected",
        "change_summary",
        "escalation_needed",
        "threat_level_change",
        "confidence",
        "recommendations",
    ],
}


def compare_frames(
    frame_a_base64: str, frame_b_base64: str, time_delta_minutes: int = 15
) -> dict:
    """
    Compare two sequential frames to detect changes and escalation
    using Gemini 2.5 Flash vision.

    Args:
        frame_a_base64: Base64-encoded first frame (earlier)
        frame_b_base64: Base64-encoded second frame (later)
        time_delta_minutes: Time between frames

    Returns:
        Change detection results with escalation assessment
    """
    logger.info(
        f"Perception: Comparing frames ({time_delta_minutes} min delta) "
        "with Gemini 2.5 Flash"
    )

    try:
        image_a = base64.b64decode(frame_a_base64)
        image_b = base64.b64decode(frame_b_base64)

        response = _get_client().models.generate_content(
            model=_VISION_MODEL,
            contents=[
                types.Part.from_text(
                    text=(
                        f"Compare these two frames taken {time_delta_minutes} "
                        f"minutes apart in a flood disaster zone. "
                        f"Frame A (earlier) is first, Frame B (later) is second. "
                        f"Identify changes, escalation, and rate of change."
                    )
                ),
                types.Part.from_bytes(data=image_a, mime_type="image/jpeg"),
                types.Part.from_bytes(data=image_b, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(
                system_instruction=_COMPARE_SYSTEM,
                response_mime_type="application/json",
                response_schema=_COMPARE_SCHEMA,
                temperature=0.2,
            ),
        )

        result = json.loads(response.text)
        logger.info(
            f"Frame comparison: changes={result.get('changes_detected')}, "
            f"trend={result.get('threat_level_change')}"
        )
        return result

    except Exception as e:
        logger.error(f"Frame comparison failed: {e}")
        return {
            "changes_detected": False,
            "change_summary": f"Comparison unavailable: {e}",
            "escalation_needed": False,
            "threat_level_change": "STABLE",
            "details": {
                "water_level_change_m": 0.0,
                "new_areas_affected": 0,
                "new_hazards": [],
            },
            "rate_of_change": "unknown",
            "confidence": 0.0,
            "recommendations": ["Retry comparison", "Manual review recommended"],
            "error": str(e),
        }
