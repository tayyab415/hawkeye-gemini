import { SERVER_MESSAGE_TYPES } from '../types/messages';
import floodExtentRaw from '../../../data/geojson/flood_extent.geojson?raw';
import analysisProvenanceRaw from '../../../data/geojson/analysis_provenance.json?raw';

/**
 * Demo Event Simulator
 * Replays the 5-phase HawkEye demo scenario perfectly by injecting mock events
 * into the same pipeline the real WebSocket uses.
 *
 * Enhanced with all 10 strategic view features + visual effects:
 *   Phase 1: Deploy command post, scanline ON, storm atmosphere ON
 *   Phase 2: Toggle FLOOD_EXTENT, FLOOD_REPLAY, orbit camera
 *   Phase 3: Flood level rise, threat ring highlight, deploy helicopter, toggle INFRASTRUCTURE
 *   Phase 4: Measurement line on evac route, street-level camera
 *   Phase 5: Overview camera, storm OFF, summary
 */

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let isSimulationRunning = false;
let simulationStep = 0;

let DEMO_FLOOD_GEOJSON = null;
let DEMO_PROVENANCE = null;
try { DEMO_FLOOD_GEOJSON = JSON.parse(floodExtentRaw); } catch { /* demo fallback below */ }
try { DEMO_PROVENANCE = JSON.parse(analysisProvenanceRaw); } catch { /* demo fallback below */ }

function parseNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

const DEMO_FLOOD_AREA_SQKM = (
  parseNumber(DEMO_PROVENANCE?.estimated_area_sqkm)
  ?? parseNumber(DEMO_FLOOD_GEOJSON?.properties?.flood_area_sqkm)
  ?? parseNumber(DEMO_FLOOD_GEOJSON?.features?.[0]?.properties?.flood_area_sqkm)
  ?? null
);
const DEMO_FLOOD_AREA_LABEL = DEMO_FLOOD_AREA_SQKM === null ? 'N/A' : DEMO_FLOOD_AREA_SQKM.toFixed(2);
const DEMO_FLOOD_AREA_METRIC = DEMO_FLOOD_AREA_SQKM ?? 0;
const DEMO_FLOOD_SOURCE = (
  DEMO_PROVENANCE?.source
  ?? DEMO_FLOOD_GEOJSON?.properties?.source
  ?? DEMO_FLOOD_GEOJSON?.features?.[0]?.properties?.source
  ?? 'Sentinel-1 SAR'
);
const DEMO_FLOOD_UPDATED_AT = (
  DEMO_PROVENANCE?.generated_at
  ?? DEMO_FLOOD_GEOJSON?.properties?.computed_at
  ?? DEMO_FLOOD_GEOJSON?.features?.[0]?.properties?.computed_at
  ?? new Date().toISOString()
);

/**
 * Dispatch an event to the global window object. App.jsx will listen for these.
 */
function emitEvent(eventData) {
  if (!isSimulationRunning) return;
  const event = new CustomEvent('hawkeye-mock-event', { detail: eventData });
  window.dispatchEvent(event);
}

