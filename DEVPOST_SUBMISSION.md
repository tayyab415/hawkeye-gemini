# Hawk Eye — Devpost Submission

---

## Title
**Hawk Eye — AI Disaster Response Command Center**

---

## Tagline
A voice-controlled 3D command center that predicts multi-order disaster consequences using Google's Groundsource dataset and Gemini Live API.

---

## Description

### The Problem: When Floods Strike, Every Minute Costs Lives

Indonesia faces devastating floods every year. In 2025 alone, over 1,000 people died in flood-related disasters across the archipelago. First responders in Jakarta — a metro area of 11 million people — still rely on 2D paper maps, radio chatter, and gut instinct to make life-or-death decisions about evacuations, hospital routing, and resource allocation.

There is no system that:
- Predicts what happens when water rises 2 meters
- Knows which hospitals will be cut off before it happens
- Disagrees with a commander when an evacuation route is unsafe
- Speaks proactively when critical thresholds are exceeded

### The Solution: Hawk Eye

Hawk Eye is an AI-powered disaster response command center that combines real-time voice interaction, 3D geospatial visualization, and predictive intelligence to help incident commanders make faster, safer decisions during floods.

**What makes Hawk Eye different:**
- **Voice-controlled**: Hands-free operation during crisis — commanders speak naturally, Hawk Eye responds instantly with native audio
- **Proactive alerts**: Breaks silence when water levels exceed 4.0m, no prompting needed
- **Agent disagreement**: The AI can (and will) refuse unsafe evacuation routes and suggest alternatives
- **Multi-order cascade prediction**: Not just "where is the flood now" but "what breaks if water rises 2 more meters"
- **3D visualization**: Google Photorealistic 3D Tiles of Jakarta with live flood overlays

### How It Works

**The Multi-Agent Architecture**

Hawk Eye is built on Google's Agent Development Kit (ADK) with four specialized sub-agents:

1. **Perception** — Analyzes drone footage and satellite imagery for structural damage, crowd movements, and flood extent
2. **Analyst** — The intelligence engine. Queries 2.6 million historical flood events, computes cascade consequences, evaluates route safety
3. **Predictor** — Uses Nano Banana 2 (Gemini 3.1 Flash Image) to generate visual risk projections of future flood scenarios
4. **Coordinator** — Executes actions: sends emergency alerts via Gmail, generates evacuation routes, logs incidents

All four agents are orchestrated by the **Hawk Eye Commander** root agent using Gemini 2.5 Flash Native Audio for real-time bidi-streaming voice interaction.

**The Groundsource Integration Story**

Three days before the hackathon deadline, Google released Groundsource — an open-source dataset of 2.6 million historical flash flood events worldwide. We downloaded it from Zenodo, loaded it into BigQuery, and built a spatial intelligence layer that powers Hawk Eye's pattern matching.

When the commander asks "What happens if water rises 2 meters?" Hawk Eye:
1. Queries BigQuery for infrastructure currently at risk (hospitals, schools, power stations)
2. Queries for infrastructure newly at risk at expanded water levels
3. Applies Jakarta demographic ratios (8.5% children under 5, 5.7% elderly over 65)
4. Estimates power grid impact (80,000 residents per substation)
5. Returns a four-order cascade with specific numbers: 128,000 at risk, 3 hospitals isolated, 160,000 without power, 10,880 children in danger

**The Cascade: Multi-Order Consequence Chaining**

Hawk Eye doesn't just show flood polygons. It predicts the chain reaction:

- **First Order**: Direct flood impact — 128,000 people at risk
- **Second Order**: Infrastructure isolation — 3 hospitals cut off, Jalan Casablanca impassable
- **Third Order**: Power/utilities cascade — 2 substations at risk, 160,000 residents without electricity
- **Fourth Order**: Humanitarian impact — 10,880 children under 5, 7,296 elderly over 65 in severe risk zone

Each order depends on the previous. The system names specific facilities (RS Jakarta, RS Pondok Indah) and provides actionable recommendations.

**Technical Architecture**

