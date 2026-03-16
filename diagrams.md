# Diagram 1: Google Earth Engine (Terrain & Environmental Compute)

**Prompt for Diagram Generation:**
Create a highly detailed architectural diagram illustrating how Google Earth Engine (GEE) is utilized as the planetary-scale computation backend in this application. Make sure to use Google's official logos and present a polished, professional look.

**Visual Elements & Logos Needed:**
*   **Google Earth Engine Logo:** Central computation hub.
*   **Python Logo:** For the backend service API (`earth_engine_service.py`).
*   **Satellite Icon:** Representing raw multisensor spatial data (SAR, Optical).
*   **Database Icon:** Representing GeoJSON and metadata caches.

**Diagrammatic Plan (Structure & Arrows):**
*   **[Client Request]** ➔ *(Arrow label: "Triggers Analysis Task")* ➔ **[Earth Engine Service API]** (Python backend).
*   **[Earth Engine Service API]** ➔ *(Arrow label: "Submits ee.Compute() & task generation")* ➔ **[Google Earth Engine (GEE)]**.
*   **[Raw Satellite Data]** ➔ *(Arrow label: "Ingests Temporal Frames & Multisensor Data")* ➔ **[GEE]**.
*   **[GEE]** ➔ *(Arrow label: "Calculates Flood Extent, Area (SqKm), Growth Rate, & Terrain Base")* ➔ **[Terrain & Flood Processors]** (internal GEE nodes).
*   **[GEE]** ➔ *(Arrow label: "Returns Live Tile URL & Metadata")* ➔ **[Earth Engine Service API]**.
*   **[Earth Engine Service API]** ➔ *(Arrow label: "Caches as GeoJSON & Serves Data Layers")* ➔ **[HawkEye Frontend]**.

