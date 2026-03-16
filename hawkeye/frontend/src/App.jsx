import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import TopBar from './components/TopBar';
import StrategicViewPanel from './components/StrategicViewPanel';
import AnalyticsDashboard from './components/charts/AnalyticsDashboard';
import NeuralLinkPanel from './components/NeuralLinkPanel';
import IncidentLogPanel from './components/IncidentLogPanel';
import MissionSelect from './components/MissionSelect';
import VideoIntro from './components/VideoIntro';
import { useHawkEyeSocket } from './hooks/useHawkEyeSocket';
import { useAudioPipeline } from './hooks/useAudioPipeline';
import {
  SERVER_MESSAGE_TYPES,
  OPERATIONAL_MODES,
  MAP_ACTIONS,
  CAMERA_MODES,
} from './types/messages';
import './utils/demoSimulator';

import {
  MOCK_TOPBAR,
  MOCK_STRATEGIC_VIEW,
  MOCK_RECON_FEED,
  MOCK_EARTH_ENGINE,
  MOCK_NEURAL_LINK,
  MOCK_INCIDENT_LOG,
} from './data/mockData';
import analysisProvenanceRaw from '../../data/geojson/analysis_provenance.json?raw';
import floodExtentRaw from '../../data/geojson/flood_extent.geojson?raw';

const ENABLE_DEMO_TIMELINE = import.meta.env.VITE_ENABLE_DEMO_TIMELINE === 'true';
const EVACUATION_ROUTE_OVERLAY_ID = 'evacuation-route-runtime';
const EVACUATION_ZONE_OVERLAY_ID = 'evacuation-zone-runtime';
const EVACUATION_ALT_ROUTE_OVERLAY_PREFIX = 'evacuation-route-alt-runtime';
const MAX_EVACUATION_ALT_ROUTES = 2;
const DEFAULT_EVACUATION_ROUTE_COMMAND =
  'Generate evacuation plan';
const MAX_VIDEO_CADENCE_FPS = 1;
const DEFAULT_VIDEO_CADENCE_FPS = 1;
const VIDEO_CADENCE_OPTIONS = [0.25, 0.5, 1];
const LIVE_TRANSCRIPTS_CAP = 300;
const LIVE_INCIDENT_ENTRIES_CAP = 400;
const LIVE_MAP_COMMANDS_CAP = 300;
const TOOL_MISSION_RAIL_CAP = 12;
const SESSION_IDENTITY_STORAGE_KEY = 'hawkeye.live.session.identity.v1';
const INTRO_ACTIVATION_TOOL_NAMES = new Set([
  'transfer_to_agent',
  'get_flood_extent',
  'get_population_at_risk',
]);

function shouldActivateIntroFromToolCall(toolName) {
  return typeof toolName === 'string' && INTRO_ACTIVATION_TOOL_NAMES.has(toolName);
}

function sanitizeRuntimeId(rawValue, fallbackPrefix) {
  if (typeof rawValue !== 'string') {
    return `${fallbackPrefix}-${Date.now().toString(36)}`;
  }
  const normalized = rawValue
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-_]/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!normalized) {
    return `${fallbackPrefix}-${Date.now().toString(36)}`;
  }
  return normalized.slice(0, 64);
}

function buildRuntimeSessionIdentity() {
  const randomToken = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    ? crypto.randomUUID().replace(/-/g, '')
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const compactToken = randomToken.slice(0, 16);
  return {
    userId: sanitizeRuntimeId(`user-${compactToken.slice(0, 8)}`, 'user'),
    sessionId: sanitizeRuntimeId(`session-${compactToken.slice(8) || compactToken.slice(0, 8)}`, 'session'),
  };
}

function getOrCreateRuntimeSessionIdentity() {
  if (typeof window === 'undefined') {
    return buildRuntimeSessionIdentity();
  }

  try {
    const rawStoredIdentity = window.sessionStorage.getItem(SESSION_IDENTITY_STORAGE_KEY);
    if (rawStoredIdentity) {
      const parsedIdentity = JSON.parse(rawStoredIdentity);
      const userId = sanitizeRuntimeId(parsedIdentity?.userId, 'user');
      const sessionId = sanitizeRuntimeId(parsedIdentity?.sessionId, 'session');
      const stableIdentity = { userId, sessionId };
      window.sessionStorage.setItem(SESSION_IDENTITY_STORAGE_KEY, JSON.stringify(stableIdentity));
      return stableIdentity;
    }
  } catch (error) {
    console.warn('[App] Failed reading runtime session identity from storage:', error);
  }

  const generatedIdentity = buildRuntimeSessionIdentity();
  try {
    window.sessionStorage.setItem(SESSION_IDENTITY_STORAGE_KEY, JSON.stringify(generatedIdentity));
  } catch (error) {
    console.warn('[App] Failed persisting runtime session identity:', error);
  }
  return generatedIdentity;
}

function appendWithRollingCap(previousEntries, nextEntry, cap) {
  const nextEntries = [...previousEntries, nextEntry];
  if (nextEntries.length <= cap) {
    return nextEntries;
  }
  return nextEntries.slice(nextEntries.length - cap);
}

function safeParseJson(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function pickFirstFiniteNumber(values) {
  for (const value of values) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function pickFirstNonEmptyString(values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

function formatToolName(toolName) {
  if (typeof toolName !== 'string' || toolName.trim().length === 0) {
    return 'Tool';
  }
  return toolName
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/^\w/, (match) => match.toUpperCase());
}

function formatToolDuration(durationMs) {
  if (!Number.isFinite(durationMs) || durationMs < 0) return '';
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}

function buildToolStatusIncidentEntry(event) {
  if (!event || typeof event !== 'object') return null;
  const status = typeof event.state === 'string'
    ? event.state.toLowerCase()
    : typeof event.status === 'string'
      ? event.status.toLowerCase()
      : '';
  if (!status) return null;

  const toolLabel = formatToolName(event.tool);
  const durationLabel = formatToolDuration(Number(event.duration_ms));
  const toolError = typeof event.error === 'string' && event.error.trim().length > 0
    ? event.error.trim()
    : null;

  if (status === 'pending') {
    return {
      id: `tool-${event.call_id || Date.now()}-pending`,
      severity: 'INFO',
      message: `${toolLabel}: pending`,
      timestamp: event.timestamp || Date.now(),
    };
  }
  if (status === 'running') {
    return {
      id: `tool-${event.call_id || Date.now()}-running`,
      severity: 'INFO',
      message: `${toolLabel}: running`,
      timestamp: event.timestamp || Date.now(),
    };
  }
  if (status === 'complete') {
    return {
      id: `tool-${event.call_id || Date.now()}-complete`,
      severity: 'LOW',
      message: `${toolLabel}: complete${durationLabel ? ` (${durationLabel})` : ''}`,
      timestamp: event.timestamp || Date.now(),
    };
  }
  if (status === 'error') {
    return {
      id: `tool-${event.call_id || Date.now()}-error`,
      severity: 'WARNING',
      message: `${toolLabel}: failed${toolError ? ` — ${toolError}` : ''}`,
      timestamp: event.timestamp || Date.now(),
    };
  }
  return null;
}

function normalizeToolStatusState(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().toLowerCase();
  if (['pending', 'running', 'complete', 'error'].includes(normalized)) {
    return normalized;
  }
  if (normalized === 'completed' || normalized === 'done' || normalized === 'success') {
    return 'complete';
  }
  if (normalized === 'failed') {
    return 'error';
  }
  return null;
}

function normalizeTimestampMs(value, fallbackValue = Date.now()) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim().length > 0) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallbackValue;
}

function upsertToolMissionRailEntry(previousEntries, event) {
  if (!event || typeof event !== 'object') return previousEntries;
  const normalizedState = normalizeToolStatusState(event.state ?? event.status);
  if (!normalizedState) return previousEntries;

  const timestamp = normalizeTimestampMs(event.timestamp, Date.now());
  const callId = pickFirstNonEmptyString([event.call_id, event.callId]);
  const toolLabel = formatToolName(event.tool);
  const rawDurationMs = pickFirstFiniteNumber([event.duration_ms, event.durationMs]);
  const durationMs = rawDurationMs !== null ? Math.max(0, rawDurationMs) : null;
  const error = pickFirstNonEmptyString([event.error]);
  const fallbackId = `${toolLabel.toLowerCase().replace(/\s+/g, '-')}-${timestamp}`;
  const stableId = callId || fallbackId;

  const entryIndex = previousEntries.findIndex((entry) => (
    (callId && entry.callId === callId) || entry.id === stableId
  ));
  const nextEntryBase = {
    id: stableId,
    callId: callId || null,
    tool: toolLabel,
    state: normalizedState,
    durationMs,
    error,
    timestamp,
  };

  if (entryIndex >= 0) {
    const existingEntry = previousEntries[entryIndex];
    const mergedEntry = {
      ...existingEntry,
      ...nextEntryBase,
      startedAt: existingEntry.startedAt || timestamp,
      updatedAt: timestamp,
      durationMs:
        durationMs !== null
          ? durationMs
          : Number.isFinite(existingEntry.durationMs)
            ? existingEntry.durationMs
            : null,
      error: error || existingEntry.error || null,
    };
    const withoutExisting = [...previousEntries];
    withoutExisting.splice(entryIndex, 1);
    return [...withoutExisting, mergedEntry];
  }

  const appended = [
    ...previousEntries,
    {
      ...nextEntryBase,
      startedAt: timestamp,
      updatedAt: timestamp,
    },
  ];
  if (appended.length <= TOOL_MISSION_RAIL_CAP) return appended;
  return appended.slice(appended.length - TOOL_MISSION_RAIL_CAP);
}

function formatCompactTokenValue(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return '--';
  if (parsed >= 1_000_000) return `${(parsed / 1_000_000).toFixed(2)}M`;
  if (parsed >= 10_000) return `${(parsed / 1_000).toFixed(1)}k`;
  return `${Math.round(parsed)}`;
}

function normalizeUsageTelemetryEvent(event) {
  if (!event || typeof event !== 'object') return null;

  const usage = event.usage && typeof event.usage === 'object' ? event.usage : {};
  const context = event.context && typeof event.context === 'object' ? event.context : {};
  const sessionHealth =
    event.session_health && typeof event.session_health === 'object'
      ? event.session_health
      : event.sessionHealth && typeof event.sessionHealth === 'object'
        ? event.sessionHealth
        : {};

  const inputTokens = pickFirstFiniteNumber([usage.input_tokens, usage.inputTokens]);
  const outputTokens = pickFirstFiniteNumber([usage.output_tokens, usage.outputTokens]);
  const totalTokens = pickFirstFiniteNumber([usage.total_tokens, usage.totalTokens]);
  const contextTokens = pickFirstFiniteNumber([context.context_tokens, context.contextTokens]);
  const utilizationRatioRaw = pickFirstFiniteNumber([
    context.utilization_ratio,
    context.utilizationRatio,
    sessionHealth.pressure_score,
    sessionHealth.pressureScore,
  ]);
  const utilizationRatio = utilizationRatioRaw !== null
    ? Math.min(Math.max(utilizationRatioRaw, 0), 1)
    : null;
  const pressure = pickFirstNonEmptyString([sessionHealth.pressure, sessionHealth.status]);
  const normalizedPressure = pressure ? pressure.toUpperCase() : null;

  const summaryParts = [];
  if (totalTokens !== null) {
    summaryParts.push(`TOK ${formatCompactTokenValue(totalTokens)}`);
  } else if (inputTokens !== null || outputTokens !== null) {
    const inValue = inputTokens !== null ? formatCompactTokenValue(inputTokens) : '--';
    const outValue = outputTokens !== null ? formatCompactTokenValue(outputTokens) : '--';
    summaryParts.push(`TOK IN ${inValue} OUT ${outValue}`);
  }
  if (utilizationRatio !== null) {
    summaryParts.push(`CTX ${(utilizationRatio * 100).toFixed(0)}%`);
  } else if (contextTokens !== null) {
    summaryParts.push(`CTX ${formatCompactTokenValue(contextTokens)}`);
  }
  if (normalizedPressure) {
    summaryParts.push(`PRESS ${normalizedPressure}`);
  }

  return {
    usage,
    context,
    sessionHealth,
    inputTokens,
    outputTokens,
    totalTokens,
    contextTokens,
    utilizationRatio,
    pressure: normalizedPressure,
    summary: summaryParts.join(' · ') || null,
    timestamp: event.timestamp || Date.now(),
  };
}

function normalizeGroundingUpdateEvent(event) {
  if (!event || typeof event !== 'object') return null;
  const rawCitations = Array.isArray(event.citations) ? event.citations : [];
  const citations = rawCitations
    .map((citation, index) => {
      if (!citation || typeof citation !== 'object') {
        return null;
      }
      const title = pickFirstNonEmptyString([
        citation.title,
        citation.label,
        citation.source,
        citation.url,
      ]) || `Source ${index + 1}`;
      const source = pickFirstNonEmptyString([citation.source, citation.publisher]);
      const snippet = pickFirstNonEmptyString([citation.snippet, citation.summary, citation.description]);
      const url = typeof citation.url === 'string' && /^https?:\/\//i.test(citation.url.trim())
        ? citation.url.trim()
        : null;
      return {
        id: citation.id || `citation-${Date.now()}-${index}`,
        title,
        source,
        snippet,
        url,
      };
    })
    .filter(Boolean);

  if (citations.length === 0) return null;

  const label = pickFirstNonEmptyString([event.label, event.tool]) || 'Grounding';
  const sourceCountRaw = pickFirstFiniteNumber([
    event.source_count,
    event.sourceCount,
    citations.length,
  ]);
  const sourceCount = sourceCountRaw !== null
    ? Math.max(1, Math.round(sourceCountRaw))
    : citations.length;
  const summary = pickFirstNonEmptyString([event.summary, event.response])
    || `${label}: ${sourceCount} source${sourceCount === 1 ? '' : 's'} linked.`;

  return {
    label,
    tool: pickFirstNonEmptyString([event.tool]),
    query: pickFirstNonEmptyString([event.query]),
    summary,
    grounded: event.grounded !== false,
    sourceCount,
    citations,
    timestamp: event.timestamp || Date.now(),
  };
}

function normalizeConfidencePct(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  const normalized = parsed <= 1 ? parsed * 100 : parsed;
  return Math.min(100, Math.max(0, Math.round(normalized)));
}

function buildFreshnessSignal(updatedAtValue) {
  const updatedAtMs = normalizeTimestampMs(updatedAtValue, Number.NaN);
  if (!Number.isFinite(updatedAtMs)) {
    return {
      label: 'NO CLOCK',
      state: 'unknown',
    };
  }

  const ageMs = Math.max(0, Date.now() - updatedAtMs);
  const ageMinutes = Math.round(ageMs / 60000);
  if (ageMinutes < 1) {
    return {
      label: 'LIVE',
      state: 'fresh',
    };
  }
  if (ageMinutes < 15) {
    return {
      label: `${ageMinutes}m ago`,
      state: 'fresh',
    };
  }
  if (ageMinutes < 60) {
    return {
      label: `${ageMinutes}m ago`,
      state: 'aging',
    };
  }

  const ageHours = Math.max(1, Math.round(ageMinutes / 60));
  return {
    label: `${ageHours}h ago`,
    state: 'stale',
  };
}

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

function resolveRuntimeGeoJsonUrl(urlValue) {
  if (typeof urlValue !== 'string' || urlValue.trim().length === 0) return null;
  try {
    return new URL(urlValue.trim(), `${resolveBackendHttpBase()}/`).toString();
  } catch {
    return urlValue.trim();
  }
}

function normalizeRouteOverlayStyle(style) {
  const sourceStyle = style && typeof style === 'object' ? style : {};
  const outlineColor = pickFirstNonEmptyString([
    sourceStyle.outlineColor,
    sourceStyle.strokeColor,
    sourceStyle.color,
  ]) || '#00ff88';
  const outlineWidth = pickFirstFiniteNumber([
    sourceStyle.outlineWidth,
    sourceStyle.strokeWidth,
    sourceStyle.width,
  ]) ?? 5;
  const opacity = pickFirstFiniteNumber([
    sourceStyle.opacity,
    sourceStyle.alpha,
  ]);
  const dashPattern = Array.isArray(sourceStyle.dashPattern)
    ? sourceStyle.dashPattern
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0)
      .slice(0, 2)
    : [];

  const normalizedStyle = {
    ...sourceStyle,
    isRoute: true,
    fillColor: sourceStyle.fillColor || '#00ff88',
    outlineColor,
    outlineWidth,
    opacity:
      opacity !== null
        ? Math.min(Math.max(opacity, 0.1), 1.0)
        : 0.95,
    glowPower: pickFirstFiniteNumber([sourceStyle.glowPower]) ?? 0.2,
  };
  if (dashPattern.length === 2) {
    normalizedStyle.dashPattern = dashPattern;
  } else {
    delete normalizedStyle.dashPattern;
  }
  return normalizedStyle;
}

function normalizeEvacuationZoneStyle(style) {
  const sourceStyle = style && typeof style === 'object' ? style : {};
  const fillColor = pickFirstNonEmptyString([
    sourceStyle.fillColor,
    sourceStyle.color,
  ]) || '#2dd4bf';
  const outlineColor = pickFirstNonEmptyString([
    sourceStyle.outlineColor,
    sourceStyle.strokeColor,
  ]) || '#22c55e';
  const opacity = pickFirstFiniteNumber([sourceStyle.opacity]) ?? 0.25;
  const outlineWidth = pickFirstFiniteNumber([
    sourceStyle.outlineWidth,
    sourceStyle.strokeWidth,
    sourceStyle.width,
  ]) ?? 2;

  return {
    ...sourceStyle,
    fillColor,
    outlineColor,
    outlineWidth,
    opacity: Math.min(Math.max(opacity, 0.05), 0.8),
    autoFocus: false,
  };
}

function normalizeConfidence(value) {
  if (typeof value !== 'string' || value.trim().length === 0) return null;
  return value.toUpperCase();
}

function toGrowthRatePctPerHour(value) {
  if (typeof value === 'number') return value;
  if (!value || typeof value !== 'object') return null;
  return pickFirstFiniteNumber([
    value.rate_pct_per_hour,
    value.growth_rate_pct_per_hour,
    value.growth_rate_pct,
  ]);
}

function isRuntimeRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getRuntimeLayerIdentity(layer, index, sourceTag) {
  if (layer == null) return `${sourceTag}-layer-${index}`;
  if (typeof layer === 'string' || typeof layer === 'number') {
    return String(layer);
  }
  if (!isRuntimeRecord(layer)) return `${sourceTag}-layer-${index}`;

  const preferredId = layer.layer_id || layer.id || layer.name || layer.label || layer.dataset;
  if (typeof preferredId === 'string' && preferredId.trim()) {
    return preferredId.trim().toLowerCase();
  }
  if (typeof preferredId === 'number') {
    return String(preferredId);
  }

  try {
    return JSON.stringify(layer);
  } catch {
    return `${sourceTag}-layer-${index}`;
  }
}

