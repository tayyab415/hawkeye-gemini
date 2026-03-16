import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import * as Cesium from 'cesium';
import { INFRASTRUCTURE_DATA } from '../../data/infrastructureData';
import floodExtentRaw from '../../../../data/geojson/flood_extent.geojson?raw';
import triageZonesRaw from '../../../../data/geojson/triage_zones.geojson?raw';
import './DataLayerPanel.css';

// ── Static GeoJSON parsing ──────────────────────────────────────
let FLOOD_EXTENT_GEOJSON = null;
let TRIAGE_ZONES_GEOJSON = null;
try { FLOOD_EXTENT_GEOJSON = JSON.parse(floodExtentRaw); } catch { /* will be null */ }
try { TRIAGE_ZONES_GEOJSON = JSON.parse(triageZonesRaw); } catch { /* will be null */ }

// Wrap single Feature in a FeatureCollection for consistency
if (FLOOD_EXTENT_GEOJSON && FLOOD_EXTENT_GEOJSON.type === 'Feature') {
  FLOOD_EXTENT_GEOJSON = { type: 'FeatureCollection', features: [FLOOD_EXTENT_GEOJSON] };
}

function getFloodBadge(geojson) {
  if (!geojson) return 'n/a';
  const candidates = [
    geojson?.properties?.flood_area_sqkm,
    geojson?.features?.[0]?.properties?.flood_area_sqkm,
    geojson?.features?.[0]?.properties?.total_vector_area_sqkm,
  ];
  for (const value of candidates) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) {
      return `${parsed.toFixed(2)} km\u00B2`;
    }
  }
  return `${geojson?.features?.length ?? 0} polygon${(geojson?.features?.length ?? 0) === 1 ? '' : 's'}`;
}

const FLOOD_BADGE = getFloodBadge(FLOOD_EXTENT_GEOJSON);

// ── Historical flood polygons (synthetic variants of the real extent) ──
function makeHistoricalFlood(year, scale, offsetLat, offsetLng) {
  if (!FLOOD_EXTENT_GEOJSON) return null;
  const base = FLOOD_EXTENT_GEOJSON.features[0].geometry.coordinates[0];
  const centerLat = base.reduce((s, c) => s + c[1], 0) / base.length;
  const centerLng = base.reduce((s, c) => s + c[0], 0) / base.length;
  const coords = base.map(([lng, lat]) => [
    centerLng + (lng - centerLng) * scale + offsetLng,
    centerLat + (lat - centerLat) * scale + offsetLat,
  ]);
  return {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { year, name: `${year} Flood Extent` },
      geometry: { type: 'Polygon', coordinates: [coords] },
    }],
  };
}

const HISTORICAL_FLOODS = [
  makeHistoricalFlood(2020, 0.85, 0.012, -0.015),
  makeHistoricalFlood(2021, 1.2, -0.008, 0.018),
  makeHistoricalFlood(2023, 0.65, 0.018, 0.008),
].filter(Boolean);

// ── Population density grid (~1,200+ candidate points over Jakarta metro) ──
// Use localized hotspot kernels so density fades out cleanly instead of
// summing into a blanket tint across the full bounding box. Rows are
// staggered to avoid a visible square-dot lattice from altitude.
function generatePopulationGrid() {
  const points = [];
  const latMin = -6.35, latMax = -6.10;
  const lngMin = 106.70, lngMax = 107.00;
  const step = 0.005; // ~550m between points for stronger overlap
  const minVisibleDensity = 0.18;

  // High-density centers for multi-peak Gaussian density model
  const densityCenters = [
    { lat: -6.200, lng: 106.850, weight: 1.0 },   // Central Jakarta
    { lat: -6.225, lng: 106.855, weight: 0.9 },   // Kampung Melayu
    { lat: -6.185, lng: 106.810, weight: 0.7 },   // Tanah Abang
    { lat: -6.170, lng: 106.830, weight: 0.6 },   // Kemayoran area
    { lat: -6.215, lng: 106.870, weight: 0.65 },  // Jatinegara
    { lat: -6.260, lng: 106.810, weight: 0.5 },   // South Jakarta
  ];

  let rowIndex = 0;
  for (let lat = latMin; lat <= latMax; lat += step, rowIndex += 1) {
    const rowOffset = rowIndex % 2 === 0 ? 0 : step / 2;
    for (let lng = lngMin + rowOffset; lng <= lngMax; lng += step) {
      // Use the strongest nearby hotspot rather than summing all hotspots.
      // That keeps density concentrated around urban centers instead of
      // spreading low-intensity color across the entire metro area.
      let density = 0;
      for (const c of densityCenters) {
        const dLat = (lat - c.lat) * 111.0;
        const dLng = (lng - c.lng) * 111.0 * Math.cos(lat * Math.PI / 180);
        const distKm = Math.sqrt(dLat * dLat + dLng * dLng);
        const hotspotDensity = c.weight * Math.exp(-(distKm * distKm) / 8);
        density = Math.max(density, hotspotDensity);
      }
      density = Math.min(1.0, density);
      if (density >= minVisibleDensity) {
        points.push({ lat, lng, density });
      }
    }
  }
  return points;
}

const POPULATION_GRID = generatePopulationGrid();

function getPopulationHeatStyle(density) {
  if (density > 0.7) {
    return {
      coreColor: new Cesium.Color(1.0, 0.14, 0.14, 0.30),
      haloColor: new Cesium.Color(1.0, 0.30, 0.16, 0.13),
      coreRadius: 500,
      haloRadius: 900,
    };
  }
  if (density > 0.35) {
    return {
      coreColor: new Cesium.Color(1.0, 0.34, 0.14, 0.18),
      haloColor: new Cesium.Color(1.0, 0.47, 0.20, 0.09),
      coreRadius: 500,
      haloRadius: 820,
    };
  }
  return {
    coreColor: new Cesium.Color(1.0, 0.50, 0.22, 0.10),
    haloColor: new Cesium.Color(1.0, 0.67, 0.28, 0.05),
    coreRadius: 500,
    haloRadius: 720,
  };
}

// ── Infrastructure color map ────────────────────────────────────
const INFRA_COLORS = {
  hospital: '#ff3333',
  school: '#ffcc00',
  shelter: '#33ff33',
  power_station: '#ff8800',
};

const INFRA_ICONS = {
  hospital: '\u271A',  // cross
  school: '\u{1F4D6}', // open book emoji fallback
  shelter: '\u2302',    // house
  power_station: '\u26A1', // lightning
};

// ── Inline SVG data URIs for infrastructure billboards ──────────
// Each icon: 32x32 with dark circle background, white stroke, colored symbol
const INFRA_BILLBOARD_ICONS = {
  hospital: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
    <circle cx="16" cy="16" r="14" fill="#1a1e2e" stroke="white" stroke-width="2"/>
    <line x1="16" y1="8" x2="16" y2="24" stroke="#ff3333" stroke-width="3" stroke-linecap="round"/>
    <line x1="8" y1="16" x2="24" y2="16" stroke="#ff3333" stroke-width="3" stroke-linecap="round"/>
  </svg>`)}`,
  school: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
    <circle cx="16" cy="16" r="14" fill="#1a1e2e" stroke="white" stroke-width="2"/>
    <rect x="9" y="10" width="14" height="12" rx="1" fill="none" stroke="#ffcc00" stroke-width="2"/>
    <line x1="16" y1="10" x2="16" y2="22" stroke="#ffcc00" stroke-width="1.5"/>
    <line x1="9" y1="14" x2="23" y2="14" stroke="#ffcc00" stroke-width="1"/>
    <line x1="9" y1="18" x2="23" y2="18" stroke="#ffcc00" stroke-width="1"/>
  </svg>`)}`,
  shelter: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
    <circle cx="16" cy="16" r="14" fill="#1a1e2e" stroke="white" stroke-width="2"/>
    <polygon points="16,7 24,15 22,15 22,24 10,24 10,15 8,15" fill="none" stroke="#33ff33" stroke-width="2" stroke-linejoin="round"/>
    <rect x="13" y="18" width="6" height="6" fill="none" stroke="#33ff33" stroke-width="1.5"/>
  </svg>`)}`,
  power_station: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
    <circle cx="16" cy="16" r="14" fill="#1a1e2e" stroke="white" stroke-width="2"/>
    <polygon points="18,6 11,18 15,18 14,26 21,14 17,14" fill="#ff8800" stroke="#ff8800" stroke-width="0.5"/>
  </svg>`)}`,
};

// ── Layer definitions ───────────────────────────────────────────
const LAYERS = [
  {
    id: 'FLOOD_EXTENT',
    name: 'Flood Extent',
    icon: '\u{1F4A7}',
    iconBg: 'rgba(0, 100, 255, 0.25)',
    badge: FLOOD_BADGE,
  },
  {
    id: 'TRIAGE_ZONES',
    name: 'Triage Zones',
    icon: '\u25B2',
    iconBg: 'rgba(255, 68, 68, 0.2)',
    badge: '15 zones',
  },
  {
    id: 'INFRASTRUCTURE',
    name: 'Infrastructure',
    icon: '\u271A',
    iconBg: 'rgba(255, 136, 0, 0.2)',
    badge: '470 from BigQuery \u2022 161H 263S 26Sh 20P',
    source: 'BigQuery',
  },
  {
    id: 'EVACUATION_ROUTES',
    name: 'Evacuation Routes',
    icon: '\u2192',
    iconBg: 'rgba(0, 255, 136, 0.2)',
    badge: 'agent-generated',
  },
  {
    id: 'POPULATION_DENSITY',
    name: 'Population Density',
    icon: '\u2593',
    iconBg: 'rgba(255, 68, 68, 0.15)',
    badge: '~1,200 grid cells',
  },
  {
    id: 'HISTORICAL_FLOODS',
    name: 'Historical Floods',
    icon: '\u23F3',
    iconBg: 'rgba(255, 255, 255, 0.1)',
    badge: '2020, 2021, 2023',
  },
  {
    id: 'THREAT_RADIUS',
    name: 'Threat Radius',
    icon: '\u25CE',
    iconBg: 'rgba(255, 170, 0, 0.2)',
    badge: '+1m, +2m, +3m rings',
  },
];

// ── Effects layer definitions (separate from data layers) ───────
const EFFECTS_LAYERS = [
  {
    id: 'STORM_EFFECT',
    name: 'Storm Effect',
    icon: '\u26C8',
    iconBg: 'rgba(100, 120, 180, 0.25)',
    badge: 'atmosphere shader',
  },
  {
    id: 'FLOOD_REPLAY',
    name: 'Flood Replay',
    icon: '\u25B6',
    iconBg: 'rgba(0, 180, 255, 0.2)',
    badge: 'sequential progression',
  },
];

function resolveBackendHttpBase() {
  const wsUrl = import.meta.env.VITE_WS_URL;
  if (typeof wsUrl !== 'string' || wsUrl.trim().length === 0) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    const hostname = window.location.hostname || 'localhost';
    return `${protocol}//${hostname}:8000`;
  }

  try {
    const parsed = new URL(wsUrl);
    parsed.protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
    parsed.pathname = parsed.pathname.replace(/\/ws\/?$/, '');
    return `${parsed.origin}${parsed.pathname === '/' ? '' : parsed.pathname}`;
  } catch {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    const hostname = window.location.hostname || 'localhost';
    return `${protocol}//${hostname}:8000`;
  }
}

function resolveTileUrlTemplate(urlTemplate) {
  if (typeof urlTemplate !== 'string' || urlTemplate.trim().length === 0) {
    return null;
  }
  try {
    return new URL(urlTemplate, `${resolveBackendHttpBase()}/`).toString();
  } catch {
    return null;
  }
}

function normalizeRuntimeGeoJson(payload) {
  if (!payload) return null;

  let parsed = payload;
  if (typeof parsed === 'string') {
    if (!parsed.trim()) return null;
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }

  if (!parsed || typeof parsed !== 'object') return null;

  if (parsed.type === 'FeatureCollection' && Array.isArray(parsed.features)) {
    return parsed;
  }
  if (parsed.type === 'Feature') {
    return { type: 'FeatureCollection', features: [parsed] };
  }
  if (parsed.type && Array.isArray(parsed.coordinates)) {
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: parsed }],
    };
  }

  return null;
}

function firstNonEmptyString(values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function normalizeTemporalFrameToken(frameId) {
  if (typeof frameId !== 'string') return null;
  const normalized = frameId.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === 'before') return 'baseline';
  if (normalized === 'after') return 'event';
  if (normalized === 'delta' || normalized === 'difference') return 'change';
  return normalized;
}

function normalizeTemporalTextToken(value) {
  if (typeof value !== 'string') return '';
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_');
}

function getRuntimeLayerSearchToken(layer) {
  if (!layer || typeof layer !== 'object') return '';
  return normalizeTemporalTextToken([
    layer.id,
    layer.layer_id,
    layer.name,
    layer.label,
    layer.temporal_frame,
    layer.temporalFrame,
    layer.frame_id,
    layer.frameId,
  ].filter(Boolean).join(' '));
}

function getRuntimeLayerTemporalFrame(layer) {
  if (!layer || typeof layer !== 'object') return null;
  const candidate = firstNonEmptyString([
    layer.temporal_frame,
    layer.temporalFrame,
    layer.temporal_frame_id,
    layer.temporalFrameId,
    layer.frame_id,
    layer.frameId,
    layer?.frame?.frame_id,
    layer?.frame?.id,
    layer?.temporal?.frame_id,
    layer?.temporal?.id,
    layer?.timestamps?.frame_id,
  ]);
  const normalizedCandidate = normalizeTemporalFrameToken(candidate);
  if (normalizedCandidate) return normalizedCandidate;

  const layerToken = getRuntimeLayerSearchToken(layer);
  if (!layerToken) return null;
  if (layerToken.includes('baseline') || layerToken.includes('before')) return 'baseline';
  if (layerToken.includes('event') || layerToken.includes('after')) return 'event';
  if (
    layerToken.includes('change') ||
    layerToken.includes('delta') ||
    layerToken.includes('difference') ||
    layerToken.includes('diff')
  ) {
    return 'change';
  }
  return null;
}

function layerMatchesTemporalFrame(layer, frameId) {
  const normalizedFrameId = normalizeTemporalFrameToken(frameId);
  if (!normalizedFrameId) return false;

  const layerFrame = getRuntimeLayerTemporalFrame(layer);
  if (layerFrame === normalizedFrameId) return true;

  const frameToken = normalizeTemporalTextToken(normalizedFrameId);
  const layerToken = getRuntimeLayerSearchToken(layer);
  if (!frameToken || !layerToken) return false;

  const aliasTokens = [frameToken];
  if (normalizedFrameId === 'baseline') aliasTokens.push('before');
  if (normalizedFrameId === 'event') aliasTokens.push('after');
  if (normalizedFrameId === 'change') aliasTokens.push('delta', 'difference', 'diff');

  return aliasTokens.some((token) => token && layerToken.includes(token));
}