**Behind the Scenes Context (to guide the diagram's detail):**
When a live analysis task is submitted, the Python backend connects to GEE. GEE processes complex algorithms like temporal frame generation and multisensor fusion on raw spatial data. It evaluates terrain geometry and environmental signals, returning actionable outputs: Live Tile URLs for visual rendering and GeoJSON shapes for exact flood extents.

---

# Diagram 2: Google Earth & Maps (Photorealistic 3D Tiles & Motion)

**Prompt for Diagram Generation:**
Create a detailed architectural diagram showing how Google Earth and Google Maps Platform are used to render photorealistic 3D tiles and handle dynamic camera motion and tactical routing. Make sure to use Google's official logos and present a polished, professional look.

**Visual Elements & Logos Needed:**
*   **Google Earth Logo:** Representing the photorealistic 3D globe rendering.
*   **Google Maps Logo:** Representing routing, geocoding, and elevation APIs.
*   **Camera Icon:** Representing motion and viewport control.
*   **Map Marker / Route Icons:** Representing dynamic tactical layers.

**Diagrammatic Plan (Structure & Arrows):**
*   **[HawkEye Frontend (Vite/React/JS)]** ➔ *(Arrow label: "Requests Photorealistic 3D Tiles via Maps API Key")* ➔ **[Google Maps API]**.
*   **[Google Maps API]** ➔ *(Arrow label: "Streams 3D Tileset Data")* ➔ **[Google Earth 3D Tiles Renderer]** (Frontend Map).
*   **[Globe Control Agent Tool (`globe_control.py`)]** ➔ *(Arrow label: "Commands Camera (Fly-To, Orbit, Tilt)")* ➔ **[Frontend Map Camera]**.
*   **[Maps Service (`maps_service.py`)]** ➔ *(Arrow label: "Geocodes Locations & Queries Elevation Profiles")* ➔ **[Google Maps API]**.
*   **[Maps Service]** ➔ *(Arrow label: "Computes Safe Evacuation Routes avoiding Flood GeoJSON")* ➔ **[Frontend Map Routes]**.
*   **[Frontend Map]** ➔ *(Arrow label: "Renders Data Layers & Threat Rings overlaying the 3D Globe")* ➔ **[User Viewport]**.

**Behind the Scenes Context (to guide the diagram's detail):**
The frontend utilizes the Maps API to stream Earth's 3D Tiles. The agent (`globe_control.py`) manipulates the viewport (zoom, orbit, tilt). Simultaneously, `maps_service.py` provides spatial context (geocoding, elevation patches) and calculates safe evacuation routes by dynamically avoiding flood geometries, drawing paths directly onto the 3D globe.

---

# Diagram 3: Gemini Live API (Tool Calling & Instruction Analysis)

**Prompt for Diagram Generation:**
Create an architectural diagram illustrating how the Gemini Live API powers a multi-agent system, handling multimodal inputs (screen, data, instructions) and autonomous tool calls. Make sure to use Google's official logos and present a polished, professional look.

**Visual Elements & Logos Needed:**
*   **Google Gemini Logo:** The core LLM orchestrator.
*   **Google Cloud / Vertex AI Logo:** Representing the hosting environment.
*   **Robot/Agent Icons:** Representing Sub-agents (Perception, Analyst, Predictor, Coordinator).
*   **Tools Icon (Wrench/Gear):** Representing Python function definitions (ADK).
*   **Screen/Eye Icon:** Representing visual frame analysis.

**Diagrammatic Plan (Structure & Arrows):**
*   **[User Voice/Text Command]** ➔ *(Arrow label: "Streams Instructions & Context")* ➔ **[Gemini Live API (Root Agent)]**.
*   **[Current Screen Frame / Viewport]** ➔ *(Arrow label: "Sends Visual Context for Perception")* ➔ **[Gemini Live API]**.
*   **[Gemini Live API]** ➔ *(Arrow label: "Analyzes intent & Delegates Task")* ➔ **[Sub-Agents (Perception, Analyst, Coordinator)]**.
*   **[Sub-Agents]** ➔ *(Arrow label: "Formulates Tool Call (e.g., `query_floods`, `generate_route`)")* ➔ **[ADK Function Tools]**.
*   **[ADK Function Tools]** ➔ *(Arrow label: "Executes Python Code & Returns Data/Analytics")* ➔ **[Gemini Live API]**.
*   **[Gemini Live API]** ➔ *(Arrow label: "Synthesizes Output & Generates Live Voice/Text Response")* ➔ **[User]**.

**Behind the Scenes Context (to guide the diagram's detail):**
Gemini acts as the intelligent orchestration layer via the Google Agent Development Kit (ADK). It ingests voice commands, written text, and visual frames captured from the map. It analyzes intent, delegates to sub-agents, and autonomously triggers registered Python tools. After receiving structured database or API responses, it synthesizes a human-readable emergency summary.

---

# Diagram 4: BigQuery (Ground Source Dataset & Predictions)

**Prompt for Diagram Generation:**
Create a highly detailed diagram showing how BigQuery acts as the spatial data warehouse for ground source datasets, enabling temporal-spatial queries and cascade risk predictions. Make sure to use Google's official logos and present a polished, professional look.

**Visual Elements & Logos Needed:**
*   **Google BigQuery Logo:** The massive data warehouse.
*   **GCP Logo:** Encompassing cloud infrastructure.
*   **Chart/Graph Icon:** Representing visualizations and analytics.
*   **Lightning Bolt Icon:** Representing predictive capabilities and cascade risk.

**Diagrammatic Plan (Structure & Arrows):**
*   **[Data Ingestion Pipeline]** ➔ *(Arrow label: "Uploads Historical Flood, Infrastructure, & Population Data")* ➔ **[BigQuery]**.
*   **[Analyst Agent / BigQuery Service]** ➔ *(Arrow label: "Submits Spatial SQL Queries (e.g., `ST_INTERSECTS`)")* ➔ **[BigQuery]**.
*   **[BigQuery]** ➔ *(Arrow label: "Returns Geo-Indexed Infrastructure at Risk & Temporal Clusters")* ➔ **[BigQuery Service]**.
*   **[BigQuery Service]** ➔ *(Arrow label: "Aggregates Monthly Frequency & Yearly Trends")* ➔ **[Frontend Visualizations]**.
*   **[BigQuery Service]** ➔ *(Arrow label: "Computes Spatial Accuracy & Cascading Failure Risks")* ➔ **[Predictor Agent]**.
*   **[Predictor Agent]** ➔ *(Arrow label: "Generates Risk Projections for the Map")* ➔ **[Frontend Map Layers]**.

**Behind the Scenes Context (to guide the diagram's detail):**
Ground source datasets (population density, infrastructure networks, historical floods) are loaded into BigQuery. When an area floods, BigQuery's spatial indexing (`ST_INTERSECTS`) rapidly determines affected infrastructure. It aggregates temporal data for charts and analyzes patterns of failure. This feeds the Predictor Agent, which calculates cascading risks (e.g., downstream power loss) to visualize predictive "threat rings" on the map.
