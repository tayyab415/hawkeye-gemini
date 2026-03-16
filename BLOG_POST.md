# Building Hawk Eye: How We Integrated Google's Groundsource Dataset Into a Live Disaster Response Agent for the Gemini Live Agent Challenge

*This project was created for the Gemini Live Agent Challenge. #GeminiLiveAgentChallenge*

---

## The Problem: 1,000 Deaths and 2D Paper Maps

Every year, floods kill over 1,000 people in Indonesia. In January 2025, Jakarta's Ciliwung River burst its banks and submerged entire districts before many residents could evacuate. First responders — the people making life-or-death decisions about hospital routing, shelter allocation, and evacuation timing — were working with 2D paper maps, radio chatter, and gut instinct.

There was no system that could tell them: "If water rises 2 more meters, three hospitals will be cut off, 160,000 people will lose power, and you'll have 10,000 children in severe risk zones."

That gap — between "what's happening now" and "what happens next" — is why I built Hawk Eye.

---

## The Architecture: Four Agents, One Voice

Hawk Eye is a voice-controlled disaster response command center. The user experience is simple: you speak naturally, and an AI commander responds in real-time with actionable intelligence, 3D visualizations, and proactive alerts when thresholds are exceeded.

But under the hood, it's a carefully orchestrated multi-agent system built on Google's Agent Development Kit (ADK).

### The Four Sub-Agents

I divided the problem into four specialized agents, each with its own tools and expertise:

**Perception** handles visual analysis. Feed it a drone frame, and it returns threat levels, damage assessments, and water depth estimates. It can compare sequential frames to detect escalation.

**Analyst** is the intelligence engine. It queries historical flood patterns, computes multi-order cascade consequences, evaluates route safety, and cross-references infrastructure risk. This is where the heavy BigQuery lifting happens.

**Predictor** generates forward-looking risk visualizations. It uses Gemini 3.1 Flash Image (Nano Banana 2) to modify 3D view screenshots, showing commanders what the landscape will look like at +2 meters water rise.

**Coordinator** executes actions. It sends emergency alerts via Gmail, generates evacuation routes using Google Maps, logs incidents to Firestore, and produces session summaries for audit trails.

All four report to the **Hawk Eye Commander**, a root agent running Gemini 2.5 Flash Native Audio. The Commander handles voice I/O, delegates tasks to sub-agents, and manages the conversation flow.

### The WebSocket Backbone

The backend follows Google's ADK bidi-demo pattern. A FastAPI server exposes a WebSocket at `/ws/{user_id}/{session_id}`. Three concurrent async tasks run per connection:

1. **Upstream**: Receives audio/video/text from the browser and feeds it to the LiveRequestQueue
2. **Downstream**: Receives events from `runner.run_live()` and forwards audio + structured JSON to the browser
3. **Proactive monitoring**: Polls Firestore every 30 seconds for water level data; injects system alerts when thresholds are exceeded

This architecture gives us native audio latency (~200-500ms), barge-in support (the commander can interrupt the agent), and proactive alerts (the agent breaks silence when water hits 4.1m).

---

## The Groundsource Integration: 72 Hours to Deadline

Here's where the story gets interesting.

On March 12, 2026 — exactly 72 hours before the hackathon submission deadline — Google released Groundsource: an open-source dataset of 2.6 million historical flash flood events worldwide, compiled from news reports, government records, and satellite data.

I had a choice: ignore it and ship what I had, or integrate it and risk breaking everything.

I integrated it.

### Loading 2.6 Million Events into BigQuery

The Groundsource dataset comes as a 636 MB Parquet file. I uploaded it to Cloud Storage and loaded it into BigQuery:

```bash
bq load --source_format=PARQUET hawkeye.groundsource_raw \
  gs://hawkeye-data/groundsource.parquet
```

The raw table had string dates and WKT geometry. I created an optimized production table with proper GEOGRAPHY columns, computed fields for duration and area, and clustering on geometry for fast spatial queries:

