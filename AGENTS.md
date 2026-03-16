# Agent Guide

## Scope
- This workspace contains several artifacts, but the actively developed application code is in `hawkeye/`.
- Treat `hawkeye/` as the primary app unless the task explicitly mentions another directory.
- Backend code lives in `hawkeye/app/`.
- Frontend code lives in `hawkeye/frontend/`.
- Tests live in `hawkeye/tests/`.
- Data assets and helper scripts live in `hawkeye/data/`.

## Checked Rule Files
- No `.cursor/rules/` directory was found.
- No `.cursorrules` file was found.
- No `.github/copilot-instructions.md` file was found.
- There are no repo-specific Cursor or Copilot instructions to merge beyond this file.

## Planning Documents
- `plan/HawkEye_Implementation_Plan_v3.md` is the roadmap for intended architecture, sequencing, and feature scope.
- `plan/HawkEye_BigQuery_Specification.md` is the authoritative source for BigQuery ingestion, schema optimization, SQL queries, and Analyst-agent cascade logic.
- For BigQuery, Groundsource, or cascade work, read `plan/HawkEye_BigQuery_Specification.md` before changing code.
- Prefer checked-in code for current runtime behavior, and use the plan docs to preserve intended direction when extending the system.

## High-Signal Repo Facts
- Python target is `>=3.11` per `hawkeye/pyproject.toml`.
- Backend stack: FastAPI, Google ADK, Google GenAI, BigQuery, Firestore, Google Maps, Gmail API, Shapely, PyProj.
- Frontend stack: Vite + React + Cesium.
- The checked-in backend is a custom FastAPI app in `hawkeye/app/main.py` that implements the ADK bidi-demo shape: browser WebSocket at `/ws/{user_id}/{session_id}`, `LiveRequestQueue`, and `Runner.run_live()`.
- All five checked-in agents (`root_agent` plus four sub-agents) currently use `gemini-2.5-flash-native-audio-latest`.
- Frontend has two entrypoints: the main app in `hawkeye/frontend/src/main.jsx` and a standalone Cesium harness in `hawkeye/frontend/src/globe-test-entry.jsx`.
- `hawkeye/frontend/src/App.jsx` imports the demo simulator side-effect module; the timeline is gated by `VITE_ENABLE_DEMO_TIMELINE`.
- Backend tests are mostly integration tests against real GCP services.
- There are also meaningful local-only backend tests for globe-control command mapping and downstream audio/text routing.
- Frontend currently has build scripts, but no checked-in test or lint script.
- There is no checked-in repo-owned linter config for Ruff, Black, MyPy, ESLint, or Prettier.
- The repo currently includes generated caches under `hawkeye/**/__pycache__/` and `hawkeye/.pytest_cache/`; treat them as noise unless the task is explicitly cleanup-related.

## Working Directories
- Run backend Python commands from `hawkeye/`.
- Run frontend npm commands from `hawkeye/frontend/`.
- Do not run tools from the workspace root unless the task truly spans multiple projects.

## Setup Commands
- Backend env: `python3 -m venv .venv`
- Activate venv: `source .venv/bin/activate`
- Install backend deps: `pip install -e ".[dev]"`
- Frontend deps: `npm install`
- `hawkeye/app/main.py` loads both the workspace root `.env` and `hawkeye/.env` when present, even though `hawkeye/README.md` says env is centralized at the root.

## Environment Variables
- Backend and tests commonly expect `GCP_PROJECT_ID`.
- GenAI-backed tools expect `GCP_API_KEY`.
- Maps-backed tools expect `GCP_MAPS_API_KEY` or fall back to `GCP_API_KEY` in some tests.
- BigQuery, Firestore, and Gmail-backed paths may also need `GOOGLE_APPLICATION_CREDENTIALS`.
- Coordinator email send uses optional `HAWKEYE_SENDER_EMAIL` for Gmail domain-wide delegation.
- Frontend Cesium/Google 3D tiles expect `VITE_GOOGLE_MAPS_API_KEY`.
- WebSocket URL defaults to `ws://localhost:8000/ws` unless `VITE_WS_URL` is set.
- Demo replay is controlled by `VITE_ENABLE_DEMO_TIMELINE`.
- The manual live harness reads `HAWKEYE_WS_URL`, `HAWKEYE_COMMAND_TIMEOUT_S`, and `HAWKEYE_IDLE_GRACE_S`.

## Build Commands
- Preferred ADK dev entrypoint from the implementation plan: `adk web app/hawkeye_agent`
- Backend dev server: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Frontend dev server: `npm run dev`
- Frontend production build: `npm run build`
- Frontend preview build: `npm run preview`
- If `hawkeye/frontend/dist/` exists, `hawkeye/app/main.py` mounts it at `/` via `StaticFiles`.
- There is no dedicated backend packaging/build command checked in.

