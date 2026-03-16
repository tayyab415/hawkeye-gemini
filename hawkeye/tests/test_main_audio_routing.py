"""Unit tests for backend audio routing in process_event."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.main import process_event


class _CaptureWebSocket:
    def __init__(self) -> None:
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)


@pytest.mark.asyncio
async def test_process_event_forwards_content_audio_bytes() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=SimpleNamespace(data=b"\x01\x02\x03\x04"),
            parts=[],
        ),
        server_content=None,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == [b"\x01\x02\x03\x04"]
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_forwards_content_part_inline_audio_bytes() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    inline_data=SimpleNamespace(data=b"\x11\x12\x13\x14", mime_type="audio/pcm"),
                    text=None,
                    thought=False,
                    function_call=None,
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == [b"\x11\x12\x13\x14"]
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_skips_non_audio_content_part_inline_data_and_keeps_tool_call() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    inline_data=SimpleNamespace(data=b"\xff\xd8\xff", mime_type="image/jpeg"),
                    text=None,
                    thought=False,
                    function_call=SimpleNamespace(
                        name="fly_to_location",
                        args={"location": "A"},
                    ),
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "tool_call"
    assert payload["tool"] == "fly_to_location"
    assert payload["args"] == {"location": "A"}


@pytest.mark.asyncio
async def test_process_event_forwards_server_content_inline_audio_bytes() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=None,
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        inline_data=SimpleNamespace(data=b"\x05\x06\x07\x08"),
                        text=None,
                    )
                ]
            )
        ),
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == [b"\x05\x06\x07\x08"]
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_emits_transcript_from_server_content_text() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=None,
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(
                parts=[SimpleNamespace(inline_data=None, text="Agent text response")]
            )
        ),
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "transcript"
    assert payload["speaker"] == "agent"
    assert payload["text"] == "Agent text response"


@pytest.mark.asyncio
async def test_process_event_skips_transcript_for_thought_text_part() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text="**Acknowledge the Greeting**",
                    thought=True,
                    function_call=None,
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_skips_partial_transcript_text() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text="Agent text response",
                    thought=False,
                    function_call=None,
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=True,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_suppresses_non_json_content_part_text() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text="**Initiating Standard Response**",
                    thought=False,
                    function_call=None,
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=event,
        websocket=websocket,
        session_id="test-session-suppress-non-json",
    )

    assert websocket.sent_bytes == []
    assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_process_event_keeps_structured_ui_text_during_partial_event() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=json.dumps({"type": "status_update", "waterLevel": 4.2}),
                    thought=False,
                    function_call=None,
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=True,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "status_update"
    assert payload["waterLevel"] == 4.2


@pytest.mark.asyncio
async def test_process_event_buffers_output_transcription_until_finished() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-output-transcription-finish"

    partial_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="Agent ", finished=False),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    finished_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="response", finished=True),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=partial_event, websocket=websocket, session_id=session_id)
    await process_event(event=finished_event, websocket=websocket, session_id=session_id)

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "transcript"
    assert payload["speaker"] == "agent"
    assert payload["text"] == "Agent response"


@pytest.mark.asyncio
async def test_process_event_dedupes_cumulative_output_transcription_on_finish() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-output-transcription-cumulative-finish"

    initial_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="Incident command ", finished=False),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    cumulative_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(
            text="Incident command system ready for deployment",
            finished=False,
        ),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    repeated_finished_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(
            text="Incident command system ready for deployment",
            finished=True,
        ),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=initial_event, websocket=websocket, session_id=session_id)
    await process_event(event=cumulative_event, websocket=websocket, session_id=session_id)
    await process_event(
        event=repeated_finished_event,
        websocket=websocket,
        session_id=session_id,
    )

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "transcript"
    assert payload["speaker"] == "agent"
    assert payload["text"] == "Incident command system ready for deployment"


@pytest.mark.asyncio
async def test_process_event_dedupes_cumulative_output_transcription_on_turn_complete() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-output-transcription-cumulative-turn-complete"

    initial_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="Hawk Eye Commander ", finished=False),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    cumulative_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(
            text="Hawk Eye Commander operational and standing by",
            finished=False,
        ),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    repeated_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(
            text="Hawk Eye Commander operational and standing by",
            finished=False,
        ),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    turn_complete_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=None,
        partial=False,
        turn_complete=True,
        interrupted=False,
    )

    await process_event(event=initial_event, websocket=websocket, session_id=session_id)
    await process_event(event=cumulative_event, websocket=websocket, session_id=session_id)
    await process_event(event=repeated_event, websocket=websocket, session_id=session_id)
    await process_event(
        event=turn_complete_event,
        websocket=websocket,
        session_id=session_id,
    )

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 2
    transcript_payload = json.loads(websocket.sent_text[0])
    turn_complete_payload = json.loads(websocket.sent_text[1])
    assert transcript_payload["type"] == "transcript"
    assert transcript_payload["speaker"] == "agent"
    assert transcript_payload["text"] == "Hawk Eye Commander operational and standing by"
    assert turn_complete_payload["type"] == "turn_complete"


@pytest.mark.asyncio
async def test_process_event_clears_output_transcription_on_interruption() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-output-transcription-interrupt"

    buffered_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="stale ", finished=False),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    interrupted_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=None,
        partial=False,
        turn_complete=False,
        interrupted=True,
    )
    next_finished_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="fresh", finished=True),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=buffered_event, websocket=websocket, session_id=session_id)
    await process_event(event=interrupted_event, websocket=websocket, session_id=session_id)
    await process_event(
        event=next_finished_event,
        websocket=websocket,
        session_id=session_id,
    )

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 2
    interrupted_payload = json.loads(websocket.sent_text[0])
    transcript_payload = json.loads(websocket.sent_text[1])
    assert interrupted_payload["type"] == "interrupted"
    assert transcript_payload["type"] == "transcript"
    assert transcript_payload["text"] == "fresh"


@pytest.mark.asyncio
async def test_process_event_flushes_output_transcription_on_turn_complete() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-output-transcription-turn-complete"

    buffered_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=SimpleNamespace(text="spoken text", finished=False),
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    turn_complete_event = SimpleNamespace(
        content=None,
        server_content=None,
        output_transcription=None,
        partial=False,
        turn_complete=True,
        interrupted=False,
    )

    await process_event(event=buffered_event, websocket=websocket, session_id=session_id)
    await process_event(
        event=turn_complete_event,
        websocket=websocket,
        session_id=session_id,
    )

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 2
    transcript_payload = json.loads(websocket.sent_text[0])
    turn_complete_payload = json.loads(websocket.sent_text[1])
    assert transcript_payload["type"] == "transcript"
    assert transcript_payload["text"] == "spoken text"
    assert turn_complete_payload["type"] == "turn_complete"


@pytest.mark.asyncio
async def test_process_event_preserves_tool_call_when_thought_text_present() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text="hidden reasoning",
                    thought=True,
                    function_call=SimpleNamespace(name="fly_to_location", args={"location": "A"}),
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "tool_call"
    assert payload["tool"] == "fly_to_location"
    assert payload["args"] == {"location": "A"}


@pytest.mark.asyncio
async def test_process_event_sends_turn_complete_without_content() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=None,
        server_content=None,
        turn_complete=True,
        interrupted=False,
    )

    await process_event(event=event, websocket=websocket, session_id="test-session")

    assert websocket.sent_bytes == []
    assert len(websocket.sent_text) == 1
    payload = json.loads(websocket.sent_text[0])
    assert payload["type"] == "turn_complete"


@pytest.mark.asyncio
async def test_process_event_emits_pending_status_for_long_running_tool_call() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=SimpleNamespace(
                        name="compute_cascade",
                        args={
                            "flood_geojson": "{}",
                            "water_level_delta_m": 1.2,
                        },
                    ),
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=event,
        websocket=websocket,
        session_id="test-session-tool-status-pending",
    )

    payloads = [json.loads(message) for message in websocket.sent_text]
    assert len(payloads) == 2
    assert payloads[0]["type"] == "tool_call"
    assert payloads[1]["type"] == "tool_status"
    assert payloads[1]["tool"] == "compute_cascade"
    assert payloads[1]["state"] == "pending"
    assert payloads[1]["status"] == "pending"
    assert payloads[1]["long_running"] is True
    assert isinstance(payloads[1]["call_id"], str) and payloads[1]["call_id"]


@pytest.mark.asyncio
async def test_process_event_emits_complete_status_for_long_running_tool_response() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-tool-status-complete"

    tool_call_event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=SimpleNamespace(
                        name="compute_cascade",
                        args={
                            "flood_geojson": "{}",
                            "water_level_delta_m": 1.5,
                        },
                    ),
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    tool_response_event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=None,
                    function_response=SimpleNamespace(
                        name="compute_cascade",
                        response={
                            "result": {
                                "first_order": {"population_at_risk": 1234},
                                "second_order": {"newly_isolated_hospitals": []},
                                "third_order": {},
                                "fourth_order": {},
                                "hospitals_at_risk": [],
                                "summary": "Cascade simulated",
                            }
                        },
                    ),
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=tool_call_event,
        websocket=websocket,
        session_id=session_id,
    )
    await process_event(
        event=tool_response_event,
        websocket=websocket,
        session_id=session_id,
    )

    payloads = [json.loads(message) for message in websocket.sent_text]
    tool_status_events = [
        payload for payload in payloads if payload.get("type") == "tool_status"
    ]
    assert [payload["state"] for payload in tool_status_events] == [
        "pending",
        "running",
        "complete",
    ]
    complete_status = tool_status_events[-1]
    assert complete_status["tool"] == "compute_cascade"
    assert complete_status["status"] == "complete"
    assert isinstance(complete_status.get("duration_ms"), int)
    call_ids = {payload["call_id"] for payload in tool_status_events}
    assert len(call_ids) == 1


@pytest.mark.asyncio
async def test_process_event_emits_error_status_for_long_running_tool_error_result() -> None:
    websocket = _CaptureWebSocket()
    session_id = "test-session-tool-status-error"

    tool_call_event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=SimpleNamespace(
                        name="get_population_at_risk",
                        args={
                            "flood_geojson": "{}",
                        },
                    ),
                    function_response=None,
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )
    tool_response_event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=None,
                    function_response=SimpleNamespace(
                        name="get_population_at_risk",
                        response={"error": "BigQuery unavailable"},
                    ),
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=tool_call_event,
        websocket=websocket,
        session_id=session_id,
    )
    await process_event(
        event=tool_response_event,
        websocket=websocket,
        session_id=session_id,
    )

    payloads = [json.loads(message) for message in websocket.sent_text]
    tool_status_events = [
        payload for payload in payloads if payload.get("type") == "tool_status"
    ]
    assert [payload["state"] for payload in tool_status_events] == [
        "pending",
        "running",
        "error",
    ]
    error_status = tool_status_events[-1]
    assert error_status["tool"] == "get_population_at_risk"
    assert error_status["status"] == "error"
    assert error_status["error"] == "BigQuery unavailable"


@pytest.mark.asyncio
async def test_process_event_emits_usage_update_when_usage_metadata_present() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=None,
        server_content=None,
        usage_metadata={
            "prompt_token_count": 320,
            "candidates_token_count": 85,
            "total_token_count": 405,
            "context_window_token_count": 6000,
            "session_pressure": "moderate",
        },
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=event,
        websocket=websocket,
        session_id="test-session-usage-update",
    )

    payloads = [json.loads(message) for message in websocket.sent_text]
    assert len(payloads) == 1
    usage_payload = payloads[0]
    assert usage_payload["type"] == "usage_update"
    assert usage_payload["usage"]["input_tokens"] == 320
    assert usage_payload["usage"]["output_tokens"] == 85
    assert usage_payload["usage"]["total_tokens"] == 405
    assert usage_payload["context"]["context_tokens"] == 6000
    assert usage_payload["session_health"]["pressure"] == "MODERATE"


@pytest.mark.asyncio
async def test_process_event_emits_grounding_update_for_tool_sources() -> None:
    websocket = _CaptureWebSocket()
    event = SimpleNamespace(
        content=SimpleNamespace(
            audio=None,
            parts=[
                SimpleNamespace(
                    text=None,
                    thought=False,
                    function_call=None,
                    function_response=SimpleNamespace(
                        name="get_search_grounding",
                        response={
                            "result": {
                                "query": "Jakarta flood advisories",
                                "response": "Latest advisories sourced from official channels.",
                                "grounded": True,
                                "sources": [
                                    {
                                        "title": "Jakarta BPBD Bulletin",
                                        "uri": "https://bpbd.jakarta.go.id/advisory",
                                    }
                                ],
                            }
                        },
                    ),
                )
            ],
        ),
        server_content=None,
        partial=False,
        turn_complete=False,
        interrupted=False,
    )

    await process_event(
        event=event,
        websocket=websocket,
        session_id="test-session-grounding-update",
    )

    payloads = [json.loads(message) for message in websocket.sent_text]
    assert len(payloads) == 1
    grounding_payload = payloads[0]
    assert grounding_payload["type"] == "grounding_update"
    assert grounding_payload["tool"] == "get_search_grounding"
    assert grounding_payload["grounded"] is True
    assert grounding_payload["source_count"] == 1
    assert grounding_payload["citations"][0]["title"] == "Jakarta BPBD Bulletin"
    assert grounding_payload["citations"][0]["url"] == "https://bpbd.jakarta.go.id/advisory"