```sql
CREATE OR REPLACE TABLE hawkeye.groundsource
CLUSTER BY geometry AS
SELECT
  ST_GEOGFROMTEXT(geometry, make_valid => TRUE) AS geometry,
  SAFE.PARSE_DATE('%Y-%m-%d', start_date) AS start_date,
  SAFE.PARSE_DATE('%Y-%m-%d', end_date) AS end_date,
  DATE_DIFF(
    SAFE.PARSE_DATE('%Y-%m-%d', end_date),
    SAFE.PARSE_DATE('%Y-%m-%d', start_date),
    DAY
  ) AS duration_days,
  ST_AREA(ST_GEOGFROMTEXT(geometry, make_valid => TRUE)) / 1000000 AS area_sqkm
FROM hawkeye.groundsource_raw
WHERE SAFE.PARSE_DATE('%Y-%m-%d', start_date) IS NOT NULL;
```

Then I created a materialized view for Jakarta, filtering to a 50km radius around the city center. This reduced every query from scanning 2.6M rows to scanning a few thousand.

### The Power of Pattern Matching

With Groundsource integrated, Hawk Eye could answer questions that were previously impossible:

- "How many times has this area flooded since 2000?"
- "What's the average duration of floods in this basin?"
- "Which historical event matches current conditions?"

When the commander asks about flood extent, the Analyst agent queries for historical floods intersecting the current polygon. It finds similar events from 2013 and 2020. It reports: "This pattern indicates a severe multi-day event."

That's not a guess. That's 2.6 million data points speaking.

---

## The Cascade: Multi-Order Consequence Chaining

The feature I'm most proud of is the cascade: a four-order prediction of what happens when water rises.

Here's how it works:

**First Order**: Direct flood impact. The system calculates population at risk based on flood area expansion.

**Second Order**: Infrastructure isolation. Using BigQuery spatial joins with OpenStreetMap data, it identifies hospitals, schools, and shelters inside the expanded flood zone. It specifically names facilities: "RS Jakarta and RS Pondok Indah will be isolated."

**Third Order**: Power and utilities cascade. Each substation at risk is mapped to an estimated affected population (80,000 residents per substation, derived from Jakarta density data). "Two substations at risk, threatening power to 160,000 residents."

**Fourth Order**: Humanitarian impact. Demographic ratios are applied: 8.5% children under 5, 5.7% elderly over 65. "10,880 children and 7,296 elderly individuals in severe risk zones."

The cascade isn't just numbers. It's a narrative the agent speaks aloud:

> "At +2 meters, population at risk reaches 128,000. Three hospitals are in the flood zone. Jalan Casablanca will be cut off. Two power substations at risk, potentially affecting 160,000 residents. Recommendation: Begin evacuation of Kampung Melayu immediately, route to University of Indonesia campus."

### Agent Disagreement

The cascade enables something rare in AI systems: the agent can disagree with the user.

When the commander says "Route evacuation from Kampung Melayu to the nearest shelter," the Coordinator generates a route to Tebet. But then the Analyst evaluates route safety against the flood polygon. It discovers 45% of the route passes through the projected flood zone.

The agent speaks:

> "Commander, the closest shelter at Tebet is within the projected flood zone in 4 hours. I strongly advise against that route. I recommend rerouting to the University of Indonesia campus — higher elevation, capacity for 5,000."

That's not a chatbot following orders. That's an AI system with enough context to flag dangerous decisions and suggest alternatives.

---

## The Demo: 4 Minutes of Tension

The demo video is structured like a thriller. It has to be — disaster response is life-or-death, and the UI should feel that way.

**0:00-0:15**: The UI snaps into focus. Water level is at 3.0m. Suddenly, the gauge flashes red — 4.1m. The agent breaks silence without prompting:

> "Commander — Ciliwung River basin has exceeded critical threshold. Water level at Kampung Melayu now 4.1 meters. Rate of rise: 0.5 meters per hour."

**0:15-1:15**: The commander speaks: "Show me the flood extent." A blue flood polygon fades onto the 3D map. The agent reports: "23.4 square kilometers affected. Growth rate 12% per hour. Based on Groundsource matches from the 2020 floods, this pattern indicates a severe multi-day event."

**1:15-2:15**: The critical question: "What happens if water rises another 2 meters?" The population counter rolls up to 128,000. A red expanded flood zone drops on the map. The agent narrates the full cascade — hospitals, power stations, children, elderly.

**2:15-3:10**: "Route evacuation from Kampung Melayu to the nearest shelter." The agent pauses, evaluates, disagrees. It presents a safer alternative. The green route animates across the 3D view.

