export const MOCK_TOPBAR = {
  incidentName: 'Jakarta Flash Flood',
  elapsedSeconds: 0,
  mode: 'ALERT',
  populationAtRisk: 47000,
  waterLevelMeters: 4.1,
  connectionStatus: 'CONNECTED',
};

export const MOCK_STRATEGIC_VIEW = {
  mode: 'SURVEILLANCE',
  label: '3D STRATEGIC VIEW',
  siteLabel: 'Kampung Melayu',
  fieldMode: false,
  lowBandwidth: false,
  bandwidthProfile: 'FULL',
  activeComparison: 'CHANGE',
  timeWindow: {
    activeIndex: 3,
    steps: [
      { id: 'baseline-1', label: 'Jun 04', kind: 'before' },
      { id: 'baseline-2', label: 'Jun 18', kind: 'before' },
      { id: 'event-1', label: 'Nov 03', kind: 'after' },
      { id: 'event-2', label: 'Nov 14', kind: 'after' },
      { id: 'event-3', label: 'Nov 28', kind: 'after' },
    ],
    beforeLabel: 'Dry Baseline',
    afterLabel: 'Flood Event',
    lastUpdated: '2026-03-14T08:23:00Z',
  },
};

// Inline SVG placeholders for recon feed (always available, no network dependency)
const DRONE_PLACEHOLDER = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600"><rect fill="#0d1117" width="800" height="600"/><rect fill="#1a2332" x="40" y="40" width="720" height="520" rx="4"/><text x="400" y="260" text-anchor="middle" fill="#00d4ff" font-family="monospace" font-size="18" letter-spacing="4" opacity="0.7">DRONE CAM — KAMPUNG MELAYU</text><text x="400" y="300" text-anchor="middle" fill="#6b7b8d" font-family="monospace" font-size="12" letter-spacing="2">6.225S 106.855E  ALT 120m</text><circle cx="400" cy="380" r="40" fill="none" stroke="#00d4ff" stroke-width="1" opacity="0.3"/><line x1="380" y1="380" x2="420" y2="380" stroke="#00d4ff" stroke-width="1" opacity="0.5"/><line x1="400" y1="360" x2="400" y2="400" stroke="#00d4ff" stroke-width="1" opacity="0.5"/></svg>')}`;
const SAR_PLACEHOLDER = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600"><rect fill="#0a0e17" width="800" height="600"/><rect fill="#1a1a2e" x="40" y="40" width="720" height="520" rx="4"/><text x="400" y="260" text-anchor="middle" fill="#00ff88" font-family="monospace" font-size="18" letter-spacing="4" opacity="0.7">SENTINEL-1 SAR</text><text x="400" y="300" text-anchor="middle" fill="#6b7b8d" font-family="monospace" font-size="12" letter-spacing="2">VV DESCENDING  CHANGE DETECTION</text><rect x="200" y="340" width="400" height="120" fill="none" stroke="#00ff88" stroke-width="1" opacity="0.2"/><rect x="280" y="360" width="120" height="60" fill="#00ff88" opacity="0.1"/><rect x="420" y="370" width="80" height="50" fill="#00ff88" opacity="0.15"/></svg>')}`;

export const MOCK_RECON_FEED = {
  activeMode: 'DRONE',
  currentFrame: DRONE_PLACEHOLDER,
  predictionImage: null,
  sarImage: SAR_PLACEHOLDER,
  timestamp: '2026-03-14T08:23:00Z',
};

export const MOCK_EARTH_ENGINE = {
  metrics: {
    floodAreaSqKm: 23.4,
    growthRatePctHr: 12,
    populationAtRisk: 47000,
    infrastructureAtRisk: 14,
    historicalMatches: 3,
    confidencePct: 87,
  },
  systemStatus: [
    { name: 'Gemini Live API', status: 'ONLINE' },
    { name: 'BigQuery', status: 'ONLINE' },
    { name: 'Cloud Firestore', status: 'ONLINE' },
    { name: 'Earth Engine', status: 'ONLINE' },
    { name: 'Cloud Storage', status: 'ONLINE' },
    { name: 'Cloud Run', status: 'ONLINE' },
    { name: 'Google Maps Platform', status: 'ONLINE' },
    { name: 'Places API', status: 'ONLINE' },
    { name: 'Directions API', status: 'ONLINE' },
    { name: 'Geocoding API', status: 'ONLINE' },
    { name: 'Elevation API', status: 'ONLINE' },
    { name: 'Gmail API', status: 'ONLINE' },
    { name: 'CesiumJS 3D Tiles', status: 'ONLINE' },
    { name: 'Groundsource DB', status: 'ONLINE' },
    { name: 'Sentinel-1 SAR', status: 'ONLINE' },
    { name: 'Sentinel-2 NDWI', status: 'ONLINE' },
    { name: 'Nano Banana 2', status: 'ONLINE' },
  ],
  activeComparison: 'CHANGE',
  timeSliderIndex: 3,
  comparisonModes: ['BEFORE', 'AFTER', 'CHANGE'],
  timeline: {
    title: 'Temporal Compare',
    beforeLabel: 'Dry season baseline',
    afterLabel: 'Flood event composite',
    steps: [
      { id: 't0', label: 'Jun 04', caption: 'Low-water baseline' },
      { id: 't1', label: 'Jun 18', caption: 'Stable river profile' },
      { id: 't2', label: 'Nov 03', caption: 'Initial inundation signal' },
      { id: 't3', label: 'Nov 14', caption: 'Peak flood footprint' },
      { id: 't4', label: 'Nov 28', caption: 'Water recession trend' },
    ],
  },
  lowBandwidth: false,
  bandwidthProfile: 'FULL',
  provenance: {
    source: 'Sentinel-1 SAR',
    sourceDetail: 'COPERNICUS/S1_GRD descending VV composite',
    acquisitionWindow: '2025-11-01 to 2025-11-30',
    baselineWindow: '2025-06-01 to 2025-09-30',
    method: 'SAR change detection',
    confidence: 'HIGH',
    status: 'PRECOMPUTED',
    updatedAt: '2026-03-14T08:21:17Z',
    sidecar: 'analysis_provenance.json',
  },
};

