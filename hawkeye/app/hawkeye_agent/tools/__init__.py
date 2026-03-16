"""Tool modules for Hawk Eye sub-agents.

This module exports all tool functions for use by the Hawk Eye agent hierarchy.
"""

from .perception import analyze_frame, compare_frames
from .analyst import (
    query_historical_floods,
    get_flood_extent,
    get_infrastructure_at_risk,
    get_population_at_risk,
    compute_cascade,
    evaluate_route_safety,
    get_search_grounding,
)
from .predictor import generate_risk_projection, compute_confidence_decay
from .coordinator import (
    send_emergency_alert,
    generate_evacuation_route,
    log_incident,
    generate_incident_summary,
    update_map,
)
from .globe_control import (
    fly_to_location,
    set_camera_mode,
    toggle_data_layer,
    deploy_entity,
    move_entity,
    add_measurement,
    set_atmosphere,
    capture_current_view,
    add_threat_rings,
)

__all__ = [
    # Perception tools
    "analyze_frame",
    "compare_frames",
    # Analyst tools
    "query_historical_floods",
    "get_flood_extent",
    "get_infrastructure_at_risk",
    "get_population_at_risk",
    "compute_cascade",
    "evaluate_route_safety",
    "get_search_grounding",
    # Predictor tools
    "generate_risk_projection",
    "compute_confidence_decay",
    # Coordinator tools
    "send_emergency_alert",
    "generate_evacuation_route",
    "log_incident",
    "generate_incident_summary",
    "update_map",
    # Globe control tools
    "fly_to_location",
    "set_camera_mode",
    "toggle_data_layer",
    "deploy_entity",
    "move_entity",
    "add_measurement",
    "set_atmosphere",
    "capture_current_view",
    "add_threat_rings",
]