**3:10-3:55**: "Send emergency advisory." Gmail integration fires. Incident log updates. "Give me the incident summary." A complete audit trail appears.

Fade to black.

---

## Lessons Learned: Vibe Coding Under Pressure

I built Hawk Eye in 7 days, solo. Here are the real lessons:

### Lesson 1: Parallel Agent Development Works

I structured the project so that frontend, backend services, and agent logic could be built in parallel. While I was wiring the WebSocket, I already had the BigQuery service class passing tests. While I was building the React UI, the Analyst agent was already computing cascades against real data.

The key was clear contracts. Every service method had a defined input/output shape. Every tool function returned structured data. When it came time to wire everything together, the pieces fit.

### Lesson 2: Groundsource Changed Everything (and Almost Broke Everything)

Integrating a new dataset 72 hours before deadline was risky. The Parquet load failed twice — once because of invalid geometries (18% of Groundsource records have errors), once because I got longitude/latitude order wrong and put Jakarta in the middle of the Indian Ocean.

But the risk was worth it. Without Groundsource, Hawk Eye would be a demo with mock data. With it, Hawk Eye is a system that can tell commanders "this matches the 2020 floods" with real historical backing.

### Lesson 3: Native Audio Is a Different Beast

I started with text-based responses and added audio later. That was a mistake. The latency, interruption handling, and proactive audio behavior are fundamentally different with native audio models.

The breakthrough was embracing the ADK bidi-demo pattern fully. Stop trying to manage the WebSocket manually. Use `LiveRequestQueue`. Use `runner.run_live()`. Let ADK handle session resumption and context window compression.

### Lesson 4: The Cascade Is the Demo

Every successful hackathon project has one "whoa" moment. For Hawk Eye, it's the cascade. When the agent says "10,880 children under 5 are now in severe risk zones," that's not a statistic — that's a decision-making trigger.

I spent 40% of my time on the Analyst agent and the BigQuery queries that power it. That was the right allocation. Everything else supports that moment.

### Lesson 5: Vibe Coding Has Limits

I used AI coding assistants heavily for this project. They were invaluable for boilerplate, SQL syntax, and React component structure. But they couldn't architect the cascade logic. They couldn't decide that the agent should disagree with the user. They couldn't figure out that Groundsource needed a materialized view for Jakarta.

The vibe coding got me 70% of the way there. The last 30% — the architecture, the safety-critical logic, the demo flow — that required human judgment.

---

## The 17 Google Services That Power Hawk Eye

A quick inventory of what this system actually uses:

1. **Gemini Live API** — Native audio streaming, 200ms latency, barge-in support
2. **Gemini Standard API** — Text analysis and structured output
3. **Nano Banana 2** — Risk projection image generation from 3D screenshots
4. **Google ADK** — Multi-agent orchestration, tool delegation, session management
5. **BigQuery** — Groundsource (2.6M events) + Jakarta infrastructure, spatial SQL
6. **Firestore** — Real-time water level monitoring, session state
7. **Cloud Storage** — Drone footage, pre-computed GeoJSON assets
8. **Google Maps Platform** — Routes, geocoding, elevation
9. **Google 3D Tiles API** — Photorealistic 3D visualization in CesiumJS
10. **Earth Engine** — Sentinel-1 SAR flood extent detection
11. **Gmail API** — Emergency alert delivery via MCP
12. **Places API** — Shelter location discovery
13. **Directions API** — Evacuation route generation
14. **Geocoding API** — Address-to-coordinate conversion
15. **Vertex AI** — Model deployment
16. **Cloud Run** — Backend hosting
17. **Google Search Grounding** — Current event verification

---

## What's Next

Hawk Eye is a proof of concept. The next steps are:

- **Real-time sensor integration**: Connect to actual Ciliwung River water level sensors
- **SMS/push alerts**: Expand beyond Gmail to multi-channel emergency notifications
- **Mobile field app**: A companion app for responders on the ground
- **Multi-city expansion**: Apply the same architecture to other flood-prone cities

But even as a demo, Hawk Eye proves something important: AI agents can do more than answer questions. They can anticipate consequences, flag dangerous decisions, and speak up when lives are at stake.

That's the future of disaster response.

---

*Built with Google's Gemini Live API, ADK, and Groundsource dataset. Created for the Gemini Live Agent Challenge.*

**#GeminiLiveAgentChallenge**