const DEMO_SEQUENCE = [
  // ═══════════════════════════════════════════════════════════════
  // PHASE 1: Silent Detection (T+5s)
  //   → Deploy command post, scanline ON, storm atmosphere ON
  // ═══════════════════════════════════════════════════════════════
  async () => {
    console.log('[Demo] Phase 1 - Silent Detection');

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'info',
      message: 'Water level monitoring active. All systems nominal.',
      timestamp: Date.now()
    });

    await delay(1000);

    // Activate storm atmosphere for dramatic effect
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'STORM_EFFECT',
      enabled: true,
    });

    await delay(1500);

    // Deploy command post at Kampung Melayu
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'deploy_entity',
      entity_id: 'cmd-post-1',
      entity_type: 'command_post',
      lat: -6.224,
      lng: 106.856,
      altitude: 20,
      label: 'CMD POST ALPHA',
    });

    await delay(1000);

    // Ensure scanlines are ON
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_scanlines',
      enabled: true,
    });

    await delay(1000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.STATUS_UPDATE,
      water_level_m: 4.1
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'critical',
      message: 'CRITICAL: Ciliwung River basin exceeded threshold. Water level 4.1m.',
      timestamp: Date.now()
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: 'Commander — Ciliwung River basin has exceeded critical threshold. Water level at Kampung Melayu now 4.1 meters. Rate of rise: 0.5 meters per hour.',
      timestamp: Date.now(),
      confidence: 0.95
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'marker',
      geojson: { coordinates: [106.855, -6.225] },
      label: 'Kampung Melayu',
      style: { color: '#ff4444' }
    });
  },

  // ═══════════════════════════════════════════════════════════════
  // PHASE 2: Investigation (T+15s)
  //   → Toggle FLOOD_EXTENT + FLOOD_REPLAY, orbit camera
  // ═══════════════════════════════════════════════════════════════
  async () => {
    console.log('[Demo] Phase 2 - Investigation');

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'user',
      text: 'Show me the flood extent.',
      timestamp: Date.now()
    });

    await delay(1500);

    // Turn off the default FLOOD_EXTENT first so FLOOD_REPLAY starts clean
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'FLOOD_EXTENT',
      enabled: false,
    });

    await delay(500);

    // Activate FLOOD_REPLAY — sequential polygon progression
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'FLOOD_REPLAY',
      enabled: true,
    });

    await delay(1000);

    // Start orbit camera to survey the area
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'camera_mode',
      mode: 'ORBIT',
    });

    await delay(1000);

    // Also send legacy flood overlay event using the same real GeoJSON payload
    // so demo visuals match the latest computed flood extent.
    if (DEMO_FLOOD_GEOJSON) {
      emitEvent({
        type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
        layer_type: 'flood',
        geojson: DEMO_FLOOD_GEOJSON,
        label: 'Flood Extent',
        style: { fillColor: '#0066ff', opacity: 0.4, outlineColor: '#00d4ff' }
      });
    }

    await delay(2000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'fly_to',
      lat: -6.225,
      lng: 106.855,
      altitude: 2000,
      location_name: 'Kampung Melayu Flood Zone',
    });

    await delay(5000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'camera_mode',
      mode: 'orbit',
      lat: -6.225,
      lng: 106.855,
    });

    await delay(3000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer: 'INFRASTRUCTURE',
      enabled: true,
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.EE_UPDATE,
      area_sqkm: DEMO_FLOOD_AREA_METRIC,
      growth_rate: 12,
      confidence: 0.92,
      metadata: { source: DEMO_FLOOD_SOURCE, updated_at: DEMO_FLOOD_UPDATED_AT }
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.STATUS_UPDATE,
      population_at_risk: 47000
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: `Flood extent analysis complete. ${DEMO_FLOOD_AREA_LABEL} square kilometers affected. Growth rate is 12% per hour. Based on Groundsource matches from previous floods, this pattern indicates a severe multi-day event.`,
      timestamp: Date.now(),
      confidence: 0.92
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'warning',
      message: `Flood extent analysis complete. ${DEMO_FLOOD_AREA_LABEL} km² affected.`,
      timestamp: Date.now()
    });
  },

  // ═══════════════════════════════════════════════════════════════
  // PHASE 3: Cascade (T+40s)
  //   → Flood level rise, threat rings, deploy helicopter,
  //     toggle INFRASTRUCTURE + THREAT_RADIUS layers
  // ═══════════════════════════════════════════════════════════════
  async () => {
    console.log('[Demo] Phase 3 - Cascade');

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'user',
      text: 'What happens if water rises another 2 meters?',
      timestamp: Date.now()
    });

    await delay(2000);

    // Stop orbit, switch to bird-eye for overview
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'camera_mode',
      mode: 'BIRD_EYE',
    });

    await delay(1000);

    // Toggle on infrastructure layer
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'INFRASTRUCTURE',
      enabled: true,
    });

    await delay(1000);

    // Toggle on threat radius layer
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'THREAT_RADIUS',
      enabled: true,
    });

    await delay(1500);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.STATUS_UPDATE,
      population_at_risk: 128000
    });

    // Deploy helicopter for aerial recon
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'deploy_entity',
      entity_id: 'heli-1',
      entity_type: 'helicopter',
      lat: -6.220,
      lng: 106.850,
      altitude: 300,
      label: 'RECON ALPHA',
    });

    await delay(1000);

    // Move helicopter to survey flood edge
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'move_entity',
      entity_id: 'heli-1',
      lat: -6.230,
      lng: 106.860,
      altitude: 250,
      duration_ms: 4000,
    });

    const mockExpandedFloodPoly = {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[[106.830, -6.245], [106.880, -6.245], [106.880, -6.205], [106.830, -6.205], [106.830, -6.245]]]
        },
        properties: {}
      }]
    };

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'flood',
      geojson: mockExpandedFloodPoly,
      label: 'Expanded Flood Extent (+2m)',
      style: { fillColor: '#ff0000', opacity: 0.2, outlineColor: '#ff4444' }
    });

    // Add some hospital markers
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'marker',
      geojson: { coordinates: [106.850, -6.220] },
      label: 'RS Hermina Jatinegara',
      style: { color: '#ff4444' }
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'marker',
      geojson: { coordinates: [106.860, -6.230] },
      label: 'RS Premier Jatinegara',
      style: { color: '#ff4444' }
    });

    // Provide a placeholder prediction image
    emitEvent({
      type: SERVER_MESSAGE_TYPES.FEED_UPDATE,
      mode: 'PREDICTION',
      data: {
        image: 'https://via.placeholder.com/800x600/1a2332/ff4444?text=RISK+PROJECTION+-+WORST+CASE',
        scenario: '+2m Rise Projection',
        confidence: 0.87
      }
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: 'Running multi-order cascade analysis. First order: flood area expands by 40%, placing 128,000 at risk. Second order: 3 major hospitals will be isolated, and Jalan Casablanca will be cut off. Third order: 2 power substations are in the expanded zone, threatening power to 160,000. Fourth order demographic impact: 12,400 children under 5 and 8,200 elderly individuals are now in the severe risk zone.',
      timestamp: Date.now(),
      confidence: 0.87
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'critical',
      message: 'CASCADE PROJECTION: +2m scenario. 128,000 at risk.',
      timestamp: Date.now()
    });

    await delay(2000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'deploy_entity',
      entity_type: 'command_post',
      entity_id: 'cp1',
      lat: -6.21,
      lng: 106.85,
      label: 'Forward Command Post',
    });

    await delay(3000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'set_atmosphere',
      mode: 'tactical',
    });

    await delay(2000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'add_threat_rings',
      lat: -6.225,
      lng: 106.855,
      rings: [1, 2, 3],
    });
  },

  // ═══════════════════════════════════════════════════════════════
  // PHASE 4: Disagreement + Action (T+70s)
  //   → Measurement line on evac route, street-level camera,
  //     deploy rescue boat
  // ═══════════════════════════════════════════════════════════════
  async () => {
    console.log('[Demo] Phase 4 - Disagreement + Action');

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'user',
      text: 'Route evacuation from Kampung Melayu to the nearest shelter.',
      timestamp: Date.now()
    });

    await delay(2000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: 'Commander, the closest shelter at Tebet is within the projected flood zone in 4 hours. I strongly advise against that route.',
      timestamp: Date.now(),
      confidence: 0.99
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'deploy_entity',
      entity_type: 'helicopter',
      entity_id: 'heli1',
      lat: -6.20,
      lng: 106.84,
      label: 'Rescue Heli Alpha',
    });

    await delay(3000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'move_entity',
      entity_id: 'heli1',
      lat: -6.225,
      lng: 106.855,
      duration: 5,
    });

    await delay(1000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'add_measurement',
      id: 'measure_heli',
      from_lat: -6.20,
      from_lng: 106.84,
      to_lat: -6.225,
      to_lng: 106.855,
      label: '3.2 km',
    });

    // Highlight Tebet danger zone
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'marker',
      geojson: { coordinates: [106.840, -6.235] },
      label: 'Tebet Shelter (DANGER ZONE)',
      style: { color: '#ffaa00' }
    });

    await delay(1500);

    // Add measurement line from Kampung Melayu to Tebet to show distance
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'add_measurement',
      line_id: 'evac-measure-1',
      lat1: -6.225,
      lng1: 106.855,
      lat2: -6.235,
      lng2: 106.840,
    });

    await delay(1000);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: 'I recommend rerouting to the University of Indonesia campus — higher elevation, capacity for 5,000.',
      timestamp: Date.now(),
      confidence: 0.95
    });

    // Switch to street-level camera for route preview
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'camera_mode',
      mode: 'STREET_LEVEL',
    });

    await delay(1000);

    // Deploy rescue boat
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'deploy_entity',
      entity_id: 'boat-1',
      entity_type: 'boat',
      lat: -6.228,
      lng: 106.852,
      altitude: 5,
      label: 'RESCUE BRAVO',
    });

    await delay(500);

    // Mock route from Kampung Melayu to UI
    const mockRoute = {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [[106.855, -6.225], [106.850, -6.250], [106.840, -6.300], [106.830, -6.360]]
        },
        properties: {}
      }]
    };

    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      layer_type: 'route',
      geojson: mockRoute,
      label: 'Evacuation Route to UI Campus',
      style: { strokeColor: '#00ff88', strokeWidth: 4, dashPattern: [10, 5] }
    });

    // Add measurement along evacuation route
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'add_measurement',
      line_id: 'evac-route-dist',
      lat1: -6.225,
      lng1: 106.855,
      lat2: -6.360,
      lng2: 106.830,
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'warning',
      message: 'Evacuation route generated. Tebet rejected (flood zone). Rerouted to UI campus.',
      timestamp: Date.now()
    });

    // Enable PiP drone view
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_pip',
      enabled: true,
    });
  },

  // ═══════════════════════════════════════════════════════════════
  // PHASE 5: Alert + Summary (T+70s)
  //   → Overview camera, clear storm, summary
  // ═══════════════════════════════════════════════════════════════
  async () => {
    console.log('[Demo] Phase 5 - Alert + Summary');

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'user',
      text: 'Send emergency advisory.',
      timestamp: Date.now()
    });

    await delay(1500);

    // Switch to overview camera for final briefing
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'camera_mode',
      mode: 'overview',
    });

    await delay(2000);

    // Toggle on historical floods for comparison
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'HISTORICAL_FLOODS',
      enabled: true,
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY,
      severity: 'info',
      message: 'Emergency advisory sent via Gmail to jakarta-emergency@example.com',
      timestamp: Date.now()
    });

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: 'Emergency advisory email has been dispatched to all relevant agencies.',
      timestamp: Date.now(),
      confidence: 0.99
    });

    await delay(2000);

    // Clear storm atmosphere for clean summary view
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'STORM_EFFECT',
      enabled: false,
    });

    await delay(1500);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'user',
      text: 'Give me the incident summary.',
      timestamp: Date.now()
    });

    await delay(1500);

    emitEvent({
      type: SERVER_MESSAGE_TYPES.TRANSCRIPT,
      speaker: 'agent',
      text: `Incident Summary: At 14:00, Ciliwung River exceeded the 4.0m threshold. Current extent affects ${DEMO_FLOOD_AREA_LABEL} sq km. A +2m rise cascade analysis revealed severe multi-order impacts across infrastructure and demographics to 128,000 people. An evacuation route to University of Indonesia was established, safely bypassing the Tebet danger zone. Emergency advisories have been successfully delivered.`,
      timestamp: Date.now(),
      confidence: 0.99
    });

    // Toggle on population density for final context
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'POPULATION_DENSITY',
      enabled: true,
    });

    // Clean up FLOOD_REPLAY, re-enable normal FLOOD_EXTENT
    await delay(1000);
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'FLOOD_REPLAY',
      enabled: false,
    });
    emitEvent({
      type: SERVER_MESSAGE_TYPES.MAP_UPDATE,
      action: 'toggle_layer',
      layer_id: 'FLOOD_EXTENT',
      enabled: true,
    });
  }
];