## Test Commands
- Run all backend tests: `pytest`
- Quiet test run: `pytest -q`
- Run one file: `pytest tests/test_bigquery_service.py -q`
- Run local globe-control contract tests: `pytest tests/test_globe_control_backend.py -q`
- Run local audio-routing tests: `pytest tests/test_main_audio_routing.py -q`
- Run one test class: `pytest tests/test_bigquery_service.py::TestFloodFrequency -q`
- Run one test method: `pytest tests/test_tools.py::TestRouteSafety::test_safe_route_outside_flood -q`
- Run by keyword: `pytest -k route_safety -q`
- Stop after first failure: `pytest -x -q`
- Manual live harness: `python tests/test_live_agent.py`

## Test Reality
- `hawkeye/tests/` is not purely unit-test oriented; many tests call live GCP services.
- If credentials or data files are missing, tests intentionally skip via fixtures.
- Local-only tests do exist, especially in `hawkeye/tests/test_tools.py` for pure math and local geometry behavior.
- `hawkeye/tests/test_globe_control_backend.py` and `hawkeye/tests/test_main_audio_routing.py` are the best narrow checks for WebSocket protocol and direct globe-command changes.
- `hawkeye/tests/test_live_agent.py` is a manual WebSocket harness, not a normal `pytest` integration suite.
- Treat `hawkeye/TEST_REPORT.md` as evidence that `pytest` and `npm run build` were used successfully in this codebase.
- Prefer local-only or narrow tests first when touching Analyst or geometry logic, then expand to integration coverage.

## Single-Test Guidance
- Prefer exact node selection with `pytest path::Class::test_name -q`.
- Use `-k` only when exact test names are cumbersome.
- For integration-heavy files, run a single class or single method first before expanding scope.
- Good examples:
- `pytest tests/test_maps_service.py::TestGeocode::test_kampung_melayu -q`
- `pytest tests/test_earth_engine_service.py::TestFloodMetadata::test_falls_back_to_geojson_properties -q`

## Lint / Validation Commands
- No official lint command is configured in `hawkeye/pyproject.toml` or `hawkeye/frontend/package.json`.
- Do not invent `ruff`, `black`, `mypy`, `eslint`, or `prettier` as mandatory repo commands unless you also add and document them.
- Safe lightweight backend validation: `python -m compileall app tests`
- Safe narrow backend validation for protocol work: `pytest tests/test_globe_control_backend.py -q` and `pytest tests/test_main_audio_routing.py -q`
- Safe frontend validation: `npm run build`
- When touching WebSocket schemas or tool payloads, validate both backend and frontend sides together.

## Architecture Contracts
- Keep the browser/backend contract centered on the WebSocket at `/ws/{user_id}/{session_id}`.
- Audio/video streams and structured JSON events share the same WebSocket connection.
- Backend event emitters live primarily in `hawkeye/app/main.py`; keep emitted payloads aligned with frontend consumers.
- `hawkeye/app/main.py` also exposes `/health` and `/api/geojson/{layer_id}`; large GeoJSON payloads are cached server-side and served over REST instead of always being inlined on the socket.
- Direct globe-navigation commands are short-circuited in `hawkeye/app/main.py` before normal sub-agent routing. Preserve that path when changing navigation behavior.
- Use REST endpoints only for supporting concerns such as health checks or large GeoJSON payloads.
- When adding geospatial UI outputs, include provenance metadata where practical: `source`, `acquisition_window`, `method`, `confidence`, `updated_at`.

## BigQuery Rules
- Treat `plan/HawkEye_BigQuery_Specification.md` as the source of truth for query intent and SQL shape.
- Do not improvise alternative SQL for GroundsourceService if the spec already defines the query.
- Prefer parameterized BigQuery queries with explicit parameter types.
- For GeoJSON inputs, pass them as `STRING` parameters and use `ST_GEOGFROMGEOJSON()` inside SQL.
- Use `ST_DWITHIN` for radius queries rather than `ST_DISTANCE(...) < ...`.
- Preserve the distinction between current-risk infrastructure and newly-at-risk infrastructure at expanded flood levels.
- Cache static temporal queries such as monthly frequency and yearly trend.

## Geospatial Conventions
- BigQuery geography points use longitude first, latitude second: `ST_GEOGPOINT(lng, lat)`.
- GeoJSON coordinate arrays also use `[lng, lat]` ordering.
- Shapely and pyproj helpers should preserve consistent coordinate ordering across conversions.
- Buffer-based flood expansion is an approximation; do not present it as physically exact inundation modeling.
- When extending map outputs, prefer JSON-serializable GeoJSON payloads and stable keys like `route_geojson` and `flood_extent`.

## Files and Paths to Avoid Touching Lightly
- Do not edit `hawkeye/frontend/node_modules/`.
- Do not edit `hawkeye/frontend/dist/` unless the task is explicitly about build artifacts.
- Do not edit `hawkeye/.venv/`.
- Avoid editing checked-in cache artifacts such as `hawkeye/**/__pycache__/` and `hawkeye/.pytest_cache/`; they are generated noise, not source of truth.
- Avoid modifying `.env`, `credentials/*.json`, checkpoint files, parquet files, and generated GeoJSON unless requested.
- Treat `hawkeye/credentials/hawkeye-runtime-key.json` and any root `.env` files as sensitive.

