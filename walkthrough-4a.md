# HawkEye End-to-End Demo Wiring — Walkthrough

## What Changed

Two files modified to wire the full demo flow:

### [main.py](file:///Users/tayyabkhan/Downloads/gemini-agent/hawkeye/app/main.py) (370 → 779 lines)

render_diffs(file:///Users/tayyabkhan/Downloads/gemini-agent/hawkeye/app/main.py)

**New capabilities:**

| Feature | How It Works |
|---------|-------------|
| **Tool Result → UI Events** | `_map_tool_result_to_events()` maps 13 tool names to structured WebSocket JSON events. `process_event()` now detects `function_response` parts and calls this mapper. |
| **Large Payload REST** | GeoJSON payloads >50KB are cached in `_geojson_cache` and served via `GET /api/geojson/{id}`. The WebSocket event contains a `url` field instead of inline data. |
| **Proactive Monitoring** | Third concurrent task per connection polls Firestore `sensor_data/current` every 30s. Injects `[SYSTEM ALERT]` into `LiveRequestQueue` when water > 4.0m. Only fires once per session. |
| **Mode Switching** | `mode_change` messages inject `[SYSTEM]` text into `LiveRequestQueue` with mode-specific behavior description. |
| **Seed Endpoint** | `POST /api/seed-water-level?level=4.1` writes to Firestore for demo setup. |

---

### [agent.py](file:///Users/tayyabkhan/Downloads/gemini-agent/hawkeye/app/hawkeye_agent/agent.py) (428 → 457 lines)

render_diffs(file:///Users/tayyabkhan/Downloads/gemini-agent/hawkeye/app/hawkeye_agent/agent.py)

**Key instruction additions:**

1. **Route Safety Check** — `COMMANDER_INSTRUCTION` now explicitly mandates calling `evaluate_route_safety()` before confirming any evacuation route. If unsafe, the agent must disagree with specific evidence.

2. **Mode Awareness** — Instruction tells the agent to respond to `[SYSTEM]` messages about mode changes.

3. **Proactive Alert Response** — Instruction tells the agent to respond to `[SYSTEM ALERT]` messages by immediately reporting threat level and calling `get_flood_extent()`.

4. **Clearer Sub-Agent Routing** — Descriptions now list exact trigger phrases for each sub-agent so the LLM routes correctly.

---

## What Was Tested

### Automated (8/8 passing)

| Test | Tool | Events Emitted |
|------|------|---------------|
| `test_get_flood_extent` | `get_flood_extent` | `map_update` + `ee_update` |
| `test_compute_cascade` | `compute_cascade` | `status_update` + `incident_log_entry` + `map_update` (markers) |
| `test_get_infrastructure_at_risk` | `get_infrastructure_at_risk` | `map_update` (markers) |
| `test_generate_evacuation_route` | `generate_evacuation_route` | `map_update` (route overlay) |
| `test_generate_risk_projection` | `generate_risk_projection` | `feed_update` (PREDICTION mode) |
| `test_send_emergency_alert` | `send_emergency_alert` | `incident_log_entry` |
| `test_evaluate_route_safety_unsafe` | `evaluate_route_safety` | `map_update` (danger markers) |
| `test_large_payload_uses_rest` | Large GeoJSON | URL ref in event, cache populated |

Both files pass `py_compile` syntax check.

---

## Manual Testing Instructions

```bash
# 1. Seed Firestore water level for proactive alert
curl -X POST "http://localhost:8000/api/seed-water-level?level=4.1"

# 2. Start the backend
cd hawkeye && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Open frontend (in another terminal)
cd hawkeye/frontend && npm run dev

# 4. Open browser → Start Mission → wait 30s for proactive alert
# 5. Follow demo sequence: flood extent → cascade → evacuation → alert → summary
```

> [!IMPORTANT]
> The backend needs these env vars set: `GCP_PROJECT_ID`, `GCP_API_KEY`, `GCP_MAPS_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`
