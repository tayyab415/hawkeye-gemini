"""
Predictor Agent Tools — Track 3C
Risk projections using Nano Banana 2 (Gemini 3.1 Flash Image Preview)
and confidence decay modeling.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: genai.Client | None = None

# Nano Banana 2 model for image generation
_IMAGE_GEN_MODEL = "gemini-2.0-flash-exp"


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GCP_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client


# ─────────────────────────────────────────────────────────────────────
# Tool 1: generate_risk_projection  *** NOW REAL ***
# ─────────────────────────────────────────────────────────────────────

def generate_risk_projection(
    screenshot_base64: str, scenario: str, water_level_delta: float
) -> dict:
    """
    Generate a visual risk projection using Gemini image generation.

    Takes a 3D view screenshot and generates an image showing what the area
    would look like with increased water levels.

    Args:
        screenshot_base64: Base64-encoded screenshot of current 3D view
        scenario: Description of the scenario (e.g., "+2m water rise")
        water_level_delta: Meters of water rise to simulate

    Returns:
        Projection image and confidence metrics
    """
    logger.info(
        f"Predictor: Generating risk projection for +{water_level_delta}m scenario"
    )

    try:
        # Validate input image
        image_bytes = base64.b64decode(screenshot_base64)
        logger.debug(f"Received screenshot: {len(image_bytes)} bytes")

        # Build the projection prompt
        prompt = (
            f"Modify this aerial/satellite view of a flood disaster zone to show "
            f"what it would look like if water levels rise by {water_level_delta} "
            f"meters. Scenario: {scenario}.\n\n"
            f"Instructions:\n"
            f"- Add realistic flooding: water covering streets and low-lying areas\n"
            f"- Make the water level increase visually dramatic but physically plausible\n"
            f"- Preserve the building structures but show water around and between them\n"
            f"- Use realistic muddy brown/grey water colors typical of urban flooding\n"
            f"- Show partially submerged vehicles and debris where appropriate\n"
            f"- Keep the same camera angle and perspective as the original image\n"
            f"- The result should look like a realistic flood projection photograph"
        )

        response = _get_client().models.generate_content(
            model=_IMAGE_GEN_MODEL,
            contents=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                temperature=0.4,
            ),
        )

        # Extract generated image
        projection_image_base64 = None
        description_text = None

        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    # Encode the raw image bytes as base64
                    projection_image_base64 = base64.b64encode(
                        part.inline_data.data
                    ).decode("utf-8")
                    logger.info(
                        f"Generated projection image: "
                        f"{len(part.inline_data.data)} bytes"
                    )
                elif hasattr(part, "text") and part.text:
                    description_text = part.text

        # Compute confidence for this time horizon
        # Use the confidence decay model internally
        confidence_data = compute_confidence_decay(water_level_delta * 6)
        confidence = confidence_data["confidence_pct"]

        return {
            "projection_generated": projection_image_base64 is not None,
            "scenario": scenario,
            "water_level_delta_m": water_level_delta,
            "projection_image_base64": projection_image_base64,
            "description": description_text or f"Flood projection at +{water_level_delta}m",
            "confidence": confidence,
            "confidence_color": confidence_data["color"],
            "caveats": [
                "Projection based on current topography and land use",
                "Assumes no additional rainfall or drainage changes",
                "Does not account for drainage system capacity",
                f"Water rise of {water_level_delta}m applied as uniform increase",
                "Generated image is an AI approximation, not hydrological simulation",
            ],
            "recommended_re_evaluation_hours": max(
                1, int(4 / water_level_delta)
            ),
        }

    except Exception as e:
        logger.error(f"Risk projection generation failed: {e}")

        # Compute confidence even on failure (it's math-only)
        confidence_data = compute_confidence_decay(water_level_delta * 6)

        return {
            "projection_generated": False,
            "scenario": scenario,
            "water_level_delta_m": water_level_delta,
            "projection_image_base64": None,
            "description": f"Projection generation failed: {e}",
            "confidence": confidence_data["confidence_pct"],
            "confidence_color": confidence_data["color"],
            "caveats": [
                "Image generation failed — verbal projection only",
                f"Error: {e}",
            ],
            "recommended_re_evaluation_hours": 2,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────
# Tool 2: compute_confidence_decay (ALREADY REAL — unchanged)
# ─────────────────────────────────────────────────────────────────────

def compute_confidence_decay(hours_ahead: float) -> dict:
    """
    Compute confidence decay over time using exponential model.

    Formula: confidence = 95 * exp(-0.15 * hours)

    Args:
        hours_ahead: Hours into the future to project

    Returns:
        Confidence percentage, color coding, and recommendation
    """
    logger.info(f"Predictor: Computing confidence decay for {hours_ahead} hours ahead")

    # Exponential decay: 95% at t=0, decays with half-life ~4.6 hours
    confidence = 95 * math.exp(-0.15 * hours_ahead)
    confidence_pct = round(confidence)

    # Color coding
    if confidence_pct >= 70:
        color = "green"
        recommendation = "High confidence - proceed with standard protocols"
    elif confidence_pct >= 40:
        color = "yellow"
        recommendation = "Moderate confidence - monitor closely, prepare contingencies"
    else:
        color = "red"
        recommendation = "Low confidence - avoid definitive predictions, plan for multiple scenarios"

    return {
        "hours_ahead": hours_ahead,
        "confidence_pct": confidence_pct,
        "color": color,
        "recommendation": recommendation,
        "model": "exponential_decay",
        "formula": "confidence = 95 * exp(-0.15 * hours)",
    }
