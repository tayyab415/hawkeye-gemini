# HAWK EYE — Parallelized Implementation Plan v3

## Companion Documents

- **HawkEye_Master_Plan.md** — What we're building, why, and the full feature set
- **HawkEye_BigQuery_Specification.md** — Complete BigQuery schema, queries, service layer, and setup. Any agent working on BigQuery MUST read this document first.

## How To Read This Document

Each **Step** is a sequential gate — complete Step N before starting Step N+1. Within each Step, the **Tracks** are fully independent and can be built by different coding agents simultaneously. No Track within a Step depends on another Track in the same Step.

Each Track states its **output contract** — the exact interface it produces — so the next Step knows what to consume.

---

## Architecture Decision: ADK bidi-demo as Foundation

The backend is NOT a custom FastAPI server. It is Google's official ADK bidi-demo pattern:

- FastAPI is bundled inside `google-adk`. ADK's `adk web` command runs it automatically.
- The browser connects to the backend via a **WebSocket** at `/ws/{user_id}/{session_id}`.
- Inside the backend, ADK's `LiveRequestQueue` buffers all incoming messages (audio, video frames, text) and feeds them to `Runner.run_live()`.
- `Runner.run_live()` manages an internal WebSocket to the Gemini Live API. You never touch this directly.
- Two concurrent async tasks run per connection: **upstream** (browser → LiveRequestQueue) and **downstream** (run_live() events → browser).
- ADK handles session resumption (10-min timeout), reconnection, and context window compression automatically via `RunConfig`.
- Deployment is `adk deploy cloud_run` — one command builds the container and deploys.

The reference implementation: `google/adk-samples/python/agents/bidi-demo`

```
Browser (React)
    │
    │  WebSocket  /ws/{user_id}/{session_id}
    ▼
FastAPI (bundled in ADK, deployed on Cloud Run)
    │
    ├── WebSocket endpoint
    │    ├── Upstream task: browser audio/video/text → LiveRequestQueue
    │    └── Downstream task: run_live() events → browser
    │
    ├── LiveRequestQueue (ADK async buffer)
    │
    ├── Runner.run_live() ──→ Gemini Live API (internal WebSocket, managed by ADK)
    │    └── Root Agent: Hawk Eye Commander
    │         ├── Sub-Agent: Perception
    │         ├── Sub-Agent: Analyst
    │         ├── Sub-Agent: Predictor
    │         └── Sub-Agent: Coordinator
    │
    ├── Custom REST endpoints (optional: serve GeoJSON, health check)
    │
    └── Services (BigQuery, Firestore, Maps — imported by agent tools)
```

Two types of data flow through the single WebSocket:

