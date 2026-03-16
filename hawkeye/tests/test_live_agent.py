"""Manual live WebSocket harness for HawkEye backend agent verification.

Run directly (recommended):
    python tests/test_live_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

try:
    import websockets
except ImportError:  # pragma: no cover - manual harness safeguard
    websockets = None


WS_URL = os.getenv(
    "HAWKEYE_WS_URL",
    "ws://localhost:8000/ws/test_user/test_session",
)
COMMAND_TIMEOUT_S = float(os.getenv("HAWKEYE_COMMAND_TIMEOUT_S", "90"))
IDLE_GRACE_S = float(os.getenv("HAWKEYE_IDLE_GRACE_S", "4"))

COMMAND_SEQUENCE = [
    "Show me the flood extent",
    "What happens if water rises 2 more meters",
    "Route evacuation from Kampung Melayu to nearest shelter",
    "Send emergency advisory",
    "Give me the incident summary",
    "Fly to Kampung Melayu",
    "Show me the hospitals",
    "Orbit this area",
    "Deploy a helicopter to the university campus",
    "Switch to night vision",
    "How far is Kampung Melayu from University of Indonesia",
]

GLOBE_COMMAND_EXPECTATIONS = {
    "Fly to Kampung Melayu": {"action": "fly_to"},
    "Show me the hospitals": {"action": "toggle_layer"},
    "Orbit this area": {"action": "camera_mode"},
    "Deploy a helicopter to the university campus": {"action": "deploy_entity"},
    "Switch to night vision": {"action": "set_atmosphere"},
    "How far is Kampung Melayu from University of Indonesia": {
        "action": "add_measurement",
        "requires_label": True,
    },
}


@dataclass
class CommandRun:
    command: str
    events: list[dict[str, Any]]
    saw_turn_complete: bool
    timed_out: bool


def _decode_message(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, (bytes, bytearray)):
        return {
            "type": "audio_chunk",
            "bytes": len(message),
        }

    try:
        payload = json.loads(message)
        if isinstance(payload, dict):
            return payload
        return {"type": "raw_json", "payload": payload}
    except json.JSONDecodeError:
        return {
            "type": "transcript",
            "speaker": "agent",
            "text": message,
        }


def _print_event(event: dict[str, Any]) -> None:
    event_type = event.get("type", "unknown")
    if event_type == "audio_chunk":
        print(f"[audio_chunk] {event.get('bytes', 0)} bytes")
        return
    print(f"[{event_type}] {json.dumps(event, ensure_ascii=True, default=str)}")


def _collect_text(event: dict[str, Any]) -> str:
    text_fragments = []
    if isinstance(event.get("text"), str):
        text_fragments.append(event["text"])
    if isinstance(event.get("summary_text"), str):
        text_fragments.append(event["summary_text"])
    if isinstance(event.get("recommendation"), str):
        text_fragments.append(event["recommendation"])
    return " ".join(text_fragments)


async def _send_text_command(ws: Any, text: str) -> None:
    payload = {"text": text}
    print(f"\n>>> COMMAND: {text}")
    await ws.send(json.dumps(payload))


async def _collect_turn(ws: Any, command: str) -> CommandRun:
    events: list[dict[str, Any]] = []
    saw_turn_complete = False
    timed_out = False

    turn_start = time.monotonic()
    last_event_ts = turn_start

    while True:
        elapsed = time.monotonic() - turn_start
        if elapsed >= COMMAND_TIMEOUT_S:
            timed_out = True
            print(
                f"[timeout] No turn completion after {COMMAND_TIMEOUT_S:.0f}s for command: {command}"
            )
            break

        wait_timeout = min(2.0, COMMAND_TIMEOUT_S - elapsed)
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            idle_time = time.monotonic() - last_event_ts
            if saw_turn_complete and idle_time >= IDLE_GRACE_S:
                break
            continue

        last_event_ts = time.monotonic()
        event = _decode_message(message)
        events.append(event)
        _print_event(event)

        if event.get("type") == "turn_complete":
            saw_turn_complete = True

    return CommandRun(
        command=command,
        events=events,
        saw_turn_complete=saw_turn_complete,
        timed_out=timed_out,
    )


def _summarize(results: list[CommandRun]) -> int:
    print("\n=== LIVE AGENT HARNESS SUMMARY ===")

    overall_counter: Counter[str] = Counter()
    failures: list[str] = []

    route_text_corpus = ""
    cascade_text_corpus = ""

    for run in results:
        type_counter: Counter[str] = Counter(evt.get("type", "unknown") for evt in run.events)
        overall_counter.update(type_counter)
        print(f"\nCommand: {run.command}")
        print(f"- Event counts: {dict(type_counter)}")
        print(f"- Turn complete seen: {run.saw_turn_complete}")
        print(f"- Timed out: {run.timed_out}")

        if run.timed_out:
            failures.append(f"Timed out waiting for completion: {run.command}")

        expected = GLOBE_COMMAND_EXPECTATIONS.get(run.command)
        if expected:
            map_updates = [e for e in run.events if e.get("type") == "map_update"]
            if not map_updates:
                failures.append(f"No map_update emitted for command: {run.command}")
            else:
                matching = [
                    e
                    for e in map_updates
                    if e.get("action") == expected.get("action")
                ]
                if not matching:
                    failures.append(
                        f"Expected map_update action={expected.get('action')} for command: {run.command}"
                    )
                elif expected.get("requires_label"):
                    if not any(
                        isinstance(evt.get("label"), str) and evt.get("label").strip()
                        for evt in matching
                    ):
                        failures.append(
                            f"Expected a non-empty measurement label for command: {run.command}"
                        )

        if run.command == "What happens if water rises 2 more meters":
            cascade_text_corpus = " ".join(_collect_text(e) for e in run.events)
            if "status_update" not in type_counter:
                failures.append("Cascade command did not emit status_update")

        if run.command == "Route evacuation from Kampung Melayu to nearest shelter":
            route_text_corpus = " ".join(_collect_text(e) for e in run.events)
            if "map_update" not in type_counter:
                failures.append("Route command did not emit map_update")

    print(f"\nOverall event totals: {dict(overall_counter)}")

    cascade_preview = cascade_text_corpus.strip()[:400]
    if cascade_preview:
        print(f"\nCascade text preview: {cascade_preview}")
    else:
        failures.append("No cascade narration captured")

    route_text_lower = route_text_corpus.lower()
    mentions_tebet = "tebet" in route_text_lower
    shows_disagreement = any(
        phrase in route_text_lower
        for phrase in (
            "must disagree",
            "cannot recommend",
            "advise against",
            "do not use this route",
            "unsafe",
        )
    )
    print(
        f"\nRoute disagreement check: tebet={mentions_tebet}, disagreement_language={shows_disagreement}"
    )
    if not mentions_tebet:
        failures.append("Route flow did not mention Tebet")
    if not shows_disagreement:
        failures.append("Route flow did not show explicit disagreement/unsafe warning")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nAll live-harness checks passed.")
    return 0


async def _run() -> int:
    if websockets is None:
        print(
            "Missing dependency: websockets. Install project dependencies before running this harness.",
            file=sys.stderr,
        )
        return 2

    print(f"Connecting to: {WS_URL}")
    try:
        async with websockets.connect(WS_URL, max_size=20_000_000) as ws:
            all_runs: list[CommandRun] = []
            for command in COMMAND_SEQUENCE:
                await _send_text_command(ws, command)
                run = await _collect_turn(ws, command)
                all_runs.append(run)
            return _summarize(all_runs)
    except Exception as exc:
        print(f"WebSocket harness failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
