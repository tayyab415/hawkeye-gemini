# HawkEye Prototype — Video Overview Script

## Video Goal

Show HawkEye as a live, operational flood command center where:

- **Google Earth Engine** detects and tracks flood conditions.
- **BigQuery + Groundsource** explain impact and consequence at city scale.
- **Gemini Live API + ADK** turn voice commands into real geospatial actions.

---

## One-Line Pitch (Use Early)

HawkEye combines Earth Engine for flood intelligence, BigQuery and Google’s newly released Groundsource data for impact analysis, and Gemini Live + ADK for real-time command execution in a 3D operations interface.

---

## 4-Minute Video Script (Scene-by-Scene)

## 0:00-0:20 — Problem Framing

**On screen**
- Fast cuts: flood context, 3D Jakarta view, incident panel.
- Title card: `HAWKEYE — LIVE FLOOD COMMAND CENTER`.

**Voiceover**
In urban flooding, teams lose time switching between maps, satellite outputs, and fragmented reports. HawkEye unifies all of this into one live command center.

---

## 0:20-0:45 — System Architecture in One Frame

**On screen**
- Simple 3-layer overlay:
  - Earth Engine = Detection
  - BigQuery + Groundsource = Intelligence
  - ADK + Live API + Cesium = Command + Action

**Voiceover**
Earth Engine detects what is happening now. BigQuery and Groundsource explain who and what is affected. Gemini Live API and Google ADK make it conversational and actionable in real time.

---

## 0:45-1:30 — Google Earth Engine Highlight

**On screen**
- Flood extent overlay in strategic 3D view.
- Earth Engine runtime panel with area, growth, provenance/confidence.
- Temporal controls/replay visible.

**Voiceover**
We use Google Earth Engine as the flood intelligence layer. Sentinel-based analysis outputs flood extent, growth signals, and temporal context that we project directly into the command view.  
Every layer carries provenance and confidence metadata so operators know source quality and freshness, not just a pretty polygon.

**Judge callout**
- Earth Engine is not treated as static imagery; it drives runtime flood intelligence and map updates.

---

## 1:30-2:10 — BigQuery + Groundsource Highlight

**On screen**
- Hotspot markers and infrastructure overlays.
- Incident/analytics cards updating with counts and risk language.

**Voiceover**
We use BigQuery as our consequence engine. It runs spatial joins between current flood geometry and critical infrastructure, then computes risk exposure patterns.  
We also integrate Google’s newly released Groundsource flood dataset to bring historical and spatial context into live decision-making, not just post-event reporting.

**Judge callout**
- Groundsource improves credibility by grounding live alerts in historical flood behavior.

---

## 2:10-2:55 — Live API + ADK + Voice Control

**On screen**
- Neural Link panel, mic activation, live transcript, globe movement.
- Commands execute on map (`fly to`, `orbit`, overlays).

**Voiceover**
HawkEye runs on Google ADK’s live streaming architecture. Audio enters once, and the system continuously reasons, responds, and emits structured events over the same live session.  
This is not a chatbot wrapper. Voice commands directly trigger geospatial actions, overlays, and analysis updates in the operational interface.

---

## 2:55-3:40 — Route Optimization + Evacuation Story

**On screen**
- Ask for evacuation route.
- Route appears with distance, ETA, and safety rating.
- Flood overlays remain visible to show routing context.

**Voiceover**
For evacuation, HawkEye generates route guidance using geocoding, routing, and flood-aware constraints.  
The route response includes travel time, distance, and safety assessment against active flood geometry, helping teams avoid unsafe corridors and act faster under pressure.

**Important narration tip**
- Say: “This is decision support for incident commanders: route, risk, and response in one flow.”

---

## 3:40-4:00 — Closing Impact

**On screen**
- Final dashboard state: map, route, incident log, status cards.
- End card with stack: `Earth Engine • BigQuery • Groundsource • Gemini Live API • ADK`.

**Voiceover**
HawkEye demonstrates how Google’s geospatial intelligence stack and live agent infrastructure can move emergency response from reactive reporting to proactive command execution.

---

## Live Demo Prompt Script (Recommended Order)

Use these exact prompts during recording:

1. HawkEye, confirm comms.
2. Fly me to Jakarta.
3. Show me orbit mode.
4. What are the major flood locations right now?
5. Show me the flood extent and summarize current risk.
6. What happens if water rises by 2 meters?
7. Route evacuation from Kampung Melayu to Gelora Bung Karno Stadium.
8. Send emergency advisory.
9. Give me the incident summary.

---

## What to Emphasize to Judges (High-Signal Lines)

- This project uses **Earth Engine for flood detection and temporal situational awareness**.
- It uses **BigQuery for live spatial intelligence and consequence analysis**.
- It integrates **Google’s newly released Groundsource dataset** for real flood-history grounding.
- It uses **Gemini Live API + ADK** for low-latency, voice-driven command execution.
- It visualizes everything in a **3D operational command interface**, not static dashboards.

---

## Recording Notes

- Keep map camera motion smooth and intentional (avoid rapid manual panning).
- Pause briefly after each command to let overlays and transcript be visible.
- While route is displayed, zoom enough to show relation to flood zones.
- If a command asks for extra clarification, repeat with explicit origin/destination to keep the flow tight.

---

## Optional 90-Second Cutdown Script

**0:00-0:15 — Hook**  
HawkEye is a live flood command center that combines Earth Engine detection, BigQuery intelligence, and voice-driven action.

**0:15-0:35 — Earth Engine + Groundsource**  
Earth Engine tracks flood extent and progression. Groundsource and BigQuery add historical and spatial context so we can explain not only where flooding is, but what it means operationally.

**0:35-0:55 — Live Command**  
Using Gemini Live API with ADK, commanders can speak naturally: fly to locations, switch camera modes, and trigger flood analysis in real time.

**0:55-1:15 — Evacuation Value**  
HawkEye generates flood-aware evacuation routing with ETA, distance, and safety context to support rapid incident decisions.

**1:15-1:30 — Close**  
This is how we move from reactive flood reporting to proactive, AI-assisted command operations on Google’s geospatial stack.
