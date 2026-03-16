"""Prompt guardrail tests for tool-grounded agent behavior."""

from __future__ import annotations

from app.hawkeye_agent.agent import (
    ANALYST_INSTRUCTION,
    COMMANDER_INSTRUCTION,
    COORDINATOR_INSTRUCTION,
)


def test_instructions_do_not_embed_demo_numeric_outputs():
    combined = " ".join(
        [COMMANDER_INSTRUCTION, ANALYST_INSTRUCTION, COORDINATOR_INSTRUCTION]
    )
    banned_literals = (
        '"population_at_risk": 128000',
        '"estimated_residents_without_power": 160000',
        "state EXACTLY: \"Commander, I must disagree with routing to Tebet.\"",
    )
    for literal in banned_literals:
        assert literal not in combined


def test_instruction_sets_include_tool_grounding_contract():
    assert "Tool-Grounded Response Contract" in COMMANDER_INSTRUCTION
    assert "Never output scenario/example values" in ANALYST_INSTRUCTION