function mergeRuntimeLayers(previousLayers, incomingLayers) {
  const prior = Array.isArray(previousLayers) ? previousLayers : [];
  const incoming = Array.isArray(incomingLayers) ? incomingLayers : [];
  if (incoming.length === 0) return prior;

  const merged = [];
  const layerIndexById = new Map();

  const upsertLayer = (layer, isIncoming, index) => {
    const identity = getRuntimeLayerIdentity(layer, index, isIncoming ? 'next' : 'prev');
    const existingIndex = layerIndexById.get(identity);
    if (existingIndex === undefined) {
      layerIndexById.set(identity, merged.length);
      merged.push(layer);
      return;
    }

    const existingLayer = merged[existingIndex];
    if (isRuntimeRecord(existingLayer) && isRuntimeRecord(layer)) {
      merged[existingIndex] = { ...existingLayer, ...layer };
      return;
    }

    if (isIncoming) {
      merged[existingIndex] = layer;
    }
  };

  prior.forEach((layer, index) => upsertLayer(layer, false, index));
  incoming.forEach((layer, index) => upsertLayer(layer, true, index));

  return merged;
}

function mergeRuntimeRecord(previousValue, incomingValue) {
  const previousRecord = isRuntimeRecord(previousValue) ? previousValue : {};
  const incomingRecord = isRuntimeRecord(incomingValue) ? incomingValue : {};
  return {
    ...previousRecord,
    ...incomingRecord,
  };
}

function mergeTemporalFrames(previousFrames, incomingFrames) {
  const mergedFrames = mergeRuntimeRecord(previousFrames, null);
  if (!isRuntimeRecord(incomingFrames)) return mergedFrames;

  Object.entries(incomingFrames).forEach(([frameId, frameValue]) => {
    if (isRuntimeRecord(frameValue) && isRuntimeRecord(mergedFrames[frameId])) {
      mergedFrames[frameId] = {
        ...mergedFrames[frameId],
        ...frameValue,
      };
      return;
    }
    mergedFrames[frameId] = frameValue;
  });

  return mergedFrames;
}

function mergeTemporalPlaybackFrames(previousFrames, incomingFrames) {
  const prior = Array.isArray(previousFrames) ? previousFrames : [];
  const incoming = Array.isArray(incomingFrames) ? incomingFrames : [];
  if (incoming.length === 0) return prior;

  const merged = [];
  const frameIndexById = new Map();

  const upsertFrame = (frame, isIncoming, index) => {
    const normalizedFrameId = normalizeTemporalFrameId(
      frame?.frame_id || frame?.id || frame?.frameId
    );
    const fallbackId = `${isIncoming ? 'next' : 'prev'}-frame-${index}`;
    const frameIdentity = normalizedFrameId || fallbackId;

    const existingIndex = frameIndexById.get(frameIdentity);
    if (existingIndex === undefined) {
      frameIndexById.set(frameIdentity, merged.length);
      merged.push(frame);
      return;
    }

    const existingFrame = merged[existingIndex];
    if (isRuntimeRecord(existingFrame) && isRuntimeRecord(frame)) {
      merged[existingIndex] = { ...existingFrame, ...frame };
      return;
    }

    if (isIncoming) {
      merged[existingIndex] = frame;
    }
  };

  prior.forEach((frame, index) => upsertFrame(frame, false, index));
  incoming.forEach((frame, index) => upsertFrame(frame, true, index));
  return merged;
}

function mergeTemporalPlayback(previousPlayback, incomingPlayback) {
  const previousRecord = isRuntimeRecord(previousPlayback) ? previousPlayback : {};
  if (!isRuntimeRecord(incomingPlayback)) return previousRecord;

  const mergedPlayback = {
    ...previousRecord,
    ...incomingPlayback,
  };

  const orderedFrameIds = [];
  const seenFrameIds = new Set();
  const appendOrderedFrameIds = (candidateIds) => {
    if (!Array.isArray(candidateIds)) return;
    candidateIds.forEach((frameId) => {
      const normalizedFrameId = normalizeTemporalFrameId(frameId);
      const safeFrameId =
        normalizedFrameId || (typeof frameId === 'string' ? frameId.trim() : null);
      if (!safeFrameId || seenFrameIds.has(safeFrameId)) return;
      seenFrameIds.add(safeFrameId);
      orderedFrameIds.push(safeFrameId);
    });
  };

  appendOrderedFrameIds(previousRecord.ordered_frame_ids);
  appendOrderedFrameIds(incomingPlayback.ordered_frame_ids);
  if (orderedFrameIds.length > 0) {
    mergedPlayback.ordered_frame_ids = orderedFrameIds;
  }

  const mergedFrames = mergeTemporalPlaybackFrames(
    previousRecord.frames,
    incomingPlayback.frames,
  );
  if (mergedFrames.length > 0) {
    mergedPlayback.frames = mergedFrames;
  }

  return mergedPlayback;
}

const TEMPORAL_FRAME_ORDER = ['baseline', 'event', 'change'];
const COMPARISON_MODE_TO_FRAME_ID = {
  BEFORE: 'baseline',
  AFTER: 'event',
  CHANGE: 'change',
};
const FRAME_ID_TO_COMPARISON_MODE = {
  baseline: 'BEFORE',
  event: 'AFTER',
  change: 'CHANGE',
};

function normalizeTemporalFrameId(frameId) {
  if (typeof frameId !== 'string') return null;
  const normalized = frameId.trim().toLowerCase();
  if (normalized === 'before') return 'baseline';
  if (normalized === 'after') return 'event';
  return normalized.length > 0 ? normalized : null;
}

function normalizeComparisonMode(mode) {
  if (typeof mode !== 'string') return null;
  const normalized = mode.trim().toUpperCase();
  if (!normalized) return null;
  return Object.prototype.hasOwnProperty.call(COMPARISON_MODE_TO_FRAME_ID, normalized)
    ? normalized
    : null;
}

function getFrameIdForComparisonMode(mode) {
  const normalizedMode = normalizeComparisonMode(mode);
  if (!normalizedMode) return null;
  return COMPARISON_MODE_TO_FRAME_ID[normalizedMode] ?? null;
}

function getComparisonModeForFrameId(frameId) {
  const normalizedFrameId = normalizeTemporalFrameId(frameId);
  if (!normalizedFrameId) return null;
  return FRAME_ID_TO_COMPARISON_MODE[normalizedFrameId] ?? null;
}

function getTemporalFrameOrder(frameId) {
  const normalized = normalizeTemporalFrameId(frameId);
  if (!normalized) return TEMPORAL_FRAME_ORDER.length;
  const idx = TEMPORAL_FRAME_ORDER.indexOf(normalized);
  return idx >= 0 ? idx : TEMPORAL_FRAME_ORDER.length;
}

function resolveTimelineStepFrameId(step) {
  if (!step || typeof step !== 'object') return null;
  return normalizeTemporalFrameId(step.frameId || step.frame_id || step.id);
}

function findTimelineStepIndex(steps, frameId) {
  if (!Array.isArray(steps) || steps.length === 0) return -1;
  const normalizedFrameId = normalizeTemporalFrameId(frameId);
  if (!normalizedFrameId) return -1;
  return steps.findIndex((step) => resolveTimelineStepFrameId(step) === normalizedFrameId);
}

function inferComparisonModeFromToken(value) {
  if (typeof value !== 'string' || value.trim().length === 0) return null;
  const normalized = value.trim().toLowerCase();
  if (
    normalized.includes('baseline') ||
    normalized.includes('before') ||
    normalized.includes('pre_event') ||
    normalized.includes('pre-event')
  ) {
    return 'BEFORE';
  }
  if (
    normalized.includes('event') ||
    normalized.includes('after') ||
    normalized.includes('peak') ||
    normalized.includes('flood')
  ) {
    return 'AFTER';
  }
  if (
    normalized.includes('change') ||
    normalized.includes('delta') ||
    normalized.includes('difference') ||
    normalized.includes('progression')
  ) {
    return 'CHANGE';
  }
  return normalizeComparisonMode(value);
}

function inferComparisonModeForTimelineIndex(index, totalSteps) {
  if (!Number.isFinite(index) || !Number.isFinite(totalSteps) || totalSteps <= 0) {
    return null;
  }
  if (totalSteps === 1) return 'CHANGE';
  if (totalSteps === 2) {
    return index <= 0 ? 'BEFORE' : 'AFTER';
  }

  const clampedIndex = Math.min(Math.max(Math.trunc(index), 0), totalSteps - 1);
  const progress = clampedIndex / Math.max(totalSteps - 1, 1);
  if (progress < 0.34) return 'BEFORE';
  if (progress < 0.8) return 'AFTER';
  return 'CHANGE';
}

function getComparisonModeForTimelineStep(step, index, totalSteps) {
  const frameComparison = getComparisonModeForFrameId(resolveTimelineStepFrameId(step));
  if (frameComparison) return frameComparison;

  if (step && typeof step === 'object') {
    const candidates = [
      step.comparison,
      step.comparisonMode,
      step.kind,
      step.phase,
      step.stage,
      step.frameType,
      step.type,
      step.frameId,
      step.frame_id,
      step.id,
      step.label,
      step.caption,
    ];
    for (const candidate of candidates) {
      const inferred = inferComparisonModeFromToken(candidate);
      if (inferred) return inferred;
    }
  }

  return inferComparisonModeForTimelineIndex(index, totalSteps);
}

function findTimelineStepIndexForComparison(steps, comparisonMode) {
  if (!Array.isArray(steps) || steps.length === 0) return -1;
  const normalizedMode = normalizeComparisonMode(comparisonMode);
  if (!normalizedMode) return -1;

  const frameId = getFrameIdForComparisonMode(normalizedMode);
  const directIndex = frameId ? findTimelineStepIndex(steps, frameId) : -1;
  if (directIndex >= 0) return directIndex;

  return steps.findIndex((step, index) => (
    getComparisonModeForTimelineStep(step, index, steps.length) === normalizedMode
  ));
}

function normalizeReplaySignal(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return null;

  const absoluteValue = Math.abs(numericValue);
  if (absoluteValue <= 1) return Math.min(100, absoluteValue * 100);
  if (absoluteValue <= 100) return absoluteValue;
  return Math.min(100, Math.log10(absoluteValue + 1) * 35);
}