## Python Style
- Use `from __future__ import annotations` at the top of Python modules, matching existing files.
- Keep a short module docstring in triple quotes when the file defines a service, agent, or test module.
- Use 4-space indentation.
- Favor explicit type hints on public functions, methods, fixtures, and important locals.
- Prefer built-in generics like `list[dict]`, `dict[str, Any]`, and union syntax like `X | None`.
- Import order is typically standard library, third-party, then local app imports, separated by blank lines.
- Use `snake_case` for functions, methods, variables, and fixtures.
- Use `PascalCase` for classes.
- Use `UPPER_SNAKE_CASE` for module-level constants.

## Python Design Conventions
- Keep service classes thin and focused on one external system or domain concern.
- Return JSON-serializable dictionaries from tool functions and service helpers that feed agents or WebSocket events.
- Cache expensive query results with private instance attributes when repeated calls are expected.
- Prefer small helper functions for spatial or serialization logic.
- Keep integration boundaries explicit: BigQuery in `bigquery_service.py`, Firestore in `firestore_service.py`, Maps in `maps_service.py`.
- For Analyst work, keep cascade logic in tools/services that directly mirror the documented multi-order structure.

## Python Error Handling
- Catch exceptions at integration boundaries and log them.
- For agent tools, prefer graceful fallback payloads with an `error` field instead of raising uncaught exceptions.
- Preserve stable response shapes on failure when existing code already does so.
- Use `logger = logging.getLogger(__name__)` instead of ad hoc prints in backend code.
- Return safe defaults like empty lists, `0`, `None`, or summary text when external services fail.
- When external services fail, keep tool outputs suitable for WebSocket emission instead of returning ad hoc exception structures.

## Testing Style
- Tests use `pytest` fixtures heavily; check `hawkeye/tests/conftest.py` before adding duplicate setup.
- Group related tests into `Test...` classes.
- Name tests descriptively with behavior-first phrases like `test_returns_nonzero_events`.
- Prefer direct assertions over elaborate helper layers.
- Skip integration tests when required env vars or data files are missing rather than forcing failures.

## Frontend Style
- Prefer function components and hooks.
- Use `PascalCase` for components and `camelCase` for hooks/utilities.
- Hooks should start with `use`, matching `useHawkEyeSocket` and `useAudioPipeline`.
- Keep message-type constants centralized in `hawkeye/frontend/src/types/messages.js`.
- Co-locate component CSS beside the component when following existing patterns.
- Preserve the local file's quote style and formatting style instead of restyling entire files; the frontend is currently not perfectly uniform.

## Frontend State and Events
- This app is event-driven around WebSocket messages.
- When changing message shapes, update backend emitters in `hawkeye/app/main.py` and frontend consumers together.
- Keep constant names and runtime payload keys aligned; this codebase already has some naming drift, so avoid making it worse.
- Add new message types to `hawkeye/frontend/src/types/messages.js` before wiring them into components.
- Prefer append-only event history updates via functional `setState` calls.
- `hawkeye/frontend/src/App.jsx` still supports both action-based `map_update` events and an older `layer_type` path; if you remove one, update both backend and demo flows together.
- `STATUS_UPDATE` naming is currently not perfectly aligned across backend and frontend (`water_level_m` / `population_at_risk` versus `waterLevel` / `population` in some paths). Treat protocol renames as coordinated changes.
- Strategic view map commands should remain compatible with the Cesium globe ref API (`flyTo`, `addGeoJsonOverlay`, `removeOverlay`, `addPulsingMarker`, `removeMarker`, `captureScreenshot`, `addFloodOverlay`, `updateFloodLevel`, `addEntity`, `moveEntity`, `addMeasurementLine`, `removeMeasurementLine`, `addThreatRings`, `setAtmosphere`, `setBirdEyeCamera`, `setStreetLevelCamera`, `startOrbitCamera`).

## Imports and Module Boundaries
- Avoid deep cross-module coupling when a service helper already exists.
- Backend code should import from `app.services` or `app.hawkeye_agent` rather than duplicating service clients inline.
- Frontend components should import shared constants/hooks instead of re-declaring protocol values.
- Keep side-effect imports obvious, such as CSS imports and demo simulator bootstrapping.

## Naming Guidance
- Use domain names that match the disaster-response vocabulary already present: `flood_extent`, `water_level`, `population_at_risk`, `route_geojson`.
- Prefer full descriptive names over abbreviations unless the abbreviation is already standard in the file.
- Mirror existing API payload keys when extending an established response object.

## Change Strategy
- Make surgical changes.
- Preserve public payload shapes unless the task explicitly includes coordinated protocol updates.
- If you must change a contract, update tests and both sides of the interface in the same patch.
- Avoid broad formatting-only diffs.

## Practical Defaults for Agents
- Assume backend-first validation with `pytest -q` when Python code changes.
- Assume frontend validation with `npm run build` when React/Cesium code changes.
- For WebSocket, globe-control, or message-schema changes, start with `pytest tests/test_globe_control_backend.py -q` and `pytest tests/test_main_audio_routing.py -q` before broader integration coverage.
- For risky integration changes, run the narrowest relevant test first, then broaden scope.
- If a command is missing from project config, state that clearly instead of pretending it exists.