export const MOCK_NEURAL_LINK = {
  transcript: [
    {
      id: '1',
      role: 'system',
      text: 'HAWK EYE v1.0 initialized. All 17 services nominal.',
      timestamp: '08:20:00',
    },
    {
      id: '2',
      role: 'agent',
      text: 'Hawk Eye online. Monitoring Jakarta flood basin. Groundsource database loaded — 2.6 million historical events available for cross-reference.',
      timestamp: '08:21:03',
      confidence: 0.95,
    },
    {
      id: '3',
      role: 'agent',
      text: 'Water level at Kampung Melayu gauge: 4.1 meters. This exceeds the 3.5m critical threshold. Activating ALERT mode.',
      timestamp: '08:21:08',
      confidence: 0.92,
    },
    {
      id: '4',
      role: 'user',
      text: 'Show me the flood extent.',
      timestamp: '08:21:15',
    },
    {
      id: '5',
      role: 'agent',
      text: 'Activating Earth Engine analysis. Sentinel-1 SAR change detection running for Jakarta metro area. Estimated flood area: 23.4 square kilometers.',
      timestamp: '08:21:17',
      confidence: 0.91,
    },
    {
      id: '6',
      role: 'agent',
      text: 'Cross-referencing with Groundsource database — this area has flooded 47 times since 2003. Current pattern matches the January 2020 event with 84% similarity.',
      timestamp: '08:21:34',
      confidence: 0.88,
    },
    {
      id: '7',
      role: 'user',
      text: 'What happens if water rises 2 more meters?',
      timestamp: '08:22:01',
    },
    {
      id: '8',
      role: 'agent',
      text: 'Running cascade analysis at +2 meters... First order: population at risk jumps to 128,000. Second order: 3 hospitals lose road access. Third order: 2 power substations at risk, potentially affecting 160,000 residents.',
      timestamp: '08:22:05',
      confidence: 0.87,
    },
  ],
  isRecording: false,
  isAgentSpeaking: false,
  overallConfidence: 0.87,
  riskLevel: 'HIGH',
  sourceCount: 3,
};

export const MOCK_INCIDENT_LOG = {
  entries: [
    { id: '1', timestamp: '08:20:00', severity: 'INFO', text: 'System initialized. All services nominal.' },
    { id: '2', timestamp: '08:20:01', severity: 'INFO', text: 'Groundsource DB connected — 2,646,302 records loaded.' },
    { id: '3', timestamp: '08:20:02', severity: 'INFO', text: 'Jakarta materialized view active — 847 local events indexed.' },
    { id: '4', timestamp: '08:21:03', severity: 'WARNING', text: 'Water level threshold exceeded: 4.1m (critical: 3.5m)' },
    { id: '5', timestamp: '08:21:08', severity: 'WARNING', text: 'Mode escalated: SILENT → ALERT' },
    { id: '6', timestamp: '08:21:17', severity: 'INFO', text: 'Earth Engine SAR analysis initiated.' },
    { id: '7', timestamp: '08:21:22', severity: 'CRITICAL', text: 'Flood area expanding: 23.4 km² (+12%/hr)' },
    { id: '8', timestamp: '08:21:34', severity: 'INFO', text: 'Groundsource pattern match: Jan 2020 event (84% similarity)' },
    { id: '9', timestamp: '08:22:05', severity: 'CRITICAL', text: 'Cascade analysis: 3 hospitals at risk at +2m' },
    { id: '10', timestamp: '08:22:06', severity: 'CRITICAL', text: 'Population at risk updated: 47,000 → 128,000 (projected +2m)' },
    { id: '11', timestamp: '08:22:30', severity: 'WARNING', text: '2 power substations in expanded flood zone' },
    { id: '12', timestamp: '08:23:12', severity: 'INFO', text: 'Evacuation route computed: Kampung Melayu → UI Depok' },
  ],
};