function humanizeFrameId(frameId) {
  if (!frameId || typeof frameId !== 'string') return 'Frame';
  return frameId
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function buildTimelineStepsFromTemporalFrames(temporalFrames) {
  if (!temporalFrames || typeof temporalFrames !== 'object') return [];

  const entries = Object.entries(temporalFrames).filter(([, frame]) => (
    frame && typeof frame === 'object'
  ));

  if (entries.length === 0) return [];

  return entries
    .sort(([frameA], [frameB]) => {
      const orderDiff = getTemporalFrameOrder(frameA) - getTemporalFrameOrder(frameB);
      return orderDiff !== 0 ? orderDiff : String(frameA).localeCompare(String(frameB));
    })
    .map(([frameId, frame]) => {
      const normalizedFrameId = normalizeTemporalFrameId(
        frame.frame_id || frame.id || frameId
      );
      const label = frame.name || humanizeFrameId(frameId);
      const caption =
        frame.window ||
        frame.timestamp ||
        frame.end_timestamp ||
        frame.start_timestamp ||
        `${label} frame`;

      return {
        id: frame.id || frame.frame_id || frameId,
        frameId: normalizedFrameId,
        label,
        caption,
      };
    });
}

function buildTimelineStepsFromTemporalPlayback(temporalPlayback, temporalFrames) {
  if (!temporalPlayback || typeof temporalPlayback !== 'object') return [];

  const playbackFrames = Array.isArray(temporalPlayback.frames)
    ? temporalPlayback.frames.filter((frame) => frame && typeof frame === 'object')
    : [];
  const orderedFrameIds = Array.isArray(temporalPlayback.ordered_frame_ids)
    ? temporalPlayback.ordered_frame_ids
        .map((frameId) => normalizeTemporalFrameId(frameId))
        .filter(Boolean)
    : [];

  if (playbackFrames.length === 0 && orderedFrameIds.length === 0) return [];

  const playbackFrameById = new Map();
  playbackFrames.forEach((frame) => {
    const normalizedFrameId = normalizeTemporalFrameId(frame.frame_id || frame.id);
    if (!normalizedFrameId || playbackFrameById.has(normalizedFrameId)) return;
    playbackFrameById.set(normalizedFrameId, frame);
  });

  const orderedFrames = [];
  if (orderedFrameIds.length > 0) {
    orderedFrameIds.forEach((frameId, index) => {
      const temporalFrame =
        temporalFrames && typeof temporalFrames === 'object'
          ? temporalFrames[frameId]
          : null;
      orderedFrames.push({
        frameId,
        frame: playbackFrameById.get(frameId) || temporalFrame || {},
        index,
      });
    });
  } else {
    playbackFrames
      .map((frame, index) => ({
        frameId: normalizeTemporalFrameId(frame.frame_id || frame.id),
        frame,
        index,
      }))
      .sort((a, b) => {
        const aIdx = Number(a.frame?.index);
        const bIdx = Number(b.frame?.index);
        if (Number.isFinite(aIdx) && Number.isFinite(bIdx) && aIdx !== bIdx) {
          return aIdx - bIdx;
        }
        const orderDiff = getTemporalFrameOrder(a.frameId) - getTemporalFrameOrder(b.frameId);
        return orderDiff !== 0 ? orderDiff : a.index - b.index;
      })
      .forEach((entry) => orderedFrames.push(entry));
  }

  return orderedFrames.map(({ frameId, frame, index }) => {
    const normalizedFrameId = normalizeTemporalFrameId(
      frame?.frame_id || frame?.id || frameId
    );
    const safeId = frame?.id || frame?.frame_id || frameId || `frame-${index + 1}`;
    const label =
      frame?.name ||
      humanizeFrameId(normalizedFrameId || safeId);
    const caption =
      frame?.window ||
      frame?.timestamp ||
      frame?.end_timestamp ||
      frame?.start_timestamp ||
      `${label} frame`;

    return {
      id: safeId,
      frameId: normalizedFrameId,
      label,
      caption,
    };
  });
}

function buildRuntimeTimelineSteps(temporalFrames, temporalPlayback) {
  const playbackSteps = buildTimelineStepsFromTemporalPlayback(
    temporalPlayback,
    temporalFrames,
  );
  if (playbackSteps.length > 0) return playbackSteps;
  return buildTimelineStepsFromTemporalFrames(temporalFrames);
}

function getRuntimePreferredFrameId(temporalPlayback, temporalSummary) {
  const candidates = [
    temporalPlayback?.default_frame_id,
    temporalSummary?.latest_frame_id,
    temporalSummary?.default_frame_id,
  ];

  for (const candidate of candidates) {
    const normalized = normalizeTemporalFrameId(candidate);
    if (normalized) return normalized;
  }

  const orderedFrameIds = Array.isArray(temporalPlayback?.ordered_frame_ids)
    ? temporalPlayback.ordered_frame_ids
    : [];
  if (orderedFrameIds.length > 0) {
    return normalizeTemporalFrameId(orderedFrameIds[orderedFrameIds.length - 1]);
  }

  return null;
}

function getTemporalFrameWindow(temporalFrames, frameKeys) {
  if (!temporalFrames || typeof temporalFrames !== 'object') return null;
  for (const frameKey of frameKeys) {
    const frame = temporalFrames[frameKey];
    if (!frame || typeof frame !== 'object') continue;
    if (typeof frame.window === 'string' && frame.window.trim()) return frame.window;
  }
  return null;
}

function normalizeRuntimeConfidenceLabel(value) {
  if (!value) return null;
  if (typeof value === 'string') return normalizeConfidence(value);
  if (typeof value === 'object' && typeof value.label === 'string') {
    return normalizeConfidence(value.label);
  }
  return null;
}

const STATIC_PROVENANCE = safeParseJson(analysisProvenanceRaw);
const STATIC_FLOOD_GEOJSON = safeParseJson(floodExtentRaw);

function buildEarthEngineSeedData() {
  const areaSqKm = pickFirstFiniteNumber([
    STATIC_PROVENANCE?.estimated_area_sqkm,
    STATIC_PROVENANCE?.total_vector_area_sqkm,
    STATIC_PROVENANCE?.flood_area_sqkm,
    STATIC_FLOOD_GEOJSON?.properties?.flood_area_sqkm,
    STATIC_FLOOD_GEOJSON?.features?.[0]?.properties?.flood_area_sqkm,
  ]);

  const baselineWindow =
    STATIC_PROVENANCE?.baseline_window ||
    STATIC_PROVENANCE?.baseline_period ||
    STATIC_FLOOD_GEOJSON?.properties?.baseline_period ||
    null;

  const acquisitionWindow =
    STATIC_PROVENANCE?.event_window ||
    STATIC_PROVENANCE?.acquisition_period ||
    STATIC_FLOOD_GEOJSON?.properties?.flood_period ||
    STATIC_FLOOD_GEOJSON?.features?.[0]?.properties?.acquisition ||
    null;

  const updatedAt =
    STATIC_PROVENANCE?.generated_at ||
    STATIC_PROVENANCE?.computed_at ||
    STATIC_FLOOD_GEOJSON?.properties?.computed_at ||
    null;

  return {
    metrics: {
      floodAreaSqKm: areaSqKm ?? MOCK_EARTH_ENGINE.metrics.floodAreaSqKm,
    },
    provenance: {
      source:
        STATIC_PROVENANCE?.source_dataset ||
        STATIC_PROVENANCE?.source ||
        MOCK_EARTH_ENGINE.provenance.source,
      sourceDetail:
        STATIC_PROVENANCE?.source_dataset_detail ||
        STATIC_PROVENANCE?.source_detail ||
        STATIC_PROVENANCE?.source_dataset ||
        STATIC_PROVENANCE?.source ||
        MOCK_EARTH_ENGINE.provenance.sourceDetail,
      acquisitionWindow:
        acquisitionWindow || MOCK_EARTH_ENGINE.provenance.acquisitionWindow,
      baselineWindow:
        baselineWindow || MOCK_EARTH_ENGINE.provenance.baselineWindow,
      method: STATIC_PROVENANCE?.method || MOCK_EARTH_ENGINE.provenance.method,
      confidence:
        normalizeConfidence(STATIC_PROVENANCE?.confidence) ||
        MOCK_EARTH_ENGINE.provenance.confidence,
      status: STATIC_PROVENANCE ? 'LIVE' : MOCK_EARTH_ENGINE.provenance.status,
      updatedAt: updatedAt || MOCK_EARTH_ENGINE.provenance.updatedAt,
      sidecar: STATIC_PROVENANCE
        ? 'analysis_provenance.json'
        : MOCK_EARTH_ENGINE.provenance.sidecar,
    },
    timeline: {
      beforeLabel: baselineWindow || MOCK_EARTH_ENGINE.timeline.beforeLabel,
      afterLabel: acquisitionWindow || MOCK_EARTH_ENGINE.timeline.afterLabel,
    },
  };
}

const EARTH_ENGINE_SEED = buildEarthEngineSeedData();

function getShellSignals() {
  const isFieldMode = window.innerWidth <= 1280;
  const connection = navigator.connection;
  const lowBandwidth =
    Boolean(connection?.saveData) ||
    ['slow-2g', '2g', '3g'].includes(connection?.effectiveType ?? '');

  return { isFieldMode, lowBandwidth };
}

// ── Default layer toggle state ──────────────────────────────────
const DEFAULT_LAYER_TOGGLES = {
  FLOOD_EXTENT: true,
  TRIAGE_ZONES: false,
  INFRASTRUCTURE: false,
  EVACUATION_ROUTES: false,
  POPULATION_DENSITY: false,
  HISTORICAL_FLOODS: false,
  THREAT_RADIUS: false,
  STORM_EFFECT: false,
  FLOOD_REPLAY: false,
};

export default function App() {
  const [shellSignals, setShellSignals] = useState(getShellSignals);
  const [operationalMode, setOperationalMode] = useState(OPERATIONAL_MODES.ALERT);
  const [waterLevel, setWaterLevel] = useState(4.1);
  const [populationAtRisk, setPopulationAtRisk] = useState(47000);
  const [transcripts, setTranscripts] = useState([]);
  const [incidentEntries, setIncidentEntries] = useState([]);
  const [mapCommands, setMapCommands] = useState([]);
  const [missionStarted, setMissionStarted] = useState(false);
  const [selectedMission, setSelectedMission] = useState(null); // null | 'jakarta' | 'washington'
  const [videoPhase, setVideoPhase] = useState(null); // null | 'intro' | 'active'
  const [reconFeed, setReconFeed] = useState(() => ({ ...MOCK_RECON_FEED }));
  const [chartData, setChartData] = useState({});
  const [pointAnalytics, setPointAnalytics] = useState(null); // { lat, lng, loading, data, error }
  
  // ── New state for strategic view features ───────────────────
  const [layerToggles, setLayerToggles] = useState(DEFAULT_LAYER_TOGGLES);
  const [cameraMode, setCameraMode] = useState(null);
  const [showScanlines, setShowScanlines] = useState(true);
  const [showPip, setShowPip] = useState(false);
  const [hudNotification, setHudNotification] = useState(null);
  const [sessionStartTime] = useState(() => Date.now());
  const [demoRunning, setDemoRunning] = useState(false);
  const [earthEngineOverrides, setEarthEngineOverrides] = useState(
    () => EARTH_ENGINE_SEED
  );
  const [earthEngineRuntime, setEarthEngineRuntime] = useState(() => ({
    eeRuntime: null,
    runtimeLayers: [],
    temporalFrames: {},
    temporalPlayback: {},
    temporalSummary: {},
    multisensorFusion: {},
    floodGeojson: null,
    floodGeojsonUrl: null,
    runtimeState: null,
    liveAnalysisTask: null,
  }));
  const [evacuationRouteRuntime, setEvacuationRouteRuntime] = useState(null);
  const [temporalControl, setTemporalControl] = useState(() => ({
    activeFrameIndex: (() => (
      Number.isFinite(MOCK_EARTH_ENGINE.timeSliderIndex)
        ? Math.max(0, Math.trunc(MOCK_EARTH_ENGINE.timeSliderIndex))
        : 0
    ))(),
    activeFrameId: (() => {
      const initialIndex = Number.isFinite(MOCK_EARTH_ENGINE.timeSliderIndex)
        ? Math.max(0, Math.trunc(MOCK_EARTH_ENGINE.timeSliderIndex))
        : 0;
      return normalizeTemporalFrameId(
        MOCK_EARTH_ENGINE.timeline?.steps?.[initialIndex]?.frameId ||
        MOCK_EARTH_ENGINE.timeline?.steps?.[initialIndex]?.frame_id ||
        MOCK_EARTH_ENGINE.timeline?.steps?.[initialIndex]?.id ||
        MOCK_EARTH_ENGINE.timeline?.steps?.[0]?.frameId ||
        MOCK_EARTH_ENGINE.timeline?.steps?.[0]?.frame_id ||
        MOCK_EARTH_ENGINE.timeline?.steps?.[0]?.id
      );
    })(),
    activeComparison:
      normalizeComparisonMode(MOCK_EARTH_ENGINE.activeComparison) ||
      'AFTER',
  }));
  const [incidentReplayState, setIncidentReplayState] = useState(() => ({
    isPlaying: false,
    speed: 1,
  }));
  const [usageDiagnostics, setUsageDiagnostics] = useState(() => ({
    summary: null,
    pressure: null,
    totalTokens: null,
    contextTokens: null,
    utilizationRatio: null,
    timestamp: null,
  }));
  const [videoStreamingEnabled, setVideoStreamingEnabled] = useState(false);
  const [videoCadenceFps, setVideoCadenceFps] = useState(DEFAULT_VIDEO_CADENCE_FPS);
  const [videoStreamStatus, setVideoStreamStatus] = useState(() => ({
    state: 'idle',
    sentFrames: 0,
    droppedFrames: 0,
    lastFrameAt: null,
    lastError: null,
  }));
  const [latestGroundingSourceCount, setLatestGroundingSourceCount] = useState(0);
  const [latestGroundingTelemetry, setLatestGroundingTelemetry] = useState(() => ({
    label: null,
    sourceCount: 0,
    timestamp: null,
  }));
  const [toolMissionRail, setToolMissionRail] = useState([]);
  const [demoController, setDemoController] = useState(null);
  const [directorModeEnabled, setDirectorModeEnabled] = useState(false);
  const [directorFallbackState, setDirectorFallbackState] = useState(() => ({
    active: false,
    reason: null,
    since: null,
  }));

  const globeRef = useRef(null);
  const audioCallbackRef = useRef(null);
  const hudTimeoutRef = useRef(null);
  const processedEventSeqRef = useRef(0);
  const videoCaptureInFlightRef = useRef(false);
  const autoVideoEnableOnRecordingRef = useRef(false);
  const lastUsagePressureRef = useRef(null);
  const demoAutoStopTimeoutRef = useRef(null);
  const analyticsDebounceRef = useRef(null);
  const analyticsFetchedRegionRef = useRef(null);
  const pointClickAbortRef = useRef(null);
  const activePointMarkerRef = useRef(null);
  const routeContextSignatureRef = useRef('');
  const videoPhaseRef = useRef(videoPhase);
  videoPhaseRef.current = videoPhase;
  const activateHawkEye = useCallback((source = 'unknown') => {
    console.log('[HawkEye] Activating mission control via', source);
    setVideoPhase((prev) => (prev === 'active' ? prev : 'active'));
  }, []);
  const runtimeSessionIdentity = useMemo(() => getOrCreateRuntimeSessionIdentity(), []);
  const manualActivitySignalsEnabled = useMemo(() => {
    const raw = import.meta.env.VITE_ENABLE_MANUAL_ACTIVITY_SIGNALS;
    if (typeof raw !== 'string') {
      return false;
    }
    const normalized = raw.trim().toLowerCase();
    return ['1', 'true', 'yes', 'on'].includes(normalized);
  }, []);

  // WebSocket connection
  const {
    connectionStatus,
    connectionHealth,
    events,
    sendAudio,
    sendAudioWithStatus,
    sendTextWithStatus,
    sendVideoFrame,
    sendModeChange,
    sendContextUpdate,
    sendActivityStart,
    sendActivityEnd,
    sendAudioStreamEnd,
    sendScreenshotResponse,
  } = useHawkEyeSocket(
    runtimeSessionIdentity.userId,
    runtimeSessionIdentity.sessionId,
    audioCallbackRef,
  );

  const appendTranscript = useCallback((entry) => {
    setTranscripts((prev) => appendWithRollingCap(prev, entry, LIVE_TRANSCRIPTS_CAP));
  }, []);

  const appendIncidentEntry = useCallback((entry) => {
    setIncidentEntries((prev) => appendWithRollingCap(prev, entry, LIVE_INCIDENT_ENTRIES_CAP));
  }, []);

  const appendMapCommand = useCallback((entry) => {
    setMapCommands((prev) => appendWithRollingCap(prev, entry, LIVE_MAP_COMMANDS_CAP));
  }, []);

  const clearDemoAutoStopTimer = useCallback(() => {
    if (demoAutoStopTimeoutRef.current) {
      window.clearTimeout(demoAutoStopTimeoutRef.current);
      demoAutoStopTimeoutRef.current = null;
    }
  }, []);

  const startDemoSimulationPlayback = useCallback((source, options = {}) => {
    const autoStopMs = Number(options.autoStopMs);
    clearDemoAutoStopTimer();
    if (window.startDemoSimulation) {
      void window.startDemoSimulation();
    }
    setDemoController(source);
    setDemoRunning(true);
    if (Number.isFinite(autoStopMs) && autoStopMs > 0) {
      demoAutoStopTimeoutRef.current = window.setTimeout(() => {
        if (window.stopDemoSimulation) {
          window.stopDemoSimulation();
        }
        setDemoRunning(false);
        setDemoController(null);
        demoAutoStopTimeoutRef.current = null;
      }, autoStopMs);
    }
  }, [clearDemoAutoStopTimer]);

  const stopDemoSimulationPlayback = useCallback(() => {
    clearDemoAutoStopTimer();
    if (window.stopDemoSimulation) {
      window.stopDemoSimulation();
    }
    setDemoRunning(false);
    setDemoController(null);
  }, [clearDemoAutoStopTimer]);

  const trackToolMissionStatus = useCallback((event) => {
    setToolMissionRail((prev) => upsertToolMissionRailEntry(prev, event));
  }, []);

  // Audio pipeline — wired to real WebSocket sendAudio
  const {
    isRecording,
    isPlaying,
    isInputMuted,
    error: audioPipelineError,
    micStatus,
    startRecording,
    toggleRecording,
    playAudio,
    prepareAudio,
    handleTurnComplete,
    handleInterrupted,
  } = useAudioPipeline({
    sendAudio,
    sendAudioWithStatus,
    sendActivityStart,
    sendActivityEnd,
    sendAudioStreamEnd,
  }, {
    manualActivitySignalsEnabled,
    connectionStatus,
  });

  // Wire received audio from backend to audio player
  useEffect(() => {
    audioCallbackRef.current = playAudio;
    console.log('[APP] audioCallbackRef.current set to playAudio function');
  }, [playAudio]);

  useEffect(() => {
    if (!hudNotification) return undefined;

    if (hudTimeoutRef.current) {
      clearTimeout(hudTimeoutRef.current);
    }

    hudTimeoutRef.current = setTimeout(() => {
      setHudNotification(null);
    }, 3000);

    return () => {
      if (hudTimeoutRef.current) {
        clearTimeout(hudTimeoutRef.current);
      }
    };
  }, [hudNotification]);

  useEffect(() => (
    () => {
      clearDemoAutoStopTimer();
    }
  ), [clearDemoAutoStopTimer]);

  // ── STORM_EFFECT toggle → globe atmosphere shader ──
  useEffect(() => {
    const globe = globeRef.current;
    if (!globe || !globe.setAtmosphere) return;
    if (layerToggles.STORM_EFFECT) {
      globe.setAtmosphere('storm');
    } else {
      globe.setAtmosphere('clear');
    }
  }, [layerToggles.STORM_EFFECT]);

  const runtimeTimelineSteps = useMemo(
    () => buildRuntimeTimelineSteps(
      earthEngineRuntime.temporalFrames,
      earthEngineRuntime.temporalPlayback,
    ),
    [earthEngineRuntime.temporalFrames, earthEngineRuntime.temporalPlayback],
  );

  const resolvedTimelineSteps = useMemo(() => {
    const overrideTimelineSteps = earthEngineOverrides.timeline?.steps;
    if (Array.isArray(overrideTimelineSteps) && overrideTimelineSteps.length > 0) {
      return overrideTimelineSteps;
    }
    if (runtimeTimelineSteps.length > 0) {
      return runtimeTimelineSteps;
    }
    return Array.isArray(MOCK_EARTH_ENGINE.timeline?.steps)
      ? MOCK_EARTH_ENGINE.timeline.steps
      : [];
  }, [earthEngineOverrides.timeline, runtimeTimelineSteps]);

  const runtimePreferredFrameId = useMemo(
    () => getRuntimePreferredFrameId(
      earthEngineRuntime.temporalPlayback,
      earthEngineRuntime.temporalSummary,
    ),
    [earthEngineRuntime.temporalPlayback, earthEngineRuntime.temporalSummary],
  );

  const incidentReplayTimeline = useMemo(() => {
    const timelineSteps = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps : [];
    if (timelineSteps.length === 0) {
      return {
        track: [],
        hotspots: [],
      };
    }

    const temporalFrames =
      earthEngineRuntime.temporalFrames && typeof earthEngineRuntime.temporalFrames === 'object'
        ? earthEngineRuntime.temporalFrames
        : {};
    const playbackFrames = Array.isArray(earthEngineRuntime.temporalPlayback?.frames)
      ? earthEngineRuntime.temporalPlayback.frames
      : [];
    const playbackFrameById = new Map();
    playbackFrames.forEach((frame) => {
      const normalizedFrameId = normalizeTemporalFrameId(frame?.frame_id || frame?.id);
      if (!normalizedFrameId || playbackFrameById.has(normalizedFrameId)) return;
      playbackFrameById.set(normalizedFrameId, frame);
    });

    const resolveTemporalFrameRecord = (frameId) => {
      if (!frameId || !temporalFrames || typeof temporalFrames !== 'object') return null;
      if (temporalFrames[frameId] && typeof temporalFrames[frameId] === 'object') {
        return temporalFrames[frameId];
      }
      const matchedEntry = Object.entries(temporalFrames).find(([key]) => (
        normalizeTemporalFrameId(key) === frameId
      ));
      if (!matchedEntry) return null;
      const [, value] = matchedEntry;
      return value && typeof value === 'object' ? value : null;
    };

    const track = timelineSteps.map((step, index) => {
      const comparisonMode = getComparisonModeForTimelineStep(step, index, timelineSteps.length) || 'AFTER';
      const resolvedStepFrameId = normalizeTemporalFrameId(
        resolveTimelineStepFrameId(step) ||
        normalizeTemporalFrameId(step?.id)
      );
      const comparisonFrameId = getFrameIdForComparisonMode(comparisonMode);
      const frameId = resolvedStepFrameId || comparisonFrameId || `frame-${index + 1}`;
      const temporalFrameRecord = resolveTemporalFrameRecord(frameId);
      const playbackFrameRecord =
        playbackFrameById.get(frameId) ||
        (comparisonFrameId ? playbackFrameById.get(comparisonFrameId) : null) ||
        null;

      const signalCandidates = [
        temporalFrameRecord?.risk_score,
        temporalFrameRecord?.severity_score,
        temporalFrameRecord?.impact_score,
        temporalFrameRecord?.flood_area_sqkm,
        temporalFrameRecord?.population_at_risk,
        temporalFrameRecord?.delta_area_pct,
        temporalFrameRecord?.change_pct,
        playbackFrameRecord?.risk_score,
        playbackFrameRecord?.severity_score,
        playbackFrameRecord?.impact_score,
        playbackFrameRecord?.flood_area_sqkm,
        playbackFrameRecord?.population_at_risk,
        playbackFrameRecord?.delta_area_pct,
        playbackFrameRecord?.change_pct,
      ];
      const normalizedSignals = signalCandidates
        .map((value) => normalizeReplaySignal(value))
        .filter((value) => value !== null);
      const intensityScore = normalizedSignals.length > 0
        ? Math.max(...normalizedSignals)
        : (
          normalizeReplaySignal((index + 1) / Math.max(timelineSteps.length, 1)) ||
          0
        );

      const explicitHotspot = Boolean(
        temporalFrameRecord?.is_hotspot ||
        temporalFrameRecord?.hotspot ||
        playbackFrameRecord?.is_hotspot ||
        playbackFrameRecord?.hotspot ||
        temporalFrameRecord?.priority === 'HIGH' ||
        playbackFrameRecord?.priority === 'HIGH'
      );
      const reason = pickFirstNonEmptyString([
        temporalFrameRecord?.hotspot_reason,
        playbackFrameRecord?.hotspot_reason,
        temporalFrameRecord?.summary,
        playbackFrameRecord?.summary,
        step?.caption,
      ]) || `${comparisonMode} frame progression`;

      return {
        index,
        id: step?.id || `timeline-step-${index + 1}`,
        frameId,
        comparisonMode,
        label: step?.label || humanizeFrameId(frameId),
        caption: step?.caption || `${comparisonMode} frame`,
        reason,
        intensityScore,
        explicitHotspot,
      };
    });

    const rankedTrack = [...track].sort((a, b) => (
      b.intensityScore - a.intensityScore || a.index - b.index
    ));
    const explicitHotspots = track.filter((entry) => entry.explicitHotspot);
    const fallbackHotspotCount = Math.min(track.length > 4 ? 3 : 2, track.length);
    const fallbackHotspots = rankedTrack.slice(0, fallbackHotspotCount);
    const selectedHotspots = explicitHotspots.length > 0
      ? [...explicitHotspots]
      : [...fallbackHotspots];
    const progressionStep = track.find((entry) => entry.comparisonMode === 'CHANGE');
    if (
      progressionStep &&
      !selectedHotspots.some((entry) => entry.index === progressionStep.index)
    ) {
      selectedHotspots.push(progressionStep);
    }

    const hotspotIndices = [...new Set(selectedHotspots.map((entry) => entry.index))]
      .sort((a, b) => a - b);
    const hotspotSet = new Set(hotspotIndices);

    return {
      track: track.map((entry) => ({
        ...entry,
        hotspot: hotspotSet.has(entry.index),
      })),
      hotspots: hotspotIndices
        .map((hotspotIndex) => track.find((entry) => entry.index === hotspotIndex))
        .filter(Boolean),
    };
  }, [resolvedTimelineSteps, earthEngineRuntime.temporalFrames, earthEngineRuntime.temporalPlayback]);

  useEffect(() => {
    const timelineSteps = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps : [];
    const maxIndex = Math.max(timelineSteps.length - 1, 0);

    setTemporalControl((prev) => {
      const prevIndex = Number.isFinite(prev.activeFrameIndex)
        ? Math.min(Math.max(Math.trunc(prev.activeFrameIndex), 0), maxIndex)
        : 0;
      const previousComparison =
        normalizeComparisonMode(prev.activeComparison) ||
        getComparisonModeForFrameId(prev.activeFrameId) ||
        getComparisonModeForTimelineStep(
          timelineSteps[prevIndex],
          prevIndex,
          timelineSteps.length,
        ) ||
        normalizeComparisonMode(MOCK_EARTH_ENGINE.activeComparison) ||
        'AFTER';

      if (timelineSteps.length === 0) {
        if (
          prev.activeFrameIndex === 0 &&
          prev.activeFrameId === null &&
          prev.activeComparison === previousComparison
        ) {
          return prev;
        }
        return {
          activeFrameIndex: 0,
          activeFrameId: null,
          activeComparison: previousComparison,
        };
      }

      const clampedIndex = Number.isFinite(prev.activeFrameIndex)
        ? Math.min(Math.max(Math.trunc(prev.activeFrameIndex), 0), maxIndex)
        : maxIndex;
      const selectedFrameIndex = findTimelineStepIndex(timelineSteps, prev.activeFrameId);

      let nextIndex = selectedFrameIndex >= 0 ? selectedFrameIndex : clampedIndex;
      let nextFrameId =
        resolveTimelineStepFrameId(timelineSteps[nextIndex]) ||
        normalizeTemporalFrameId(timelineSteps[nextIndex]?.id);

      if (selectedFrameIndex < 0 && runtimePreferredFrameId) {
        const preferredFrameIndex = findTimelineStepIndex(timelineSteps, runtimePreferredFrameId);
        if (preferredFrameIndex >= 0) {
          nextIndex = preferredFrameIndex;
          nextFrameId =
            resolveTimelineStepFrameId(timelineSteps[preferredFrameIndex]) ||
            runtimePreferredFrameId;
        }
      }

      if (!nextFrameId) {
        nextIndex = 0;
        nextFrameId =
          resolveTimelineStepFrameId(timelineSteps[0]) ||
          normalizeTemporalFrameId(timelineSteps[0]?.id);
      }

      const nextComparison =
        getComparisonModeForTimelineStep(timelineSteps[nextIndex], nextIndex, timelineSteps.length) ||
        getComparisonModeForFrameId(nextFrameId) ||
        previousComparison;
      if (
        prev.activeFrameIndex === nextIndex &&
        prev.activeFrameId === nextFrameId &&
        prev.activeComparison === nextComparison
      ) {
        return prev;
      }

      return {
        activeFrameIndex: nextIndex,
        activeFrameId: nextFrameId,
        activeComparison: nextComparison,
      };
    });
  }, [resolvedTimelineSteps, runtimePreferredFrameId]);

  const handleTemporalFrameChange = useCallback((nextFrameIndex, options = {}) => {
    if (!options?.fromReplay) {
      setIncidentReplayState((prev) => (
        prev.isPlaying
          ? { ...prev, isPlaying: false }
          : prev
      ));
    }

    setTemporalControl((prev) => {
      const timelineSteps = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps : [];
      const maxIndex = Math.max(timelineSteps.length - 1, 0);
      const parsedIndex = Number(nextFrameIndex);
      const safeIndex = Number.isFinite(parsedIndex)
        ? Math.min(Math.max(Math.trunc(parsedIndex), 0), maxIndex)
        : 0;
      const nextFrameId = timelineSteps.length > 0
        ? (
          resolveTimelineStepFrameId(timelineSteps[safeIndex]) ||
          normalizeTemporalFrameId(timelineSteps[safeIndex]?.id)
        )
        : null;
      const nextComparison =
        getComparisonModeForTimelineStep(timelineSteps[safeIndex], safeIndex, timelineSteps.length) ||
        getComparisonModeForFrameId(nextFrameId) ||
        normalizeComparisonMode(prev.activeComparison) ||
        'AFTER';

      if (
        prev.activeFrameIndex === safeIndex &&
        prev.activeFrameId === nextFrameId &&
        prev.activeComparison === nextComparison
      ) {
        return prev;
      }

      return {
        activeFrameIndex: safeIndex,
        activeFrameId: nextFrameId,
        activeComparison: nextComparison,
      };
    });
  }, [resolvedTimelineSteps]);

  const handleTemporalComparisonChange = useCallback((mode) => {
    const normalizedMode = normalizeComparisonMode(mode);
    if (!normalizedMode) return;

    setIncidentReplayState((prev) => (
      prev.isPlaying
        ? { ...prev, isPlaying: false }
        : prev
    ));

    setTemporalControl((prev) => {
      const timelineSteps = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps : [];
      const targetFrameId = getFrameIdForComparisonMode(normalizedMode);
      const directFrameIndex = targetFrameId
        ? findTimelineStepIndex(timelineSteps, targetFrameId)
        : -1;
      const targetFrameIndex = directFrameIndex >= 0
        ? directFrameIndex
        : findTimelineStepIndexForComparison(timelineSteps, normalizedMode);

      if (targetFrameIndex >= 0) {
        const resolvedFrameId =
          resolveTimelineStepFrameId(timelineSteps[targetFrameIndex]) ||
          normalizeTemporalFrameId(timelineSteps[targetFrameIndex]?.id) ||
          targetFrameId;

        if (
          prev.activeComparison === normalizedMode &&
          prev.activeFrameIndex === targetFrameIndex &&
          prev.activeFrameId === resolvedFrameId
        ) {
          return prev;
        }
        return {
          activeComparison: normalizedMode,
          activeFrameIndex: targetFrameIndex,
          activeFrameId: resolvedFrameId,
        };
      }

      if (prev.activeComparison === normalizedMode) {
        return prev;
      }

      return {
        ...prev,
        activeComparison: normalizedMode,
      };
    });
  }, [resolvedTimelineSteps]);

  const handleReplayToggle = useCallback(() => {
    setIncidentReplayState((prev) => {
      const timelineLength = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps.length : 0;
      if (timelineLength <= 1) {
        return prev.isPlaying
          ? { ...prev, isPlaying: false }
          : prev;
      }
      return {
        ...prev,
        isPlaying: !prev.isPlaying,
      };
    });
  }, [resolvedTimelineSteps]);

  const handleReplaySpeedChange = useCallback((nextSpeed) => {
    const parsedSpeed = Number(nextSpeed);
    if (!Number.isFinite(parsedSpeed)) return;

    const normalizedSpeed = parsedSpeed <= 0.75
      ? 0.5
      : parsedSpeed <= 1.5
        ? 1
        : parsedSpeed <= 3
          ? 2
          : 4;

    setIncidentReplayState((prev) => (
      prev.speed === normalizedSpeed
        ? prev
        : { ...prev, speed: normalizedSpeed }
    ));
  }, []);

  const handleReplaySeek = useCallback((nextFrameIndex, options = {}) => {
    handleTemporalFrameChange(nextFrameIndex, options);
  }, [handleTemporalFrameChange]);

  const handleReplayJumpToHotspot = useCallback((nextFrameIndex) => {
    handleReplaySeek(nextFrameIndex);
    const replayStep = incidentReplayTimeline.track[nextFrameIndex];
    if (replayStep?.label) {
      setHudNotification(`REPLAY HOTSPOT: ${String(replayStep.label).toUpperCase()}`);
    }
  }, [handleReplaySeek, incidentReplayTimeline.track]);

  useEffect(() => {
    if (!incidentReplayState.isPlaying) return undefined;

    const timelineSteps = Array.isArray(resolvedTimelineSteps) ? resolvedTimelineSteps : [];
    if (timelineSteps.length <= 1) {
      setIncidentReplayState((prev) => (
        prev.isPlaying
          ? { ...prev, isPlaying: false }
          : prev
      ));
      return undefined;
    }

    const playbackDelayMs = Math.max(
      500,
      Math.round(1700 / Math.max(0.5, incidentReplayState.speed)),
    );
    const timerId = window.setTimeout(() => {
      const maxIndex = timelineSteps.length - 1;
      const currentIndex = Number.isFinite(temporalControl.activeFrameIndex)
        ? Math.min(Math.max(Math.trunc(temporalControl.activeFrameIndex), 0), maxIndex)
        : 0;
      const nextIndex = currentIndex >= maxIndex ? 0 : currentIndex + 1;
      handleTemporalFrameChange(nextIndex, { fromReplay: true });
    }, playbackDelayMs);

    return () => window.clearTimeout(timerId);
  }, [
    incidentReplayState.isPlaying,
    incidentReplayState.speed,
    resolvedTimelineSteps,
    temporalControl.activeFrameIndex,
    handleTemporalFrameChange,
  ]);

  // ── Layer toggle callback ───────────────────────────────────
  const handleToggleLayer = useCallback((layerId) => {
    const enablingEvacuationRoutes =
      layerId === 'EVACUATION_ROUTES' &&
      !layerToggles.EVACUATION_ROUTES;

    setLayerToggles((prev) => ({ ...prev, [layerId]: !prev[layerId] }));

    if (enablingEvacuationRoutes && !evacuationRouteRuntime) {
      const delivery = sendTextWithStatus(DEFAULT_EVACUATION_ROUTE_COMMAND);
      if (delivery.status === 'sent') {
        setHudNotification('REQUESTING EVACUATION ROUTE...');
      } else if (delivery.status === 'queued') {
        setHudNotification('LINK UNSTABLE - ROUTE REQUEST QUEUED');
      } else {
        setHudNotification('BACKEND OFFLINE - CANNOT REQUEST ROUTE');
      }
    }
  }, [layerToggles.EVACUATION_ROUTES, evacuationRouteRuntime, sendTextWithStatus]);

  const handleScreenshotCapture = useCallback(async (requestId) => {
    setHudNotification('CAPTURING VIEW...');

    try {
      const globe = globeRef.current;
      if (!globe || !globe.captureScreenshot) {
        console.error('[SCREENSHOT] Globe ref or captureScreenshot not available');
        return;
      }

      const base64Image = await globe.captureScreenshot();
      if (!base64Image) {
        console.error('[SCREENSHOT] Empty screenshot');
        return;
      }

      const sizeBytes = (base64Image.length * 3) / 4;
      if (sizeBytes > 7 * 1024 * 1024) {
        console.warn(`[SCREENSHOT] Image too large (${(sizeBytes / 1024 / 1024).toFixed(1)}MB), may be rejected`);
      }

      const sent = sendScreenshotResponse(requestId, base64Image);

      if (!sent) {
        console.warn('[SCREENSHOT] sendScreenshotResponse failed (socket not open)');
      } else {
        console.log(`[SCREENSHOT] Sent (request_id: ${requestId}, size: ${(sizeBytes / 1024).toFixed(0)}KB)`);
      }
    } catch (err) {
      console.error('[SCREENSHOT] Capture failed:', err);
    }
  }, [sendScreenshotResponse]);

  const handleSetVideoCadenceFps = useCallback((nextFps) => {
    const parsed = Number(nextFps);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setVideoCadenceFps(DEFAULT_VIDEO_CADENCE_FPS);
      return;
    }
    const cappedCadence = Math.min(MAX_VIDEO_CADENCE_FPS, Math.max(0.1, parsed));
    setVideoCadenceFps(cappedCadence);
  }, []);

  const captureAndSendVideoFrame = useCallback(async () => {
    if (videoCaptureInFlightRef.current) {
      setVideoStreamStatus((prev) => ({
        ...prev,
        droppedFrames: prev.droppedFrames + 1,
        state: 'throttled',
      }));
      return;
    }

    const globe = globeRef.current;
    if (!globe || typeof globe.captureScreenshot !== 'function') {
      setVideoStreamStatus((prev) => ({
        ...prev,
        state: 'degraded',
        lastError: 'Globe capture unavailable',
      }));
      return;
    }

    videoCaptureInFlightRef.current = true;
    try {
      const base64Image = await globe.captureScreenshot();
      if (!base64Image) {
        setVideoStreamStatus((prev) => ({
          ...prev,
          state: 'degraded',
          droppedFrames: prev.droppedFrames + 1,
          lastError: 'Empty video frame',
        }));
        return;
      }

      const capturedAt = Date.now();
      const activeLayersLabel = Object.entries(layerToggles)
        .filter(([, isEnabled]) => Boolean(isEnabled))
        .map(([layerId]) => layerId)
        .join(', ') || 'none';
      const resolvedCameraMode = typeof cameraMode === 'string' && cameraMode.trim().length > 0
        ? cameraMode.trim().toLowerCase()
        : 'overview';
      const frameCaption = `Live strategic frame (layers: ${activeLayersLabel}; camera: ${resolvedCameraMode})`;
      const sent = sendVideoFrame(base64Image, frameCaption, {
        frame_id: `video-${capturedAt}`,
        captured_at_ms: capturedAt,
        cadence_fps: Math.min(MAX_VIDEO_CADENCE_FPS, videoCadenceFps),
        source: 'strategic_globe',
        active_layers: activeLayersLabel,
        camera_mode: resolvedCameraMode,
      });

      if (!sent) {
        setVideoStreamStatus((prev) => ({
          ...prev,
          state: 'waiting_socket',
          droppedFrames: prev.droppedFrames + 1,
          lastError: 'WebSocket not connected',
        }));
        return;
      }

      setVideoStreamStatus((prev) => ({
        ...prev,
        state: 'streaming',
        sentFrames: prev.sentFrames + 1,
        lastFrameAt: capturedAt,
        lastError: null,
      }));
    } catch (error) {
      setVideoStreamStatus((prev) => ({
        ...prev,
        state: 'degraded',
        droppedFrames: prev.droppedFrames + 1,
        lastError: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      videoCaptureInFlightRef.current = false;
    }
  }, [cameraMode, layerToggles, sendVideoFrame, videoCadenceFps]);

  useEffect(() => {
    if (!missionStarted || !videoStreamingEnabled) {
      return undefined;
    }

    const cappedCadence = Math.min(MAX_VIDEO_CADENCE_FPS, Math.max(0.1, videoCadenceFps));
    const cadenceMs = Math.max(1000, Math.round(1000 / cappedCadence));
    setVideoStreamStatus((prev) => ({
      ...prev,
      state: connectionStatus === 'connected' ? 'starting' : 'waiting_socket',
      lastError: null,
    }));

    void captureAndSendVideoFrame();
    const intervalId = window.setInterval(() => {
      void captureAndSendVideoFrame();
    }, cadenceMs);
    return () => window.clearInterval(intervalId);
  }, [
    captureAndSendVideoFrame,
    connectionStatus,
    missionStarted,
    videoCadenceFps,
    videoStreamingEnabled,
  ]);

  useEffect(() => {
    if (!missionStarted || !isRecording || videoStreamingEnabled) {
      return;
    }
    if (autoVideoEnableOnRecordingRef.current) {
      return;
    }
    autoVideoEnableOnRecordingRef.current = true;
    setVideoStreamingEnabled(true);
    setVideoStreamStatus((prev) => ({
      ...prev,
      state: connectionStatus === 'connected' ? 'starting' : 'waiting_socket',
      lastError: null,
    }));
  }, [connectionStatus, isRecording, missionStarted, videoStreamingEnabled]);

  useEffect(() => {
    if (videoStreamingEnabled || !videoCaptureInFlightRef.current) {
      return;
    }
    videoCaptureInFlightRef.current = false;
  }, [videoStreamingEnabled]);

  // ── Camera-driven analytics: fetch BigQuery data when globe camera settles ──
  useEffect(() => {
    let removeListener = null;
    let pollTimer = null;
    let cancelled = false;
    let activeAbort = null;
    let requestSeq = 0;
    let viewportAbort = null;
    let viewportSeq = 0;

    const fetchAnalytics = (lat, lng) => {
      if (cancelled) return;

      // Cancel any in-flight fetch
      if (activeAbort) {
        activeAbort.abort();
        activeAbort = null;
      }

      const seq = ++requestSeq;
      const controller = new AbortController();
      activeAbort = controller;

      fetch(
        `/api/location-analytics?lat=${lat.toFixed(4)}&lng=${lng.toFixed(4)}`,
        { signal: controller.signal },
      )
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((result) => {
          if (cancelled || seq !== requestSeq) return;
          activeAbort = null;
          if (result.region && result.data) {
            analyticsFetchedRegionRef.current = result.region;
            setChartData(result.data);
          }
          // If outside known regions, keep existing chartData (don't clear)
        })
        .catch((err) => {
          if (err?.name === 'AbortError') return;
          console.warn('[HawkEye] location-analytics fetch failed:', err?.message);
        });
    };

    const fetchViewportStats = (lat, lng, altKm) => {
      if (cancelled) return;

      // Map camera altitude to query radius — closer = tighter local scope
      let radius = 3.0;
      if (altKm < 3) radius = 1.0;
      else if (altKm < 8) radius = 2.0;
      else if (altKm < 20) radius = 5.0;
      else if (altKm < 50) radius = 10.0;
      else radius = 20.0;

      if (viewportAbort) { viewportAbort.abort(); }
      const vpSeq = ++viewportSeq;
      const vpCtrl = new AbortController();
      viewportAbort = vpCtrl;

      setPointAnalytics((prev) => ({
        lat, lng, loading: true,
        data: prev?.data ?? null,
        error: null,
        radius,
        source: 'viewport',
      }));

      fetch(
        `/api/point-analytics?lat=${lat.toFixed(4)}&lng=${lng.toFixed(4)}&radius_km=${radius}`,
        { signal: vpCtrl.signal },
      )
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((result) => {
          if (cancelled || vpSeq !== viewportSeq) return;
          viewportAbort = null;
          if (result?.data) {
            setPointAnalytics({
              lat, lng, loading: false,
              data: result.data,
              error: null,
              radius,
              source: 'viewport',
            });
          } else {
            // Outside coverage or empty result — clear loading, keep previous data
            setPointAnalytics((prev) => ({
              ...prev,
              lat, lng, loading: false,
              error: null,
              radius,
              source: 'viewport',
            }));
          }
        })
        .catch((err) => {
          if (err?.name === 'AbortError') return;
          if (cancelled) return;
          console.warn('[HawkEye] viewport point-analytics failed:', err?.message);
          setPointAnalytics((prev) => ({
            ...prev,
            lat, lng, loading: false,
            error: null,   // don't show error for viewport auto-queries, just keep stale data
            radius,
            source: 'viewport',
          }));
        });
    };

    const onCameraMoveEnd = (viewer) => {
      if (analyticsDebounceRef.current) {
        clearTimeout(analyticsDebounceRef.current);
      }
      analyticsDebounceRef.current = setTimeout(() => {
        if (cancelled) return;
        try {
          const carto = viewer.camera.positionCartographic;
          const lat = (carto.latitude * 180) / Math.PI;
          const lng = (carto.longitude * 180) / Math.PI;
          fetchAnalytics(lat, lng);

          // Also fetch viewport-scoped point analytics
          const altKm = carto.height / 1000;
          fetchViewportStats(lat, lng, altKm);
        } catch {
          // viewer may be destroyed
        }
      }, 1500);
    };

    const tryAttach = () => {
      const viewer = globeRef.current?.getViewer?.();
      if (!viewer || viewer.isDestroyed?.()) return false;

      // Delay initial fetch by 2s to let flyTo-Jakarta settle
      // (Cesium starts at default position before flying to Jakarta)
      setTimeout(() => {
        if (cancelled) return;
        try {
          const carto = viewer.camera.positionCartographic;
          const lat = (carto.latitude * 180) / Math.PI;
          const lng = (carto.longitude * 180) / Math.PI;
          fetchAnalytics(lat, lng);
          const altKm = carto.height / 1000;
          fetchViewportStats(lat, lng, altKm);
        } catch {
          // ignore
        }
      }, 2000);

      const handler = () => onCameraMoveEnd(viewer);
      viewer.camera.moveEnd.addEventListener(handler);
      removeListener = () => {
        try {
          viewer.camera.moveEnd.removeEventListener(handler);
        } catch {
          // viewer already destroyed
        }
      };
      return true;
    };

    // Poll until the Cesium viewer is ready
    if (!tryAttach()) {
      pollTimer = setInterval(() => {
        if (cancelled) {
          clearInterval(pollTimer);
          return;
        }
        if (tryAttach()) {
          clearInterval(pollTimer);
        }
      }, 500);
    }

    // ── Retry mechanism ──────────────────────────────────────
    // If the initial fetch or moveEnd fetch failed/was aborted, keep
    // retrying every 5s until data is successfully received.
    const retryTimer = setInterval(() => {
      if (cancelled) return;
      // Stop retrying once we've received valid data
      if (analyticsFetchedRegionRef.current) return;
      const viewer = globeRef.current?.getViewer?.();
      if (!viewer || viewer.isDestroyed?.()) return;
      try {
        const carto = viewer.camera.positionCartographic;
        const lat = (carto.latitude * 180) / Math.PI;
        const lng = (carto.longitude * 180) / Math.PI;
        fetchAnalytics(lat, lng);
      } catch {
        // viewer may be destroyed
      }
    }, 5000);

    return () => {
      cancelled = true;
      if (pollTimer) clearInterval(pollTimer);
      if (retryTimer) clearInterval(retryTimer);
      if (analyticsDebounceRef.current) clearTimeout(analyticsDebounceRef.current);
      if (removeListener) removeListener();
      if (activeAbort) activeAbort.abort();
      if (viewportAbort) viewportAbort.abort();
    };
  }, []); // stable — runs once on mount

  // ── Click-to-query: BigQuery point analytics on globe click ──
  useEffect(() => {
    let cancelled = false;
    let removeHandler = null;
    let debounceTimer = null;

    const fetchPointAnalytics = (lat, lng) => {
      if (cancelled) return;

      // Cancel any in-flight request
      if (pointClickAbortRef.current) {
        pointClickAbortRef.current.abort();
        pointClickAbortRef.current = null;
      }

      setPointAnalytics({ lat, lng, loading: true, data: null, error: null });

      const controller = new AbortController();
      pointClickAbortRef.current = controller;

      fetch(
        `/api/point-analytics?lat=${lat.toFixed(4)}&lng=${lng.toFixed(4)}&radius_km=3.0`,
        { signal: controller.signal },
      )
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((result) => {
          if (cancelled) return;
          pointClickAbortRef.current = null;
          if (result.data) {
            setPointAnalytics({
              lat,
              lng,
              loading: false,
              data: result.data,
              error: null,
              radius: 3.0,
              source: 'click',
            });
          } else {
            setPointAnalytics({
              lat,
              lng,
              loading: false,
              data: null,
              error: 'Outside coverage area',
              radius: 3.0,
              source: 'click',
            });
          }
        })
        .catch((err) => {
          if (err?.name === 'AbortError') return;
          if (cancelled) return;
          setPointAnalytics((prev) => (prev
            ? {
              ...prev,
              loading: false,
              error: err?.message || 'Query failed',
              radius: Number.isFinite(prev.radius) ? prev.radius : 3.0,
              source: prev.source || 'click',
            }
            : null));
        });
    };

    const tryAttach = () => {
      const viewer = globeRef.current?.getViewer?.();
      if (!viewer || viewer.isDestroyed?.()) return false;

      const canvas = viewer.scene.canvas;
      const ellipsoid = viewer.scene.globe.ellipsoid;

      const handleClick = (event) => {
        if (cancelled) return;

        // Debounce rapid clicks (300ms)
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          try {
            const cartesian = viewer.camera.pickEllipsoid(
              { x: event.offsetX, y: event.offsetY },
              ellipsoid,
            );
            if (!cartesian) return; // clicked sky / off globe

            const carto = ellipsoid.cartesianToCartographic(cartesian);
            const lat = (carto.latitude * 180) / Math.PI;
            const lng = (carto.longitude * 180) / Math.PI;

            // Place pulsing marker on globe
            const globe = globeRef.current;
            if (globe) {
              // Remove previous marker
              if (activePointMarkerRef.current) {
                try { globe.removeMarker(activePointMarkerRef.current); } catch {}
                activePointMarkerRef.current = null;
              }
              // Add new marker
              const markerId = globe.addPulsingMarker(lat, lng, {
                label: 'Query Point',
                color: '#00d4ff',
                size: 12,
              });
              activePointMarkerRef.current = markerId;
            }

            // Create ping effect at click position
            const ping = document.createElement('div');
            ping.className = 'reticle-click-ping';
            const rect = canvas.getBoundingClientRect();
            ping.style.left = `${rect.left + event.offsetX}px`;
            ping.style.top = `${rect.top + event.offsetY}px`;
            document.body.appendChild(ping);
            setTimeout(() => ping.remove(), 700);

            fetchPointAnalytics(lat, lng);
          } catch {
            // viewer may be destroyed
          }
        }, 300);
      };

      canvas.addEventListener('click', handleClick);
      removeHandler = () => canvas.removeEventListener('click', handleClick);
      return true;
    };

    // Poll until viewer is ready
    const pollTimer = setInterval(() => {
      if (cancelled) {
        clearInterval(pollTimer);
        return;
      }
      if (tryAttach()) {
        clearInterval(pollTimer);
      }
    }, 500);

    // Try immediately
    if (tryAttach()) clearInterval(pollTimer);

    return () => {
      cancelled = true;
      clearInterval(pollTimer);
      if (debounceTimer) clearTimeout(debounceTimer);
      if (removeHandler) removeHandler();
      if (pointClickAbortRef.current) pointClickAbortRef.current.abort();
    };
  }, []); // stable — runs once on mount

  // Publish tactical context for dynamic evacuation-origin resolution.
  useEffect(() => {
    if (!missionStarted) return;

    let contextPayload = null;
    const pointLat = Number(pointAnalytics?.lat);
    const pointLng = Number(pointAnalytics?.lng);
    if (Number.isFinite(pointLat) && Number.isFinite(pointLng)) {
      contextPayload = {
        lat: pointLat,
        lng: pointLng,
        source: pointAnalytics?.source || 'viewport',
        label: pointAnalytics?.source === 'click' ? 'Selected map point' : 'Current viewport center',
        radius_km: Number.isFinite(pointAnalytics?.radius) ? Number(pointAnalytics.radius) : undefined,
      };
    } else {
      const viewer = globeRef.current?.getViewer?.();
      if (viewer && !viewer.isDestroyed?.()) {
        try {
          const carto = viewer.camera.positionCartographic;
          const lat = (carto.latitude * 180) / Math.PI;
          const lng = (carto.longitude * 180) / Math.PI;
          if (Number.isFinite(lat) && Number.isFinite(lng)) {
            contextPayload = {
              lat,
              lng,
              source: 'camera',
              label: 'Current camera center',
            };
          }
        } catch {
          // Viewer may be reinitializing; skip this tick.
        }
      }
    }

    if (!contextPayload) return;
    const signature = [
      contextPayload.source,
      contextPayload.lat.toFixed(5),
      contextPayload.lng.toFixed(5),
      Number.isFinite(contextPayload.radius_km) ? Number(contextPayload.radius_km).toFixed(2) : 'na',
    ].join(':');
    if (routeContextSignatureRef.current === signature) return;
    routeContextSignatureRef.current = signature;

    sendContextUpdate({
      ...contextPayload,
      timestamp: Date.now(),
    });
  }, [
    missionStarted,
    pointAnalytics?.lat,
    pointAnalytics?.lng,
    pointAnalytics?.radius,
    pointAnalytics?.source,
    sendContextUpdate,
  ]);

  const handleToggleVideoStreaming = useCallback(() => {
    setVideoStreamingEnabled((prev) => {
      const nextEnabled = !prev;
      setVideoStreamStatus((current) => ({
        ...current,
        state: nextEnabled
          ? (connectionStatus === 'connected' ? 'starting' : 'waiting_socket')
          : 'idle',
        lastError: null,
      }));
      return nextEnabled;
    });
  }, [connectionStatus]);

  const applyEarthEngineUpdate = useCallback((event) => {
    const runtimePayload =
      isRuntimeRecord(event?.ee_runtime)
        ? event.ee_runtime
        : null;
    const fallbackRuntimeLayers = Array.isArray(event?.runtime_layers)
      ? event.runtime_layers
      : [];
    const runtimeLayers = Array.isArray(runtimePayload?.layers)
      ? runtimePayload.layers
      : fallbackRuntimeLayers;
    const fallbackTemporalFrames =
      isRuntimeRecord(event?.temporal_frames)
        ? event.temporal_frames
        : {};
    const runtimeTemporalFrames =
      isRuntimeRecord(runtimePayload?.temporal_frames)
        ? runtimePayload.temporal_frames
        : fallbackTemporalFrames;
    const fallbackTemporalPlayback =
      isRuntimeRecord(event?.temporal_playback)
        ? event.temporal_playback
        : {};
    const runtimeTemporalPlayback =
      isRuntimeRecord(runtimePayload?.temporal_playback)
        ? runtimePayload.temporal_playback
        : fallbackTemporalPlayback;
    const fallbackTemporalSummary =
      isRuntimeRecord(event?.temporal_summary)
        ? event.temporal_summary
        : {};
    const runtimeTemporalSummary =
      isRuntimeRecord(runtimePayload?.temporal_summary)
        ? runtimePayload.temporal_summary
        : fallbackTemporalSummary;
    const runtimeMultisensorFusion =
      isRuntimeRecord(event?.multisensor_fusion)
        ? event.multisensor_fusion
        : isRuntimeRecord(runtimePayload?.multisensor_fusion)
          ? runtimePayload.multisensor_fusion
          : null;
    const runtimeProvenance =
      isRuntimeRecord(runtimePayload?.provenance)
        ? runtimePayload.provenance
        : isRuntimeRecord(event?.runtime_provenance)
          ? event.runtime_provenance
          : null;
    const runtimeState =
      isRuntimeRecord(event?.runtime_state)
        ? event.runtime_state
        : isRuntimeRecord(runtimePayload?.runtime_state)
          ? runtimePayload.runtime_state
          : null;
    const liveAnalysisTask =
      isRuntimeRecord(event?.live_analysis_task)
        ? event.live_analysis_task
        : isRuntimeRecord(runtimePayload?.live_analysis_task)
          ? runtimePayload.live_analysis_task
          : null;
    const runtimeFloodGeojson =
      event?.geojson ??
      runtimePayload?.geojson ??
      runtimePayload?.flood_geojson ??
      runtimePayload?.flood_extent_geojson ??
      runtimePayload?.floodExtentGeojson ??
      null;
    const runtimeFloodGeojsonUrl = pickFirstNonEmptyString([
      event?.geojson_url,
      event?.url,
      runtimePayload?.geojson_url,
      runtimePayload?.flood_geojson_url,
      runtimePayload?.flood_extent_geojson_url,
      runtimePayload?.floodExtentGeojsonUrl,
    ]);
    const hasRuntimePayload =
      isRuntimeRecord(runtimePayload) &&
      Object.keys(runtimePayload).length > 0;
    const hasRuntimeDescriptors =
      hasRuntimePayload ||
      runtimeLayers.length > 0 ||
      Object.keys(runtimeTemporalFrames).length > 0 ||
      Object.keys(runtimeTemporalPlayback).length > 0 ||
      Object.keys(runtimeTemporalSummary).length > 0 ||
      Boolean(runtimeProvenance) ||
      Boolean(runtimeMultisensorFusion) ||
      Boolean(runtimeFloodGeojson) ||
      Boolean(runtimeFloodGeojsonUrl) ||
      Boolean(runtimeState) ||
      Boolean(liveAnalysisTask);

    if (hasRuntimeDescriptors) {
      setEarthEngineRuntime((prev) => {
        const previousRuntime =
          isRuntimeRecord(prev.eeRuntime)
            ? prev.eeRuntime
            : {};
        const nextRuntime = {
          ...previousRuntime,
          ...(runtimePayload || {}),
        };

        const resolvedLayers = mergeRuntimeLayers(
          prev.runtimeLayers,
          runtimeLayers.length > 0
            ? runtimeLayers
            : Array.isArray(nextRuntime.layers)
              ? nextRuntime.layers
              : [],
        );
        const resolvedTemporalFrames = mergeTemporalFrames(
          prev.temporalFrames,
          Object.keys(runtimeTemporalFrames).length > 0
            ? runtimeTemporalFrames
            : nextRuntime.temporal_frames,
        );
        const resolvedTemporalPlayback = mergeTemporalPlayback(
          prev.temporalPlayback,
          Object.keys(runtimeTemporalPlayback).length > 0
            ? runtimeTemporalPlayback
            : nextRuntime.temporal_playback,
        );
        const resolvedTemporalSummary = mergeRuntimeRecord(
          prev.temporalSummary,
          Object.keys(runtimeTemporalSummary).length > 0
            ? runtimeTemporalSummary
            : nextRuntime.temporal_summary,
        );
        const resolvedMultisensorFusion = mergeRuntimeRecord(
          prev.multisensorFusion,
          runtimeMultisensorFusion || nextRuntime.multisensor_fusion,
        );

        if (resolvedLayers.length > 0) {
          nextRuntime.layers = resolvedLayers;
        }
        if (Object.keys(resolvedTemporalFrames).length > 0) {
          nextRuntime.temporal_frames = resolvedTemporalFrames;
        }
        if (Object.keys(resolvedTemporalPlayback).length > 0) {
          nextRuntime.temporal_playback = resolvedTemporalPlayback;
        }
        if (Object.keys(resolvedTemporalSummary).length > 0) {
          nextRuntime.temporal_summary = resolvedTemporalSummary;
        }
        if (Object.keys(resolvedMultisensorFusion).length > 0) {
          nextRuntime.multisensor_fusion = resolvedMultisensorFusion;
        }
        if (runtimeProvenance) {
          nextRuntime.provenance = {
            ...(isRuntimeRecord(nextRuntime.provenance)
              ? nextRuntime.provenance
              : {}),
            ...runtimeProvenance,
          };
        }
        if (runtimeState) {
          nextRuntime.runtime_state = {
            ...(isRuntimeRecord(nextRuntime.runtime_state)
              ? nextRuntime.runtime_state
              : {}),
            ...runtimeState,
          };
        }
        if (liveAnalysisTask) {
          nextRuntime.live_analysis_task = liveAnalysisTask;
        }

        const resolvedFloodGeojson =
          runtimeFloodGeojson ??
          nextRuntime.geojson ??
          nextRuntime.flood_geojson ??
          nextRuntime.flood_extent_geojson ??
          nextRuntime.floodExtentGeojson ??
          prev.floodGeojson ??
          null;
        const resolvedFloodGeojsonUrl = pickFirstNonEmptyString([
          runtimeFloodGeojsonUrl,
          nextRuntime.geojson_url,
          nextRuntime.flood_geojson_url,
          nextRuntime.flood_extent_geojson_url,
          nextRuntime.floodExtentGeojsonUrl,
          prev.floodGeojsonUrl,
        ]);

        return {
          eeRuntime: Object.keys(nextRuntime).length > 0 ? nextRuntime : prev.eeRuntime,
          runtimeLayers: resolvedLayers,
          temporalFrames: resolvedTemporalFrames,
          temporalPlayback: resolvedTemporalPlayback,
          temporalSummary: resolvedTemporalSummary,
          multisensorFusion: resolvedMultisensorFusion,
          floodGeojson: resolvedFloodGeojson,
          floodGeojsonUrl: resolvedFloodGeojsonUrl,
          runtimeState: runtimeState
            ? mergeRuntimeRecord(prev.runtimeState, runtimeState)
            : prev.runtimeState,
          liveAnalysisTask: liveAnalysisTask || prev.liveAnalysisTask,
        };
      });
    }

    setEarthEngineOverrides((prev) => {
      const next = {
        metrics: { ...(prev?.metrics || {}) },
        provenance: { ...(prev?.provenance || {}) },
        timeline: { ...(prev?.timeline || {}) },
      };

      const metadata =
        isRuntimeRecord(event?.metadata)
          ? event.metadata
          : null;
      const metadataEnvelope = {
        ...(runtimeProvenance || {}),
        ...(metadata || {}),
      };
      const hasMetadataEnvelope = Object.keys(metadataEnvelope).length > 0;

      const areaSqKm = pickFirstFiniteNumber([
        event?.area_sqkm,
        event?.areaSqKm,
        runtimePayload?.area_sqkm,
        runtimePayload?.areaSqKm,
        runtimePayload?.flood_area_sqkm,
        metadataEnvelope?.estimated_area_sqkm,
        metadataEnvelope?.total_vector_area_sqkm,
        metadataEnvelope?.flood_area_sqkm,
      ]);
      if (areaSqKm !== null) {
        next.metrics.floodAreaSqKm = Number(areaSqKm.toFixed(2));
      }

      const growthRatePctHr = toGrowthRatePctPerHour(
        event?.growth_rate_pct ??
        event?.growthRatePctHr ??
        event?.growth_rate ??
        runtimePayload?.growth_rate_pct ??
        runtimePayload?.growth_rate_pct_per_hour ??
        runtimePayload?.growth_rate
      );
      if (growthRatePctHr !== null) {
        next.metrics.growthRatePctHr = Number(growthRatePctHr.toFixed(1));
      }

      const confidencePct = pickFirstFiniteNumber([
        event?.confidence_pct,
        event?.confidencePct,
        typeof event?.confidence === 'number'
          ? (event.confidence <= 1 ? event.confidence * 100 : event.confidence)
          : null,
        typeof event?.runtime_confidence === 'number'
          ? (event.runtime_confidence <= 1 ? event.runtime_confidence * 100 : event.runtime_confidence)
          : null,
        typeof runtimePayload?.confidence === 'number'
          ? (runtimePayload.confidence <= 1 ? runtimePayload.confidence * 100 : runtimePayload.confidence)
          : null,
        typeof runtimePayload?.confidence?.score === 'number'
          ? (runtimePayload.confidence.score <= 1
            ? runtimePayload.confidence.score * 100
            : runtimePayload.confidence.score)
          : null,
        typeof metadataEnvelope?.confidence === 'number'
          ? (metadataEnvelope.confidence <= 1
            ? metadataEnvelope.confidence * 100
            : metadataEnvelope.confidence)
          : null,
      ]);
      if (confidencePct !== null) {
        next.metrics.confidencePct = Math.round(confidencePct);
      }

      const historicalMatches = pickFirstFiniteNumber([
        event?.historical_events,
        event?.historicalMatches,
        runtimePayload?.historical_events,
        runtimePayload?.historicalMatches,
      ]);
      if (historicalMatches !== null) {
        next.metrics.historicalMatches = Math.round(historicalMatches);
      }

      if (hasMetadataEnvelope) {
        const source =
          metadataEnvelope.source_dataset || metadataEnvelope.source || next.provenance.source;
        const sourceDetail =
          metadataEnvelope.source_dataset_detail ||
          metadataEnvelope.source_detail ||
          source ||
          next.provenance.sourceDetail;
        const baselineWindow =
          metadataEnvelope.baseline_window || metadataEnvelope.baseline_period || null;
        const acquisitionWindow =
          metadataEnvelope.event_window ||
          metadataEnvelope.acquisition_period ||
          metadataEnvelope.flood_period ||
          null;
        const updatedAt =
          metadataEnvelope.generated_at ||
          metadataEnvelope.computed_at ||
          metadataEnvelope.updated_at ||
          null;

        next.provenance.source = source;
        next.provenance.sourceDetail = sourceDetail;
        if (baselineWindow) {
          next.provenance.baselineWindow = baselineWindow;
          next.timeline.beforeLabel = baselineWindow;
        }
        if (acquisitionWindow) {
          next.provenance.acquisitionWindow = acquisitionWindow;
          next.timeline.afterLabel = acquisitionWindow;
        }
        if (metadataEnvelope.method) next.provenance.method = metadataEnvelope.method;
        if (updatedAt) next.provenance.updatedAt = updatedAt;
        const confidence = normalizeConfidence(metadataEnvelope.confidence);
        if (confidence) next.provenance.confidence = confidence;
        next.provenance.status = 'LIVE';
        next.provenance.sidecar =
          metadataEnvelope.sidecar_path ||
          metadataEnvelope.generated_from ||
          next.provenance.sidecar;
      }

      const runtimeConfidenceLabel = normalizeRuntimeConfidenceLabel(
        event?.runtime_confidence ?? runtimePayload?.confidence
      );
      if (runtimeConfidenceLabel) {
        next.provenance.confidence = runtimeConfidenceLabel;
      }

      if (
        Object.keys(runtimeTemporalFrames).length > 0 ||
        Object.keys(runtimeTemporalPlayback).length > 0
      ) {
        const timelineSteps = buildRuntimeTimelineSteps(
          runtimeTemporalFrames,
          runtimeTemporalPlayback,
        );
        if (timelineSteps.length > 0) {
          next.timeline.steps = timelineSteps;
        }

        const baselineWindow = getTemporalFrameWindow(runtimeTemporalFrames, ['baseline']);
        const eventWindow = getTemporalFrameWindow(runtimeTemporalFrames, ['event']);
        if (baselineWindow) {
          next.timeline.beforeLabel = baselineWindow;
          next.provenance.baselineWindow = baselineWindow;
        }
        if (eventWindow) {
          next.timeline.afterLabel = eventWindow;
          next.provenance.acquisitionWindow = eventWindow;
        }
      }

      if (
        hasRuntimeDescriptors
      ) {
        next.provenance.status = 'LIVE';
      }

      return next;
    });
  }, []);

  // ── Shared MAP_UPDATE action handler (used by both WS + demo events) ──
  const handleMapUpdate = useCallback((event) => {
    appendMapCommand(event);

    const action = typeof event?.action === 'string' ? event.action.toLowerCase() : null;
    const mapLayer = typeof event?.layer === 'string' ? event.layer.toLowerCase() : '';
    const mapLayerType = typeof event?.layerType === 'string'
      ? event.layerType.toLowerCase()
      : typeof event?.layer_type === 'string'
        ? event.layer_type.toLowerCase()
        : '';
    const isFloodOverlayEvent =
      mapLayerType === 'flood' ||
      mapLayer === 'flood_extent' ||
      mapLayer === 'flood' ||
      action === MAP_ACTIONS.ADD_FLOOD_OVERLAY ||
      action === 'add_flood_overlay';
    const isRouteOverlayEvent =
      mapLayerType === 'route' ||
      mapLayer === 'evacuation_route' ||
      mapLayer === 'route' ||
      String(event?.layer_type || '').toLowerCase() === 'route';
    if (isFloodOverlayEvent && (event?.geojson || event?.url || event?.geojson_url)) {
      const runtimeFloodGeojsonUrl = resolveRuntimeGeoJsonUrl(pickFirstNonEmptyString([
        event?.geojson_url,
        event?.url,
      ]));
      setEarthEngineRuntime((prev) => ({
        ...prev,
        floodGeojson: event?.geojson ?? prev.floodGeojson,
        floodGeojsonUrl: runtimeFloodGeojsonUrl || prev.floodGeojsonUrl,
      }));
    }
    if (isRouteOverlayEvent && (event?.geojson || event?.url || event?.geojson_url)) {
      const routeGeojsonUrl = resolveRuntimeGeoJsonUrl(pickFirstNonEmptyString([
        event?.geojson_url,
        event?.url,
      ]));
      const routeOptions = Array.isArray(event?.route_options)
        ? event.route_options
          .filter((option) => option && typeof option === 'object')
          .slice(0, MAX_EVACUATION_ALT_ROUTES + 1)
          .map((option, index) => ({
            ...option,
            index: Number.isFinite(option.index) ? Number(option.index) : index,
            route_url: resolveRuntimeGeoJsonUrl(pickFirstNonEmptyString([
              option.route_url,
              option.route_geojson_url,
              option.url,
            ])),
          }))
        : [];
      const evacuationZone = {
        geojson: event?.evacuation_zone_geojson ?? null,
        url: resolveRuntimeGeoJsonUrl(pickFirstNonEmptyString([
          event?.evacuation_zone_url,
        ])),
        radius_m: pickFirstFiniteNumber([
          event?.evacuation_zone_radius_m,
        ]),
      };

      setEvacuationRouteRuntime({
        id: pickFirstNonEmptyString([
          event?.id,
          event?.overlay_id,
          event?.layer_id,
          event?.layer,
        ]) || EVACUATION_ROUTE_OVERLAY_ID,
        geojson: event?.geojson ?? null,
        url: routeGeojsonUrl,
        style: normalizeRouteOverlayStyle(event?.style),
        metadata: {
          label: event?.label || 'Evacuation Route',
          distance_m: event?.distance_m ?? null,
          duration_minutes: event?.duration_minutes ?? null,
          safety_rating: event?.safety_rating ?? null,
          route_options: routeOptions,
          destination_mode: pickFirstNonEmptyString([event?.destination_mode]),
          alternate_count: pickFirstFiniteNumber([event?.alternate_count]),
          candidate_shelter_count: pickFirstFiniteNumber([event?.candidate_shelter_count]),
          origin: event?.origin && typeof event.origin === 'object' ? event.origin : null,
          destination: event?.destination && typeof event.destination === 'object' ? event.destination : null,
          evacuation_zone: evacuationZone,
        },
      });
      setLayerToggles((prev) => ({
        ...prev,
        EVACUATION_ROUTES: true,
      }));

      const etaMinutes = pickFirstFiniteNumber([event?.duration_minutes]);
      const safetyLabel = pickFirstNonEmptyString([event?.safety_rating]);
      const evacuationHud = [
        etaMinutes !== null ? `ETA ${Math.round(etaMinutes)} MIN` : null,
        safetyLabel ? `SAFETY ${String(safetyLabel).toUpperCase()}` : null,
      ].filter(Boolean).join(' · ');
      if (evacuationHud) {
        setHudNotification(`EVAC PLAN READY: ${evacuationHud}`);
      } else {
        setHudNotification('EVAC PLAN READY');
      }
    }

    if (action === MAP_ACTIONS.TOGGLE_LAYER) {
      const layerId = event.layer || event.layer_id;
      if (!layerId) return;
      const normalizedLayerId = String(layerId).toUpperCase();
      setLayerToggles((prev) => ({
        ...prev,
        [normalizedLayerId]:
          event.enabled !== undefined ? event.enabled : !prev[normalizedLayerId],
      }));
      setHudNotification(
        `${event.enabled === false ? 'HIDING' : 'SHOWING'} ${normalizedLayerId.replace(/_/g, ' ')}`
      );
      return;
    }

    const globe = globeRef.current;
    if (!globe) {
      console.warn('[MAP_UPDATE] Globe ref not available');
      return;
    }

    if (action) {
      switch (action) {
        // === CAMERA CONTROLS ===
        case 'fly_to': {
          if (globe.flyTo) {
            globe.flyTo(event.lat ?? -6.2, event.lng ?? 106.85, event.altitude || 3000, event.duration || 3);
          }
          setHudNotification(`NAVIGATING TO ${(event.location_name || 'TARGET').toUpperCase()}`);
          return;
        }

        case MAP_ACTIONS.CAMERA_MODE: {
          const mode = String(event.mode || '').toLowerCase();
          if (mode === 'orbit') {
            if (globe.startOrbitCamera) globe.startOrbitCamera(event.lat ?? -6.2, event.lng ?? 106.85);
            setCameraMode(CAMERA_MODES.ORBIT);
          } else if (mode === 'bird_eye') {
            if (globe.setBirdEyeCamera) globe.setBirdEyeCamera(event.lat ?? -6.2, event.lng ?? 106.85);
            setCameraMode(CAMERA_MODES.BIRD_EYE);
          } else if (mode === 'street_level') {
            if (globe.setStreetLevelCamera) globe.setStreetLevelCamera(event.lat ?? -6.2, event.lng ?? 106.85);
            setCameraMode(CAMERA_MODES.STREET_LEVEL);
          } else if (mode === 'overview') {
            if (globe.flyTo) globe.flyTo(-6.2, 106.85, 25000, 3);
            setCameraMode(CAMERA_MODES.OVERVIEW);
          } else {
            console.warn('[MAP_UPDATE] Unknown camera mode:', event.mode);
            return;
          }
          setHudNotification(`CAMERA: ${mode.toUpperCase().replace(/_/g, ' ')}`);
          return;
        }

        // === OVERLAYS ===
        case 'add_overlay':
          if (isRouteOverlayEvent) {
            return;
          }
          if (globe.addGeoJsonOverlay && (event.geojson || event.url || event.geojson_url)) {
            globe.addGeoJsonOverlay(
              event.id || event.overlay_id || `overlay-${Date.now()}`,
              event.geojson || event.geojson_url || event.url,
              event.style || {},
            );
          }
          return;

        case 'add_markers':
          if (globe.addPulsingMarker) {
            (event.markers || []).forEach((marker) => {
              globe.addPulsingMarker(
                marker.id || `marker-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                marker.lat,
                marker.lng,
                marker.color || '#ff0000',
                marker.label || '',
              );
            });
          }
          return;

        case MAP_ACTIONS.ADD_FLOOD_OVERLAY:
          if (globe.addFloodOverlay && event.geojson) {
            globe.addFloodOverlay(
              event.id || event.overlay_id || `flood-${Date.now()}`,
              event.geojson,
              event.height ?? event.level_m ?? 3,
            );
          }
          return;

        case MAP_ACTIONS.UPDATE_FLOOD_LEVEL:
          if (globe.updateFloodLevel) {
            globe.updateFloodLevel(
              event.id || event.overlay_id || 'flood_main',
              event.target_height ?? event.level_m ?? 5,
              event.duration_ms ?? 3000,
            );
          }
          return;

        // === ENTITIES ===
        case MAP_ACTIONS.DEPLOY_ENTITY:
          if (globe.addEntity) {
            globe.addEntity(
              event.entity_id,
              event.entity_type,
              event.lat ?? -6.225,
              event.lng ?? 106.855,
              { altitude: event.altitude ?? 100, label: event.label || event.entity_type },
            );
          }
          setHudNotification(`DEPLOYING ${(event.entity_type || 'ASSET').toUpperCase()}`);
          return;

        case MAP_ACTIONS.MOVE_ENTITY:
          if (globe.moveEntity) {
            const durationMs = event.duration_ms ?? ((event.duration || 3) * 1000);
            globe.moveEntity(
              event.entity_id,
              event.lat ?? -6.225,
              event.lng ?? 106.855,
              { altitude: event.altitude, durationMs },
            );
          }
          setHudNotification('MOVING ASSET TO TARGET');
          return;

        // === MEASUREMENTS ===
        case MAP_ACTIONS.ADD_MEASUREMENT: {
          const measurementId = event.id || event.line_id;
          if (globe.addMeasurementLine && measurementId) {
            globe.addMeasurementLine(
              measurementId,
              { lat: event.from_lat ?? event.lat1, lng: event.from_lng ?? event.lng1 },
              { lat: event.to_lat ?? event.lat2, lng: event.to_lng ?? event.lng2 },
              event.label || '',
            );
          }
          setHudNotification(`MEASUREMENT: ${event.label || ''}`);
          return;
        }

        case MAP_ACTIONS.REMOVE_MEASUREMENT:
          if (globe.removeMeasurementLine) {
            globe.removeMeasurementLine(event.id || event.line_id);
          }
          return;

        // === THREAT RADIUS ===
        case 'add_threat_rings':
          if (globe.addThreatRings) {
            globe.addThreatRings(event.lat ?? -6.225, event.lng ?? 106.855, event.rings || [1, 2, 3]);
          }
          setHudNotification('THREAT RINGS DEPLOYED');
          return;

        // === ATMOSPHERE ===
        case MAP_ACTIONS.SET_ATMOSPHERE: {
          const requestedMode = String(event.mode || 'clear').toLowerCase();
          const normalizedMode = requestedMode === 'normal'
            ? 'clear'
            : requestedMode === 'tactical'
              ? 'night'
              : requestedMode === 'night_vision'
                ? 'night'
              : requestedMode;
          if (globe.setAtmosphere) {
            globe.setAtmosphere(normalizedMode);
          }
          setHudNotification(`ATMOSPHERE: ${requestedMode.toUpperCase().replace(/_/g, ' ')}`);
          return;
        }

        // === SCREENSHOT CAPTURE ===
        case MAP_ACTIONS.CAPTURE_SCREENSHOT:
          handleScreenshotCapture(event.request_id || event.requestId || `capture-${Date.now()}`);
          return;

        case MAP_ACTIONS.TOGGLE_SCANLINES:
          setShowScanlines((prev) => (event.enabled !== undefined ? event.enabled : !prev));
          return;

        case MAP_ACTIONS.TOGGLE_PIP:
          setShowPip((prev) => (event.enabled !== undefined ? event.enabled : !prev));
          return;

        default:
          console.warn('[MAP_UPDATE] Unhandled action:', event.action, event);
          return;
      }
    }

    // ── Legacy layer_type dispatch (existing behavior) ──────
    if (event.geojson) {
      if (event.layer_type === 'flood') {
        globe.addGeoJsonOverlay?.(
          `flood-${Date.now()}`,
          event.geojson,
          event.style || { fillColor: '#0066ff', opacity: 0.3, outlineColor: '#00d4ff' }
        );
      } else if (event.layer_type === 'route') {
        return;
      } else if (event.layer_type === 'marker') {
        const coords = event.geojson.coordinates || [106.855, -6.225];
        globe.addPulsingMarker?.(
          `marker-${Date.now()}`,
          coords[1],
          coords[0],
          event.style?.color || '#ff4444',
          event.label
        );
      }
    }
  }, [handleScreenshotCapture]);

  useEffect(() => {
    const routePayload =
      evacuationRouteRuntime?.geojson ||
      evacuationRouteRuntime?.url ||
      null;
    const style = normalizeRouteOverlayStyle(evacuationRouteRuntime?.style);
    const routeOptions = Array.isArray(evacuationRouteRuntime?.metadata?.route_options)
      ? evacuationRouteRuntime.metadata.route_options
      : [];
    const alternateRouteOptions = routeOptions.slice(1, MAX_EVACUATION_ALT_ROUTES + 1);
    const evacuationZonePayload =
      evacuationRouteRuntime?.metadata?.evacuation_zone?.geojson ||
      evacuationRouteRuntime?.metadata?.evacuation_zone?.url ||
      null;
    let pollId = null;

    const syncRouteOverlay = () => {
      const globe = globeRef.current;
      if (!globe) return false;

      const removeEvacuationOverlays = () => {
        globe.removeOverlay?.(EVACUATION_ROUTE_OVERLAY_ID);
        globe.removeOverlay?.(EVACUATION_ZONE_OVERLAY_ID);
        for (let idx = 1; idx <= MAX_EVACUATION_ALT_ROUTES; idx += 1) {
          globe.removeOverlay?.(`${EVACUATION_ALT_ROUTE_OVERLAY_PREFIX}-${idx}`);
        }
      };

      if (!layerToggles.EVACUATION_ROUTES || !routePayload) {
        removeEvacuationOverlays();
        return true;
      }

      Promise.resolve(
        globe.addGeoJsonOverlay?.(
          EVACUATION_ROUTE_OVERLAY_ID,
          routePayload,
          style,
        )
      ).catch((err) => {
        console.error('[ROUTE] Failed to render evacuation route overlay:', err);
      });

      alternateRouteOptions.forEach((option, optionIdx) => {
        const optionPayload =
          option?.route_geojson ||
          option?.route_url ||
          option?.url ||
          null;
        const overlayId = `${EVACUATION_ALT_ROUTE_OVERLAY_PREFIX}-${optionIdx + 1}`;
        if (!optionPayload) {
          globe.removeOverlay?.(overlayId);
          return;
        }

        const optionSafety = pickFirstNonEmptyString([option?.safety_rating]);
        const safetyColor =
          String(optionSafety || '').toUpperCase() === 'UNSAFE'
            ? '#ef4444'
            : String(optionSafety || '').toUpperCase() === 'CAUTION'
              ? '#f59e0b'
              : String(optionSafety || '').toUpperCase() === 'SAFE'
                ? '#16a34a'
                : '#60a5fa';
        const alternateStyle = normalizeRouteOverlayStyle({
          strokeColor: safetyColor,
          strokeWidth: 3,
          dashPattern: [6, 4],
          opacity: 0.8,
          autoFocus: false,
          glowPower: 0.1,
        });

        Promise.resolve(
          globe.addGeoJsonOverlay?.(
            overlayId,
            optionPayload,
            alternateStyle,
          )
        ).catch((err) => {
          console.error('[ROUTE] Failed to render alternate evacuation route:', err);
        });
      });
      for (
        let idx = alternateRouteOptions.length + 1;
        idx <= MAX_EVACUATION_ALT_ROUTES;
        idx += 1
      ) {
        globe.removeOverlay?.(`${EVACUATION_ALT_ROUTE_OVERLAY_PREFIX}-${idx}`);
      }

      if (evacuationZonePayload) {
        const evacuationZoneStyle = normalizeEvacuationZoneStyle({
          fillColor: '#34d399',
          outlineColor: '#22c55e',
          opacity: 0.22,
        });
        Promise.resolve(
          globe.addGeoJsonOverlay?.(
            EVACUATION_ZONE_OVERLAY_ID,
            evacuationZonePayload,
            evacuationZoneStyle,
          )
        ).catch((err) => {
          console.error('[ROUTE] Failed to render evacuation zone overlay:', err);
        });
      } else {
        globe.removeOverlay?.(EVACUATION_ZONE_OVERLAY_ID);
      }
      return true;
    };

    if (!syncRouteOverlay()) {
      pollId = window.setInterval(() => {
        if (syncRouteOverlay()) {
          window.clearInterval(pollId);
          pollId = null;
        }
      }, 250);
    }

    return () => {
      if (pollId !== null) {
        window.clearInterval(pollId);
      }
    };
  }, [layerToggles.EVACUATION_ROUTES, evacuationRouteRuntime]);

  // Process WebSocket events
  useEffect(() => {
    if (events.length === 0) {
      processedEventSeqRef.current = 0;
      return;
    }

    const sequencedEvents = events
      .map((event, index) => ({
        event,
        seq: Number.isFinite(event?._seq) ? event._seq : index + 1,
      }))
      .filter(({ seq }) => seq > processedEventSeqRef.current);

    if (sequencedEvents.length === 0) {
      return;
    }

    processedEventSeqRef.current = sequencedEvents[sequencedEvents.length - 1].seq;

    sequencedEvents.forEach(({ event }) => {
      console.log('[WS Event]', event.type);
      switch (event.type) {
        case SERVER_MESSAGE_TYPES.TRANSCRIPT:
          appendTranscript({
            id: `live-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            role: event.speaker || 'agent',
            text: event.text,
            timestamp: event.timestamp,
          });
          // ── Voice activation: detect "activate hawkeye" during video intro ──
          if (
            videoPhaseRef.current === 'intro'
            && event.speaker === 'user'
            && typeof event.text === 'string'
            && /activat/i.test(event.text)
          ) {
            console.log('[Voice Activation] Detected activation command:', event.text);
            activateHawkEye('voice-transcript');
          }
          break;
          
        case SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY:
          appendIncidentEntry({
            severity: event.severity,
            message: event.message,
            timestamp: event.timestamp,
          });
          break;
          
        case SERVER_MESSAGE_TYPES.STATUS_UPDATE:
          {
            const normalizedWaterLevel = event.water_level_m !== undefined
              ? event.water_level_m
              : event.waterLevel;
            if (normalizedWaterLevel !== undefined) {
              setWaterLevel(normalizedWaterLevel);
            }
          }
          {
            const normalizedPopulationAtRisk = event.population_at_risk !== undefined
              ? event.population_at_risk
              : event.population;
            if (normalizedPopulationAtRisk !== undefined) {
              setPopulationAtRisk(normalizedPopulationAtRisk);
            }
          }
          if (event.mode !== undefined) {
            setOperationalMode(event.mode);
          }
          break;
          
        case SERVER_MESSAGE_TYPES.MAP_UPDATE:
          handleMapUpdate(event);
          break;

        case SERVER_MESSAGE_TYPES.EE_UPDATE:
          applyEarthEngineUpdate(event);
          break;

        case SERVER_MESSAGE_TYPES.FEED_UPDATE:
          setReconFeed((prev) => {
            const mode = event.mode || prev.activeMode;
            const next = { ...prev, activeMode: mode, timestamp: event.timestamp || Date.now() };
            if (event.data?.image) {
              if (mode === 'DRONE') next.currentFrame = event.data.image;
              else if (mode === 'SAR') next.sarImage = event.data.image;
              else if (mode === 'PREDICTION') next.predictionImage = event.data.image;
            }
            return next;
          });
          break;

        case SERVER_MESSAGE_TYPES.USAGE_UPDATE:
          {
            const normalizedUsage = normalizeUsageTelemetryEvent(event);
            if (!normalizedUsage) break;

            setUsageDiagnostics({
              summary: normalizedUsage.summary,
              pressure: normalizedUsage.pressure,
              totalTokens: normalizedUsage.totalTokens,
              contextTokens: normalizedUsage.contextTokens,
              utilizationRatio: normalizedUsage.utilizationRatio,
              timestamp: normalizedUsage.timestamp,
            });

            const pressureChanged = normalizedUsage.pressure
              && normalizedUsage.pressure !== lastUsagePressureRef.current;
            const isElevatedPressure = normalizedUsage.pressure === 'HIGH'
              || normalizedUsage.pressure === 'CRITICAL';
            if (pressureChanged && isElevatedPressure) {
              appendIncidentEntry({
                id: `usage-${normalizedUsage.timestamp}`,
                severity: normalizedUsage.pressure === 'CRITICAL' ? 'CRITICAL' : 'WARNING',
                message: `Session pressure ${normalizedUsage.pressure}${normalizedUsage.utilizationRatio !== null ? ` (${(normalizedUsage.utilizationRatio * 100).toFixed(0)}% context)` : ''}`,
                timestamp: normalizedUsage.timestamp,
              });
            }
            lastUsagePressureRef.current = normalizedUsage.pressure;
          }
          break;

        case SERVER_MESSAGE_TYPES.GROUNDING_UPDATE:
          {
            const normalizedGrounding = normalizeGroundingUpdateEvent(event);
            if (!normalizedGrounding) break;
            setLatestGroundingSourceCount((prev) => Math.max(prev, normalizedGrounding.sourceCount));
            setLatestGroundingTelemetry({
              label: normalizedGrounding.label,
              sourceCount: normalizedGrounding.sourceCount,
              timestamp: normalizedGrounding.timestamp,
            });
            appendTranscript({
              id: `ground-${normalizedGrounding.timestamp}-${Math.random().toString(36).slice(2, 6)}`,
              role: 'system',
              text: normalizedGrounding.summary,
              citations: normalizedGrounding.citations,
              timestamp: normalizedGrounding.timestamp,
            });
            appendIncidentEntry({
              id: `ground-log-${normalizedGrounding.timestamp}-${Math.random().toString(36).slice(2, 6)}`,
              severity: normalizedGrounding.grounded ? 'INFO' : 'WARNING',
              message: `${normalizedGrounding.label}: ${normalizedGrounding.sourceCount} source${normalizedGrounding.sourceCount === 1 ? '' : 's'} linked`,
              timestamp: normalizedGrounding.timestamp,
            });
          }
          break;

        case SERVER_MESSAGE_TYPES.TOOL_CALL:
          console.log('[Tool Call]', event.tool, event.args);
          if (
            videoPhaseRef.current === 'intro'
            && shouldActivateIntroFromToolCall(event.tool)
          ) {
            console.log('[Voice Activation] Tool call triggered intro activation:', event.tool);
            activateHawkEye(`tool-call:${event.tool}`);
          }
          break;

        case SERVER_MESSAGE_TYPES.TOOL_STATUS:
          {
            trackToolMissionStatus(event);
            const statusEntry = buildToolStatusIncidentEntry(event);
            if (statusEntry) {
              appendIncidentEntry(statusEntry);
            }
          }
          break;

        case SERVER_MESSAGE_TYPES.TURN_COMPLETE:
          handleTurnComplete();
          break;

        case SERVER_MESSAGE_TYPES.INTERRUPTED:
          handleInterrupted();
          break;

        case SERVER_MESSAGE_TYPES.ERROR:
          appendTranscript({
            id: `err-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            role: 'system',
            text: event.message || 'Live agent error',
            timestamp: event.timestamp || Date.now(),
          });
          appendIncidentEntry({
            severity: 'high',
            message: event.message || 'Live agent error',
            timestamp: event.timestamp || Date.now(),
          });
          break;

        default:
          break;
      }
    });
  }, [
    appendIncidentEntry,
    appendTranscript,
    events,
    handleMapUpdate,
    applyEarthEngineUpdate,
    handleTurnComplete,
    handleInterrupted,
    trackToolMissionStatus,
    activateHawkEye,
  ]);

  // Process Mock Events from Demo Simulator
  useEffect(() => {
    const handleMockEvent = (e) => {
      const event = e.detail;
      switch (event.type) {
        case SERVER_MESSAGE_TYPES.TRANSCRIPT:
          appendTranscript({
            id: `mock-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            role: event.speaker || 'agent',
            text: event.text,
            timestamp: event.timestamp || Date.now(),
            confidence: event.confidence,
          });
          // ── Voice activation (mock path) ──
          if (
            videoPhaseRef.current === 'intro'
            && event.speaker === 'user'
            && typeof event.text === 'string'
            && /activat/i.test(event.text)
          ) {
            console.log('[Voice Activation] Mock path — detected activation command:', event.text);
            activateHawkEye('mock-voice-transcript');
          }
          break;
          
        case SERVER_MESSAGE_TYPES.INCIDENT_LOG_ENTRY:
          appendIncidentEntry({
            id: `mock-log-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            severity: event.severity,
            message: event.message,
            timestamp: event.timestamp || Date.now(),
          });
          break;
          
        case SERVER_MESSAGE_TYPES.STATUS_UPDATE:
          {
            const normalizedWaterLevel = event.water_level_m !== undefined
              ? event.water_level_m
              : event.waterLevel;
            if (normalizedWaterLevel !== undefined) {
              setWaterLevel(normalizedWaterLevel);
            }
          }
          {
            const normalizedPopulationAtRisk = event.population_at_risk !== undefined
              ? event.population_at_risk
              : event.population;
            if (normalizedPopulationAtRisk !== undefined) {
              setPopulationAtRisk(normalizedPopulationAtRisk);
            }
          }
          if (event.mode !== undefined) {
            setOperationalMode(event.mode);
          }
          break;
          
        case SERVER_MESSAGE_TYPES.MAP_UPDATE:
          handleMapUpdate(event);
          break;

        case SERVER_MESSAGE_TYPES.EE_UPDATE:
          applyEarthEngineUpdate(event);
          break;

        case SERVER_MESSAGE_TYPES.FEED_UPDATE:
          setReconFeed((prev) => {
            const mode = event.mode || prev.activeMode;
            const next = { ...prev, activeMode: mode, timestamp: event.timestamp || Date.now() };
            if (event.data?.image) {
              if (mode === 'DRONE') next.currentFrame = event.data.image;
              else if (mode === 'SAR') next.sarImage = event.data.image;
              else if (mode === 'PREDICTION') next.predictionImage = event.data.image;
            }
            return next;
          });
          break;

        case SERVER_MESSAGE_TYPES.USAGE_UPDATE:
          {
            const normalizedUsage = normalizeUsageTelemetryEvent(event);
            if (!normalizedUsage) break;
            setUsageDiagnostics({
              summary: normalizedUsage.summary,
              pressure: normalizedUsage.pressure,
              totalTokens: normalizedUsage.totalTokens,
              contextTokens: normalizedUsage.contextTokens,
              utilizationRatio: normalizedUsage.utilizationRatio,
              timestamp: normalizedUsage.timestamp,
            });
            lastUsagePressureRef.current = normalizedUsage.pressure;
          }
          break;

        case SERVER_MESSAGE_TYPES.GROUNDING_UPDATE:
          {
            const normalizedGrounding = normalizeGroundingUpdateEvent(event);
            if (!normalizedGrounding) break;
            setLatestGroundingSourceCount((prev) => Math.max(prev, normalizedGrounding.sourceCount));
            setLatestGroundingTelemetry({
              label: normalizedGrounding.label,
              sourceCount: normalizedGrounding.sourceCount,
              timestamp: normalizedGrounding.timestamp,
            });
            appendTranscript({
              id: `mock-ground-${normalizedGrounding.timestamp}-${Math.random().toString(36).slice(2, 6)}`,
              role: 'system',
              text: normalizedGrounding.summary,
              citations: normalizedGrounding.citations,
              timestamp: normalizedGrounding.timestamp,
            });
          }
          break;

        case SERVER_MESSAGE_TYPES.TOOL_CALL:
          console.log('[Mock Tool Call]', event.tool, event.args);
          if (
            videoPhaseRef.current === 'intro'
            && shouldActivateIntroFromToolCall(event.tool)
          ) {
            console.log('[Voice Activation] Mock tool call triggered intro activation:', event.tool);
            activateHawkEye(`mock-tool-call:${event.tool}`);
          }
          break;

        case SERVER_MESSAGE_TYPES.TOOL_STATUS:
          {
            trackToolMissionStatus(event);
            const statusEntry = buildToolStatusIncidentEntry(event);
            if (statusEntry) {
              appendIncidentEntry(statusEntry);
            }
          }
          break;

        case SERVER_MESSAGE_TYPES.TURN_COMPLETE:
          handleTurnComplete();
          break;

        case SERVER_MESSAGE_TYPES.INTERRUPTED:
          handleInterrupted();
          break;

        default:
          break;
      }
    };

    window.addEventListener('hawkeye-mock-event', handleMockEvent);
    return () => window.removeEventListener('hawkeye-mock-event', handleMockEvent);
  }, [
    appendIncidentEntry,
    appendTranscript,
    handleMapUpdate,
    applyEarthEngineUpdate,
    handleTurnComplete,
    handleInterrupted,
    trackToolMissionStatus,
    activateHawkEye,
  ]);

  // Handle mode change
  const handleModeChange = (mode) => {
    setOperationalMode(mode);
    sendModeChange(mode);
  };

  const handleToggleDirectorMode = useCallback(() => {
    const nextEnabled = !directorModeEnabled;
    setDirectorModeEnabled(nextEnabled);
    if (nextEnabled) {
      appendIncidentEntry({
        id: `director-on-${Date.now()}`,
        severity: 'INFO',
        message: 'Director mode armed: live-first with automated fallback.',
        timestamp: Date.now(),
      });
      return;
    }

    if (demoController === 'director') {
      stopDemoSimulationPlayback();
    }
    setDirectorFallbackState({
      active: false,
      reason: null,
      since: null,
    });
    appendIncidentEntry({
      id: `director-off-${Date.now()}`,
      severity: 'INFO',
      message: 'Director mode disabled: manual control restored.',
      timestamp: Date.now(),
    });
  }, [
    appendIncidentEntry,
    demoController,
    directorModeEnabled,
    stopDemoSimulationPlayback,
  ]);

  const handleStartMission = useCallback(async () => {
    console.log('[APP] handleStartMission called — preparing audio contexts');
    await prepareAudio();
    console.log('[APP] Audio preparation complete, mission started');
    setMissionStarted(true);
  }, [prepareAudio]);

  // Handle text command
  const handleSendCommand = useCallback((text) => {
    const normalizedText = typeof text === 'string' ? text.trim() : '';
    if (!normalizedText) {
      return;
    }

    const commandTimestamp = Date.now();
    const delivery = sendTextWithStatus(normalizedText);
    if (!delivery.ok) {
      setHudNotification('COMMAND NOT SENT');
      appendIncidentEntry({
        id: `cmd-fail-${commandTimestamp}`,
        severity: 'WARNING',
        message: `Command failed to send (${delivery.reason || 'link unavailable'}).`,
        timestamp: commandTimestamp,
      });
      appendTranscript({
        id: `cmd-fail-msg-${commandTimestamp}`,
        role: 'system',
        text: `Command was not sent: ${normalizedText}`,
        timestamp: commandTimestamp,
      });
      return;
    }

    appendTranscript({
      id: `cmd-${commandTimestamp}`,
      role: 'user',
      text: normalizedText,
      timestamp: commandTimestamp,
    });
    if (delivery.status === 'queued') {
      setHudNotification('COMMAND QUEUED - RETRYING');
      appendIncidentEntry({
        id: `cmd-queued-${commandTimestamp}`,
        severity: 'WARNING',
        message: 'Command queued during reconnect; it will be retried automatically.',
        timestamp: commandTimestamp,
      });
    }
  }, [appendIncidentEntry, appendTranscript, sendTextWithStatus]);

  useEffect(() => {
    function syncShellSignals() {
      setShellSignals(getShellSignals());
    }

    syncShellSignals();
    window.addEventListener('resize', syncShellSignals);
    navigator.connection?.addEventListener?.('change', syncShellSignals);
    return () => {
      window.removeEventListener('resize', syncShellSignals);
      navigator.connection?.removeEventListener?.('change', syncShellSignals);
    };
  }, [appendMapCommand]);

  const directorFallbackSignals = useMemo(() => {
    const signals = [];

    if (connectionStatus === 'reconnecting') {
      signals.push('link reconnecting');
    }
    if (connectionStatus === 'error' || connectionStatus === 'disconnected') {
      signals.push('live link unavailable');
    }

    const reconnectAttempt = Number(connectionHealth?.reconnectAttempt) || 0;
    if (reconnectAttempt >= 2 && connectionStatus !== 'connected') {
      signals.push(`retry ${reconnectAttempt}`);
    }

    if (usageDiagnostics.pressure === 'CRITICAL') {
      signals.push('model pressure critical');
    } else if (usageDiagnostics.pressure === 'HIGH' && connectionStatus !== 'connected') {
      signals.push('model pressure high');
    }

    return signals;
  }, [
    connectionHealth?.reconnectAttempt,
    connectionStatus,
    usageDiagnostics.pressure,
  ]);

  const directorFallbackReason = directorFallbackSignals.length > 0
    ? directorFallbackSignals.join(' · ')
    : null;
  const shouldActivateDirectorFallback = missionStarted
    && directorModeEnabled
    && directorFallbackSignals.length > 0;

  useEffect(() => {
    if (!directorModeEnabled || !missionStarted) {
      if (!directorFallbackState.active) {
        return;
      }
      if (demoController === 'director') {
        stopDemoSimulationPlayback();
      }
      setDirectorFallbackState({
        active: false,
        reason: null,
        since: null,
      });
      return;
    }

    if (shouldActivateDirectorFallback && !directorFallbackState.active) {
      const now = Date.now();
      const reason = directorFallbackReason || 'live signal degraded';
      setDirectorFallbackState({
        active: true,
        reason,
        since: now,
      });
      appendIncidentEntry({
        id: `director-fallback-${now}`,
        severity: 'WARNING',
        message: `Director fallback engaged — ${reason}`,
        timestamp: now,
      });
      appendTranscript({
        id: `director-fallback-note-${now}`,
        role: 'system',
        text: `Director mode switched to fallback feed (${reason}) while live telemetry stabilizes.`,
        timestamp: now,
      });
      if (!demoRunning) {
        startDemoSimulationPlayback('director');
      }
      return;
    }

    if (!shouldActivateDirectorFallback && directorFallbackState.active) {
      const now = Date.now();
      if (demoController === 'director') {
        stopDemoSimulationPlayback();
      }
      setDirectorFallbackState({
        active: false,
        reason: null,
        since: null,
      });
      appendIncidentEntry({
        id: `director-resume-${now}`,
        severity: 'INFO',
        message: 'Director fallback cleared — returned to live stream.',
        timestamp: now,
      });
      appendTranscript({
        id: `director-resume-note-${now}`,
        role: 'system',
        text: 'Director mode restored live stream after telemetry recovery.',
        timestamp: now,
      });
      return;
    }

    if (
      shouldActivateDirectorFallback
      && directorFallbackState.active
      && !demoRunning
    ) {
      startDemoSimulationPlayback('director');
      return;
    }

    if (
      shouldActivateDirectorFallback
      && directorFallbackState.active
      && directorFallbackReason
      && directorFallbackReason !== directorFallbackState.reason
    ) {
      setDirectorFallbackState((prev) => ({
        ...prev,
        reason: directorFallbackReason,
      }));
    }
  }, [
    appendIncidentEntry,
    appendTranscript,
    demoController,
    demoRunning,
    directorFallbackReason,
    directorFallbackState.active,
    directorFallbackState.reason,
    directorModeEnabled,
    missionStarted,
    shouldActivateDirectorFallback,
    startDemoSimulationPlayback,
    stopDemoSimulationPlayback,
  ]);

  const incidentReplayData = useMemo(() => {
    const replayTrack = Array.isArray(incidentReplayTimeline.track)
      ? incidentReplayTimeline.track
      : [];
    const replayHotspots = Array.isArray(incidentReplayTimeline.hotspots)
      ? incidentReplayTimeline.hotspots
      : [];
    const frameCount = replayTrack.length;
    const maxReplayIndex = Math.max(frameCount - 1, 0);
    const activeIndex = frameCount > 0 && Number.isFinite(temporalControl.activeFrameIndex)
      ? Math.min(Math.max(Math.trunc(temporalControl.activeFrameIndex), 0), maxReplayIndex)
      : 0;
    const activeStep = replayTrack[activeIndex] || null;

    return {
      available: frameCount > 1,
      isPlaying: incidentReplayState.isPlaying && frameCount > 1,
      speed: incidentReplayState.speed,
      frameCount,
      activeIndex,
      activeFrameId: temporalControl.activeFrameId,
      activeStepLabel: activeStep?.label || null,
      activeStepCaption: activeStep?.caption || null,
      track: replayTrack,
      hotspots: replayHotspots,
    };
  }, [incidentReplayTimeline, incidentReplayState, temporalControl.activeFrameIndex, temporalControl.activeFrameId]);

  const strategicData = useMemo(
    () => ({
      ...MOCK_STRATEGIC_VIEW,
      fieldMode: shellSignals.isFieldMode || MOCK_STRATEGIC_VIEW.fieldMode,
      lowBandwidth: shellSignals.lowBandwidth || MOCK_STRATEGIC_VIEW.lowBandwidth,
      bandwidthProfile: shellSignals.lowBandwidth ? 'LOW' : MOCK_STRATEGIC_VIEW.bandwidthProfile,
      timeWindow: {
        ...(MOCK_STRATEGIC_VIEW.timeWindow || {}),
        steps: resolvedTimelineSteps,
        activeIndex: temporalControl.activeFrameIndex,
      },
      globeRef,
      // New strategic view props
      layerToggles,
      onToggleLayer: handleToggleLayer,
      cameraMode,
      showScanlines,
      onToggleScanlines: () => setShowScanlines((prev) => !prev),
      reconFeed: reconFeed,
      showPip,
      onTogglePip: () => setShowPip((prev) => !prev),
      hudNotification,
      sessionStartTime,
      temporalControl,
      earthEngineRuntime,
      incidentReplay: incidentReplayData,
      onReplayToggle: handleReplayToggle,
      onReplaySeek: handleReplaySeek,
      onReplaySpeedChange: handleReplaySpeedChange,
      onReplayJumpToHotspot: handleReplayJumpToHotspot,
    }),
    [
      shellSignals,
      resolvedTimelineSteps,
      temporalControl,
      layerToggles,
      cameraMode,
      showScanlines,
      showPip,
      handleToggleLayer,
      hudNotification,
      sessionStartTime,
      earthEngineRuntime,
      reconFeed,
      incidentReplayData,
      handleReplayToggle,
      handleReplaySeek,
      handleReplaySpeedChange,
      handleReplayJumpToHotspot,
    ],
  );

  const earthEngineData = useMemo(
    () => {
      const hasRuntimeDescriptors =
        earthEngineRuntime.runtimeLayers.length > 0 ||
        Object.keys(earthEngineRuntime.temporalFrames).length > 0 ||
        Object.keys(earthEngineRuntime.temporalPlayback).length > 0 ||
        Object.keys(earthEngineRuntime.temporalSummary).length > 0 ||
        Object.keys(earthEngineRuntime.multisensorFusion).length > 0 ||
        Boolean(earthEngineRuntime.eeRuntime) ||
        Boolean(earthEngineRuntime.runtimeState) ||
        Boolean(earthEngineRuntime.liveAnalysisTask);

      const runtimeMetricFallbacks = hasRuntimeDescriptors
        ? {
            floodAreaSqKm: null,
            growthRatePctHr: null,
            confidencePct: null,
            historicalMatches: null,
          }
        : {};
      const runtimeProvenanceFallbacks = hasRuntimeDescriptors
        ? {
            source: null,
            sourceDetail: null,
            acquisitionWindow: null,
            baselineWindow: null,
            method: null,
            confidence: null,
            updatedAt: null,
            sidecar: null,
          }
        : {};
      const runtimeTimelineFallbacks = hasRuntimeDescriptors
        ? {
            beforeLabel: 'Baseline pending',
            afterLabel: 'Event window pending',
            steps: [],
          }
        : {};

      const fallbackData = {
        ...MOCK_EARTH_ENGINE,
        metrics: {
          ...MOCK_EARTH_ENGINE.metrics,
          ...(EARTH_ENGINE_SEED.metrics || {}),
          ...runtimeMetricFallbacks,
        },
        provenance: {
          ...MOCK_EARTH_ENGINE.provenance,
          ...(EARTH_ENGINE_SEED.provenance || {}),
          ...runtimeProvenanceFallbacks,
        },
        timeline: {
          ...MOCK_EARTH_ENGINE.timeline,
          ...(EARTH_ENGINE_SEED.timeline || {}),
          ...runtimeTimelineFallbacks,
        },
      };

      const overrideTimeline = earthEngineOverrides.timeline || {};
      const maxTimelineIndex = Math.max(resolvedTimelineSteps.length - 1, 0);
      const selectedFrameIndex = Number.isFinite(temporalControl.activeFrameIndex)
        ? Math.min(Math.max(Math.trunc(temporalControl.activeFrameIndex), 0), maxTimelineIndex)
        : 0;
      const selectedTimelineStep =
        resolvedTimelineSteps[selectedFrameIndex] ||
        resolvedTimelineSteps[0] ||
        null;
      const selectedFrameId = normalizeTemporalFrameId(
        temporalControl.activeFrameId ||
        resolveTimelineStepFrameId(selectedTimelineStep),
      );
      const selectedRuntimeFrame = selectedFrameId
        ? (earthEngineRuntime.temporalFrames[selectedFrameId] || null)
        : null;
      const runtimeWithTemporalSelection = hasRuntimeDescriptors
        ? {
            ...(isRuntimeRecord(earthEngineRuntime.eeRuntime) ? earthEngineRuntime.eeRuntime : {}),
            current_frame_id: selectedFrameId,
            current_frame_index: selectedFrameIndex,
            current_frame: selectedRuntimeFrame,
          }
        : earthEngineRuntime.eeRuntime;
      const resolvedComparisonMode =
        normalizeComparisonMode(temporalControl.activeComparison) ||
        getComparisonModeForFrameId(selectedFrameId) ||
        normalizeComparisonMode(MOCK_EARTH_ENGINE.activeComparison) ||
        fallbackData.activeComparison;

      return {
        ...fallbackData,
        activeComparison: resolvedComparisonMode,
        timeSliderIndex: selectedFrameIndex,
        activeFrameIndex: selectedFrameIndex,
        activeFrameId: selectedFrameId,
        currentFrameId: selectedFrameId,
        currentFrameIndex: selectedFrameIndex,
        currentFrame: selectedRuntimeFrame,
        metrics: {
          ...fallbackData.metrics,
          ...(earthEngineOverrides.metrics || {}),
          populationAtRisk,
        },
        provenance: {
          ...fallbackData.provenance,
          ...(earthEngineOverrides.provenance || {}),
          status: hasRuntimeDescriptors
            ? 'LIVE'
            : (earthEngineOverrides.provenance?.status || fallbackData.provenance.status),
        },
        timeline: {
          ...fallbackData.timeline,
          ...overrideTimeline,
          steps:
            resolvedTimelineSteps.length > 0
              ? resolvedTimelineSteps
              : fallbackData.timeline.steps,
        },
        lowBandwidth: shellSignals.lowBandwidth || fallbackData.lowBandwidth,
        bandwidthProfile: shellSignals.lowBandwidth ? 'LOW' : fallbackData.bandwidthProfile,
        eeRuntime: runtimeWithTemporalSelection,
        runtimeLayers: earthEngineRuntime.runtimeLayers,
        temporalFrames: earthEngineRuntime.temporalFrames,
        temporalPlayback: earthEngineRuntime.temporalPlayback,
        temporalSummary: earthEngineRuntime.temporalSummary,
        multisensorFusion: earthEngineRuntime.multisensorFusion,
        runtimeState: earthEngineRuntime.runtimeState,
        liveAnalysisTask: earthEngineRuntime.liveAnalysisTask,
        hasRuntimeDescriptors,
        incidentReplay: incidentReplayData,
      };
    },
    [
      shellSignals,
      earthEngineOverrides,
      earthEngineRuntime,
      temporalControl,
      resolvedTimelineSteps,
      populationAtRisk,
      incidentReplayData,
    ],
  );

  const handleStartDemo = useCallback(() => {
    if (demoRunning) {
      if (demoController === 'director') {
        setHudNotification('DIRECTOR MODE CONTROLS FALLBACK DEMO');
        return;
      }
      stopDemoSimulationPlayback();
      return;
    }
    startDemoSimulationPlayback('manual', { autoStopMs: 120_000 });
  }, [
    demoController,
    demoRunning,
    startDemoSimulationPlayback,
    stopDemoSimulationPlayback,
  ]);

  const videoStatusLabel = useMemo(() => {
    const cadenceLabel = `${Math.min(MAX_VIDEO_CADENCE_FPS, videoCadenceFps).toFixed(2)}FPS`;
    if (!videoStreamingEnabled) {
      return `VIDEO OFF · CAP ${MAX_VIDEO_CADENCE_FPS.toFixed(2)}FPS`;
    }

    if (videoStreamStatus.state === 'waiting_socket') {
      return `VIDEO WAITING · ${cadenceLabel}`;
    }
    if (videoStreamStatus.state === 'degraded') {
      return `VIDEO DEGRADED · ${videoStreamStatus.lastError || 'capture fault'}`;
    }
    if (videoStreamStatus.state === 'throttled') {
      return `VIDEO THROTTLED · ${cadenceLabel}`;
    }
    return `VIDEO ON · ${cadenceLabel} · F${videoStreamStatus.sentFrames}`;
  }, [videoCadenceFps, videoStreamStatus, videoStreamingEnabled]);

  const resolvedSourceCount = Math.max(
    Number(MOCK_NEURAL_LINK.sourceCount) || 0,
    latestGroundingSourceCount,
  );

  const connectionStatusDetail = useMemo(() => {
    const outboundQueueDepth = Number(connectionHealth?.outboundQueueDepth) || 0;
    if (connectionStatus === 'reconnecting') {
      const reconnectAttempt = Number(connectionHealth?.reconnectAttempt) || 0;
      const maxReconnectAttempts = Number(connectionHealth?.maxReconnectAttempts) || 0;
      const retryDelaySeconds = Number.isFinite(connectionHealth?.nextRetryDelayMs)
        ? Math.max(1, Math.round(connectionHealth.nextRetryDelayMs / 1000))
        : null;
      if (reconnectAttempt > 0 && maxReconnectAttempts > 0) {
        return `Retry ${reconnectAttempt}/${maxReconnectAttempts}${retryDelaySeconds ? ` in ${retryDelaySeconds}s` : ''}${outboundQueueDepth > 0 ? ` · queued ${outboundQueueDepth}` : ''}`;
      }
      return outboundQueueDepth > 0 ? `Re-establishing link · queued ${outboundQueueDepth}` : 'Re-establishing link';
    }

    if (connectionStatus === 'connected' && outboundQueueDepth > 0) {
      return `Flushing ${outboundQueueDepth} queued`;
    }

    const staleReconnects = Number(connectionHealth?.staleReconnects) || 0;
    if (connectionStatus === 'connected' && staleReconnects > 0) {
      return `Recovered stale links ${staleReconnects}`;
    }
    return null;
  }, [connectionHealth, connectionStatus]);

  const trustCockpitData = useMemo(() => {
    const confidencePct = pickFirstFiniteNumber([
      normalizeConfidencePct(earthEngineRuntime.runtimeState?.confidence_pct),
      normalizeConfidencePct(earthEngineRuntime.runtimeState?.confidencePct),
      normalizeConfidencePct(earthEngineRuntime.runtimeState?.confidence),
      normalizeConfidencePct(earthEngineRuntime.eeRuntime?.confidence_pct),
      normalizeConfidencePct(earthEngineRuntime.eeRuntime?.confidencePct),
      normalizeConfidencePct(earthEngineRuntime.eeRuntime?.confidence),
      normalizeConfidencePct(earthEngineOverrides.metrics?.confidencePct),
    ]);
    const confidenceLabel = pickFirstNonEmptyString([
      earthEngineRuntime.runtimeState?.confidence_label,
      earthEngineRuntime.runtimeState?.confidenceLevel,
      earthEngineRuntime.runtimeState?.confidence,
      earthEngineOverrides.provenance?.confidence,
    ]);
    const freshnessSource = pickFirstNonEmptyString([
      earthEngineRuntime.runtimeState?.updated_at,
      earthEngineRuntime.runtimeState?.updatedAt,
      earthEngineRuntime.eeRuntime?.updated_at,
      earthEngineRuntime.eeRuntime?.updatedAt,
      earthEngineOverrides.provenance?.updatedAt,
    ]) ?? pickFirstFiniteNumber([
      earthEngineRuntime.runtimeState?.updated_at_ms,
      earthEngineRuntime.runtimeState?.updatedAtMs,
    ]);
    const freshnessSignal = buildFreshnessSignal(freshnessSource);
    const citationCount = Math.max(
      Number(MOCK_NEURAL_LINK.sourceCount) || 0,
      Number(latestGroundingSourceCount) || 0,
      Number(latestGroundingTelemetry.sourceCount) || 0,
    );

    return {
      pressure: usageDiagnostics.pressure,
      usageSummary: usageDiagnostics.summary,
      utilizationRatio: usageDiagnostics.utilizationRatio,
      totalTokens: usageDiagnostics.totalTokens,
      citationCount,
      citationTimestamp: latestGroundingTelemetry.timestamp,
      confidencePct,
      confidenceLabel,
      freshnessLabel: freshnessSignal.label,
      freshnessState: freshnessSignal.state,
    };
  }, [
    earthEngineOverrides.metrics?.confidencePct,
    earthEngineOverrides.provenance?.updatedAt,
    earthEngineOverrides.provenance?.confidence,
    earthEngineRuntime.eeRuntime,
    earthEngineRuntime.runtimeState,
    latestGroundingSourceCount,
    latestGroundingTelemetry.sourceCount,
    latestGroundingTelemetry.timestamp,
    usageDiagnostics.pressure,
    usageDiagnostics.summary,
    usageDiagnostics.totalTokens,
    usageDiagnostics.utilizationRatio,
  ]);

  const directorStatusLabel = useMemo(() => {
    if (!directorModeEnabled) return 'DIR OFF';
    if (directorFallbackState.active) {
      return `DIR AUTO · ${directorFallbackState.reason || 'fallback'}`;
    }
    return 'DIR LIVE';
  }, [directorFallbackState.active, directorFallbackState.reason, directorModeEnabled]);

  const topBarData = useMemo(
    () => ({
      ...MOCK_TOPBAR,
      mode: operationalMode,
      populationAtRisk,
      waterLevelMeters: waterLevel,
      connectionStatus: connectionStatus,
      connectionStatusDetail,
      usageSummary: usageDiagnostics.summary,
      usagePressure: usageDiagnostics.pressure,
      videoStatusLabel,
      floodAreaSqKm: earthEngineOverrides.metrics?.floodAreaSqKm ?? null,
      demoRunning,
      demoLocked: demoController === 'director',
      directorModeEnabled,
      directorFallbackActive: directorFallbackState.active,
      directorStatus: directorStatusLabel,
      onToggleDirectorMode: handleToggleDirectorMode,
      trustCockpit: trustCockpitData,
      onStartDemo: handleStartDemo,
    }),
    [
      operationalMode,
      populationAtRisk,
      waterLevel,
      connectionStatus,
      connectionStatusDetail,
      usageDiagnostics,
      videoStatusLabel,
      earthEngineOverrides.metrics,
      demoRunning,
      demoController,
      directorModeEnabled,
      directorFallbackState.active,
      directorStatusLabel,
      handleToggleDirectorMode,
      trustCockpitData,
      handleStartDemo,
    ],
  );

  const neuralLinkData = useMemo(
    () => ({
      ...MOCK_NEURAL_LINK,
      transcript: [
        ...(ENABLE_DEMO_TIMELINE ? (MOCK_NEURAL_LINK.transcript || []) : []),
        ...transcripts,
      ],
      isRecording,
      isPlaying,
      isAgentSpeaking: isPlaying,
      isInputMuted,
      audioPipelineError,
      micStatus,
      connectionStatus,
      sourceCount: resolvedSourceCount,
      onToggleRecording: toggleRecording,
      onSendCommand: handleSendCommand,
      videoStreamingEnabled,
      videoCadenceFps,
      videoCadenceOptions: VIDEO_CADENCE_OPTIONS,
      videoStatusLabel,
      onToggleVideoStreaming: handleToggleVideoStreaming,
      onSetVideoCadenceFps: handleSetVideoCadenceFps,
      toolMissionRail,
    }),
    [
      MOCK_NEURAL_LINK,
      transcripts,
      isRecording,
      isPlaying,
      isInputMuted,
      audioPipelineError,
      micStatus,
      connectionStatus,
      resolvedSourceCount,
      toggleRecording,
      handleSendCommand,
      videoStreamingEnabled,
      videoCadenceFps,
      videoStatusLabel,
      handleToggleVideoStreaming,
      handleSetVideoCadenceFps,
      toolMissionRail,
    ],
  );

  const incidentLogData = useMemo(
    () => ({
      ...MOCK_INCIDENT_LOG,
      entries: [...MOCK_INCIDENT_LOG.entries, ...incidentEntries],
    }),
    [MOCK_INCIDENT_LOG, incidentEntries],
  );
  const showConnectionBanner = missionStarted
    && (connectionStatus === 'reconnecting' || connectionStatus === 'error');
  const showLowBandwidthBanner = missionStarted && shellSignals.lowBandwidth;
  const showDirectorFallbackBanner = missionStarted && directorFallbackState.active;
  const stackedBannerCount = (showConnectionBanner ? 1 : 0) + (showLowBandwidthBanner ? 1 : 0);
  const directorBannerTop = 56 + (stackedBannerCount * 42);

  // ── Mission select handler — starts audio + video phase ──
  const handleMissionSelect = useCallback(async (region) => {
    setSelectedMission(region);
    setVideoPhase('intro');
    // Prepare audio + arm the mic on user gesture so voice activation works during intro.
    await prepareAudio();
    await startRecording();
    setMissionStarted(true);
  }, [prepareAudio, startRecording]);

  return (
    <>
      {/* Phase 1: Region selection — before anything else */}
      {!selectedMission && (
        <MissionSelect onSelect={(region) => { void handleMissionSelect(region); }} />
      )}

      {/* Phase 2: Video intro — full-screen briefing video */}
      {selectedMission && videoPhase === 'intro' && (
        <VideoIntro
          mission={selectedMission}
          onActivate={() => activateHawkEye('button')}
          shrunk={false}
        />
      )}

      {/* Shrunk video after activation (top-left thumbnail) */}
      {selectedMission && videoPhase === 'active' && (
        <VideoIntro
          mission={selectedMission}
          onActivate={() => activateHawkEye('button')}
          shrunk
        />
      )}

      {/* Mission control — PiP during video intro, full after activation */}
      <div className={`mission-control cesium-reticle-active ${shellSignals.isFieldMode ? 'field-mode' : ''} ${videoPhase === 'intro' ? 'pip-mode' : ''} ${!selectedMission ? 'blur' : ''}`}>
        {showConnectionBanner && (
          <div className={`connection-banner ${connectionStatus === 'error' ? 'connection-banner--error' : ''}`}>
            <span className="connection-banner-dot" />
            {connectionStatus === 'reconnecting'
              ? `CONNECTION LOST \u2014 RECONNECTING${connectionStatusDetail ? ` (${connectionStatusDetail.toUpperCase()})` : '...'}`
              : 'CONNECTION FAILED \u2014 CHECK BACKEND'}
          </div>
        )}
        {showLowBandwidthBanner && (
          <div className={`connection-banner connection-banner--low-bandwidth ${showConnectionBanner ? 'connection-banner--stacked' : ''}`}>
            <span className="connection-banner-dot" />
            LOW-BANDWIDTH MODE — SIMPLIFIED LAYERS + LITE FIELD PACKS
          </div>
        )}
        {showDirectorFallbackBanner && (
          <div
            className="connection-banner connection-banner--director"
            style={{ top: `${directorBannerTop}px` }}
          >
            <span className="connection-banner-dot" />
            DIRECTOR FALLBACK ACTIVE — {String(directorFallbackState.reason || 'LIVE SIGNAL DEGRADED').toUpperCase()}
          </div>
        )}
        <TopBar 
          data={topBarData} 
          onModeChange={handleModeChange}
        />
        <StrategicViewPanel data={strategicData} />
        <AnalyticsDashboard
          chartData={chartData}
          pointAnalytics={pointAnalytics}
          transcripts={transcripts}
          reconFeed={reconFeed}
          earthEngineData={earthEngineData}
          onComparisonChange={handleTemporalComparisonChange}
          onTimeSliderChange={handleTemporalFrameChange}
          replay={incidentReplayData}
          onReplayToggle={handleReplayToggle}
          onReplaySeek={handleReplaySeek}
          onReplaySpeedChange={handleReplaySpeedChange}
          onReplayJumpToHotspot={handleReplayJumpToHotspot}
        />
        <NeuralLinkPanel data={neuralLinkData} />
        <IncidentLogPanel data={incidentLogData} />
      </div>
    </>
  );
}