export async function startDemoSimulation() {
  if (isSimulationRunning) return;
  console.log('--- STARTING DEMO SIMULATION ---');
  isSimulationRunning = true;

  try {
    await delay(3000);
    if (!isSimulationRunning) return;
    await DEMO_SEQUENCE[0]();

    await delay(10000);
    if (!isSimulationRunning) return;
    await DEMO_SEQUENCE[1]();

    await delay(18000);
    if (!isSimulationRunning) return;
    await DEMO_SEQUENCE[2]();

    await delay(22000);
    if (!isSimulationRunning) return;
    await DEMO_SEQUENCE[3]();

    await delay(18000);
    if (!isSimulationRunning) return;
    await DEMO_SEQUENCE[4]();

    console.log('--- DEMO SIMULATION COMPLETE ---');
  } catch (err) {
    console.error('Demo simulation error:', err);
  } finally {
    isSimulationRunning = false;
  }
}

export function stopDemoSimulation() {
  console.log('--- STOPPING DEMO SIMULATION ---');
  isSimulationRunning = false;
}

// Attach to window object
if (typeof window !== 'undefined') {
  window.startDemoSimulation = startDemoSimulation;
  window.stopDemoSimulation = stopDemoSimulation;

  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'D') {
      if (isSimulationRunning) {
        stopDemoSimulation();
      } else {
        startDemoSimulation();
      }
    }
  });
}