- **Backend**: Python FastAPI with Google ADK bidi-streaming pattern
- **Live API**: WebSocket at `/ws/{user_id}/{session_id}` with three concurrent tasks (upstream, downstream, proactive monitoring)
- **Voice**: Gemini 2.5 Flash Native Audio with barge-in support, proactive audio, and session resumption
- **Database**: BigQuery with clustered geospatial tables + Firestore for real-time sensor data
- **Visualization**: React + Vite + CesiumJS with Google 3D Tiles API
- **Satellite Analysis**: Google Earth Engine with Sentinel-1 SAR for flood extent detection

### The 17 Google Services Behind Hawk Eye

1. **Gemini Live API** — Native audio streaming for voice interaction
2. **Gemini Standard API** — Text analysis and structured output
3. **Nano Banana 2 (Gemini 3.1 Flash Image)** — Risk projection image generation
4. **Google ADK** — Multi-agent orchestration and tool delegation
5. **BigQuery** — Groundsource dataset (2.6M events) + Jakarta infrastructure spatial queries
6. **Firestore** — Real-time session state and water level monitoring
7. **Cloud Storage** — Drone footage and pre-computed GeoJSON assets
8. **Google Maps Platform** — Routes, geocoding, elevation data
9. **Google 3D Tiles API** — Photorealistic 3D visualization in CesiumJS
10. **Earth Engine** — Sentinel-1/2 flood extent analysis
11. **Gmail API** — Emergency alert delivery
12. **Places API** — Shelter location search
13. **Directions API** — Evacuation route generation
14. **Geocoding API** — Address to coordinate conversion
15. **Vertex AI** — Model deployment and management
16. **Cloud Run** — Backend hosting
17. **Google Search Grounding** — Current event verification

### Demo Video

📺 **Demo Video**: [PLACEHOLDER - Add YouTube URL]

The 4-minute demo shows:
- 0:00 — UI reveal and proactive alert when water hits 4.1m
- 0:35 — "Show me the flood extent" → 23.4 km² overlay on 3D map
- 1:15 — "What happens at +2 meters?" → Full cascade analysis
- 2:15 — "Route evacuation to nearest shelter" → Agent disagreement and safer alternative
- 3:10 — "Send emergency advisory" → Gmail delivery + incident summary

---

## Tech Stack

**Core AI/ML:**
- Google ADK (Agent Development Kit)
- Gemini 2.5 Flash Native Audio
- Gemini 3.1 Flash Image (Nano Banana 2)
- Google GenAI Python SDK

**Backend:**
- Python 3.11+
- FastAPI
- WebSocket (bidi-streaming)
- uvicorn

**Data & Storage:**
- BigQuery (geospatial SQL)
- Firestore (real-time state)
- Cloud Storage (assets)
- Google Earth Engine (satellite analysis)

**Frontend:**
- React 18
- Vite
- CesiumJS (3D globe)
- Google 3D Tiles API

**Maps & Location:**
- Google Maps Platform
- Google Places API
- Google Directions API
- Google Geocoding API
- Google Elevation API

**DevOps:**
- Google Cloud Run
- Vertex AI
- Docker

---

## Links

🔗 **GitHub Repository**: [PLACEHOLDER - Add repo URL]

🔗 **Live Deployment**: [PLACEHOLDER - Add Cloud Run/Firebase URL]

🔗 **Deployment Proof Video**: [PLACEHOLDER - Add YouTube URL showing Cloud Console]

🔗 **Architecture Diagram**: [PLACEHOLDER - Add diagram image URL]

🔗 **Blog Post**: [PLACEHOLDER - Add Medium/Dev.to URL]

🔗 **GDG Member Profile**: [PLACEHOLDER - Add GDG profile URL if applicable]

---

## Hackathon

**Submitted for**: Gemini Live Agent Challenge

**Team**: Solo submission

**Development time**: 7 days

**Lines of code**: ~3,500 (Python backend + React frontend)

---

## Built With

`google-adk` `gemini-2.5-flash-native-audio` `bigquery` `firestore` `earth-engine` `react` `cesiumjs` `google-3d-tiles` `fastapi` `websocket` `python` `javascript`

---

## Inspiration

The 2025 Jakarta floods killed 67 people in a single week. First responders told stories of choosing evacuation routes based on "which one looked less flooded" and losing radio contact with hospitals that had been silently cut off by rising water. Hawk Eye exists to make sure no commander has to make those decisions in the dark again.
