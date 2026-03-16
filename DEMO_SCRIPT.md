# HawkEye: Command Center Demo Script

**Total Duration:** ~4 Minutes
**Setup:** Operator at the terminal. Browser opened to local HawkEye frontend. Screen recording active.

---

## 0:00 - Introduction & UI Reveal
*(The screen shows the "START MISSION" overlay over a blurred military-style dashboard. The operator clicks "START MISSION" and the UI snaps into focus.)*

**NARRATION:**
"Welcome to HawkEye, an autonomous disaster response command center powered by Google's Gemini Live API. This system integrates real-time telemetry, Google Earth Engine, and 17 distinct Google services into a single multimodal interface driven entirely by voice."

---

## 0:15 - Phase 1: Silent Detection (System Prompted)
*(The UI is quiet. Map is centered on Jakarta. Water level sits at 3.0m. Suddenly, the gauge flashes red as it jumps to 4.1m. An incident log slides in down below. The agent’s mic pulses blue and it speaks unprompted.)*

**AGENT:**
"Commander — Ciliwung River basin has exceeded critical threshold. Water level at Kampung Melayu now 4.1 meters. Rate of rise: 0.5 meters per hour."

*(On-screen: The map zooms to Kampung Melayu, dropping a pulsing red marker.)*

---

## 0:35 - Phase 2: Investigation (User Triggered)
*(Operator clicks the microphone to respond.)*

**COMMANDER:**
"Show me the flood extent."

*(The agent's mic icon pulses red as the commander speaks, then blue as it replies. A large blue flood polygon fades into the 3D map over 0.5 seconds.)*

**AGENT:**
"Flood extent analysis complete. 23.4 square kilometers affected spanning 3 districts. Growth rate is 12% per hour. Based on Groundsource matches from the 2020 floods, this pattern indicates a severe multi-day event."

---

## 1:15 - Phase 3: Cascade Analysis (User Triggered)
*(The population counter starts ticking upwards.)*

**COMMANDER:**
"What happens if water rises another 2 meters?"

*(A larger, semi-transparent red polygon drops on the map, expanding the flood zone. The Recon Feed crossfades to a 'PREDICTION' view, showing a stylized rendering of flooded infrastructure with a pulsing red border. The population at risk counter rolls up rapidly to 128,000, flashing red.)*

**AGENT:**
"Running multi-order cascade analysis. First order: flood area expands by 40%, placing 128,000 at risk. Second order: 3 major hospitals will be isolated, and Jalan Casablanca will be cut off. Third order: 2 power substations are in the expanded zone, threatening power to 160,000. Fourth order demographic impact: 12,400 children under 5 and 8,200 elderly individuals are now in the severe risk zone."

---

## 2:15 - Phase 4: Disagreement & Action (Routing Conflict)
*(The operator challenges the agent to propose a solution.)*

**COMMANDER:**
"Route evacuation from Kampung Melayu to the nearest shelter."

*(A route is drawn towards the Tebet shelter, but the destination marker flashes orange.)*

**AGENT:**
"Commander, the closest shelter at Tebet is within the projected flood zone in 4 hours. I strongly advise against that route."

*(A new, dashed green route animates on the map, redirecting south.)*

**AGENT:**
"I recommend rerouting to the University of Indonesia campus — higher elevation, capacity for 5,000."

---

## 3:10 - Phase 5: Execution & Summary
*(Operator confirms the action.)*

**COMMANDER:**
"Send emergency advisory."

*(Incident log slides in from the left: "Emergency advisory sent via Gmail to jakarta-emergency@example.com")*

**AGENT:**
"Emergency advisory email has been dispatched to all relevant agencies."

**COMMANDER:**
"Give me the incident summary."

**AGENT:**
"Incident Summary: At 14:00, Ciliwung River exceeded the 4.0m threshold. Current extent affects 23.4 sq km. A +2m rise cascade analysis revealed severe multi-order impacts across infrastructure and demographics to 128,000 people. An evacuation route to University of Indonesia was established, safely bypassing the Tebet danger zone. Emergency advisories have been successfully delivered."

---

## 3:55 - Outro

**NARRATION:**
"HawkEye demonstrates the next evolution in operational orchestration: reducing cognitive load during crises by letting AI unify telemetry, simulation, and execution."

*(Fade to black)*

---
## Fallback Protocol
If the live Gemini agent endpoints are unresponsive or too slow for a smooth recording:
1. Reload the application UI.
2. Open the browser Developer Console.
3. Run `window.startDemoSimulation()`.
4. Proceed with the Commander voice lines reading exactly to the timings above while the system injects the perfect state updates.