const COMPARISON_TO_FRAME = {
  BEFORE: 'baseline',
  AFTER: 'event',
  CHANGE: 'change',
};

function normalizeTemporalComparison(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().toUpperCase();
  if (!normalized) return null;
  if (normalized === 'BASELINE') return 'BEFORE';
  if (normalized === 'EVENT') return 'AFTER';
  if (normalized === 'DELTA' || normalized === 'DIFFERENCE') return 'CHANGE';
  return COMPARISON_TO_FRAME[normalized] ? normalized : null;
}

function getTemporalFrameForComparison(comparison) {
  const normalizedComparison = normalizeTemporalComparison(comparison);
  if (!normalizedComparison) return null;
  return COMPARISON_TO_FRAME[normalizedComparison] || null;
}

function getTemporalFrameGeoJsonCandidates(frame) {
  if (!frame || typeof frame !== 'object') return [];
  const frameLayers = Array.isArray(frame.layers)
    ? frame.layers.filter((layer) => layer && typeof layer === 'object')
    : [];
  return [
    frame.geojson,
    frame.flood_geojson,
    frame.flood_extent_geojson,
    frame.vector_geojson,
    frame.overlay_geojson,
    frame?.map_overlay?.geojson,
    ...frameLayers.flatMap((layer) => [
      layer.geojson,
      layer.flood_geojson,
      layer.flood_extent_geojson,
      layer.vector_geojson,
      layer.overlay_geojson,
      layer?.tile_source?.fallback_geojson,
    ]),
  ];
}

function getTemporalFrameGeoJsonUrlCandidates(frame) {
  if (!frame || typeof frame !== 'object') return [];
  const frameLayers = Array.isArray(frame.layers)
    ? frame.layers.filter((layer) => layer && typeof layer === 'object')
    : [];
  return [
    frame.geojson_url,
    frame.flood_geojson_url,
    frame.flood_extent_geojson_url,
    frame.vector_geojson_url,
    frame.overlay_url,
    frame.url,
    frame?.map_overlay?.url,
    ...frameLayers.flatMap((layer) => [
      layer.geojson_url,
      layer.flood_geojson_url,
      layer.flood_extent_geojson_url,
      layer.vector_geojson_url,
      layer.overlay_url,
      layer.url,
      layer?.tile_source?.fallback_geojson_url,
      layer?.tile_source?.fallback_url,
    ]),
  ];
}

function getFloodFrameVisualProfile(frameId) {
  switch (normalizeTemporalFrameToken(frameId)) {
    case 'baseline':
      return {
        colorLow: '#2c77b8',
        colorHigh: '#163a6a',
        outlineColor: '#7fc7ff',
        alphaBase: 0.30,
        alphaPulse: 0.05,
        outlineAlpha: 0.56,
        polylineAlpha: 0.48,
        extrusionBase: 45,
        extrusionRange: 170,
        tileAlpha: 0.34,
      };
    case 'event':
      return {
        colorLow: '#00a6ff',
        colorHigh: '#004f9c',
        outlineColor: '#36c8ff',
        alphaBase: 0.44,
        alphaPulse: 0.08,
        outlineAlpha: 0.64,
        polylineAlpha: 0.58,
        extrusionBase: 75,
        extrusionRange: 280,
        tileAlpha: 0.52,
      };
    case 'change':
    default:
      return {
        colorLow: '#00b4ff',
        colorHigh: '#001a66',
        outlineColor: '#00d4ff',
        alphaBase: 0.55,
        alphaPulse: 0.10,
        outlineAlpha: 0.70,
        polylineAlpha: 0.70,
        extrusionBase: 100,
        extrusionRange: 400,
        tileAlpha: 0.66,
      };
  }
}

const FALLBACK_SIGNAL_CENTER = {
  lat: -6.225,
  lng: 106.855,
};

function normalizeSignalToken(value) {
  if (typeof value !== 'string' || value.trim().length === 0) return null;
  return value.trim().replace(/[-\s]+/g, '_').toUpperCase();
}

function resolveConfidenceLabel(value) {
  if (!value) return null;
  if (typeof value === 'string') return normalizeSignalToken(value);
  if (typeof value !== 'object') return null;
  return normalizeSignalToken(
    value.label ||
    value.level ||
    value.confidence_label ||
    value.confidenceLevel ||
    null
  );
}

function toPercent(value) {
  let numericValue = null;

  if (typeof value === 'number') {
    numericValue = value;
  } else if (typeof value === 'string' && value.trim().length > 0) {
    numericValue = Number(value);
  } else if (value && typeof value === 'object') {
    if (typeof value.score === 'number') {
      numericValue = value.score;
    } else if (typeof value.score === 'string' && value.score.trim().length > 0) {
      numericValue = Number(value.score);
    } else if (typeof value.confidence_score === 'number') {
      numericValue = value.confidence_score;
    } else if (
      typeof value.confidence_score === 'string' &&
      value.confidence_score.trim().length > 0
    ) {
      numericValue = Number(value.confidence_score);
    }
  }

  if (!Number.isFinite(numericValue)) return null;
  const asPercent = numericValue <= 1 ? numericValue * 100 : numericValue;
  return Math.max(0, Math.min(100, asPercent));
}

function confidenceLevelFromPercent(percent) {
  if (!Number.isFinite(percent)) return null;
  if (percent >= 80) return 'HIGH';
  if (percent >= 60) return 'MEDIUM';
  return 'LOW';
}

function uncertaintyLevelFromPercent(percent) {
  if (!Number.isFinite(percent)) return null;
  if (percent <= 20) return 'LOW';
  if (percent <= 40) return 'MEDIUM';
  return 'HIGH';
}

function uncertaintyLevelFromConfidence(confidenceLevel) {
  const normalized = normalizeSignalToken(confidenceLevel);
  if (!normalized) return null;
  if (normalized === 'HIGH') return 'LOW';
  if (normalized === 'MEDIUM') return 'MEDIUM';
  return 'HIGH';
}

function toTimestampMs(value) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const timestampMs = new Date(value).getTime();
  return Number.isNaN(timestampMs) ? null : timestampMs;
}

function freshnessLevelFromAge(ageMs) {
  if (!Number.isFinite(ageMs)) return null;
  if (ageMs <= 2 * 60 * 60 * 1000) return 'FRESH';
  if (ageMs <= 24 * 60 * 60 * 1000) return 'RECENT';
  return 'STALE';
}

function collectGeometryCoordinates(geometry, collector) {
  if (!geometry || typeof geometry !== 'object') return;
  const coordinates = geometry.coordinates;
  if (!Array.isArray(coordinates)) return;

  const maybePushCoordinate = (point) => {
    if (!Array.isArray(point) || point.length < 2) return;
    const lng = Number(point[0]);
    const lat = Number(point[1]);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
    collector.push([lng, lat]);
  };

  const walk = (node) => {
    if (!Array.isArray(node)) return;
    if (node.length >= 2 && !Array.isArray(node[0]) && !Array.isArray(node[1])) {
      maybePushCoordinate(node);
      return;
    }
    node.forEach(walk);
  };

  walk(coordinates);
}

function getGeoJsonSignalCenter(geojson) {
  const normalized = normalizeRuntimeGeoJson(geojson);
  if (!normalized) return null;

  const points = [];
  const features = Array.isArray(normalized.features) ? normalized.features : [];
  for (const feature of features) {
    if (!feature || typeof feature !== 'object') continue;
    collectGeometryCoordinates(feature.geometry, points);
    if (points.length >= 300) break;
  }

  if (points.length === 0) return null;
  const sums = points.reduce(
    (acc, [lng, lat]) => ({
      lng: acc.lng + lng,
      lat: acc.lat + lat,
    }),
    { lng: 0, lat: 0 },
  );

  return {
    lng: sums.lng / points.length,
    lat: sums.lat / points.length,
  };
}

function normalizeTemporalControlFrame(frameId) {
  return normalizeTemporalFrameToken(frameId);
}

function buildFloodGeoJsonSignature(geojson) {
  const normalized = normalizeRuntimeGeoJson(geojson);
  if (!normalized) return '';
  const featureCount = normalized.features?.length ?? 0;
  const firstFeatureGeometryType = normalized.features?.[0]?.geometry?.type || '';
  const firstFeatureArea = normalized.features?.[0]?.properties?.flood_area_sqkm;
  const rootArea = normalized.properties?.flood_area_sqkm;
  return `${featureCount}:${firstFeatureGeometryType}:${firstFeatureArea ?? rootArea ?? ''}`;
}

const FRAME_ORDER = new Map([
  ['baseline', 0],
  ['event', 1],
  ['change', 2],
]);

function sortLayersByTemporalFrame(runtimeLayerDescriptors) {
  return [...runtimeLayerDescriptors].sort((a, b) => {
    const frameA = getRuntimeLayerTemporalFrame(a.layer);
    const frameB = getRuntimeLayerTemporalFrame(b.layer);
    const orderA = FRAME_ORDER.get(frameA) ?? 99;
    const orderB = FRAME_ORDER.get(frameB) ?? 99;
    if (orderA !== orderB) return orderA - orderB;
    return String(a.layer?.id || '').localeCompare(String(b.layer?.id || ''));
  });
}

const FIELD_PACK_CRITICAL_LAYER_IDS = [
  'FLOOD_EXTENT',
  'TRIAGE_ZONES',
  'INFRASTRUCTURE',
  'EVACUATION_ROUTES',
  'HISTORICAL_FLOODS',
  'THREAT_RADIUS',
];

const FIELD_PACK_SAFE_PROPERTY_KEYS = [
  'id',
  'name',
  'label',
  'zone',
  'severity',
  'risk_level',
  'riskLevel',
  'confidence',
  'confidence_pct',
  'flood_area_sqkm',
  'total_vector_area_sqkm',
  'polygon_area_sqkm',
  'year',
  'timestamp',
  'frame_id',
  'temporal_frame',
];

const FIELD_PACK_PROFILE_OPTIONS = {
  LOW: {
    maxFeatures: 6,
    maxVerticesPerSegment: 28,
    maxPolygonRings: 1,
    maxMultiGeometryParts: 2,
    maxTimelineSteps: 4,
    maxHotspots: 2,
    maxRuntimeLayers: 4,
    maxEnabledLayers: 4,
    coordinatePrecision: 4,
  },
  FULL: {
    maxFeatures: 14,
    maxVerticesPerSegment: 84,
    maxPolygonRings: 2,
    maxMultiGeometryParts: 5,
    maxTimelineSteps: 8,
    maxHotspots: 3,
    maxRuntimeLayers: 8,
    maxEnabledLayers: 8,
    coordinatePrecision: 5,
  },
};

const LOW_BANDWIDTH_RENDER_LIMITS = {
  maxFloodTileLayers: 1,
  maxHistoricalTileLayers: 2,
  maxTileZoom: 13,
};

function getComparisonModeFromTemporalFrame(frameId) {
  const normalizedFrame = normalizeTemporalFrameToken(frameId);
  if (normalizedFrame === 'baseline') return 'BEFORE';
  if (normalizedFrame === 'event') return 'AFTER';
  if (normalizedFrame === 'change') return 'CHANGE';
  return null;
}

function roundCoordinate(value, precision = 5) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  const factor = 10 ** precision;
  return Math.round(numeric * factor) / factor;
}

function sanitizeScalarForFieldPack(value) {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed ? trimmed.slice(0, 120) : null;
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'boolean') {
    return value;
  }
  return null;
}

function sanitizeFeaturePropertiesForFieldPack(properties) {
  if (!properties || typeof properties !== 'object') return {};

  const sanitized = {};
  for (const key of FIELD_PACK_SAFE_PROPERTY_KEYS) {
    const value = sanitizeScalarForFieldPack(properties[key]);
    if (value !== null) {
      sanitized[key] = value;
    }
  }

  if (Object.keys(sanitized).length > 0) {
    return sanitized;
  }

  const fallbackEntries = Object.entries(properties)
    .filter(([, value]) => {
      const sanitizedValue = sanitizeScalarForFieldPack(value);
      return sanitizedValue !== null;
    })
    .slice(0, 5);

  for (const [key, value] of fallbackEntries) {
    const sanitizedValue = sanitizeScalarForFieldPack(value);
    if (sanitizedValue !== null) {
      sanitized[key] = sanitizedValue;
    }
  }

  return sanitized;
}

function simplifyCoordinateSeriesForFieldPack(series, maxVerticesPerSegment, precision) {
  if (!Array.isArray(series)) return [];

  const cleaned = series
    .map((point) => {
      if (!Array.isArray(point) || point.length < 2) return null;
      const lng = roundCoordinate(point[0], precision);
      const lat = roundCoordinate(point[1], precision);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
      return [lng, lat];
    })
    .filter(Boolean);

  if (cleaned.length <= maxVerticesPerSegment) {
    return cleaned;
  }

  const stride = Math.max(1, Math.ceil((cleaned.length - 1) / Math.max(maxVerticesPerSegment - 1, 1)));
  const simplified = [];
  for (let index = 0; index < cleaned.length; index += stride) {
    simplified.push(cleaned[index]);
  }

  const lastPoint = cleaned[cleaned.length - 1];
  const lastSampledPoint = simplified[simplified.length - 1];
  if (
    !lastSampledPoint ||
    lastSampledPoint[0] !== lastPoint[0] ||
    lastSampledPoint[1] !== lastPoint[1]
  ) {
    simplified.push(lastPoint);
  }

  return simplified.slice(0, maxVerticesPerSegment);
}

function ensureClosedRingForFieldPack(ring) {
  if (!Array.isArray(ring) || ring.length === 0) return [];
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (!Array.isArray(first) || !Array.isArray(last)) return ring;
  if (first[0] === last[0] && first[1] === last[1]) return ring;
  return [...ring, [...first]];
}