1. **Audio/video streams** — binary audio chunks (PCM 16-bit 16kHz) and JPEG frames, handled natively by ADK's LiveRequestQueue
2. **Structured JSON messages** — map updates, incident logs, status updates, UI commands. These are sent as text frames on the same WebSocket. The downstream task inspects `run_live()` events and also emits custom JSON messages when tools execute (e.g., when the Coordinator agent generates an evacuation route, it emits a `map_update` JSON alongside the agent's voice response)

---

## Architecture Clarification: Earth Engine + Maps/Cesium

These are complementary layers, not substitutes:

- **Google Earth Engine (analysis layer):** Detect flood extent and change from Sentinel-1/2, compute metrics, and export GeoJSON/raster assets.
- **BigQuery (intelligence layer):** Historical flood pattern matching and infrastructure-at-risk spatial joins.
- **Google Maps Tile API + CesiumJS (visualization layer):** Render photorealistic 3D operational context for commanders.

In other words: **Earth Engine decides what is happening**, **BigQuery explains consequences**, and **Cesium visualizes the situation**.

Every geospatial output emitted to the UI should include provenance metadata: `source`, `acquisition_window`, `method`, `confidence`, `updated_at`.

---

## STEP 0: Project Scaffolding + GCP Bootstrap

**Goal:** Working environment, credentials, GCP services provisioned, Groundsource data loaded.

**Sequential — one person, ~60 minutes. BigQuery setup is the longest part.**

### Tasks (In Order)

0.1 — Create GCP project. Enable APIs:
`run.googleapis.com`, `firestore.googleapis.com`, `storage.googleapis.com`, `aiplatform.googleapis.com`, `bigquery.googleapis.com`, `earthengine.googleapis.com`, `maps-backend.googleapis.com`, `places-backend.googleapis.com`, `directions-backend.googleapis.com`, `geocoding-backend.googleapis.com`, `elevation-backend.googleapis.com`, `tile.googleapis.com`, `gmail.googleapis.com`

0.2 — Create API keys: Google Maps key (restricted to Maps APIs) and Gemini API key. Use separate env vars (e.g., `GCP_MAPS_API_KEY` vs `GCP_API_KEY`) to avoid key-mix mistakes. Maps key must explicitly allow `tile.googleapis.com` for Cesium photorealistic 3D tiles. Create service account with roles for BigQuery, Firestore, Cloud Storage, Earth Engine. Download credentials JSON.

0.3 — Create project structure following ADK conventions:

```
hawkeye/
├── app/                          # ADK backend (follows bidi-demo pattern)
│   ├── main.py                   # FastAPI + WebSocket endpoint
│   ├── hawkeye_agent/            # ADK agent package
│   │   ├── __init__.py
│   │   ├── agent.py              # Root agent + sub-agent definitions
│   │   └── tools/                # Tool functions for each sub-agent
│   │       ├── perception.py
│   │       ├── analyst.py
│   │       ├── predictor.py
│   │       └── coordinator.py
│   ├── services/                 # GCP service wrappers
│   │   ├── bigquery_service.py
│   │   ├── firestore_service.py
│   │   ├── maps_service.py
│   │   └── earth_engine_service.py
│   ├── static/                   # ADK serves static files (audio worklets etc.)
│   │   └── js/
│   └── .env
├── frontend/                     # React app (separate deployment)
│   ├── src/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── App.jsx
│   └── package.json
├── data/                         # Pre-computed GeoJSON, scripts
│   ├── compute_flood_extent.py
│   ├── load_groundsource.sh
│   ├── load_infrastructure.py
│   └── geojson/
├── infra/                        # IaC scripts
│   └── deploy.sh
├── pyproject.toml                # Python dependencies (google-adk, etc.)
└── README.md
```

0.4 — Install dependencies: `google-adk` (includes FastAPI, Uvicorn, google-genai), `google-cloud-bigquery`, `google-cloud-firestore`, `google-cloud-storage`, `ee` (Earth Engine)

0.5 — Create Firestore database (`us-central1`, native mode)

0.6 — Create BigQuery dataset `hawkeye`

0.7 — Create Cloud Storage bucket `{project-id}-hawkeye-media`

0.8 — **BigQuery Groundsource Setup (follow HawkEye_BigQuery_Specification.md Phase 1 + 2):**
- 0.8a: Download Groundsource parquet from Zenodo (636 MB). Upload to Cloud Storage.
- 0.8b: Load into BigQuery raw table: `bq load --source_format=PARQUET hawkeye.groundsource_raw gs://{bucket}/data/groundsource.parquet`
- 0.8c: Verify the `geometry` column data type. If it's not GEOGRAPHY, determine if it's WKT or GeoJSON and note which conversion path to use (see BigQuery spec Phase 1.2).
- 0.8d: Create optimized table `hawkeye.groundsource` with GEOGRAPHY column, parsed DATE columns, clustering on geometry, computed `duration_days` and `area_sqkm` (see BigQuery spec Phase 2.1 — use the correct SQL variant based on 0.8c).
- 0.8e: Create materialized view `hawkeye.groundsource_jakarta` — 50km radius around Jakarta center (see BigQuery spec Phase 2.2).
- 0.8f: Verify Jakarta data exists: `SELECT COUNT(*) FROM hawkeye.groundsource_jakarta` should return 100+ rows.

0.9 — **BigQuery Infrastructure Setup (follow HawkEye_BigQuery_Specification.md Phase 1.3):**
- 0.9a: Run `data/load_infrastructure.py` to extract Jakarta hospitals, schools, shelters, power stations from OpenStreetMap Overpass API and load into `hawkeye.infrastructure_raw`.
- 0.9b: Create optimized table `hawkeye.infrastructure` with GEOGRAPHY `location` column clustered by location (see BigQuery spec Phase 2.3).
- 0.9c: Verify infrastructure data: `SELECT type, COUNT(*) FROM hawkeye.infrastructure GROUP BY type` should show hospitals, schools, etc.

0.10 — **BigQuery Verification (run ALL queries from BigQuery spec Phase 2.4):**
- Total Groundsource count (~2.2M+)
- Jakarta flood count (100+)
- Infrastructure by type
- Spatial query: floods near Kampung Melayu
- Spatial query: infrastructure near Kampung Melayu
- If ANY verification fails, debug before proceeding. The entire Analyst agent depends on these.

0.11 — Verify: run `adk web app/hawkeye_agent` with a minimal stub agent that just says "Hawk Eye online." Confirm it starts on localhost:8000 and the ADK dev UI works.

**Output:** Monorepo skeleton. All GCP services live. BigQuery has three production tables: `groundsource` (2.2M+ rows, GEOGRAPHY clustered), `groundsource_jakarta` (materialized view, 100+ rows), `infrastructure` (Jakarta hospitals/schools/shelters/power stations). All verification queries passing. A minimal ADK agent running locally via `adk web`.

---

## STEP 1: Three Independent Foundations (3 Parallel Tracks)

**Goal:** Build the three pieces that have zero shared dependencies: the React UI shell, the CesiumJS 3D component, and the data services layer. These are all separate codebases that don't import from each other.

**Why only 3 tracks, not 4?** In v1 I had "WebSocket server" and "Gemini Live connection" as separate tracks. That was wrong. They're the same thing — ADK's bidi-demo pattern. And they can't be tested without agents and tools, so they belong in Step 2.

---

### Track 1A: React UI Shell + Panel Layout

**What to build:** The complete Mission Control UI with all panels positioned, styled, and populated with mock data. No backend connection yet — everything uses hardcoded mock state.

**Specifics:**
- React + Vite project in `frontend/`
- Three-column + bottom-row layout:
  - Left (60%): Strategic 3D View placeholder (black box with "3D VIEW" label — CesiumJS plugs in here in Step 2)
  - Center (20%): Reconnaissance Feed — shows a static image placeholder, mode toggle buttons (DRONE / SAR / PREDICTION)
  - Right (20%): Earth Engine Analysis panel (mock metric cards: "Flood Area: 23.4 km²", "Growth Rate: 12%/hr", "Population at Risk: 47,000") + System Status (list of 17 Google services with green dot indicators)
  - Bottom-left (70%): Neural Link — mock transcript lines, mic button (visual only), confidence bar showing "87% | HIGH RISK | 3 sources"
  - Bottom-right (30%): Incident Log — mock timestamped entries
- Top bar: "HAWK EYE" branding, incident timer counting up from 00:00, mode buttons (SILENT/ALERT/BRIEF/ACTION), population counter ("47,000"), water level gauge ("4.1m")
- Dark command-center theme: `#0a0e17` background, `#1a2332` panels, `#00d4ff` accent, `#ff4444` critical
- Add mock **time slider + before/after toggle** controls in Earth Engine panel (UI-only for now) so Step 2 can connect temporal EO layers without redesign.
- Add a basic responsive "field mode" layout contract (single-column fallback at narrow widths) and a mock low-bandwidth state.
- Every panel is a React component that accepts a `data` prop — the shape of this prop is the contract for Step 2
- Export a `MESSAGE_TYPES` constant defining all expected WebSocket message types and their payload shapes (TypeScript interfaces or JSDoc)

**Output contract:**
- React app at `localhost:5173` with all panels rendering mock data
- Each component exported: `<StrategicViewPanel data={...} />`, `<ReconFeedPanel data={...} />`, `<EarthEnginePanel data={...} />`, `<NeuralLinkPanel data={...} />`, `<IncidentLogPanel data={...} />`, `<TopBar data={...} />`
- `MESSAGE_TYPES` schema exported from `frontend/src/types/messages.js`

**Test:** `npm run dev` — visually verify all panels. No backend needed.

---

### Track 1B: CesiumJS 3D Globe Component (Standalone)

**What to build:** A standalone CesiumJS React component that renders Google Photorealistic 3D Tiles of Jakarta. This is a self-contained visual component with an API for overlays and camera control.

**Specifics:**
- Separate from the main React shell — build and test in isolation (can use Storybook or a minimal React wrapper)
- CesiumJS viewer with Google Photorealistic 3D Tiles (needs **Maps API key** with Tile API enabled; do not use Gemini key)
- Initial camera: Jakarta, Kampung Melayu area (-6.225, 106.855), altitude ~3000m
- Exposed functions via React ref or a shared module:
  - `flyTo(lat, lng, altitude, durationSeconds)` — smooth camera animation
  - `addGeoJsonOverlay(id, geojsonData, style)` — drapes a polygon/polyline on terrain. Style: fill color, opacity, outline. Returns an overlay ID for later removal.
  - `removeOverlay(id)` — removes a previously added overlay
  - `addPulsingMarker(id, lat, lng, color, label)` — adds an animated emergency marker
  - `removeMarker(id)` — removes a marker
  - `captureScreenshot()` — returns current view as base64 JPEG (needed by Predictor agent for Nano Banana 2 input)
- No interaction with any backend — purely a rendering component
- Include a lightweight isolated harness route/page for repeatable manual verification independent of the main shell.

**Output contract:**
- React component `<CesiumGlobe ref={globeRef} apiKey={...} />` with the above methods on the ref
- Can be imported by Track 1A's `<StrategicViewPanel>` to replace the placeholder — but that integration happens in Step 2

**Test:** Mount in isolation. Call `flyTo()` from browser console. Load a test GeoJSON polygon. Verify it renders on the 3D terrain. Call `captureScreenshot()` and verify base64 image.

---

### Track 1C: GCP Service Layer (BigQuery + Firestore + Maps + Earth Engine)

**What to build:** Four Python service classes that wrap GCP APIs. These are pure backend modules with no dependency on FastAPI, ADK, or WebSockets. They're just classes with methods that call GCP and return structured data.

**IMPORTANT: The BigQuery service class MUST follow HawkEye_BigQuery_Specification.md Phase 3 and Phase 4. All SQL queries are specified there. Do not improvise queries — use the exact SQL from the spec.**

**Specifics:**

**`services/bigquery_service.py` — GroundsourceService (see BigQuery spec Phase 4 for complete implementation):**
- `query_floods_intersecting_polygon(flood_geojson)` → Query 1: spatial intersection with flood polygon
- `get_flood_frequency(lat, lng, radius_km)` → Query 2: stats (total events, avg duration, worst case)
- `get_infrastructure_at_risk(flood_geojson)` → Query 3: hospitals/schools/power stations inside polygon, grouped by type
- `get_infrastructure_at_expanded_level(flood_geojson, buffer_meters)` → Query 4: NEWLY at-risk infrastructure when water rises (excludes already-flooded)
- `find_pattern_match(current_area_sqkm, duration_estimate_days)` → Query 5: closest historical match
- `get_monthly_frequency()` → Query 6: seasonal pattern (cached on init)
- `get_yearly_trend()` → Query 7: year-over-year trend (cached on init)
- All methods use BigQuery parameterized queries with proper type binding
- All spatial queries use `ST_DWITHIN` (not `ST_DISTANCE < X`) for spatial indexing
- GEOGRAPHY parameters passed as STRING + `ST_GEOGFROMGEOJSON()` inside SQL (not as native GEOGRAPHY params)
- The `get_infrastructure_at_risk` method groups results by type and returns `{hospitals: [...], schools: [...], power_stations: [...], ...}`

**`services/firestore_service.py` — IncidentService:**
- `log_event(event_type, severity, data)` → writes to `incidents` collection with auto-generated timestamp
- `log_decision(decision_text, reasoning, confidence)` → writes to `decisions` collection
- `get_session_timeline()` → returns all events + decisions ordered by timestamp
- `set_water_level(level_meters)` → writes to `sensor_data` collection
- `get_water_level()` → reads current level

**`services/firestore_service.py` — InfrastructureService:**
- `load_jakarta_infrastructure()` → one-time script: downloads Jakarta hospitals, schools, shelters, power stations from OpenStreetMap Overpass API, writes to `infrastructure` collection in Firestore
- `get_hospitals_in_polygon(geojson)` → queries infrastructure collection, filters by geometry intersection
- `get_schools_in_polygon(geojson)` → same
- `get_shelters_near(lat, lng, radius_km)` → sorted by distance and capacity
- `get_power_stations_in_polygon(geojson)` → same

**`services/maps_service.py` — MapsService:**
- `geocode(place_name)` → `{lat, lng, formatted_address}`
- `get_evacuation_route(origin_latlng, destination_latlng, avoid_geojson=None)` → route as GeoJSON polyline + distance + duration
- `find_nearby_shelters(lat, lng, radius_km)` → Places API results
- `get_elevation(lat, lng)` → meters above sea level
- `get_elevations_batch(list_of_latlng)` → batch elevation query

**`services/earth_engine_service.py` — EarthEngineService:**
- `get_flood_extent_geojson()` → returns pre-computed Jakarta flood GeoJSON from `data/geojson/flood_extent.geojson`
- `get_flood_area_sqkm()` → computed from the polygon
- `get_flood_growth_rate()` → pre-computed metric
- `get_population_at_risk(flood_geojson)` → pre-computed population estimate
- `get_flood_extent_metadata()` → returns provenance metadata (`source`, `acquisition_window`, `method`, `confidence`, `updated_at`)
- Also includes the script `data/compute_flood_extent.py` that runs the actual Sentinel-1 SAR computation and exports both GeoJSON and provenance metadata sidecar (run once, store the result)

**Output contract:**
- Four Python modules importable from `app/services/`. Each method works with real GCP data.
- **GroundsourceService** (most critical): all 7 query methods return real Jakarta data from BigQuery. The `get_infrastructure_at_risk()` method returns grouped results by type. The `get_infrastructure_at_expanded_level()` method returns ONLY newly-at-risk items. Cached methods (`get_monthly_frequency`, `get_yearly_trend`) populate on first call.
- **IncidentService**: reads/writes Firestore correctly.
- **MapsService**: returns real geocoding and routes from Google Maps APIs.
- **EarthEngineService**: returns pre-computed satellite analysis GeoJSON with provenance metadata.

**Test:** `pytest` scripts that call each method — **these tests are critical for BigQuery:**
- `bigquery_service.get_flood_frequency(-6.225, 106.855, 10)` → returns `{total_events: >0, avg_duration_days: >0}`
- `bigquery_service.get_infrastructure_at_risk(jakarta_flood_geojson)` → returns `{hospitals: [...], schools: [...]}`  with real names
- `bigquery_service.get_infrastructure_at_expanded_level(jakarta_flood_geojson, 1000)` → returns newly-at-risk items not in the original set
- `bigquery_service.find_pattern_match(15.0, 4)` → returns at least 1 historical event
- `bigquery_service.get_monthly_frequency()` → returns 12 rows, higher counts in Nov-Feb
- `firestore_service.log_event("test", "low", {"msg": "test"})` → writes to Firestore
- `maps_service.geocode("Kampung Melayu Jakarta")` → returns coordinates near -6.225, 106.855
- `earth_engine_service.get_flood_extent_geojson()` → returns valid GeoJSON with Jakarta geometry

---

## STEP 2: ADK Agent Backbone + Frontend Connection (3 Parallel Tracks)

**Goal:** The ADK bidi-demo backbone comes online with the root agent. The frontend connects via WebSocket. Audio flows end-to-end. The agents are stubs that will be fleshed out in Step 3, but the plumbing works.

**Prerequisite:** All 3 tracks from Step 1 complete.

---

### Track 2A: ADK Bidi-Streaming Backend (Core WebSocket + Root Agent Shell)

**What to build:** The complete ADK backend based on the bidi-demo pattern. Root agent defined with system instruction. Sub-agents defined as stubs. WebSocket endpoint working. Audio in/out flowing to Gemini Live API.

**Specifics:**

**`app/main.py`** — FastAPI app following the bidi-demo pattern:
- App startup: create Agent, InMemorySessionService (or Firestore-backed session service), Runner
- WebSocket endpoint at `/ws/{user_id}/{session_id}`:
  - On connect: create Session, create RunConfig (BIDI mode, native audio, session resumption enabled, context window compression enabled), create LiveRequestQueue
  - Upstream task: receive WebSocket messages. Binary frames → audio to `live_request_queue.send_realtime()`. Text frames → parse JSON, handle text commands or video frames (base64 JPEG → `live_request_queue.send_realtime()`)
  - Downstream task: async for event in `runner.run_live()` → inspect event type → forward to browser as text (transcript JSON) or binary (audio bytes). When tool results produce structured data (map updates, incident logs), emit custom JSON messages to the WebSocket.
  - Cleanup: `live_request_queue.close()` on disconnect
- RunConfig settings:
  - `streaming_mode=StreamingMode.BIDI`
  - `session_resumption=True` (handles 10-min reconnect automatically)
  - `context_window_compression=True` (removes session duration limits)
  - `response_modalities=["AUDIO"]`
  - `speech_config` with voice `Charon` or `Fenrir`
  - `proactive_audio=True`

**`app/hawkeye_agent/agent.py`** — Agent definitions:
- Root agent: "Hawk Eye Commander"
  - Model: `gemini-2.5-flash-native-audio-preview` (or latest native audio model)
  - System instruction: comprehensive Hawk Eye persona (authoritative, four operational modes, when to speak proactively, disagreement protocol, cascade narration rules, confidence communication)
  - Sub-agents: [Perception, Analyst, Predictor, Coordinator] — defined as ADK Agent objects with their own models, instructions, and tools
  - For now, sub-agent tools are stubs that return mock data — real implementations come in Step 3

**Output contract:**
- `adk web app/hawkeye_agent` starts the server
- Browser can connect to `ws://localhost:8000/ws/user1/session1`
- Speak into microphone → hear Hawk Eye respond in voice
- Text commands work too → send JSON `{"text": "Hello"}` → receive response
- Sub-agent tool calls route correctly (even though tools return mock data)
- Transcripts flow back as JSON text frames on the WebSocket

**Test:** Open ADK dev UI at localhost:8000. Talk to Hawk Eye. Verify voice response. Send text. Verify transcript events appear. Verify proactive audio setting works (agent breaks silence after a period).

---

### Track 2B: Frontend WebSocket Integration + Audio Pipeline

**What to build:** Connect the React UI shell (Track 1A) to the ADK backend (Track 2A). Audio capture and playback in the browser. All panels consuming real WebSocket events.

**Specifics:**

**WebSocket hook — `useHawkEyeSocket(userId, sessionId)`:**
- Connects to `ws://{backend}/ws/{userId}/{sessionId}`
- Handles binary frames (audio from agent) and text frames (JSON events)
- Auto-reconnect on disconnect
- Returns: `{ sendAudio, sendText, sendVideoFrame, events, connectionStatus }`

**Audio pipeline — `useAudioPipeline(socket)`:**
- Mic capture: AudioWorklet recording PCM 16-bit 16kHz (following the bidi-demo's `pcm-recorder-processor.js` pattern)
- Send audio chunks to WebSocket as binary frames
- Receive audio from WebSocket (binary frames from downstream task) → play through AudioWorklet (`pcm-player-processor.js`)
- Mic button in Neural Link panel: toggle recording on/off
- Visual: waveform animation while recording, different animation while agent speaks

**Panel data routing:**
- Neural Link: receives transcript events → appends to scrolling transcript display. Shows confidence bar from agent metadata.
- Incident Log: receives `incident_log_entry` events → appends timestamped colored entries
- Top Bar: receives `status_update` events → updates population counter, water level gauge, mode indicator
- Strategic View: receives `map_update` events → calls CesiumJS component methods (flyTo, addGeoJsonOverlay, addPulsingMarker)
- Recon Feed: receives `feed_update` events → displays image or switches mode
- Earth Engine Panel: receives `ee_update` events → updates metric cards, timeline state, and provenance chips

**CesiumJS integration:**
- Drop the `<CesiumGlobe>` component from Track 1B into the `<StrategicViewPanel>` from Track 1A
- Wire up the `map_update` event handler to call globe ref methods

**Output contract:**
- Full audio loop working: speak → hear response → see transcript
- All panels update when the backend emits events
- CesiumJS 3D view rendering Jakarta with ability to receive overlay commands
- Mode buttons send commands to backend

**Test:** Talk to Hawk Eye through the React UI. Hear response. See transcript. Click mode buttons and verify they send to backend. Trigger a mock `map_update` from backend and verify a polygon appears on the 3D view.

---

### Track 2C: Pre-Compute All Static Data Assets

**What to build:** All the data that needs to exist before agents can use it. This is a data engineering track — scripts, not application code.

**Note: BigQuery Groundsource loading and infrastructure loading were already done in Step 0 (tasks 0.8 and 0.9). This track focuses on VALIDATING that data is correct and preparing the remaining non-BigQuery assets.**

**Specifics:**

**BigQuery end-to-end validation:**
- Run every GroundsourceService method from Track 1C against real data and verify results make sense:
  - `get_flood_frequency(-6.225, 106.855, 10)` — does the total_events count seem reasonable? Is avg_duration plausible (1-14 days)?
  - `get_infrastructure_at_risk(flood_geojson)` — using the pre-computed flood extent GeoJSON, are there hospitals and schools in the result? Do the names look like real Jakarta places?
  - `get_infrastructure_at_expanded_level(flood_geojson, 1000)` — does it return DIFFERENT items than the base query? (This is the cascade correctness check)
  - `find_pattern_match(15.0, 4)` — does it return events with start_dates in monsoon months?
  - `get_monthly_frequency()` — are Nov/Dec/Jan/Feb counts higher than dry season months?
- If any results look wrong, debug the BigQuery tables and queries before proceeding. The Analyst agent's entire intelligence depends on these queries returning correct, meaningful data.

**Earth Engine computation:**
- Run `data/compute_flood_extent.py`:
  - Sentinel-1 SAR-first pipeline for Jakarta (pre-flood baseline vs. event window), with ARD preprocessing where feasible (border noise correction, speckle filtering, terrain normalization)
  - Sentinel-2 optical support layers (NDWI/mNDWI anomaly) for cross-checking
  - Export flood extent as `data/geojson/flood_extent.geojson`
  - Export NDWI anomaly as `data/geojson/ndwi_anomaly.geojson`
  - Export provenance sidecar as `data/geojson/analysis_provenance.json` containing method/sensors/date windows/confidence
  - Compute and store metrics: flood area km², growth rate, affected population estimate
  - Perform sanity validation against known flooded neighborhoods in Jakarta (manual spot-check list)

**Drone footage preparation:**
- Download 2-3 Creative Commons Indonesian flood drone videos
- Trim to 30-60 second clips
- Upload to Cloud Storage bucket
- Note the URLs for the Recon Feed panel

**Triage zone GeoJSON:**
- Create `data/geojson/triage_zones.geojson` — hand-drawn RED/YELLOW/GREEN zones over Jakarta based on elevation data and flood extent

**Output contract:**
- All BigQuery queries validated with real Jakarta data — every service method returns correct, meaningful results
- `data/geojson/` contains: `flood_extent.geojson`, `ndwi_anomaly.geojson`, `triage_zones.geojson`, `analysis_provenance.json`
- Cloud Storage: drone footage clips uploaded
- All data ready for agents to query in Step 3

**Test:** BigQuery query returns Jakarta floods. Firestore query returns hospitals. GeoJSON files load in geojson.io and show Jakarta geometry. Provenance file includes sensor source + date window + confidence.

---

## STEP 3: Agent Intelligence (4 Parallel Tracks)

**Goal:** Replace all stub tools with real implementations. Each sub-agent becomes fully functional with real data from the service layer.

**Prerequisite:** Step 2 complete. ADK backbone running. Services working. Data pre-computed.

---

### Track 3A: Perception Sub-Agent (Drone Frame Analysis)

**What to build:** The agent that analyzes video frames from drone footage and 3D view screenshots.

**Specifics:**
- ADK Agent definition with model `gemini-2.5-flash`
- System instruction: visual analysis specialist for disaster response — detect structural damage, estimate water depth, identify crowd movements, spot debris
- Tools:
  - `analyze_frame(frame_base64: str) -> dict` — sends JPEG to Gemini 2.5 Flash for visual analysis. Returns `{threat_level, damage_detected, water_depth_estimate, objects_of_interest, confidence}`
  - `compare_frames(frame_a_base64: str, frame_b_base64: str) -> dict` — detects changes between two sequential frames. Returns `{changes_detected, description, escalation_needed}`
- Structured output via response schema (JSON mode) to ensure parseable results
- When `threat_level >= HIGH`, the downstream task emits an `alert` JSON message to the browser

**Output contract:** ADK sub-agent that, when given a JPEG frame via tool call, returns structured visual analysis. Integrates into the root agent's sub-agent list.

**Test:** Call `analyze_frame()` with a test flood image. Verify structured JSON output. Verify high-threat result triggers alert emission.

---

### Track 3B: Analyst Sub-Agent (Intelligence + Cascade)

**What to build:** The intelligence agent. This is the most important sub-agent — it does the cascade, the Groundsource queries, the demographic breakdowns, and the disagreement logic.

**IMPORTANT: The cascade logic is powered by BigQuery spatial queries. Refer to HawkEye_BigQuery_Specification.md Phase 5 for the complete `compute_cascade()` and `query_historical_floods()` implementations with working code.**

**Specifics:**
- ADK Agent definition with model `gemini-2.5-flash`
- System instruction: intelligence analyst — cross-reference all data sources, compute multi-order cascades, flag dangerous decisions, always state confidence levels
- Tools (each wraps GroundsourceService from Track 1C, which queries BigQuery):
  - `query_historical_floods(lat: float, lng: float, radius_km: float) -> dict` — calls `GroundsourceService.get_flood_frequency()` + `.find_pattern_match()` + `.get_monthly_frequency()`. Returns historical context with pattern matches.
  - `get_flood_extent() -> dict` — calls EarthEngineService. Returns `{geojson, area_sqkm, growth_rate_pct}`
  - `get_infrastructure_at_risk(flood_geojson: str) -> dict` — calls `GroundsourceService.get_infrastructure_at_risk()` (BigQuery spatial join). Returns `{hospitals: [...], schools: [...], power_stations: [...]}` grouped by type.
  - `get_population_at_risk(flood_geojson: str) -> dict` — returns `{total, children_under_5, elderly_over_65, estimated_hospital_patients}` using Jakarta demographic ratios applied to the affected population count.
  - `compute_cascade(flood_geojson: str, water_level_delta_m: float) -> dict` — **THE CASCADE FUNCTION. See BigQuery spec Phase 5 for complete implementation.** Calls `GroundsourceService.get_infrastructure_at_risk()` (current state) + `.get_infrastructure_at_expanded_level()` (expanded state) to identify NEWLY at-risk infrastructure. Applies demographic ratios. Returns structured 4-order cascade.
  - `evaluate_route_safety(route_geojson: str, flood_geojson: str) -> dict` — checks if an evacuation route passes through flood zone using spatial intersection. Returns `{is_safe, danger_zones, recommendation, alternative_route}`. This is what enables agent disagreement.
  - `get_search_grounding(query: str) -> str` — uses Google Search tool for current news/regulations

- Cascade logic inside `compute_cascade()` (detailed implementation in BigQuery spec):
  1. Call `get_infrastructure_at_risk(current_flood_geojson)` → what's at risk NOW
  2. Call `get_infrastructure_at_expanded_level(current_flood_geojson, buffer_meters)` → what's NEWLY at risk at higher water
  3. Compute population with Jakarta demographic ratios (children under 5: ~8.5%, elderly over 65: ~5.7%)
  4. Estimate power grid impact (per-substation affected population heuristic)
  5. Return structured cascade: `{first_order, second_order, third_order, fourth_order, summary}`
  6. The `summary` field is a natural language string the root agent speaks verbatim

- When tool results come back, emit corresponding WebSocket messages:
  - `map_update` with flood overlay GeoJSON → 3D view adds blue polygon
  - `map_update` with triage zones → 3D view adds colored regions
  - `status_update` with new population count → top bar updates
  - `incident_log_entry` with cascade summary → incident log updates

**Output contract:** ADK sub-agent that answers questions about the flood situation with cross-referenced BigQuery intelligence. The cascade function returns multi-order consequences sourced from real spatial queries. The route safety function enables disagreement. All tool results trigger UI updates via WebSocket messages.

**Test:** Call `compute_cascade()` with Jakarta flood polygon + 2m rise. Verify it returns REAL hospital names, REAL school names, population breakdown with demographic detail. Verify the "newly at risk" list is different from the "currently at risk" list. Call `evaluate_route_safety()` with a route through the flood zone. Verify it flags the danger and suggests an alternative.

---

### Track 3C: Predictor Sub-Agent (Nano Banana 2 Projections)

**What to build:** The agent that generates visual risk projections using Nano Banana 2.

**Specifics:**
- ADK Agent definition with model `gemini-2.5-flash` (for reasoning) — but the actual image generation calls `gemini-3.1-flash-image-preview` (Nano Banana 2) directly via the genai SDK
- Tools:
  - `generate_risk_projection(screenshot_base64: str, scenario: str, water_level_delta: float) -> dict` — sends 3D view screenshot + scenario description to Nano Banana 2. Prompt: "Modify this satellite/aerial view to show what it would look like if water levels rise by {delta}m. Add realistic flooding, show water covering streets and low areas, make it dramatic but physically plausible." Returns `{projection_image_base64, confidence, caveats}`
  - `compute_confidence_decay(hours_ahead: float) -> dict` — simple exponential decay: `confidence = 95 * exp(-0.15 * hours)`. Returns `{confidence_pct, color: green/yellow/red, recommendation}`
- The generated image is emitted as a `feed_update` WebSocket message → displayed in the Recon Feed panel when in PREDICTION mode
- A `status_update` message updates the confidence bar in the Neural Link panel

**Output contract:** ADK sub-agent that generates Nano Banana 2 projections from 3D view screenshots. Returns projection image + confidence. Emits updates to Recon Feed and confidence bar.

**Test:** Capture a 3D view screenshot (from Track 1B's `captureScreenshot()`). Call `generate_risk_projection()` with "+2m" scenario. Verify an image is returned. Verify confidence decay function returns decreasing values.

---

### Track 3D: Coordinator Sub-Agent (Actions + Reports)

**What to build:** The agent that executes real-world actions — sends emails, generates routes, creates reports.

**Specifics:**
- ADK Agent definition with model `gemini-2.5-flash`
- Tools:
  - `send_emergency_alert(subject: str, body: str, recipient_email: str) -> dict` — sends email via Gmail MCP (ADK McpToolset). Returns `{sent: true, message_id}`. Emits `incident_log_entry` to WebSocket.
  - `generate_evacuation_route(origin_name: str, destination_name: str, avoid_flood: bool) -> dict` — calls MapsService.geocode() for both locations, then MapsService.get_evacuation_route() with flood polygon as avoidance zone. Returns route GeoJSON. Emits `map_update` to WebSocket → route appears on 3D view.
  - `log_incident(description: str, severity: str) -> dict` — calls IncidentService.log_event(). Emits `incident_log_entry` to WebSocket.
  - `generate_incident_summary() -> dict` — calls IncidentService.get_session_timeline(). Formats into a structured text report. Returns `{summary_text, event_count, decision_count}`. This is the audit trail.
  - `update_map(geojson: str, style: str, label: str) -> dict` — generic map update emitter. Emits `map_update` to WebSocket.

- Gmail MCP configuration:
  - ADK McpToolset pointing to Gmail MCP server
  - Service account with Gmail API permissions (or OAuth token for the demo)

**Output contract:** ADK sub-agent that sends real emails, generates real evacuation routes (appearing on the 3D map), logs incidents to Firestore, and generates session summaries.

**Test:** Call `send_emergency_alert()` → verify real email received. Call `generate_evacuation_route("Kampung Melayu", "University of Indonesia")` → verify route GeoJSON is valid and appears as a `map_update`. Call `generate_incident_summary()` → verify formatted report text.

---

## STEP 4: Full System Integration (2 Parallel Tracks)

**Goal:** Wire everything together. The root agent routes to real sub-agents. The demo scenario works end-to-end.

**Prerequisite:** All 4 tracks from Step 3 complete.

---

### Track 4A: Root Agent Finalization + Demo Flow

**What to build:** Replace stub sub-agents with the real ones. Implement proactive monitoring. Rehearse the full demo flow.

**Specifics:**
- Wire all 4 real sub-agents into the root agent's `sub_agents` list
- Implement proactive monitoring: a background task (or a tool that the agent calls periodically) checks Firestore water level. When threshold exceeded, the root agent proactively speaks.
- Mode switching: when the frontend sends a mode change (SILENT/ALERT/BRIEF/ACTION), update the agent's behavior (this is handled via the system instruction + session state)
- Demo scenario scripting:
  1. Set water level in Firestore to 4.1m → agent breaks silence
  2. Commander: "Show me the flood extent" → Analyst runs, flood overlay appears on 3D view
  3. Commander: "What happens at plus 2 meters?" → Predictor generates projection, Analyst runs cascade, 3D view updates, population counter jumps, agent narrates the chain
  4. Commander: "Route evacuation from Kampung Melayu to nearest shelter" → Analyst evaluates safety → disagrees → suggests alternative → Coordinator generates safe route → route appears on 3D view → agent narrates
  5. Commander: "Send emergency advisory" → Coordinator sends email → confirmation in incident log
  6. Commander: "Give me the incident summary" → Coordinator generates report → text appears
- Test each scenario step. Verify timing. Identify any failures.

**Output contract:** The full demo works end-to-end. Voice commands trigger the correct agent chains. UI updates in real-time. The 6-step demo scenario completes reliably.

**Test:** Run through the demo scenario 3+ times. Time it. Ensure it fits in 4 minutes.

---

### Track 4B: UI Polish + Visual Refinements

**What to build:** Make the UI demo-ready. Animations, transitions, visual feedback for every agent action.

**Specifics:**
- Population counter: animated number roll-up when value changes (47,000 → 128,000)
- Water level gauge: animated fill with color change (green → yellow → red)
- Confidence bar: smooth color transitions, pulse animation when confidence drops below 50%
- 3D view overlays: flood polygon appears with a spreading animation, not instant pop-in
- Evacuation route: animated dashed line with directional arrows
- Emergency markers: pulsing red rings with label
- Earth Engine panel: add before/after comparison toggle + timeline scrubber for temporal context
- Strategic view: add provenance/status chip (data source + updated timestamp + confidence)
- Incident log: new entries slide in with highlight, then fade to normal
- Transcript: agent messages in blue, commander in white, system events in gray
- Recon feed: smooth crossfade when switching between DRONE/SAR/PREDICTION modes
- Agent speaking indicator: visual waveform or pulsing ring around the mic button when the agent is speaking
- Error states: if WebSocket disconnects, show reconnecting indicator
- Loading states: if agent is processing, show thinking indicator in Neural Link
- Field mode fallback: compact single-column responsive layout + low-bandwidth visual mode

**Output contract:** The UI looks polished and professional. Every state change has visual feedback. No raw text dumps, no janky transitions.

**Test:** Run through the demo and screen-record. Watch the recording. Does it look like a product or a prototype? Fix anything that looks rough.

---

## STEP 5: Ship It (3 Parallel Tracks)

**Goal:** Deploy, record, submit. These three are fully independent.

**Prerequisite:** Step 4 complete.

---

### Track 5A: Demo Video + Deployment Proof

**What to build:** The 4-minute demo video and the separate GCP deployment proof.

**Specifics:**
- Write the exact demo script (every voice command, every expected response, timing cues)
- Set up OBS or screen recording with system audio capture
- Record 3-5 takes of the full demo
- Edit best take to exactly 4:00 or under
- Add the 15-second problem framing intro (drone footage + narration) at the beginning
- Upload to YouTube
- Record separate deployment proof: screen-share showing Cloud Run dashboard, Firestore console, BigQuery console, the live URL working

**Output:** YouTube link to demo video. YouTube link to deployment proof.

---

### Track 5B: Devpost Submission + Blog Post

**What to build:** All written submission materials.

**Specifics:**
- Devpost: title, tagline, full description (problem, solution, architecture, Groundsource integration, 17 Google services, cascade logic), tech stack, links
- Architecture diagram image (draw in Figma/draw.io and export as PNG)
- Blog post on Medium/Dev.to: "Building Hawk Eye: Integrating Google's Groundsource Dataset (Released 72 Hours Before Deadline) Into a Live Disaster Response Agent"
  - Must include: #GeminiLiveAgentChallenge hashtag, statement that it was created for the hackathon
- GDG profile link if member

**Output:** Published Devpost submission. Published blog post with public URL.

---

### Track 5C: Cloud Run Deployment + IaC

**What to build:** Deploy everything to GCP. Write the automation script.

**Specifics:**
- Backend: `adk deploy cloud_run --project=$PROJECT --region=us-central1 --service_name=hawkeye-backend`
- Frontend: deploy to Firebase Hosting (`firebase deploy`) or Cloud Run as static container
- Update frontend's WebSocket URL to point to deployed Cloud Run URL
- Verify end-to-end: access deployed frontend → connect to deployed backend → voice works → agents work
- Write `infra/deploy.sh` bash script that automates the full deployment
- Write `README.md` with spin-up instructions (judges requirement)
- Push all code to public GitHub repo

**Output:** Live URL. IaC script in repo. README with instructions.

---

## Dependency Graph

```
STEP 0 (sequential, ~45 min)
  │
  ├──→ STEP 1 (3 parallel tracks)
  │     ├── 1A: React UI Shell (frontend only)
  │     ├── 1B: CesiumJS 3D Component (frontend only)
  │     └── 1C: GCP Service Layer (backend only, no ADK)
  │
  ├──→ STEP 2 (3 parallel tracks)
  │     ├── 2A: ADK Bidi Backend + Root Agent Shell
  │     │       (imports services from 1C, follows bidi-demo pattern)
  │     ├── 2B: Frontend WebSocket + Audio + CesiumJS Integration
  │     │       (imports components from 1A + 1B, connects to 2A)
  │     └── 2C: Pre-Compute Static Data Assets
  │             (uses services from 1C to load data)
  │
  ├──→ STEP 3 (4 parallel tracks)
  │     ├── 3A: Perception Agent (uses Gemini 2.5 Flash)
  │     ├── 3B: Analyst Agent (uses 1C services + cascade logic)
  │     ├── 3C: Predictor Agent (uses Nano Banana 2)
  │     └── 3D: Coordinator Agent (uses Gmail MCP + 1C services)
  │
  ├──→ STEP 4 (2 parallel tracks)
  │     ├── 4A: Root Agent Wiring + Demo Flow
  │     └── 4B: UI Polish + Animations
  │
  └──→ STEP 5 (3 parallel tracks)
        ├── 5A: Demo Video
        ├── 5B: Devpost + Blog
        └── 5C: Cloud Deployment + IaC
```

---

## Parallelism Summary

| Step | Tracks | Max Agents | Key Risk |
|------|--------|------------|----------|
| 0 | 1 | 1 | GCP quota/API enablement delays |
| 1 | 3 | 3 | CesiumJS + 3D Tiles API key issues |
| 2 | 3 | 3 | ADK bidi-demo setup — follow the sample exactly |
| 3 | 4 | 4 | Analyst cascade logic complexity |
| 4 | 2 | 2 | Demo timing — fitting everything in 4 min |
| 5 | 3 | 3 | Video editing and upload time |

---

## Critical Path

**Step 0 → Track 1C (services) → Track 2A (ADK backbone) → Track 3B (Analyst agent) → Track 4A (root agent wiring) → Track 5A (demo video)**

The Analyst agent IS the demo. The cascade IS the differentiator. If the cascade works, you win. Everything else supports that.

---

## What To Cut If Running Out of Time

Cut in this order (least impactful first):

1. **Track 2C Earth Engine computation** — use hardcoded flood GeoJSON instead. Draw it manually in geojson.io.
2. **Track 3A Perception agent** — just play drone footage without live AI analysis. The footage still looks impressive.
3. **Track 3C Predictor agent** — skip Nano Banana 2 image generation. Describe the prediction verbally ("at +2 meters, here's what happens...") and show the cascade numbers instead.
4. **Track 4B UI Polish** — ugly-but-working beats polished-but-broken. The 3D view carries the visual weight regardless.

**NEVER cut:**
- Track 3B (Analyst) — the cascade is the whole point
- Track 3D (Coordinator) — the email send and evacuation route are visible proof of action
- Track 2A (ADK backbone) — without voice, you don't have a Live Agent submission