function simplifyGeometryForFieldPack(geometry, options) {
  if (!geometry || typeof geometry !== 'object') return null;

  const {
    maxVerticesPerSegment,
    maxPolygonRings,
    maxMultiGeometryParts,
    coordinatePrecision,
  } = options;

  switch (geometry.type) {
    case 'Point': {
      if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length < 2) return null;
      const lng = roundCoordinate(geometry.coordinates[0], coordinatePrecision);
      const lat = roundCoordinate(geometry.coordinates[1], coordinatePrecision);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
      return { type: 'Point', coordinates: [lng, lat] };
    }
    case 'MultiPoint': {
      const points = Array.isArray(geometry.coordinates)
        ? geometry.coordinates
            .slice(0, maxMultiGeometryParts * maxVerticesPerSegment)
            .map((point) => simplifyCoordinateSeriesForFieldPack([point], 1, coordinatePrecision)[0])
            .filter(Boolean)
        : [];
      if (points.length === 0) return null;
      return { type: 'MultiPoint', coordinates: points };
    }
    case 'LineString': {
      const simplified = simplifyCoordinateSeriesForFieldPack(
        geometry.coordinates,
        maxVerticesPerSegment,
        coordinatePrecision,
      );
      if (simplified.length < 2) return null;
      return { type: 'LineString', coordinates: simplified };
    }
    case 'MultiLineString': {
      const lineStrings = Array.isArray(geometry.coordinates)
        ? geometry.coordinates.slice(0, maxMultiGeometryParts)
        : [];
      const simplified = lineStrings
        .map((line) => simplifyCoordinateSeriesForFieldPack(line, maxVerticesPerSegment, coordinatePrecision))
        .filter((line) => line.length >= 2);
      if (simplified.length === 0) return null;
      return { type: 'MultiLineString', coordinates: simplified };
    }
    case 'Polygon': {
      const rings = Array.isArray(geometry.coordinates)
        ? geometry.coordinates.slice(0, maxPolygonRings)
        : [];
      const simplifiedRings = rings
        .map((ring) => simplifyCoordinateSeriesForFieldPack(ring, maxVerticesPerSegment, coordinatePrecision))
        .filter((ring) => ring.length >= 3)
        .map((ring) => ensureClosedRingForFieldPack(ring));
      if (simplifiedRings.length === 0) return null;
      return { type: 'Polygon', coordinates: simplifiedRings };
    }
    case 'MultiPolygon': {
      const polygons = Array.isArray(geometry.coordinates)
        ? geometry.coordinates.slice(0, maxMultiGeometryParts)
        : [];
      const simplifiedPolygons = polygons
        .map((polygon) => {
          if (!Array.isArray(polygon)) return null;
          const rings = polygon.slice(0, maxPolygonRings)
            .map((ring) => simplifyCoordinateSeriesForFieldPack(ring, maxVerticesPerSegment, coordinatePrecision))
            .filter((ring) => ring.length >= 3)
            .map((ring) => ensureClosedRingForFieldPack(ring));
          return rings.length > 0 ? rings : null;
        })
        .filter(Boolean);
      if (simplifiedPolygons.length === 0) return null;
      return { type: 'MultiPolygon', coordinates: simplifiedPolygons };
    }
    default:
      return null;
  }
}

function countGeometryVertices(geometry) {
  const points = [];
  collectGeometryCoordinates(geometry, points);
  return points.length;
}

function buildSimplifiedGeoJsonForFieldPack(geojson, options) {
  const normalized = normalizeRuntimeGeoJson(geojson);
  if (!normalized) {
    return {
      geojson: null,
      featureCount: 0,
      vertexCount: 0,
      truncated: false,
    };
  }

  const features = Array.isArray(normalized.features) ? normalized.features : [];
  const limitedFeatures = features.slice(0, options.maxFeatures);
  const simplifiedFeatures = [];
  let vertexCount = 0;

  for (const feature of limitedFeatures) {
    if (!feature || typeof feature !== 'object') continue;
    const simplifiedGeometry = simplifyGeometryForFieldPack(feature.geometry, options);
    if (!simplifiedGeometry) continue;
    vertexCount += countGeometryVertices(simplifiedGeometry);
    const simplifiedFeature = {
      type: 'Feature',
      properties: sanitizeFeaturePropertiesForFieldPack(feature.properties),
      geometry: simplifiedGeometry,
    };
    if (typeof feature.id === 'string' || typeof feature.id === 'number') {
      simplifiedFeature.id = feature.id;
    }
    simplifiedFeatures.push(simplifiedFeature);
  }

  return {
    geojson: {
      type: 'FeatureCollection',
      features: simplifiedFeatures,
    },
    featureCount: simplifiedFeatures.length,
    vertexCount,
    truncated: features.length > simplifiedFeatures.length,
  };
}

function resolveTileSourceMaxZoom(tileSource, lowBandwidthMode) {
  const resolvedMaxZoom = Number.isFinite(tileSource?.max_zoom) ? tileSource.max_zoom : 18;
  if (!lowBandwidthMode) return resolvedMaxZoom;
  return Math.min(resolvedMaxZoom, LOW_BANDWIDTH_RENDER_LIMITS.maxTileZoom);
}

function buildLowBandwidthFloodGeoJson(geojsonCandidate) {
  const simplified = buildSimplifiedGeoJsonForFieldPack(
    geojsonCandidate,
    FIELD_PACK_PROFILE_OPTIONS.LOW,
  );
  if (simplified.featureCount === 0 || !simplified.geojson) return null;
  return simplified.geojson;
}

function formatFieldPackSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatFieldPackTimestamp(timestampMs) {
  if (!Number.isFinite(timestampMs)) return 'n/a';
  return new Date(timestampMs).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// ── Triage zone colors ──────────────────────────────────────────
const TRIAGE_COLORS = {
  RED: { fill: '#ff4444', opacity: 0.25 },
  YELLOW: { fill: '#ffaa00', opacity: 0.2 },
  GREEN: { fill: '#00ff88', opacity: 0.15 },
};

/**
 * DataLayerPanel — toggleable data layers floating over the globe.
 * Each layer independently manages its own Cesium entities/datasources.
 */
export default function DataLayerPanel({
  layers,
  onToggle,
  getViewer,
  earthEngineRuntime,
  temporalControl,
  incidentReplay,
  onReplayToggle,
  onReplaySeek,
  onReplaySpeedChange,
  onReplayJumpToHotspot,
  lowBandwidth = false,
  fieldMode = false,
  bandwidthProfile = 'FULL',
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [fieldPackSummary, setFieldPackSummary] = useState(null);
  const [fieldPackError, setFieldPackError] = useState('');
  const layerSourcesRef = useRef(new Map()); // layerId → Cesium DataSource / Entity[]
  const infraEntitiesRef = useRef([]);
  const popEntitiesRef = useRef([]);
  const histSourcesRef = useRef([]);
  const routeEntitiesRef = useRef([]);
  const threatEntitiesRef = useRef([]);
  const floodRuntimeSignatureRef = useRef('');
  const historicalRuntimeSignatureRef = useRef('');
  const runtimeFloodGeoJsonCacheRef = useRef({ url: null, geojson: null });
  const floodReplayEntitiesRef = useRef([]);
  const floodReplayTimersRef = useRef([]);
  const floodSignalEntitiesRef = useRef([]);

  const lowBandwidthActive = useMemo(() => {
    const normalizedProfile = typeof bandwidthProfile === 'string'
      ? bandwidthProfile.trim().toUpperCase()
      : '';
    return Boolean(lowBandwidth) || normalizedProfile === 'LOW';
  }, [bandwidthProfile, lowBandwidth]);

  const fieldPackProfile = lowBandwidthActive ? 'LOW' : 'FULL';
  const fieldPackProfileOptions = FIELD_PACK_PROFILE_OPTIONS[fieldPackProfile];

  const runtimeLayers = useMemo(() => {
    if (Array.isArray(earthEngineRuntime?.runtimeLayers)) {
      return earthEngineRuntime.runtimeLayers;
    }
    if (Array.isArray(earthEngineRuntime?.eeRuntime?.layers)) {
      return earthEngineRuntime.eeRuntime.layers;
    }
    return [];
  }, [earthEngineRuntime]);

  const runtimeTemporalFrames = useMemo(() => {
    if (earthEngineRuntime?.temporalFrames && typeof earthEngineRuntime.temporalFrames === 'object') {
      return earthEngineRuntime.temporalFrames;
    }
    if (earthEngineRuntime?.eeRuntime?.temporal_frames && typeof earthEngineRuntime.eeRuntime.temporal_frames === 'object') {
      return earthEngineRuntime.eeRuntime.temporal_frames;
    }
    return {};
  }, [earthEngineRuntime]);

  const runtimeTemporalPlayback = useMemo(() => {
    if (earthEngineRuntime?.temporalPlayback && typeof earthEngineRuntime.temporalPlayback === 'object') {
      return earthEngineRuntime.temporalPlayback;
    }
    if (earthEngineRuntime?.eeRuntime?.temporal_playback && typeof earthEngineRuntime.eeRuntime.temporal_playback === 'object') {
      return earthEngineRuntime.eeRuntime.temporal_playback;
    }
    return {};
  }, [earthEngineRuntime]);

  const activeRuntimeFrame = useMemo(() => {
    const orderedFrameIds = Array.isArray(runtimeTemporalPlayback.ordered_frame_ids)
      ? runtimeTemporalPlayback.ordered_frame_ids
          .map((frameId) => normalizeTemporalControlFrame(frameId))
          .filter(Boolean)
      : [];
    const knownFrameIds = Object.keys(runtimeTemporalFrames)
      .map((frameId) => normalizeTemporalControlFrame(frameId))
      .filter(Boolean)
      .sort((frameA, frameB) => {
        const orderA = FRAME_ORDER.get(frameA) ?? 99;
        const orderB = FRAME_ORDER.get(frameB) ?? 99;
        if (orderA !== orderB) return orderA - orderB;
        return String(frameA).localeCompare(String(frameB));
      });
    const candidateFrameIds = [...new Set(orderedFrameIds.length > 0 ? orderedFrameIds : knownFrameIds)];
    const normalizedActiveFrame = normalizeTemporalControlFrame(temporalControl?.activeFrameId);

    if (normalizedActiveFrame && candidateFrameIds.includes(normalizedActiveFrame)) {
      return normalizedActiveFrame;
    }

    const activeFrameIndex = Number.isFinite(temporalControl?.activeFrameIndex)
      ? Math.max(0, Math.trunc(temporalControl.activeFrameIndex))
      : -1;
    if (activeFrameIndex >= 0 && activeFrameIndex < candidateFrameIds.length) {
      return candidateFrameIds[activeFrameIndex];
    }

    if (normalizedActiveFrame && FRAME_ORDER.has(normalizedActiveFrame)) {
      return normalizedActiveFrame;
    }

    const comparisonFrame = getTemporalFrameForComparison(temporalControl?.activeComparison);
    if (comparisonFrame) {
      return comparisonFrame;
    }

    if (normalizedActiveFrame) {
      return normalizedActiveFrame;
    }

    const defaultFrameId = normalizeTemporalControlFrame(runtimeTemporalPlayback.default_frame_id);
    if (defaultFrameId) {
      return defaultFrameId;
    }

    return candidateFrameIds[0] || null;
  }, [temporalControl, runtimeTemporalFrames, runtimeTemporalPlayback]);

  const runtimeTileLayers = useMemo(
    () => runtimeLayers
      .map((layer) => {
        if (!layer || typeof layer !== 'object') return null;
        const tileSource = layer.tile_source;
        const urlTemplate = resolveTileUrlTemplate(tileSource?.url_template);
        const isTileReady = Boolean(
          urlTemplate &&
          tileSource?.available &&
          (layer.type === 'raster' || !layer.type)
        );
        if (!isTileReady) return null;
        return { layer, tileSource, urlTemplate };
      })
      .filter(Boolean),
    [runtimeLayers],
  );

  const runtimeFloodTileLayers = useMemo(() => {
    if (activeRuntimeFrame) {
      const activeFrameLayers = runtimeTileLayers.filter(
        ({ layer }) => layerMatchesTemporalFrame(layer, activeRuntimeFrame)
      );
      if (activeFrameLayers.length > 0) {
        return activeFrameLayers;
      }
    }

    const preferredLayers = runtimeTileLayers.filter(({ layer }) => {
      const temporalFrame = getRuntimeLayerTemporalFrame(layer);
      const layerId = String(layer?.id || '').toLowerCase();
      return (
        layerMatchesTemporalFrame(layer, 'change') ||
        temporalFrame === 'change' ||
        layer?.fusion?.is_fused === true ||
        layerId.includes('change') ||
        layerId.includes('fusion') ||
        layerId.includes('fused')
      );
    });
    return preferredLayers.length > 0 ? preferredLayers : runtimeTileLayers;
  }, [runtimeTileLayers, activeRuntimeFrame]);

  const runtimeFloodTileLayersForRender = useMemo(() => {
    if (!lowBandwidthActive) return runtimeFloodTileLayers;
    return runtimeFloodTileLayers.slice(0, LOW_BANDWIDTH_RENDER_LIMITS.maxFloodTileLayers);
  }, [lowBandwidthActive, runtimeFloodTileLayers]);

  const runtimeHistoricalTileLayers = useMemo(() => {
    const temporalLayers = runtimeTileLayers.filter(({ layer }) => {
      const temporalFrame = getRuntimeLayerTemporalFrame(layer);
      if (temporalFrame) {
        return true;
      }
      const layerId = String(layer?.id || '').toLowerCase();
      return (
        layerId.includes('baseline') ||
        layerId.includes('before') ||
        layerId.includes('event') ||
        layerId.includes('after') ||
        layerId.includes('change') ||
        layerId.includes('progression')
      );
    });
    const sortedTemporalLayers = sortLayersByTemporalFrame(temporalLayers);
    if (activeRuntimeFrame) {
      const activeFrameLayers = sortedTemporalLayers.filter(
        ({ layer }) => layerMatchesTemporalFrame(layer, activeRuntimeFrame)
      );
      if (activeFrameLayers.length > 0) {
        return activeFrameLayers;
      }
    }
    return sortedTemporalLayers;
  }, [runtimeTileLayers, activeRuntimeFrame]);

  const runtimeHistoricalTileLayersForRender = useMemo(() => {
    if (!lowBandwidthActive) return runtimeHistoricalTileLayers;
    return runtimeHistoricalTileLayers.slice(0, LOW_BANDWIDTH_RENDER_LIMITS.maxHistoricalTileLayers);
  }, [lowBandwidthActive, runtimeHistoricalTileLayers]);

  const prioritizedRuntimeLayers = useMemo(() => {
    if (!activeRuntimeFrame) return runtimeLayers;
    const matchingLayers = runtimeLayers.filter(
      (layer) => layerMatchesTemporalFrame(layer, activeRuntimeFrame)
    );
    if (matchingLayers.length === 0) {
      return runtimeLayers;
    }
    const nonMatchingLayers = runtimeLayers.filter(
      (layer) => !layerMatchesTemporalFrame(layer, activeRuntimeFrame)
    );
    return [...matchingLayers, ...nonMatchingLayers];
  }, [runtimeLayers, activeRuntimeFrame]);

  const selectedRuntimeTemporalFrame = useMemo(() => {
    if (!activeRuntimeFrame) return null;
    const directFrame = runtimeTemporalFrames?.[activeRuntimeFrame];
    if (directFrame && typeof directFrame === 'object') return directFrame;

    const matchingEntry = Object.entries(runtimeTemporalFrames || {}).find(([frameId]) => (
      normalizeTemporalControlFrame(frameId) === activeRuntimeFrame
    ));
    if (!matchingEntry) return null;
    const [, frame] = matchingEntry;
    return frame && typeof frame === 'object' ? frame : null;
  }, [activeRuntimeFrame, runtimeTemporalFrames]);

  const selectedRuntimePlaybackFrame = useMemo(() => {
    if (!activeRuntimeFrame) return null;
    const playbackFrames = Array.isArray(runtimeTemporalPlayback.frames)
      ? runtimeTemporalPlayback.frames
      : [];
    const matchedFrame = playbackFrames.find((frame) => (
      normalizeTemporalControlFrame(frame?.frame_id || frame?.id) === activeRuntimeFrame
    ));
    return matchedFrame && typeof matchedFrame === 'object' ? matchedFrame : null;
  }, [activeRuntimeFrame, runtimeTemporalPlayback]);

  const runtimeFloodGeoJson = useMemo(() => {
    const selectedFrameGeoJsonCandidates = [
      ...getTemporalFrameGeoJsonCandidates(selectedRuntimeTemporalFrame),
      ...getTemporalFrameGeoJsonCandidates(selectedRuntimePlaybackFrame),
    ];
    const layerGeoJsonCandidates = prioritizedRuntimeLayers.flatMap((layer) => {
      if (!layer || typeof layer !== 'object') return [];
      return [
        layer.geojson,
        layer.flood_geojson,
        layer.flood_extent_geojson,
        layer.vector_geojson,
        layer.overlay_geojson,
        layer?.tile_source?.fallback_geojson,
      ];
    });

    const candidates = [
      ...selectedFrameGeoJsonCandidates,
      earthEngineRuntime?.floodGeojson,
      earthEngineRuntime?.flood_geojson,
      earthEngineRuntime?.flood_extent_geojson,
      earthEngineRuntime?.floodExtentGeojson,
      earthEngineRuntime?.eeRuntime?.geojson,
      earthEngineRuntime?.eeRuntime?.flood_geojson,
      earthEngineRuntime?.eeRuntime?.flood_extent_geojson,
      earthEngineRuntime?.eeRuntime?.floodExtentGeojson,
      ...layerGeoJsonCandidates,
    ];

    for (const candidate of candidates) {
      const normalized = normalizeRuntimeGeoJson(candidate);
      if (normalized) return normalized;
    }
    return null;
  }, [
    earthEngineRuntime,
    prioritizedRuntimeLayers,
    selectedRuntimePlaybackFrame,
    selectedRuntimeTemporalFrame,
  ]);

  const runtimeFloodGeoJsonUrl = useMemo(() => {
    const selectedFrameUrlCandidates = [
      ...getTemporalFrameGeoJsonUrlCandidates(selectedRuntimeTemporalFrame),
      ...getTemporalFrameGeoJsonUrlCandidates(selectedRuntimePlaybackFrame),
    ];
    const layerUrlCandidates = prioritizedRuntimeLayers.flatMap((layer) => {
      if (!layer || typeof layer !== 'object') return [];
      return [
        layer.geojson_url,
        layer.flood_geojson_url,
        layer.flood_extent_geojson_url,
        layer.overlay_url,
        layer.url,
        layer?.tile_source?.fallback_geojson_url,
        layer?.tile_source?.fallback_url,
      ];
    });

    return firstNonEmptyString([
      ...selectedFrameUrlCandidates,
      earthEngineRuntime?.floodGeojsonUrl,
      earthEngineRuntime?.flood_geojson_url,
      earthEngineRuntime?.flood_extent_geojson_url,
      earthEngineRuntime?.floodExtentGeojsonUrl,
      earthEngineRuntime?.eeRuntime?.geojson_url,
      earthEngineRuntime?.eeRuntime?.flood_geojson_url,
      earthEngineRuntime?.eeRuntime?.flood_extent_geojson_url,
      ...layerUrlCandidates,
    ]);
  }, [
    earthEngineRuntime,
    prioritizedRuntimeLayers,
    selectedRuntimePlaybackFrame,
    selectedRuntimeTemporalFrame,
  ]);

  const runtimeSignalProfile = useMemo(() => {
    const runtimeRecord =
      earthEngineRuntime?.eeRuntime && typeof earthEngineRuntime.eeRuntime === 'object'
        ? earthEngineRuntime.eeRuntime
        : {};
    const runtimeStatusToken = normalizeSignalToken(runtimeRecord?.status);
    const runtimeModeToken = normalizeSignalToken(
      runtimeRecord?.runtime_mode || runtimeRecord?.runtimeMode
    );
    const runtimeError = runtimeStatusToken === 'ERROR' || runtimeModeToken === 'ERROR';
    const usesFallbackDescriptor =
      runtimeStatusToken === 'FALLBACK' ||
      (runtimeModeToken ? runtimeModeToken.includes('FALLBACK') : false);

    const selectedFrameLayers = prioritizedRuntimeLayers.filter((layer) => (
      activeRuntimeFrame ? layerMatchesTemporalFrame(layer, activeRuntimeFrame) : true
    ));
    const signalLayer = selectedFrameLayers[0] || prioritizedRuntimeLayers[0] || null;
    const fusionAggregateConfidence =
      earthEngineRuntime?.multisensorFusion?.aggregate_confidence ||
      runtimeRecord?.multisensor_fusion?.aggregate_confidence ||
      null;
    const confidenceCandidates = [
      selectedRuntimeTemporalFrame?.confidence,
      selectedRuntimePlaybackFrame?.confidence,
      ...selectedFrameLayers.map((layer) => layer?.confidence),
      signalLayer?.confidence,
      runtimeRecord?.current_frame?.confidence,
      runtimeRecord?.confidence,
      fusionAggregateConfidence,
    ];

    let confidencePct = null;
    let confidenceLevel = null;
    for (const candidate of confidenceCandidates) {
      if (confidencePct === null) {
        confidencePct = toPercent(candidate);
      }
      if (!confidenceLevel) {
        confidenceLevel = resolveConfidenceLabel(candidate);
      }
      if (confidencePct !== null && confidenceLevel) break;
    }
    confidenceLevel = confidenceLevel || confidenceLevelFromPercent(confidencePct);

    let uncertaintyPct = confidencePct !== null ? Math.max(0, 100 - confidencePct) : null;
    let uncertaintyLevel =
      uncertaintyLevelFromPercent(uncertaintyPct) || uncertaintyLevelFromConfidence(confidenceLevel);

    if (runtimeError) {
      uncertaintyLevel = 'HIGH';
      if (uncertaintyPct !== null) {
        uncertaintyPct = Math.max(uncertaintyPct, 60);
      }
    } else if (usesFallbackDescriptor && uncertaintyLevel === 'LOW') {
      uncertaintyLevel = 'MEDIUM';
    }

    const freshnessCandidates = [
      selectedRuntimeTemporalFrame?.timestamp,
      selectedRuntimeTemporalFrame?.end_timestamp,
      selectedRuntimeTemporalFrame?.start_timestamp,
      selectedRuntimePlaybackFrame?.timestamp,
      selectedRuntimePlaybackFrame?.end_timestamp,
      selectedRuntimePlaybackFrame?.start_timestamp,
      signalLayer?.timestamps?.frame_timestamp,
      signalLayer?.timestamps?.updated_at,
      earthEngineRuntime?.temporalSummary?.latest_frame_timestamp,
      earthEngineRuntime?.temporalSummary?.end_timestamp,
      runtimeRecord?.current_frame?.timestamp,
      runtimeRecord?.provenance?.updated_at,
      runtimeRecord?.provenance?.updatedAt,
    ];
    let freshnessTimestamp = null;
    for (const candidate of freshnessCandidates) {
      if (toTimestampMs(candidate) !== null) {
        freshnessTimestamp = candidate;
        break;
      }
    }
    const freshnessTimestampMs = toTimestampMs(freshnessTimestamp);
    const freshnessAgeMs =
      freshnessTimestampMs !== null ? Math.max(0, Date.now() - freshnessTimestampMs) : null;
    let freshnessLevel = freshnessLevelFromAge(freshnessAgeMs);
    if (runtimeError && freshnessLevel === null) {
      freshnessLevel = 'STALE';
    }

    const confidenceFactor = confidencePct !== null
      ? Math.max(0, Math.min(1, confidencePct / 100))
      : confidenceLevel === 'HIGH'
        ? 0.88
        : confidenceLevel === 'MEDIUM'
          ? 0.64
          : confidenceLevel === 'LOW'
            ? 0.42
            : 0.6;
    const uncertaintyFactor = uncertaintyPct !== null
      ? Math.max(0, Math.min(1, uncertaintyPct / 100))
      : uncertaintyLevel === 'HIGH'
        ? 0.78
        : uncertaintyLevel === 'MEDIUM'
          ? 0.52
          : uncertaintyLevel === 'LOW'
            ? 0.25
            : 0.45;
    const freshnessFactor =
      freshnessLevel === 'FRESH' ? 1 :
        freshnessLevel === 'RECENT' ? 0.88 :
          freshnessLevel === 'STALE' ? 0.7 : 0.82;

    return {
      confidencePct,
      confidenceLevel,
      uncertaintyPct,
      uncertaintyLevel,
      freshnessLevel,
      freshnessTimestamp,
      freshnessAgeMs,
      confidenceFactor,
      uncertaintyFactor,
      freshnessFactor,
    };
  }, [
    earthEngineRuntime,
    prioritizedRuntimeLayers,
    activeRuntimeFrame,
    selectedRuntimeTemporalFrame,
    selectedRuntimePlaybackFrame,
  ]);

  const runtimeHistoricalFrameCount = useMemo(() => {
    const temporalFrameKeys = Object.keys(runtimeTemporalFrames);
    if (temporalFrameKeys.length > 0) return temporalFrameKeys.length;

    const temporalFrames = new Set();
    runtimeLayers.forEach((layer) => {
      const temporalFrame = getRuntimeLayerTemporalFrame(layer);
      if (temporalFrame) temporalFrames.add(temporalFrame);
    });
    return temporalFrames.size;
  }, [runtimeTemporalFrames, runtimeLayers]);

  const runtimeLayerSignature = useMemo(
    () => JSON.stringify(
      runtimeLayers.map((layer) => ({
        id: layer?.id,
        temporal_frame: layer?.temporal_frame,
        type: layer?.type,
        url_template: layer?.tile_source?.url_template,
        available: layer?.tile_source?.available,
        geojson_url: layer?.geojson_url || layer?.flood_geojson_url,
        has_geojson: Boolean(layer?.geojson || layer?.flood_geojson || layer?.flood_extent_geojson),
      }))
    ),
    [runtimeLayers],
  );

  const runtimeFloodSignature = useMemo(
    () => JSON.stringify({
      activeRuntimeFrame,
      runtimeLayerSignature,
      render_profile: lowBandwidthActive ? 'LOW' : 'FULL',
      tileLayerIds: runtimeFloodTileLayersForRender.map((entry) => entry.layer?.id || null),
      geojsonSignature: buildFloodGeoJsonSignature(runtimeFloodGeoJson),
      geojsonUrl: runtimeFloodGeoJsonUrl,
      signalProfile: {
        confidencePct: runtimeSignalProfile.confidencePct,
        confidenceLevel: runtimeSignalProfile.confidenceLevel,
        uncertaintyPct: runtimeSignalProfile.uncertaintyPct,
        uncertaintyLevel: runtimeSignalProfile.uncertaintyLevel,
        freshnessLevel: runtimeSignalProfile.freshnessLevel,
        freshnessTimestamp: runtimeSignalProfile.freshnessTimestamp,
      },
    }),
    [
      activeRuntimeFrame,
      runtimeLayerSignature,
      lowBandwidthActive,
      runtimeFloodTileLayersForRender,
      runtimeFloodGeoJson,
      runtimeFloodGeoJsonUrl,
      runtimeSignalProfile,
    ],
  );

  const runtimeHistoricalSignature = useMemo(
    () => JSON.stringify({
      activeRuntimeFrame,
      runtimeLayerSignature,
      render_profile: lowBandwidthActive ? 'LOW' : 'FULL',
      tileLayerIds: runtimeHistoricalTileLayersForRender.map((entry) => entry.layer?.id || null),
      temporalFrameKeys: Object.keys(runtimeTemporalFrames).sort(),
    }),
    [
      activeRuntimeFrame,
      runtimeLayerSignature,
      lowBandwidthActive,
      runtimeHistoricalTileLayersForRender,
      runtimeTemporalFrames,
    ],
  );

  const layerDefinitions = useMemo(() => {
    const tileReadyCount = runtimeTileLayers.length;
    const hasRuntimeFloodVector = Boolean(runtimeFloodGeoJson || runtimeFloodGeoJsonUrl);
    const hasRuntimeFloodDescriptors = runtimeLayers.length > 0 || hasRuntimeFloodVector;
    const floodBadge = runtimeLayers.length > 0
      ? `${runtimeLayers.length} EE runtime layer${runtimeLayers.length === 1 ? '' : 's'} · ${tileReadyCount} tile-ready`
      : hasRuntimeFloodVector
        ? 'EE runtime flood vector'
        : FLOOD_BADGE;
    const floodSource = hasRuntimeFloodDescriptors
      ? (runtimeFloodTileLayers.length > 0 || hasRuntimeFloodVector
        ? 'EE Runtime'
        : 'EE descriptors · static fallback')
      : null;

    const historicalBadge = runtimeHistoricalFrameCount > 0
      ? `${runtimeHistoricalFrameCount} runtime frame${runtimeHistoricalFrameCount === 1 ? '' : 's'} · ${runtimeHistoricalTileLayers.length} tile-ready`
      : '2020, 2021, 2023';
    const historicalSource = runtimeHistoricalFrameCount > 0
      ? (runtimeHistoricalTileLayers.length > 0
        ? 'EE Runtime'
        : 'EE descriptors · synthetic fallback')
      : null;

    return LAYERS.map((layer) => {
      if (layer.id === 'FLOOD_EXTENT') {
        return {
          ...layer,
          badge: floodBadge,
          source: floodSource,
        };
      }
      if (layer.id === 'HISTORICAL_FLOODS') {
        return {
          ...layer,
          badge: historicalBadge,
          source: historicalSource,
        };
      }
      return layer;
    });
  }, [
    runtimeLayers,
    runtimeTileLayers,
    runtimeFloodTileLayers,
    runtimeFloodGeoJson,
    runtimeFloodGeoJsonUrl,
    runtimeHistoricalFrameCount,
    runtimeHistoricalTileLayers,
  ]);

  const replayTrack = Array.isArray(incidentReplay?.track)
    ? incidentReplay.track
    : [];
  const replayHotspots = Array.isArray(incidentReplay?.hotspots)
    ? incidentReplay.hotspots
    : [];
  const replayAvailable = Boolean(incidentReplay?.available) && replayTrack.length > 1;
  const replayActiveIndex = replayAvailable && Number.isFinite(incidentReplay?.activeIndex)
    ? Math.min(Math.max(Math.trunc(incidentReplay.activeIndex), 0), replayTrack.length - 1)
    : 0;
  const replayIsPlaying = Boolean(incidentReplay?.isPlaying) && replayAvailable;
  const replaySpeed = Number.isFinite(incidentReplay?.speed) ? incidentReplay.speed : 1;

  const handleGenerateFieldPack = useCallback(() => {
    try {
      setFieldPackError('');
      const generatedAtMs = Date.now();
      const generatedAtIso = new Date(generatedAtMs).toISOString();
      const packageId = `hawkeye-field-pack-${generatedAtIso.replace(/[:.]/g, '-')}`;
      const normalizedBandwidthProfile = typeof bandwidthProfile === 'string' && bandwidthProfile.trim().length > 0
        ? bandwidthProfile.trim().toUpperCase()
        : fieldPackProfile;

      const vectorSnapshot = buildSimplifiedGeoJsonForFieldPack(
        runtimeFloodGeoJson || runtimeFloodGeoJsonCacheRef.current.geojson || FLOOD_EXTENT_GEOJSON,
        fieldPackProfileOptions,
      );

      const layerById = new Map(layerDefinitions.map((layer) => [layer.id, layer]));
      const hasLiveLayerHandle = (layerId) => (
        layerSourcesRef.current.has(layerId) ||
        (layerId === 'INFRASTRUCTURE' && infraEntitiesRef.current.length > 0) ||
        (layerId === 'POPULATION_DENSITY' && popEntitiesRef.current.length > 0) ||
        (layerId === 'EVACUATION_ROUTES' && routeEntitiesRef.current.length > 0) ||
        (layerId === 'HISTORICAL_FLOODS' && histSourcesRef.current.length > 0) ||
        (layerId === 'THREAT_RADIUS' && threatEntitiesRef.current.length > 0)
      );

      const criticalLayers = FIELD_PACK_CRITICAL_LAYER_IDS.map((layerId) => {
        const descriptor = layerById.get(layerId);
        const enabled = Boolean(layers[layerId]);
        let cached = false;
        let cacheSource = 'on_demand';

        if (layerId === 'FLOOD_EXTENT') {
          if (runtimeFloodTileLayers.length > 0) {
            cached = true;
            cacheSource = 'runtime_tiles';
          } else if (vectorSnapshot.featureCount > 0) {
            cached = true;
            cacheSource = runtimeFloodGeoJson ? 'runtime_vector' : 'static_vector';
          }
        } else if (layerId === 'TRIAGE_ZONES') {
          cached = Boolean(TRIAGE_ZONES_GEOJSON);
          cacheSource = 'static_geojson';
        } else if (layerId === 'INFRASTRUCTURE') {
          cached = INFRASTRUCTURE_DATA.length > 0;
          cacheSource = 'static_catalog';
        } else if (layerId === 'HISTORICAL_FLOODS') {
          cached = runtimeHistoricalTileLayers.length > 0 || HISTORICAL_FLOODS.length > 0;
          cacheSource = runtimeHistoricalTileLayers.length > 0 ? 'runtime_tiles' : 'synthetic_history';
        } else if (layerId === 'THREAT_RADIUS') {
          cached = true;
          cacheSource = 'procedural_template';
        } else if (layerId === 'EVACUATION_ROUTES') {
          cached = hasLiveLayerHandle(layerId);
          cacheSource = cached ? 'live_route_overlay' : 'on_demand';
        }

        return {
          layer_id: layerId,
          name: descriptor?.name || layerId,
          enabled,
          cached,
          cache_source: cacheSource,
          source: descriptor?.source || null,
        };
      });

      const enabledLayerSnapshots = layerDefinitions
        .filter((layer) => Boolean(layers[layer.id]))
        .slice(0, fieldPackProfileOptions.maxEnabledLayers)
        .map((layer) => ({
          layer_id: layer.id,
          name: layer.name,
          source: layer.source || null,
          badge: typeof layer.badge === 'string' ? layer.badge : null,
          has_live_handle: hasLiveLayerHandle(layer.id),
        }));

      const runtimeLayerSnapshots = runtimeLayers
        .slice(0, fieldPackProfileOptions.maxRuntimeLayers)
        .map((layer, index) => ({
          layer_id:
            firstNonEmptyString([
              layer?.id,
              layer?.layer_id,
              layer?.name,
              layer?.label,
            ]) || `runtime-layer-${index + 1}`,
          type: layer?.type || 'raster',
          temporal_frame: getRuntimeLayerTemporalFrame(layer),
          tile_available: Boolean(layer?.tile_source?.available),
          has_vector_fallback: Boolean(
            normalizeRuntimeGeoJson(
              layer?.geojson ||
              layer?.flood_geojson ||
              layer?.flood_extent_geojson ||
              layer?.vector_geojson
            )
          ),
          timestamp: firstNonEmptyString([
            layer?.timestamps?.frame_timestamp,
            layer?.timestamps?.updated_at,
          ]),
        }));

      const replaySnapshots = replayTrack.map((step, index) => {
        const frameId = normalizeTemporalControlFrame(
          step?.frameId || step?.frame_id || step?.id
        );
        const timestampMs = toTimestampMs(
          step?.timestamp ||
          step?.time ||
          step?.at
        );
        return {
          index,
          frame_id: frameId || null,
          comparison: getComparisonModeFromTemporalFrame(frameId),
          label: step?.label || `Frame ${index + 1}`,
          caption: step?.caption || step?.reason || null,
          hotspot: Boolean(step?.hotspot),
          timestamp: timestampMs !== null ? new Date(timestampMs).toISOString() : null,
        };
      });

      const orderedTemporalFrameIds = Array.isArray(runtimeTemporalPlayback.ordered_frame_ids)
        ? runtimeTemporalPlayback.ordered_frame_ids
            .map((frameId) => normalizeTemporalControlFrame(frameId))
            .filter(Boolean)
        : [];
      const runtimeTemporalFrameEntries = Object.entries(runtimeTemporalFrames)
        .map(([frameId, frame]) => ({
          frameId: normalizeTemporalControlFrame(frameId) || frameId,
          frame,
        }))
        .sort((a, b) => {
          const orderA = FRAME_ORDER.get(a.frameId) ?? 99;
          const orderB = FRAME_ORDER.get(b.frameId) ?? 99;
          if (orderA !== orderB) return orderA - orderB;
          return String(a.frameId).localeCompare(String(b.frameId));
        });
      const runtimeFrameLookup = new Map(runtimeTemporalFrameEntries.map((entry) => [entry.frameId, entry.frame]));
      const timelineFallback = (
        orderedTemporalFrameIds.length > 0
          ? orderedTemporalFrameIds.map((frameId) => ({ frameId, frame: runtimeFrameLookup.get(frameId) || null }))
          : runtimeTemporalFrameEntries
      ).map((entry, index) => {
        const frame = entry.frame && typeof entry.frame === 'object' ? entry.frame : {};
        const timestampMs = toTimestampMs(
          frame.timestamp ||
          frame.end_timestamp ||
          frame.start_timestamp
        );
        return {
          index,
          frame_id: entry.frameId || null,
          comparison: getComparisonModeFromTemporalFrame(entry.frameId),
          label: firstNonEmptyString([
            frame.label,
            frame.name,
            frame.title,
          ]) || String(entry.frameId || `Frame ${index + 1}`).toUpperCase(),
          caption: firstNonEmptyString([
            frame.caption,
            frame.description,
            frame.summary,
          ]),
          hotspot: false,
          timestamp: timestampMs !== null ? new Date(timestampMs).toISOString() : null,
        };
      });
      const timelineSnapshots = (replaySnapshots.length > 0 ? replaySnapshots : timelineFallback)
        .slice(0, fieldPackProfileOptions.maxTimelineSteps);

      const hotspotSnapshots = replayHotspots
        .slice(0, fieldPackProfileOptions.maxHotspots)
        .map((hotspot, index) => ({
          id: hotspot?.id || hotspot?.frameId || `hotspot-${index + 1}`,
          index: Number.isFinite(hotspot?.index) ? hotspot.index : replayActiveIndex,
          label: hotspot?.label || `Hotspot ${index + 1}`,
          frame_id: normalizeTemporalControlFrame(hotspot?.frameId || hotspot?.frame_id || null),
        }));
      const cachedCriticalLayers = criticalLayers.filter((layer) => layer.cached);
      const timelineFrameIds = timelineSnapshots
        .map((snapshot) => snapshot?.frame_id)
        .filter((frameId) => typeof frameId === 'string' && frameId.length > 0);

      const packagePayload = {
        schema_version: 'hawkeye.field_pack.v1',
        package_id: packageId,
        generated_at: generatedAtIso,
        profile: {
          mode: fieldPackProfile,
          field_mode: Boolean(fieldMode),
          low_bandwidth: lowBandwidthActive,
          bandwidth_profile: normalizedBandwidthProfile,
        },
        summary: {
          active_frame_id: activeRuntimeFrame || normalizeTemporalControlFrame(temporalControl?.activeFrameId),
          cached_critical_layers: cachedCriticalLayers.length,
          cached_critical_layer_ids: cachedCriticalLayers.map((layer) => layer.layer_id),
          total_critical_layers: criticalLayers.length,
          enabled_layers: enabledLayerSnapshots.length,
          timeline_snapshots: timelineSnapshots.length,
          timeline_frame_ids: timelineFrameIds,
          hotspot_snapshots: hotspotSnapshots.length,
          vector_features: vectorSnapshot.featureCount,
          vector_vertices: vectorSnapshot.vertexCount,
          vector_truncated: vectorSnapshot.truncated,
          vector_profile: {
            simplified: true,
            truncated: vectorSnapshot.truncated,
            feature_count: vectorSnapshot.featureCount,
            vertex_count: vectorSnapshot.vertexCount,
          },
          runtime_layers: runtimeLayerSnapshots.length,
        },
        critical_layers: criticalLayers,
        enabled_layers: enabledLayerSnapshots,
        runtime_layers: runtimeLayerSnapshots,
        timeline: {
          active_index: replayActiveIndex,
          active_frame_id: activeRuntimeFrame || normalizeTemporalControlFrame(temporalControl?.activeFrameId),
          snapshots: timelineSnapshots,
          hotspots: hotspotSnapshots,
        },
        vectors: {
          flood_extent: vectorSnapshot.geojson,
          source:
            runtimeFloodTileLayers.length > 0
              ? 'runtime_tiles'
              : runtimeFloodGeoJson
                ? 'runtime_vector'
                : runtimeFloodGeoJsonCacheRef.current.geojson
                  ? 'cached_runtime_vector'
                  : 'static_fallback',
          simplified: true,
          truncated: vectorSnapshot.truncated,
        },
        signal_profile: {
          confidence_pct: runtimeSignalProfile.confidencePct,
          confidence_level: runtimeSignalProfile.confidenceLevel,
          uncertainty_pct: runtimeSignalProfile.uncertaintyPct,
          uncertainty_level: runtimeSignalProfile.uncertaintyLevel,
          freshness_level: runtimeSignalProfile.freshnessLevel,
          freshness_timestamp: runtimeSignalProfile.freshnessTimestamp,
        },
        notes: lowBandwidthActive
          ? [
              'Low-bandwidth profile active: critical-layer cache only.',
              'Vector payload simplified and timeline snapshots truncated for field transfer.',
            ]
          : [
              'Standard profile includes expanded timeline snapshots with simplified vectors.',
            ],
      };

      const serializedPayload = JSON.stringify(packagePayload, null, 2);
      const payloadBlob = new Blob([serializedPayload], { type: 'application/json' });
      const payloadBytes = payloadBlob.size;
      const filename = `${packageId}.json`;

      if (typeof window !== 'undefined' && typeof document !== 'undefined' && window.URL?.createObjectURL) {
        const objectUrl = window.URL.createObjectURL(payloadBlob);
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.rel = 'noopener';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 0);
      }

      setFieldPackSummary({
        packageId,
        generatedAtMs,
        profile: fieldPackProfile,
        cachedCriticalLayers: cachedCriticalLayers.length,
        totalCriticalLayers: criticalLayers.length,
        timelineSnapshots: timelineSnapshots.length,
        hotspotSnapshots: hotspotSnapshots.length,
        vectorFeatures: vectorSnapshot.featureCount,
        vectorVertices: vectorSnapshot.vertexCount,
        vectorTruncated: vectorSnapshot.truncated,
        payloadBytes,
        filename,
      });
    } catch (error) {
      console.error('[DataLayerPanel] Failed to generate field pack.', error);
      setFieldPackError('Field package export failed. Runtime fallback data remains available.');
    }
  }, [
    activeRuntimeFrame,
    bandwidthProfile,
    fieldMode,
    fieldPackProfile,
    fieldPackProfileOptions,
    layerDefinitions,
    layers,
    lowBandwidthActive,
    replayActiveIndex,
    replayHotspots,
    replayTrack,
    runtimeFloodGeoJson,
    runtimeFloodTileLayers,
    runtimeHistoricalTileLayers,
    runtimeLayers,
    runtimeSignalProfile,
    runtimeTemporalFrames,
    runtimeTemporalPlayback,
    temporalControl?.activeFrameId,
  ]);

  const clearFloodSignalEntities = useCallback((viewer) => {
    if (!viewer || viewer.isDestroyed()) return;
    floodSignalEntitiesRef.current.forEach((entity) => {
      try { viewer.entities.remove(entity); } catch { /* ok */ }
    });
    floodSignalEntitiesRef.current = [];
  }, []);

  const renderFloodSignalOverlay = useCallback((viewer, floodGeoJsonCandidate = null) => {
    if (!viewer || viewer.isDestroyed()) return;
    clearFloodSignalEntities(viewer);

    const center =
      getGeoJsonSignalCenter(floodGeoJsonCandidate) ||
      getGeoJsonSignalCenter(runtimeFloodGeoJson) ||
      getGeoJsonSignalCenter(FLOOD_EXTENT_GEOJSON) ||
      FALLBACK_SIGNAL_CENTER;

    const uncertaintyColorCss =
      runtimeSignalProfile.uncertaintyLevel === 'HIGH'
        ? '#ff6b2d'
        : runtimeSignalProfile.uncertaintyLevel === 'MEDIUM'
          ? '#ffbf47'
          : '#2cf5d2';
    const freshnessColorCss =
      runtimeSignalProfile.freshnessLevel === 'STALE'
        ? '#ff5b5b'
        : runtimeSignalProfile.freshnessLevel === 'RECENT'
          ? '#ffd166'
          : '#00d4ff';
    const uncertaintyColor = Cesium.Color.fromCssColorString(uncertaintyColorCss);
    const freshnessColor = Cesium.Color.fromCssColorString(freshnessColorCss);
    const ringRadiusM = 800 + (runtimeSignalProfile.uncertaintyPct ?? 40) * 24;
    const baseRingAlpha = 0.08 + (runtimeSignalProfile.uncertaintyFactor * 0.10);
    const ringPulse = 0.04 + (runtimeSignalProfile.uncertaintyFactor * 0.18);
    const startTime = Date.now();

    const uncertaintyRing = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(center.lng, center.lat),
      ellipse: {
        semiMajorAxis: ringRadiusM,
        semiMinorAxis: ringRadiusM,
        material: new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => {
            const t = (Date.now() - startTime) / 1800;
            const pulse = baseRingAlpha + ringPulse * Math.abs(Math.sin(t * Math.PI * 2));
            return uncertaintyColor.withAlpha(Math.min(0.38, pulse));
          }, false)
        ),
        outline: true,
        outlineColor: new Cesium.CallbackProperty(() => {
          const t = (Date.now() - startTime) / 1800;
          const pulse = 0.34 + 0.30 * Math.abs(Math.sin(t * Math.PI * 2));
          return uncertaintyColor.withAlpha(Math.min(0.85, pulse));
        }, false),
        outlineWidth: 2,
        classificationType: Cesium.ClassificationType.BOTH,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
      },
    });

    const confidenceLabel = runtimeSignalProfile.confidencePct !== null
      ? `${Math.round(runtimeSignalProfile.confidencePct)}%`
      : (runtimeSignalProfile.confidenceLevel || 'UNKNOWN');
    const freshnessLabel = runtimeSignalProfile.freshnessLevel || 'UNKNOWN';
    const freshnessBeacon = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(center.lng, center.lat, 30),
      point: {
        pixelSize: new Cesium.CallbackProperty(() => {
          const t = (Date.now() - startTime) / 1100;
          return 9 + 5 * Math.abs(Math.sin(t * Math.PI * 2));
        }, false),
        color: new Cesium.CallbackProperty(() => {
          const t = (Date.now() - startTime) / 1400;
          const pulse = 0.62 + 0.30 * Math.abs(Math.sin(t * Math.PI * 2));
          return freshnessColor.withAlpha(Math.min(0.98, pulse));
        }, false),
        outlineColor: uncertaintyColor.withAlpha(0.9),
        outlineWidth: 2,
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: `CONF ${confidenceLabel} · ${freshnessLabel}`,
        font: '10px monospace',
        fillColor: freshnessColor.withAlpha(0.95),
        outlineColor: Cesium.Color.BLACK.withAlpha(0.95),
        outlineWidth: 2,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        showBackground: true,
        backgroundColor: new Cesium.Color(0.04, 0.08, 0.14, 0.7),
        backgroundPadding: new Cesium.Cartesian2(6, 3),
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -16),
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 28000),
        scaleByDistance: new Cesium.NearFarScalar(1500, 1.0, 20000, 0.5),
      },
    });

    floodSignalEntitiesRef.current = [uncertaintyRing, freshnessBeacon];
  }, [clearFloodSignalEntities, runtimeFloodGeoJson, runtimeSignalProfile]);

  // ── Cleanup helper ────────────────────────────────────────────
  const removeLayerEntities = useCallback((layerId) => {
    const viewer = typeof getViewer === 'function' ? getViewer() : null;
    if (!viewer || viewer.isDestroyed()) return;

    // Remove DataSource overlays
    const ds = layerSourcesRef.current.get(layerId);
    if (ds) {
      const removeSource = (sourceHandle) => {
        if (!sourceHandle) return;
        if (typeof sourceHandle === 'boolean') return;
        if (sourceHandle.kind === 'imagery' && sourceHandle.layer) {
          try { viewer.imageryLayers.remove(sourceHandle.layer, true); } catch { /* ok */ }
          return;
        }
        if (sourceHandle.kind === 'datasource' && sourceHandle.layer) {
          try { viewer.dataSources.remove(sourceHandle.layer, true); } catch { /* ok */ }
          return;
        }
        try { viewer.dataSources.remove(sourceHandle, true); } catch { /* ok */ }
      };

      if (Array.isArray(ds)) {
        ds.forEach(removeSource);
      } else {
        removeSource(ds);
      }
      layerSourcesRef.current.delete(layerId);
    }

    // Remove entity-based layers
    if (layerId === 'INFRASTRUCTURE') {
      infraEntitiesRef.current.forEach((e) => {
        try { viewer.entities.remove(e); } catch { /* ok */ }
      });
      infraEntitiesRef.current = [];
    }
    if (layerId === 'POPULATION_DENSITY') {
      popEntitiesRef.current.forEach((e) => {
        try { viewer.entities.remove(e); } catch { /* ok */ }
      });
      popEntitiesRef.current = [];
    }
    if (layerId === 'HISTORICAL_FLOODS') {
      histSourcesRef.current.forEach((d) => {
        try { viewer.dataSources.remove(d, true); } catch { /* ok */ }
      });
      histSourcesRef.current = [];
      historicalRuntimeSignatureRef.current = '';
    }
    if (layerId === 'EVACUATION_ROUTES') {
      routeEntitiesRef.current.forEach((e) => {
        try { viewer.entities.remove(e); } catch { /* ok */ }
      });
      routeEntitiesRef.current = [];
    }
    if (layerId === 'THREAT_RADIUS') {
      threatEntitiesRef.current.forEach((e) => {
        try { viewer.entities.remove(e); } catch { /* ok */ }
      });
      threatEntitiesRef.current = [];
    }

    if (layerId === 'FLOOD_REPLAY') {
      floodReplayTimersRef.current.forEach((id) => clearTimeout(id));
      floodReplayTimersRef.current = [];
      floodReplayEntitiesRef.current.forEach((d) => {
        try { viewer.dataSources.remove(d, true); } catch { /* ok */ }
      });
      floodReplayEntitiesRef.current = [];
    }

    if (layerId === 'FLOOD_EXTENT') {
      floodRuntimeSignatureRef.current = '';
      clearFloodSignalEntities(viewer);
    }
  }, [getViewer, clearFloodSignalEntities]);

  // ── Layer activation effect ───────────────────────────────────
  useEffect(() => {
    let pollId = null;

    const reconcileLayers = () => {
      const viewer = typeof getViewer === 'function' ? getViewer() : null;
      if (!viewer || viewer.isDestroyed()) return false;

      // Reconcile each layer's on/off state
      for (const layer of layerDefinitions) {
        const isOn = layers[layer.id];
        const exists = layerSourcesRef.current.has(layer.id) ||
          (layer.id === 'INFRASTRUCTURE' && infraEntitiesRef.current.length > 0) ||
          (layer.id === 'POPULATION_DENSITY' && popEntitiesRef.current.length > 0) ||
          (layer.id === 'EVACUATION_ROUTES' && routeEntitiesRef.current.length > 0) ||
          (layer.id === 'HISTORICAL_FLOODS' && histSourcesRef.current.length > 0) ||
          (layer.id === 'THREAT_RADIUS' && threatEntitiesRef.current.length > 0);

        if (layer.id === 'FLOOD_EXTENT' && isOn && exists) {
          const runtimeChanged = floodRuntimeSignatureRef.current !== runtimeFloodSignature;
          if (runtimeChanged) {
            removeLayerEntities(layer.id);
            void activateLayer(viewer, layer.id);
            continue;
          }
        }

        if (layer.id === 'HISTORICAL_FLOODS' && isOn && exists) {
          const runtimeChanged =
            historicalRuntimeSignatureRef.current !== runtimeHistoricalSignature;
          if (runtimeChanged) {
            removeLayerEntities(layer.id);
            void activateLayer(viewer, layer.id);
            continue;
          }
        }

        if (isOn && !exists) {
          void activateLayer(viewer, layer.id);
        } else if (!isOn && exists) {
          removeLayerEntities(layer.id);
        }
      }

      // Reconcile FLOOD_REPLAY effect toggle
      const replayOn = layers['FLOOD_REPLAY'];
      const replayExists = floodReplayEntitiesRef.current.length > 0;
      if (replayOn && !replayExists) {
        void activateLayer(viewer, 'FLOOD_REPLAY');
      } else if (!replayOn && replayExists) {
        removeLayerEntities('FLOOD_REPLAY');
      }

      return true;
    };

    if (!reconcileLayers()) {
      pollId = window.setInterval(() => {
        if (reconcileLayers()) {
          window.clearInterval(pollId);
          pollId = null;
        }
      }, 250);
    }

    return () => {
      if (pollId !== null) window.clearInterval(pollId);
    };
  }, [
    layers,
    layerDefinitions,
    runtimeFloodSignature,
    runtimeHistoricalSignature,
    getViewer,
    removeLayerEntities,
  ]);

  // ── Cleanup on unmount ────────────────────────────────────────
  useEffect(() => {
    return () => {
      for (const layer of LAYERS) {
        removeLayerEntities(layer.id);
      }
      removeLayerEntities('FLOOD_REPLAY');
    };
  }, [removeLayerEntities]);

  async function activateLayer(viewer, layerId) {
    try {
      switch (layerId) {
        case 'FLOOD_EXTENT':
          await activateFloodExtent(viewer);
          break;
        case 'TRIAGE_ZONES':
          await activateTriageZones(viewer);
          break;
        case 'INFRASTRUCTURE':
          activateInfrastructure(viewer);
          break;
        case 'EVACUATION_ROUTES':
          activateEvacuationRoutes(viewer);
          break;
        case 'POPULATION_DENSITY':
          activatePopulationDensity(viewer);
          break;
        case 'HISTORICAL_FLOODS':
          await activateHistoricalFloods(viewer);
          break;
        case 'THREAT_RADIUS':
          activateThreatRadius(viewer);
          break;
        case 'FLOOD_REPLAY':
          activateFloodReplay(viewer);
          break;
        default:
          break;
      }
    } catch (err) {
      console.error(`[DataLayerPanel] Failed to activate ${layerId}:`, err);
    }
  }

  // ── FLOOD EXTENT ──────────────────────────────────────────────
  async function resolveRuntimeFloodGeoJsonPayload() {
    if (runtimeFloodGeoJson) return runtimeFloodGeoJson;

    if (!runtimeFloodGeoJsonUrl) return null;
    if (runtimeFloodGeoJsonCacheRef.current.url === runtimeFloodGeoJsonUrl) {
      return runtimeFloodGeoJsonCacheRef.current.geojson;
    }

    try {
      const response = await fetch(runtimeFloodGeoJsonUrl);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      const normalized = normalizeRuntimeGeoJson(payload);
      runtimeFloodGeoJsonCacheRef.current = {
        url: runtimeFloodGeoJsonUrl,
        geojson: normalized,
      };
      return normalized;
    } catch (err) {
      runtimeFloodGeoJsonCacheRef.current = {
        url: runtimeFloodGeoJsonUrl,
        geojson: null,
      };
      console.warn('[DataLayerPanel] Failed to load runtime flood GeoJSON, using fallback.', err);
      return null;
    }
  }

  async function activateFloodExtent(viewer) {
    const frameVisualProfile = getFloodFrameVisualProfile(activeRuntimeFrame);
    const confidenceFactor = runtimeSignalProfile.confidenceFactor;
    const uncertaintyFactor = runtimeSignalProfile.uncertaintyFactor;
    const freshnessFactor = runtimeSignalProfile.freshnessFactor;
    const uncertaintyTint = Cesium.Color.fromCssColorString(
      runtimeSignalProfile.uncertaintyLevel === 'HIGH'
        ? '#ff6b2d'
        : runtimeSignalProfile.uncertaintyLevel === 'MEDIUM'
          ? '#ffbf47'
          : '#2cf5d2'
    );
    const freshnessTint = Cesium.Color.fromCssColorString(
      runtimeSignalProfile.freshnessLevel === 'STALE'
        ? '#ff5b5b'
        : runtimeSignalProfile.freshnessLevel === 'RECENT'
          ? '#ffd166'
          : '#00d4ff'
    );

    if (runtimeFloodTileLayersForRender.length > 0) {
      const runtimeSources = [];

      for (const { layer, tileSource, urlTemplate } of runtimeFloodTileLayersForRender) {
        try {
          const provider = new Cesium.UrlTemplateImageryProvider({
            url: urlTemplate,
            minimumLevel: Number.isFinite(tileSource.min_zoom) ? tileSource.min_zoom : 0,
            maximumLevel: resolveTileSourceMaxZoom(tileSource, lowBandwidthActive),
          });
          const imageryLayer = viewer.imageryLayers.addImageryProvider(provider);
          const temporalFrame = getRuntimeLayerTemporalFrame(layer);
          const frameMatchesSelection = layerMatchesTemporalFrame(layer, activeRuntimeFrame);
          const fallbackAlpha = temporalFrame === 'change'
            ? 0.72
            : temporalFrame === 'event'
              ? 0.56
              : 0.42;
          const baseAlpha = frameMatchesSelection
            ? frameVisualProfile.tileAlpha
            : fallbackAlpha;
          const signalScaledAlpha = baseAlpha
            * (0.72 + (confidenceFactor * 0.48))
            * freshnessFactor
            * (1 - Math.min(0.32, uncertaintyFactor * 0.35));
          imageryLayer.alpha = Math.min(0.90, Math.max(0.12, signalScaledAlpha));
          imageryLayer.brightness = 0.85 + (confidenceFactor * 0.30);
          imageryLayer.contrast = 0.90 + (confidenceFactor * 0.25);
          imageryLayer.gamma = 1.00 + (uncertaintyFactor * 0.18);
          runtimeSources.push({
            kind: 'imagery',
            layer: imageryLayer,
          });
        } catch (err) {
          console.warn(`[DataLayerPanel] Failed to add EE runtime layer ${layer.id}:`, err);
        }
      }

      if (runtimeSources.length > 0) {
        layerSourcesRef.current.set('FLOOD_EXTENT', runtimeSources);
        floodRuntimeSignatureRef.current = runtimeFloodSignature;
        renderFloodSignalOverlay(viewer, runtimeFloodGeoJson || FLOOD_EXTENT_GEOJSON);
        console.info(
          `[DataLayerPanel] FLOOD_EXTENT loaded from EE runtime (${runtimeSources.length} raster layer${runtimeSources.length === 1 ? '' : 's'}, frame=${activeRuntimeFrame || 'default'}, confidence=${runtimeSignalProfile.confidencePct ?? runtimeSignalProfile.confidenceLevel ?? 'unknown'}${lowBandwidthActive ? ', low-bandwidth render profile' : ''}).`
        );
        return;
      }
    }

    const runtimeVectorGeoJson = await resolveRuntimeFloodGeoJsonPayload();
    const lowBandwidthFloodGeoJson =
      lowBandwidthActive
        ? buildLowBandwidthFloodGeoJson(runtimeVectorGeoJson || FLOOD_EXTENT_GEOJSON)
        : null;
    const floodGeoJson = lowBandwidthFloodGeoJson || runtimeVectorGeoJson || FLOOD_EXTENT_GEOJSON;
    if (!floodGeoJson) return;

    const ds = await Cesium.GeoJsonDataSource.load(floodGeoJson, {
      clampToGround: false,
    });
    viewer.dataSources.add(ds);

    // ── Severity gradient: compute area range across all polygons ──
    const entities = ds.entities.values;
    let minArea = Infinity;
    let maxArea = 0;
    for (const e of entities) {
      const rawArea = e.properties?.polygon_area_sqkm?.getValue?.()
        ?? e.properties?.area_sqm?.getValue?.()
        ?? 0;
      const areaSqKm = rawArea > 1000 ? rawArea / 1_000_000 : rawArea;
      if (areaSqKm > 0) {
        minArea = Math.min(minArea, areaSqKm);
        maxArea = Math.max(maxArea, areaSqKm);
      }
    }
    if (!Number.isFinite(minArea) || minArea === Infinity) minArea = 0;
    const areaRange = maxArea - minArea || 1;

    // Frame-aware colour profile keeps temporal selection visibly distinct.
    const colorLow = Cesium.Color.fromCssColorString(frameVisualProfile.colorLow);
    const colorHigh = Cesium.Color.fromCssColorString(frameVisualProfile.colorHigh);
    const outlineColorBase = Cesium.Color.fromCssColorString(frameVisualProfile.outlineColor);
    const uncertaintyOutlineColor = Cesium.Color.lerp(
      outlineColorBase,
      uncertaintyTint,
      Math.min(0.7, uncertaintyFactor * 0.75),
      new Cesium.Color(),
    );
    const outlineColor = Cesium.Color.lerp(
      uncertaintyOutlineColor,
      freshnessTint,
      runtimeSignalProfile.freshnessLevel === 'STALE' ? 0.42 : 0.16,
      new Cesium.Color(),
    );

    const startTime = Date.now();
    for (const entity of entities) {
      if (entity.polygon) {
        // Per-polygon severity (0 = smallest, 1 = largest)
        const rawArea = entity.properties?.polygon_area_sqkm?.getValue?.()
          ?? entity.properties?.area_sqm?.getValue?.()
          ?? 0;
        const areaSqKm = rawArea > 1000 ? rawArea / 1_000_000 : rawArea;
        const severity = Math.min(1, Math.max(0, (areaSqKm - minArea) / areaRange));

        // Lerp colour between low (small flood) and high (large flood)
        const r = colorLow.red + (colorHigh.red - colorLow.red) * severity;
        const g = colorLow.green + (colorHigh.green - colorLow.green) * severity;
        const b = colorLow.blue + (colorHigh.blue - colorLow.blue) * severity;
        const baseColor = new Cesium.Color(r, g, b);
        const confidenceTintedColor = Cesium.Color.lerp(
          baseColor,
          uncertaintyTint,
          Math.min(0.45, uncertaintyFactor * 0.5),
          new Cesium.Color(),
        );

        // 3D extrusion: scale by temporal frame to highlight baseline/event/change shifts.
        const extrudedH =
          (frameVisualProfile.extrusionBase + (severity * frameVisualProfile.extrusionRange))
          * (0.75 + (confidenceFactor * 0.5));

        entity.polygon.height = 0;
        entity.polygon.extrudedHeight = extrudedH;
        entity.polygon.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
        entity.polygon.material = new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => {
            const t = (Date.now() - startTime) / 4000;
            const confidenceAdjustedBase = frameVisualProfile.alphaBase * (0.55 + confidenceFactor * 0.7);
            const freshnessAdjustedBase = confidenceAdjustedBase * freshnessFactor;
            const dynamicPulse = frameVisualProfile.alphaPulse + (uncertaintyFactor * 0.08);
            const alpha = Math.min(
              0.95,
              Math.max(
                0.08,
                freshnessAdjustedBase + dynamicPulse * Math.sin(t * Math.PI * 2),
              ),
            );
            return confidenceTintedColor.withAlpha(alpha);
          }, false)
        );
        entity.polygon.outline = true;
        entity.polygon.outlineColor = outlineColor.withAlpha(
          Math.min(0.95, frameVisualProfile.outlineAlpha + (uncertaintyFactor * 0.12))
        );
      }

      if (entity.polyline) {
        const polylineAlpha = Math.min(
          0.96,
          frameVisualProfile.polylineAlpha + (uncertaintyFactor * 0.15),
        );
        if (uncertaintyFactor > 0.55) {
          entity.polyline.material = new Cesium.PolylineDashMaterialProperty({
            color: outlineColor.withAlpha(polylineAlpha),
            gapColor: Cesium.Color.TRANSPARENT,
            dashLength: 18,
          });
        } else {
          entity.polyline.material = outlineColor.withAlpha(polylineAlpha);
        }
        entity.polyline.width = 3;
        entity.polyline.clampToGround = true;
      }
    }

    console.info(
      `[DataLayerPanel] FLOOD_EXTENT loaded (${entities.length} entities, frame=${activeRuntimeFrame || 'default'}, confidence=${runtimeSignalProfile.confidencePct ?? runtimeSignalProfile.confidenceLevel ?? 'unknown'}, uncertainty=${runtimeSignalProfile.uncertaintyPct ?? runtimeSignalProfile.uncertaintyLevel ?? 'unknown'})${runtimeVectorGeoJson ? ' from EE runtime vector.' : '.'}${lowBandwidthActive ? ' (simplified low-bandwidth vector profile)' : ''}`
    );
    layerSourcesRef.current.set('FLOOD_EXTENT', {
      kind: 'datasource',
      layer: ds,
    });
    floodRuntimeSignatureRef.current = runtimeFloodSignature;
    renderFloodSignalOverlay(viewer, floodGeoJson);
  }

  // ── TRIAGE ZONES ──────────────────────────────────────────────
  async function activateTriageZones(viewer) {
    if (!TRIAGE_ZONES_GEOJSON) return;
    const ds = await Cesium.GeoJsonDataSource.load(TRIAGE_ZONES_GEOJSON, {
      clampToGround: true,
    });
    viewer.dataSources.add(ds);

    ds.entities.values.forEach((entity) => {
      if (entity.polygon) {
        const zone = entity.properties?.zone?.getValue?.() || 'GREEN';
        const cfg = TRIAGE_COLORS[zone] || TRIAGE_COLORS.GREEN;
        entity.polygon.material = Cesium.Color.fromCssColorString(cfg.fill).withAlpha(cfg.opacity);
        entity.polygon.outlineColor = Cesium.Color.fromCssColorString(cfg.fill).withAlpha(0.6);
        entity.polygon.outline = true;
        entity.polygon.classificationType = Cesium.ClassificationType.BOTH;
      }
      // Add label for zone name
      const name = entity.properties?.name?.getValue?.() || '';
      if (name && entity.polygon) {
        entity.label = new Cesium.LabelGraphics({
          text: name,
          font: '10px monospace',
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scale: 0.8,
        });
      }
    });

    layerSourcesRef.current.set('TRIAGE_ZONES', ds);
  }

  // ── EVACUATION ROUTES ──────────────────────────────────────────
  function activateEvacuationRoutes(_viewer) {
    // Route overlays are rendered from backend map-update events in App.jsx.
    // This layer stays as a lifecycle/toggle anchor only.
    routeEntitiesRef.current = [];
    layerSourcesRef.current.set('EVACUATION_ROUTES', true);
  }

  // ── INFRASTRUCTURE ────────────────────────────────────────────
  function activateInfrastructure(viewer, dataOverride) {
    const data = dataOverride || INFRASTRUCTURE_DATA;
    const entities = [];

    // ── Individual markers (visible within 5km) ──────────────────
    for (const item of data) {
      const iconUri = INFRA_BILLBOARD_ICONS[item.type] || INFRA_BILLBOARD_ICONS.hospital;
      const color = INFRA_COLORS[item.type] || '#ffffff';
      const entity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(item.longitude, item.latitude, 20),
        billboard: {
          image: iconUri,
          width: 32,
          height: 32,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
          heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scaleByDistance: new Cesium.NearFarScalar(500, 1.5, 8000, 0.4),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 5000),
        },
        label: {
          text: item.name || 'Unknown',
          font: '10px monospace',
          fillColor: Cesium.Color.fromCssColorString(color).withAlpha(0.95),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -20),
          heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scaleByDistance: new Cesium.NearFarScalar(500, 1, 5000, 0.3),
          translucencyByDistance: new Cesium.NearFarScalar(500, 1, 4000, 0.15),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 5000),
        },
      });
      entities.push(entity);
    }

    // ── Summary labels at high altitude (visible above 5km) ──────
    const typeConfig = {
      hospital:      { emoji: '\u{1F3E5}', label: 'Hospitals',  color: '#ff3333' },
      school:        { emoji: '\u{1F3EB}', label: 'Schools',    color: '#ffcc00' },
      shelter:       { emoji: '\u{1F3E0}', label: 'Shelters',   color: '#33ff33' },
      power_station: { emoji: '\u26A1',     label: 'Power',      color: '#ff8800' },
    };

    for (const [type, cfg] of Object.entries(typeConfig)) {
      const items = data.filter((d) => d.type === type);
      if (items.length === 0) continue;

      // Compute geographic centroid
      const avgLat = items.reduce((s, d) => s + d.latitude, 0) / items.length;
      const avgLng = items.reduce((s, d) => s + d.longitude, 0) / items.length;

      const summaryEntity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(avgLng, avgLat, 50),
        label: {
          text: `${cfg.emoji} ${items.length} ${cfg.label}`,
          font: 'bold 16px sans-serif',
          fillColor: Cesium.Color.fromCssColorString(cfg.color).withAlpha(0.95),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          showBackground: true,
          backgroundColor: new Cesium.Color(0.04, 0.055, 0.09, 0.85),
          backgroundPadding: new Cesium.Cartesian2(10, 6),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
          heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(5000, 50000),
          scaleByDistance: new Cesium.NearFarScalar(5000, 1.0, 30000, 0.5),
        },
      });
      entities.push(summaryEntity);
    }

    infraEntitiesRef.current = entities;
    layerSourcesRef.current.set('INFRASTRUCTURE', true);

    if (!dataOverride) {
      // ── Fire-and-forget: try loading from BigQuery REST API ────
      fetch('/api/infrastructure')
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((apiData) => {
          if (!Array.isArray(apiData) || apiData.length === 0) {
            console.log('[DATA] Infrastructure loaded from: static fallback (API returned empty)');
            return;
          }
          console.log(`[DATA] Infrastructure loaded from: API (${apiData.length} facilities)`);
          // Clear existing entities and re-render with API data
          const v = typeof getViewer === 'function' ? getViewer() : null;
          if (!v || v.isDestroyed()) return;
          infraEntitiesRef.current.forEach((e) => {
            try { v.entities.remove(e); } catch { /* ok */ }
          });
          infraEntitiesRef.current = [];
          activateInfrastructure(v, apiData);
        })
        .catch(() => {
          console.log('[DATA] Infrastructure loaded from: static fallback');
        });
    }
  }

  // ── POPULATION DENSITY ────────────────────────────────────────
  // Staggered circles with a soft outer halo so the field reads more like
  // a blended heat surface than a checkerboard of discrete dots.
  function activatePopulationDensity(viewer) {
    const entities = [];
    for (const pt of POPULATION_GRID) {
      const { coreColor, haloColor, coreRadius, haloRadius } = getPopulationHeatStyle(pt.density);

      const haloEntity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(pt.lng, pt.lat),
        ellipse: {
          semiMajorAxis: haloRadius,
          semiMinorAxis: haloRadius,
          material: haloColor,
          classificationType: Cesium.ClassificationType.BOTH,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });
      entities.push(haloEntity);

      const coreEntity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(pt.lng, pt.lat),
        ellipse: {
          semiMajorAxis: coreRadius,
          semiMinorAxis: coreRadius,
          material: coreColor,
          classificationType: Cesium.ClassificationType.BOTH,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });
      entities.push(coreEntity);
    }
    popEntitiesRef.current = entities;
    layerSourcesRef.current.set('POPULATION_DENSITY', true);
  }

  // ── HISTORICAL FLOODS ─────────────────────────────────────────
  async function activateHistoricalFloods(viewer) {
    if (runtimeHistoricalTileLayersForRender.length > 0) {
      const runtimeSources = [];
      for (const { layer, tileSource, urlTemplate } of runtimeHistoricalTileLayersForRender) {
        try {
          const provider = new Cesium.UrlTemplateImageryProvider({
            url: urlTemplate,
            minimumLevel: Number.isFinite(tileSource.min_zoom) ? tileSource.min_zoom : 0,
            maximumLevel: resolveTileSourceMaxZoom(tileSource, lowBandwidthActive),
          });
          const imageryLayer = viewer.imageryLayers.addImageryProvider(provider);
          const temporalFrame = getRuntimeLayerTemporalFrame(layer);
          imageryLayer.alpha = temporalFrame === 'baseline'
            ? 0.30
            : temporalFrame === 'event'
              ? 0.46
              : 0.60;
          runtimeSources.push({
            kind: 'imagery',
            layer: imageryLayer,
          });
        } catch (err) {
          console.warn(`[DataLayerPanel] Failed to add historical runtime layer ${layer?.id}:`, err);
        }
      }

      if (runtimeSources.length > 0) {
        histSourcesRef.current = [];
        layerSourcesRef.current.set('HISTORICAL_FLOODS', runtimeSources);
        historicalRuntimeSignatureRef.current = runtimeHistoricalSignature;
        console.info(
          `[DataLayerPanel] HISTORICAL_FLOODS loaded from EE runtime (${runtimeSources.length} temporal layer${runtimeSources.length === 1 ? '' : 's'}${lowBandwidthActive ? ', low-bandwidth render profile' : ''}).`
        );
        return;
      }
    }

    const sources = [];
    const histColors = ['#8888ff', '#aa66ff', '#cc88ff']; // distinct ghost tints
    const frameTintByTemporalFrame = {
      baseline: '#8888ff',
      event: '#aa66ff',
      change: '#cc88ff',
    };
    const selectedTemporalFrame = normalizeTemporalFrameToken(activeRuntimeFrame);
    const frameScopedFloods = (
      selectedTemporalFrame === 'baseline'
        ? [HISTORICAL_FLOODS[0]]
        : selectedTemporalFrame === 'event'
          ? [HISTORICAL_FLOODS[1]]
          : selectedTemporalFrame === 'change'
            ? [HISTORICAL_FLOODS[2]]
            : HISTORICAL_FLOODS
    ).filter(Boolean);

    for (let idx = 0; idx < frameScopedFloods.length; idx++) {
      const flood = frameScopedFloods[idx];
      const year = flood.features[0]?.properties?.year || '?';
      const tint = selectedTemporalFrame
        ? (frameTintByTemporalFrame[selectedTemporalFrame] || histColors[idx % histColors.length])
        : histColors[idx % histColors.length];
      const ds = await Cesium.GeoJsonDataSource.load(flood, {
        clampToGround: true,
        fill: Cesium.Color.fromCssColorString(tint).withAlpha(0.25),
        stroke: Cesium.Color.fromCssColorString(tint).withAlpha(0.6),
        strokeWidth: 2,
      });
      viewer.dataSources.add(ds);

      ds.entities.values.forEach((entity) => {
        if (entity.polygon) {
          entity.polygon.classificationType = Cesium.ClassificationType.BOTH;
        }
        // Add year label
        entity.label = new Cesium.LabelGraphics({
          text: `${year} FLOOD`,
          font: '12px monospace',
          fillColor: Cesium.Color.fromCssColorString(tint).withAlpha(0.85),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        });
      });

      sources.push(ds);
    }
    histSourcesRef.current = sources;
    layerSourcesRef.current.set('HISTORICAL_FLOODS', true);
    historicalRuntimeSignatureRef.current = runtimeHistoricalSignature;
  }

  // ── FLOOD REPLAY (sequential polygon progression) ──────────────
  async function activateFloodReplay(viewer) {
    const floodGeoJson = FLOOD_EXTENT_GEOJSON;
    if (!floodGeoJson) return;

    const ds = await Cesium.GeoJsonDataSource.load(floodGeoJson, {
      clampToGround: false,
    });
    viewer.dataSources.add(ds);

    const entities = ds.entities.values;

    // Build (entity, area) pairs and sort by area descending (largest first)
    const entityAreaPairs = [];
    for (const entity of entities) {
      const rawArea = entity.properties?.polygon_area_sqkm?.getValue?.()
        ?? entity.properties?.area_sqm?.getValue?.()
        ?? 0;
      const areaSqKm = rawArea > 1000 ? rawArea / 1_000_000 : rawArea;
      entityAreaPairs.push({ entity, areaSqKm });
    }
    entityAreaPairs.sort((a, b) => b.areaSqKm - a.areaSqKm);

    // Compute area range for severity gradient
    let minArea = Infinity;
    let maxArea = 0;
    for (const { areaSqKm } of entityAreaPairs) {
      if (areaSqKm > 0) {
        minArea = Math.min(minArea, areaSqKm);
        maxArea = Math.max(maxArea, areaSqKm);
      }
    }
    if (!Number.isFinite(minArea) || minArea === Infinity) minArea = 0;
    const areaRange = maxArea - minArea || 1;

    const colorLow = Cesium.Color.fromCssColorString('#00b4ff');
    const colorHigh = Cesium.Color.fromCssColorString('#001a66');
    const outlineColor = Cesium.Color.fromCssColorString('#00d4ff');
    const startTime = Date.now();

    // Hide all entities initially
    for (const { entity } of entityAreaPairs) {
      entity.show = false;
    }

    // Reveal one-by-one with animated extrusion
    const timers = [];
    const REVEAL_INTERVAL_MS = 250;
    const EXTRUSION_DURATION_MS = 600;

    for (let i = 0; i < entityAreaPairs.length; i++) {
      const { entity, areaSqKm } = entityAreaPairs[i];
      const severity = Math.min(1, Math.max(0, (areaSqKm - minArea) / areaRange));
      const finalHeight = 100 + severity * 400;

      // Colour lerp
      const r = colorLow.red + (colorHigh.red - colorLow.red) * severity;
      const g = colorLow.green + (colorHigh.green - colorLow.green) * severity;
      const b = colorLow.blue + (colorHigh.blue - colorLow.blue) * severity;
      const baseColor = new Cesium.Color(r, g, b);

      const timerId = setTimeout(() => {
        entity.show = true;
        const revealTime = Date.now();

        if (entity.polygon) {
          entity.polygon.height = 0;
          entity.polygon.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
          entity.polygon.extrudedHeight = new Cesium.CallbackProperty(() => {
            const elapsed = Date.now() - revealTime;
            if (elapsed >= EXTRUSION_DURATION_MS) return finalHeight;
            // Ease-out cubic: 1 - (1 - t)^3
            const t = elapsed / EXTRUSION_DURATION_MS;
            const eased = 1 - Math.pow(1 - t, 3);
            return eased * finalHeight;
          }, false);

          entity.polygon.material = new Cesium.ColorMaterialProperty(
            new Cesium.CallbackProperty(() => {
              const t = (Date.now() - startTime) / 4000;
              const alpha = Math.min(
                0.95,
                Math.max(0.1, 0.55 + 0.10 * Math.sin(t * Math.PI * 2)),
              );
              return baseColor.withAlpha(alpha);
            }, false)
          );
          entity.polygon.outline = true;
          entity.polygon.outlineColor = outlineColor.withAlpha(0.70);
        }

        if (entity.polyline) {
          entity.polyline.material = outlineColor.withAlpha(0.70);
          entity.polyline.width = 3;
          entity.polyline.clampToGround = true;
        }
      }, i * REVEAL_INTERVAL_MS);

      timers.push(timerId);
    }

    floodReplayTimersRef.current = timers;
    floodReplayEntitiesRef.current = [ds];
    layerSourcesRef.current.set('FLOOD_REPLAY', true);
    console.info(
      `[DataLayerPanel] FLOOD_REPLAY activated: ${entityAreaPairs.length} polygons, ${REVEAL_INTERVAL_MS}ms apart.`
    );
  }

  // ── THREAT RADIUS ─────────────────────────────────────────────
  function activateThreatRadius(viewer) {
    const center = { lat: -6.225, lng: 106.855 }; // Kampung Melayu
    const rings = [
      { level: 1, radius: 2500, color: '#ffaa00', baseAlpha: 0.25 },
      { level: 2, radius: 4000, color: '#ff6600', baseAlpha: 0.2 },
      { level: 3, radius: 5500, color: '#ff4444', baseAlpha: 0.15 },
    ];

    const entities = [];
    const startTime = Date.now();

    for (const ring of rings) {
      const entity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(center.lng, center.lat),
        ellipse: {
          semiMajorAxis: ring.radius,
          semiMinorAxis: ring.radius,
          material: new Cesium.ColorMaterialProperty(
            new Cesium.CallbackProperty(() => {
              const t = (Date.now() - startTime) / 2000;
              const pulse = ring.baseAlpha + 0.05 * Math.sin(t * Math.PI * 2 + ring.level);
              return Cesium.Color.fromCssColorString(ring.color).withAlpha(pulse);
            }, false)
          ),
          outline: true,
          outlineColor: new Cesium.CallbackProperty(() => {
            return Cesium.Color.fromCssColorString(ring.color).withAlpha(0.65);
          }, false),
          classificationType: Cesium.ClassificationType.BOTH,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
        label: {
          text: `+${ring.level}m`,
          font: '10px monospace',
          fillColor: Cesium.Color.fromCssColorString(ring.color).withAlpha(0.7),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 1,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          pixelOffset: new Cesium.Cartesian2(ring.radius / 30, 0),
        },
      });
      entities.push(entity);
    }

    threatEntitiesRef.current = entities;
    layerSourcesRef.current.set('THREAT_RADIUS', true);
  }

  // ── Render ────────────────────────────────────────────────────
  return (
    <div className="data-layer-panel">
      <div className="data-layer-header">
        <span>Data Layers</span>
        <button
          className="data-layer-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
        >
          {collapsed ? '\u25BC' : '\u25B2'}
        </button>
      </div>
      {!collapsed && (
        <>
          <div className="data-layer-list">
            {layerDefinitions.map((layer) => {
              const isOn = layers[layer.id] || false;
              return (
                <div
                  key={layer.id}
                  className={`data-layer-row ${isOn ? '' : 'data-layer-row--off'}`}
                  onClick={() => onToggle(layer.id)}
                >
                  <div
                    className="data-layer-icon"
                    style={{ background: layer.iconBg }}
                  >
                    {layer.icon}
                  </div>
                  <div className="data-layer-info">
                    <div className="data-layer-name">
                      {layer.name}
                      {layer.source && (
                        <span className="data-layer-source">{layer.source}</span>
                      )}
                    </div>
                    <div className="data-layer-badge">{layer.badge}</div>
                  </div>
                  <div className={`data-layer-toggle ${isOn ? 'data-layer-toggle--on' : ''}`} />
                </div>
              );
            })}

            {/* ── Effects section divider ── */}
            <div className="data-layer-divider" />
            <div className="data-layer-section-label">Effects</div>
            {EFFECTS_LAYERS.map((layer) => {
              const isOn = layers[layer.id] || false;
              return (
                <div
                  key={layer.id}
                  className={`data-layer-row ${isOn ? '' : 'data-layer-row--off'}`}
                  onClick={() => onToggle(layer.id)}
                >
                  <div
                    className="data-layer-icon"
                    style={{ background: layer.iconBg }}
                  >
                    {layer.icon}
                  </div>
                  <div className="data-layer-info">
                    <div className="data-layer-name">{layer.name}</div>
                    <div className="data-layer-badge">{layer.badge}</div>
                  </div>
                  <div className={`data-layer-toggle ${isOn ? 'data-layer-toggle--on' : ''}`} />
                </div>
              );
            })}
          </div>
          {replayAvailable && (
            <div className="data-layer-replay">
              <div className="data-layer-replay-header">
                <span className="data-layer-replay-title">Incident Replay</span>
                <span className={`data-layer-replay-state ${replayIsPlaying ? 'live' : ''}`}>
                  {replayIsPlaying ? 'PLAYING' : 'PAUSED'}
                </span>
              </div>
              <div className="data-layer-replay-controls">
                <button
                  type="button"
                  className="data-layer-replay-btn"
                  onClick={() => onReplayToggle?.()}
                  disabled={typeof onReplayToggle !== 'function'}
                >
                  {replayIsPlaying ? 'Pause' : 'Play'}
                </button>
                <button
                  type="button"
                  className="data-layer-replay-btn"
                  onClick={() => onReplaySeek?.(0)}
                  disabled={typeof onReplaySeek !== 'function'}
                >
                  Reset
                </button>
                <div className="data-layer-replay-speed-group">
                  {[0.5, 1, 2].map((speed) => (
                    <button
                      key={`replay-speed-${speed}`}
                      type="button"
                      className={`data-layer-replay-speed-btn ${replaySpeed === speed ? 'active' : ''}`}
                      onClick={() => onReplaySpeedChange?.(speed)}
                      disabled={typeof onReplaySpeedChange !== 'function'}
                    >
                      {speed}x
                    </button>
                  ))}
                </div>
              </div>
              <div className="data-layer-replay-strip" role="tablist" aria-label="Incident replay timeline strip">
                {replayTrack.map((step, index) => {
                  const stepIndex = Number.isFinite(step?.index) ? step.index : index;
                  const isActive = stepIndex === replayActiveIndex;
                  const isHotspot = Boolean(step?.hotspot);
                  return (
                    <button
                      key={step?.id || `${step?.frameId || 'frame'}-${stepIndex}`}
                      type="button"
                      className={`data-layer-replay-node ${isActive ? 'active' : ''} ${isHotspot ? 'hotspot' : ''}`}
                      onClick={() => onReplaySeek?.(stepIndex)}
                      title={`${step?.label || `Frame ${stepIndex + 1}`} — ${step?.reason || step?.caption || ''}`}
                      disabled={typeof onReplaySeek !== 'function'}
                      aria-label={`Replay frame ${stepIndex + 1}: ${step?.label || 'Temporal step'}`}
                    >
                      <span className="data-layer-replay-node-dot" />
                    </button>
                  );
                })}
              </div>
              <div className="data-layer-replay-meta">
                <span>{incidentReplay?.activeStepLabel || `Frame ${replayActiveIndex + 1}`}</span>
                <span>{incidentReplay?.activeStepCaption || 'Temporal progression synchronized to flood overlays'}</span>
              </div>
              {replayHotspots.length > 0 && (
                <div className="data-layer-replay-hotspots">
                  {replayHotspots.slice(0, 3).map((hotspot, hotspotIndex) => {
                    const stepIndex = Number.isFinite(hotspot?.index)
                      ? hotspot.index
                      : replayActiveIndex;
                    const isActiveHotspot = stepIndex === replayActiveIndex;
                    return (
                      <button
                        key={`hotspot-${hotspot?.id || hotspot?.frameId || hotspotIndex}`}
                        type="button"
                        className={`data-layer-replay-hotspot ${isActiveHotspot ? 'active' : ''}`}
                        onClick={() => {
                          if (typeof onReplayJumpToHotspot === 'function') {
                            onReplayJumpToHotspot(stepIndex);
                            return;
                          }
                          onReplaySeek?.(stepIndex);
                        }}
                        disabled={typeof onReplayJumpToHotspot !== 'function' && typeof onReplaySeek !== 'function'}
                      >
                        H{hotspotIndex + 1}: {hotspot?.label || `Frame ${stepIndex + 1}`}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
          <div className="data-layer-field-pack">
            <div className="data-layer-field-pack-header">
              <span className="data-layer-field-pack-title">Field Offline Pack</span>
              <span className={`data-layer-field-pack-profile ${lowBandwidthActive ? 'low' : ''}`}>
                {fieldPackProfile}
              </span>
            </div>
            <p className="data-layer-field-pack-copy">
              {lowBandwidthActive
                ? 'Low-bandwidth profile active: export uses cached critical layers with simplified vectors.'
                : 'Generate an operator-ready package with critical layers, timeline snapshots, and safe vector payloads.'}
            </p>
            <button
              type="button"
              className="data-layer-field-pack-btn"
              onClick={handleGenerateFieldPack}
            >
              {fieldPackProfile === 'LOW' ? 'Generate Lite Pack' : 'Generate Field Pack'}
            </button>
            {fieldPackSummary && (
              <div className="data-layer-field-pack-summary">
                <div className="data-layer-field-pack-row">
                  <span>Generated</span>
                  <span>{formatFieldPackTimestamp(fieldPackSummary.generatedAtMs)}</span>
                </div>
                <div className="data-layer-field-pack-row">
                  <span>Critical cache</span>
                  <span>{fieldPackSummary.cachedCriticalLayers}/{fieldPackSummary.totalCriticalLayers}</span>
                </div>
                <div className="data-layer-field-pack-row">
                  <span>Timeline</span>
                  <span>{fieldPackSummary.timelineSnapshots} snapshots · {fieldPackSummary.hotspotSnapshots} hotspots</span>
                </div>
                <div className="data-layer-field-pack-row">
                  <span>Vectors</span>
                  <span>{fieldPackSummary.vectorFeatures} features · {fieldPackSummary.vectorVertices} vertices</span>
                </div>
                <div className="data-layer-field-pack-row">
                  <span>Profile</span>
                  <span>
                    {fieldPackSummary.profile} · simplified
                    {fieldPackSummary.vectorTruncated ? ' · truncated geometry' : ''}
                  </span>
                </div>
                <div className="data-layer-field-pack-row">
                  <span>Payload</span>
                  <span>{formatFieldPackSize(fieldPackSummary.payloadBytes)}</span>
                </div>
                <div className="data-layer-field-pack-file" title={fieldPackSummary.filename}>
                  {fieldPackSummary.filename}
                </div>
              </div>
            )}
            {fieldPackError && (
              <div className="data-layer-field-pack-error">{fieldPackError}</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
