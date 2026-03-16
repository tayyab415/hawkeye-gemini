import { useEffect, useMemo, useState } from 'react';
import './EarthEnginePanel.css';

const METRIC_CONFIG = [
  { key: 'floodAreaSqKm', label: 'Flood Area', format: (v) => `${v} km²`, critical: true },
  { key: 'growthRatePctHr', label: 'Growth Rate', format: (v) => `${v}%/hr`, critical: true },
  { key: 'populationAtRisk', label: 'Pop. at Risk', format: (v) => v.toLocaleString(), critical: true },
  { key: 'infrastructureAtRisk', label: 'Infra at Risk', format: (v) => v, critical: false },
  { key: 'historicalMatches', label: 'Hist. Matches', format: (v) => v, critical: false },
  { key: 'confidencePct', label: 'Confidence', format: (v) => `${v}%`, critical: false },
];

function StatusDot({ status }) {
  const cls =
    status === 'ONLINE' ? 'ee-dot online' :
    status === 'DEGRADED' ? 'ee-dot degraded' : 'ee-dot offline';
  return <span className={cls} />;
}

function formatTimestamp(value) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function normalizeSignalToken(value) {
  if (typeof value !== 'string' || value.trim().length === 0) return null;
  return value.trim().replace(/[-\s]+/g, '_').toUpperCase();
}

function humanizeSignalToken(value) {
  const normalized = normalizeSignalToken(value);
  if (!normalized) return null;
  return normalized
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(' ');
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

function formatAge(ageMs) {
  if (!Number.isFinite(ageMs)) return null;
  const clamped = Math.max(0, ageMs);
  const minutes = Math.round(clamped / (60 * 1000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function normalizeTemporalFrameId(frameId) {
  if (typeof frameId !== 'string') return null;
  const normalized = frameId.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === 'before') return 'baseline';
  if (normalized === 'after') return 'event';
  if (normalized === 'delta' || normalized === 'difference') return 'change';
  return normalized;
}

function getFrameById(frames, frameId) {
  if (!frames || typeof frames !== 'object') return null;
  const normalizedFrameId = normalizeTemporalFrameId(frameId);
  if (!normalizedFrameId) return null;
  if (frames[normalizedFrameId] && typeof frames[normalizedFrameId] === 'object') {
    return frames[normalizedFrameId];
  }
  const matchingEntry = Object.entries(frames).find(([candidateFrameId]) => (
    normalizeTemporalFrameId(candidateFrameId) === normalizedFrameId
  ));
  if (!matchingEntry) return null;
  const [, frame] = matchingEntry;
  return frame && typeof frame === 'object' ? frame : null;
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

export default function EarthEnginePanel({
  data,
  onComparisonChange,
  onTimeSliderChange,
  replay,
  onReplayToggle,
  onReplaySeek,
  onReplaySpeedChange,
  onReplayJumpToHotspot,
}) {
  const [localActiveComparison, setLocalActiveComparison] = useState(data.activeComparison ?? 'AFTER');
  const [localTimeSliderIndex, setLocalTimeSliderIndex] = useState(data.timeSliderIndex ?? 0);
  const timelineSteps = data.timeline?.steps ?? [];
  const maxTimelineIndex = Math.max(timelineSteps.length - 1, 0);
  const runtimeLayerCount = Array.isArray(data.runtimeLayers)
    ? data.runtimeLayers.length
    : Array.isArray(data.eeRuntime?.layers)
      ? data.eeRuntime.layers.length
      : 0;
  const runtimeTemporalFrameCount =
    data.temporalFrames && typeof data.temporalFrames === 'object'
      ? Object.keys(data.temporalFrames).length
      : data.eeRuntime?.temporal_frames && typeof data.eeRuntime.temporal_frames === 'object'
        ? Object.keys(data.eeRuntime.temporal_frames).length
        : 0;
  const hasRuntimePlayback =
    data.temporalPlayback && typeof data.temporalPlayback === 'object'
      ? Object.keys(data.temporalPlayback).length > 0
      : data.eeRuntime?.temporal_playback && typeof data.eeRuntime.temporal_playback === 'object'
        ? Object.keys(data.eeRuntime.temporal_playback).length > 0
        : false;
  const temporalSummary =
    data.temporalSummary && typeof data.temporalSummary === 'object'
      ? data.temporalSummary
      : data.eeRuntime?.temporal_summary && typeof data.eeRuntime.temporal_summary === 'object'
        ? data.eeRuntime.temporal_summary
        : {};
  const hasRuntimeSummary = Object.keys(temporalSummary).length > 0;
  const hasMultisensorFusion =
    data.multisensorFusion && typeof data.multisensorFusion === 'object'
      ? Object.keys(data.multisensorFusion).length > 0
      : data.eeRuntime?.multisensor_fusion && typeof data.eeRuntime.multisensor_fusion === 'object'
        ? Object.keys(data.eeRuntime.multisensor_fusion).length > 0
        : false;
  const runtimeMode =
    data.eeRuntime?.runtime_mode ||
    data.eeRuntime?.status ||
    temporalSummary.mode ||
    null;
  const hasRuntimeDescriptors =
    Boolean(data.hasRuntimeDescriptors) ||
    runtimeLayerCount > 0 ||
    runtimeTemporalFrameCount > 0 ||
    hasRuntimePlayback ||
    hasRuntimeSummary ||
    hasMultisensorFusion ||
    Boolean(data.eeRuntime);
  const runtimeReady = runtimeLayerCount > 0 || runtimeTemporalFrameCount > 0 || timelineSteps.length > 0;
  const runtimeState =
    data.runtimeState && typeof data.runtimeState === 'object'
      ? data.runtimeState
      : data.eeRuntime?.runtime_state && typeof data.eeRuntime.runtime_state === 'object'
        ? data.eeRuntime.runtime_state
        : null;
  const liveAnalysisTask =
    data.liveAnalysisTask && typeof data.liveAnalysisTask === 'object'
      ? data.liveAnalysisTask
      : data.eeRuntime?.live_analysis_task && typeof data.eeRuntime.live_analysis_task === 'object'
        ? data.eeRuntime.live_analysis_task
        : null;

  useEffect(() => {
    setLocalActiveComparison(data.activeComparison ?? 'AFTER');
  }, [data.activeComparison]);

  useEffect(() => {
    const targetIndex = Number.isFinite(data.timeSliderIndex)
      ? data.timeSliderIndex
      : maxTimelineIndex;
    setLocalTimeSliderIndex(Math.min(Math.max(Math.trunc(targetIndex), 0), maxTimelineIndex));
  }, [data.timeSliderIndex, maxTimelineIndex]);

  const activeComparison = typeof onComparisonChange === 'function'
    ? (data.activeComparison ?? localActiveComparison)
    : localActiveComparison;
  const localSliderIndex = Number.isFinite(localTimeSliderIndex)
    ? Math.trunc(localTimeSliderIndex)
    : 0;
  const timeSliderIndex = typeof onTimeSliderChange === 'function'
    ? Number.isFinite(data.timeSliderIndex)
      ? Math.min(Math.max(Math.trunc(data.timeSliderIndex), 0), maxTimelineIndex)
      : Math.min(Math.max(localSliderIndex, 0), maxTimelineIndex)
    : Math.min(Math.max(localSliderIndex, 0), maxTimelineIndex);

  const handleComparisonSelection = (mode) => {
    setLocalActiveComparison(mode);
    onComparisonChange?.(mode);
  };

  const handleSliderChange = (value) => {
    const parsedValue = Number(value);
    const safeValue = Number.isFinite(parsedValue)
      ? Math.min(Math.max(Math.trunc(parsedValue), 0), maxTimelineIndex)
      : 0;
    setLocalTimeSliderIndex(safeValue);
    onTimeSliderChange?.(safeValue);
  };

  const activeStep = timelineSteps[timeSliderIndex] ?? timelineSteps[0];
  const replayState =
    replay && typeof replay === 'object'
      ? replay
      : data.incidentReplay && typeof data.incidentReplay === 'object'
        ? data.incidentReplay
        : null;
  const replayTrack = Array.isArray(replayState?.track) ? replayState.track : [];
  const replayHotspots = Array.isArray(replayState?.hotspots) ? replayState.hotspots : [];
  const replayAvailable = Boolean(replayState?.available) && replayTrack.length > 1;
  const replaySpeed = Number.isFinite(replayState?.speed) ? replayState.speed : 1;
  const replayActiveIndex = replayAvailable
    ? Number.isFinite(replayState?.activeIndex)
      ? Math.min(Math.max(Math.trunc(replayState.activeIndex), 0), replayTrack.length - 1)
      : Math.min(Math.max(timeSliderIndex, 0), replayTrack.length - 1)
    : 0;
  const replayActiveStep = replayTrack[replayActiveIndex] ?? activeStep;
  const runtimeTemporalFrames = (
    data.temporalFrames && typeof data.temporalFrames === 'object'
      ? data.temporalFrames
      : data.eeRuntime?.temporal_frames && typeof data.eeRuntime.temporal_frames === 'object'
        ? data.eeRuntime.temporal_frames
        : {}
  );
  const runtimeTemporalPlayback = (
    data.temporalPlayback && typeof data.temporalPlayback === 'object'
      ? data.temporalPlayback
      : data.eeRuntime?.temporal_playback && typeof data.eeRuntime.temporal_playback === 'object'
        ? data.eeRuntime.temporal_playback
        : {}
  );
  const selectedFrameId = normalizeTemporalFrameId(
    data.currentFrameId ||
    data.activeFrameId ||
    data.eeRuntime?.current_frame_id ||
    activeStep?.frameId ||
    activeStep?.frame_id ||
    activeStep?.id ||
    replayActiveStep?.frameId ||
    replayActiveStep?.frame_id ||
    replayActiveStep?.id
  );
  const selectedRuntimeTemporalFrame = getFrameById(runtimeTemporalFrames, selectedFrameId);
  const selectedRuntimePlaybackFrame = Array.isArray(runtimeTemporalPlayback.frames)
    ? (
      runtimeTemporalPlayback.frames.find((frame) => (
        normalizeTemporalFrameId(frame?.frame_id || frame?.id) === selectedFrameId
      )) || null
    )
    : null;
  const selectedRuntimeFrame =
    data.currentFrame && typeof data.currentFrame === 'object'
      ? data.currentFrame
      : data.eeRuntime?.current_frame && typeof data.eeRuntime.current_frame === 'object'
        ? data.eeRuntime.current_frame
        : selectedRuntimeTemporalFrame ||
          selectedRuntimePlaybackFrame;
  const selectedFrameLabel =
    selectedRuntimeFrame?.name ||
    selectedRuntimeTemporalFrame?.name ||
    selectedRuntimePlaybackFrame?.name ||
    activeStep?.label ||
    replayActiveStep?.label ||
    humanizeSignalToken(selectedFrameId) ||
    'selected frame';

  const seekReplayIndex = (index) => {
    if (typeof onReplaySeek === 'function') {
      onReplaySeek(index);
      return;
    }
    handleSliderChange(index);
  };

  const handleReplayHotspotSelection = (index) => {
    if (typeof onReplayJumpToHotspot === 'function') {
      onReplayJumpToHotspot(index);
      return;
    }
    seekReplayIndex(index);
  };

  const provenanceItems = useMemo(
    () => [
      data.provenance?.source,
      data.provenance?.method,
      data.provenance?.confidence && `${data.provenance.confidence} CONF`,
      data.provenance?.status,
    ].filter(Boolean),
    [data.provenance],
  );

  const runtimeStatusToken = normalizeSignalToken(data.eeRuntime?.status);
  const runtimeModeToken = normalizeSignalToken(runtimeMode);
  const runtimeError = runtimeStatusToken === 'ERROR' || runtimeModeToken === 'ERROR';
  const usesFallbackDescriptor =
    runtimeStatusToken === 'FALLBACK' ||
    (runtimeModeToken ? runtimeModeToken.includes('FALLBACK') : false);

  const selectedFrameConfidence =
    selectedRuntimeFrame?.confidence ||
    selectedRuntimeTemporalFrame?.confidence ||
    selectedRuntimePlaybackFrame?.confidence ||
    null;
  const selectedFrameConfidenceLabel = resolveConfidenceLabel(selectedFrameConfidence);
  const selectedFrameConfidencePct = toPercent(selectedFrameConfidence);
  const runtimeConfidence = data.eeRuntime?.confidence;
  const runtimeConfidenceLabel = resolveConfidenceLabel(runtimeConfidence);
  const runtimeConfidencePct = toPercent(runtimeConfidence);
  const fusionAggregateConfidence =
    data.multisensorFusion?.aggregate_confidence ||
    data.eeRuntime?.multisensor_fusion?.aggregate_confidence ||
    null;
  const fusionConfidenceLabel = resolveConfidenceLabel(fusionAggregateConfidence);
  const fusionConfidencePct = toPercent(fusionAggregateConfidence);
  const provenanceConfidenceLabel = normalizeSignalToken(data.provenance?.confidence);
  const provenanceConfidencePct = toPercent(data.provenance?.confidence);
  const metricConfidencePct = toPercent(data.metrics?.confidencePct);

  const confidencePct =
    selectedFrameConfidencePct ??
    runtimeConfidencePct ??
    fusionConfidencePct ??
    metricConfidencePct ??
    provenanceConfidencePct;
  const confidenceLevel =
    selectedFrameConfidenceLabel ||
    runtimeConfidenceLabel ||
    fusionConfidenceLabel ||
    provenanceConfidenceLabel ||
    confidenceLevelFromPercent(confidencePct);
  const confidenceValue = confidenceLevel
    ? `${humanizeSignalToken(confidenceLevel)}${confidencePct !== null ? ` (${Math.round(confidencePct)}%)` : ''}`
    : confidencePct !== null
      ? `${Math.round(confidencePct)}%`
      : 'Unknown';
  const confidenceDetail =
    selectedFrameConfidenceLabel || selectedFrameConfidencePct !== null
      ? `${selectedFrameLabel} frame descriptor`
      : runtimeConfidenceLabel || runtimeConfidencePct !== null
      ? `Runtime ${humanizeSignalToken(runtimeStatusToken || 'descriptor')}`
      : fusionConfidenceLabel || fusionConfidencePct !== null
        ? 'Multisensor fusion aggregate'
      : provenanceConfidenceLabel || provenanceConfidencePct !== null
        ? 'Provenance metadata'
        : 'No confidence payload';
  const confidenceTone =
    confidenceLevel === 'HIGH' ? 'positive' :
      confidenceLevel === 'MEDIUM' ? 'warning' :
        confidenceLevel === 'LOW' ? 'critical' : 'neutral';

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

  const uncertaintyValue = uncertaintyLevel
    ? `${humanizeSignalToken(uncertaintyLevel)}${uncertaintyPct !== null ? ` (${Math.round(uncertaintyPct)}%)` : ''}`
    : uncertaintyPct !== null
      ? `${Math.round(uncertaintyPct)}%`
      : 'Unknown';
  const uncertaintyDetail =
    runtimeError
      ? 'Runtime descriptor unavailable'
      : usesFallbackDescriptor
        ? `Inferred (${humanizeSignalToken(runtimeModeToken || 'fallback_descriptor')})`
        : selectedFrameConfidenceLabel || selectedFrameConfidencePct !== null
          ? `Inverse of ${selectedFrameLabel} confidence`
          : confidencePct !== null
            ? 'Inverse of confidence'
          : 'Derived from confidence label';
  const uncertaintyTone =
    uncertaintyLevel === 'LOW' ? 'positive' :
      uncertaintyLevel === 'MEDIUM' ? 'warning' :
        uncertaintyLevel === 'HIGH' ? 'critical' : 'neutral';

  const freshnessCandidates = [
    { value: selectedRuntimeFrame?.timestamp, source: 'selected' },
    { value: selectedRuntimeFrame?.end_timestamp, source: 'selected' },
    { value: selectedRuntimeFrame?.start_timestamp, source: 'selected' },
    { value: selectedRuntimeTemporalFrame?.timestamp, source: 'selected' },
    { value: selectedRuntimeTemporalFrame?.end_timestamp, source: 'selected' },
    { value: selectedRuntimeTemporalFrame?.start_timestamp, source: 'selected' },
    { value: selectedRuntimePlaybackFrame?.timestamp, source: 'selected' },
    { value: selectedRuntimePlaybackFrame?.end_timestamp, source: 'selected' },
    { value: selectedRuntimePlaybackFrame?.start_timestamp, source: 'selected' },
    { value: temporalSummary.latest_frame_timestamp, source: 'summary' },
    { value: temporalSummary.end_timestamp, source: 'summary' },
    { value: data.eeRuntime?.provenance?.updated_at, source: 'runtime' },
    { value: data.provenance?.updatedAt, source: 'provenance' },
    { value: temporalSummary.start_timestamp, source: 'summary' },
  ];
  let freshnessTimestamp = null;
  let freshnessSource = null;
  for (const candidate of freshnessCandidates) {
    const parsedTimestamp = toTimestampMs(candidate.value);
    if (parsedTimestamp !== null) {
      freshnessTimestamp = candidate.value;
      freshnessSource = candidate.source;
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
  const freshnessAgeLabel = formatAge(freshnessAgeMs);
  const freshnessValue = freshnessLevel
    ? `${humanizeSignalToken(freshnessLevel)}${freshnessAgeLabel ? ` (${freshnessAgeLabel} old)` : ''}`
    : 'Unknown';
  const freshnessDetail = freshnessTimestamp
    ? freshnessSource === 'selected'
      ? `${selectedFrameLabel} update ${formatTimestamp(freshnessTimestamp)}`
      : freshnessSource === 'summary'
        ? `Latest frame update ${formatTimestamp(freshnessTimestamp)}`
        : `Last update ${formatTimestamp(freshnessTimestamp)}`
    : selectedFrameId
      ? `${selectedFrameLabel} timestamp unavailable`
      : temporalSummary.latest_frame_id
      ? `Latest frame ${humanizeSignalToken(temporalSummary.latest_frame_id) || temporalSummary.latest_frame_id}`
      : 'No temporal timestamp';
  const freshnessTone =
    freshnessLevel === 'FRESH' ? 'positive' :
      freshnessLevel === 'RECENT' ? 'warning' :
        freshnessLevel === 'STALE' ? 'critical' : 'neutral';

  const signalIndicators = [
    {
      key: 'confidence',
      label: 'Confidence',
      value: confidenceValue,
      detail: confidenceDetail,
      tone: confidenceTone,
    },
    {
      key: 'uncertainty',
      label: 'Uncertainty',
      value: uncertaintyValue,
      detail: uncertaintyDetail,
      tone: uncertaintyTone,
    },
    {
      key: 'freshness',
      label: 'Freshness',
      value: freshnessValue,
      detail: freshnessDetail,
      tone: freshnessTone,
    },
  ];

  return (
    <div className="panel earth">
      <div className="panel-header">
        <span>Earth Engine Analysis</span>
      </div>

      <div className="panel-body">
        <div className="ee-toolbar">
          <div className="ee-toolbar-label">{data.timeline?.title ?? 'Temporal Compare'}</div>
          <div className="ee-toggle-group" role="tablist" aria-label="Comparison mode">
            {(data.comparisonModes ?? []).map((mode) => (
              <button
                key={mode}
                type="button"
                className={`ee-toggle-btn ${activeComparison === mode ? 'active' : ''}`}
                onClick={() => handleComparisonSelection(mode)}
              >
                {mode}
              </button>
            ))}
          </div>
        </div>

        <div className="ee-timeline-card">
          <div className="ee-timeline-header">
            <span>{data.timeline?.beforeLabel ?? 'Baseline pending'}</span>
            <span>{data.timeline?.afterLabel ?? 'Event window pending'}</span>
          </div>
          <input
            className="ee-slider"
            type="range"
            min="0"
            max={maxTimelineIndex}
            step="1"
            value={Math.min(timeSliderIndex, maxTimelineIndex)}
            onChange={(event) => handleSliderChange(event.target.value)}
            aria-label="Earth Engine timeline"
          />
          <div className="ee-timeline-meta">
            <span className="ee-step-label">{activeStep?.label ?? 'No frames'}</span>
            <span className="ee-step-caption">{activeStep?.caption ?? 'Awaiting analysis frames'}</span>
          </div>
          {replayAvailable && (
            <div className="ee-replay-card">
              <div className="ee-replay-header">
                <span className="ee-replay-title">Incident Replay Products</span>
                <span className={`ee-replay-state ${replayState?.isPlaying ? 'live' : ''}`}>
                  {replayState?.isPlaying ? 'Live' : 'Paused'}
                </span>
              </div>
              <div className="ee-replay-controls">
                <button
                  type="button"
                  className="ee-replay-btn"
                  onClick={() => onReplayToggle?.()}
                  disabled={typeof onReplayToggle !== 'function'}
                >
                  {replayState?.isPlaying ? 'Pause' : 'Play'}
                </button>
                <button
                  type="button"
                  className="ee-replay-btn"
                  onClick={() => seekReplayIndex(0)}
                >
                  Reset
                </button>
                <span className="ee-replay-progress">
                  Frame {replayActiveIndex + 1}/{replayTrack.length}
                </span>
                <div className="ee-replay-speed-group">
                  {[0.5, 1, 2].map((speed) => (
                    <button
                      key={`ee-replay-speed-${speed}`}
                      type="button"
                      className={`ee-replay-speed-btn ${replaySpeed === speed ? 'active' : ''}`}
                      onClick={() => onReplaySpeedChange?.(speed)}
                      disabled={typeof onReplaySpeedChange !== 'function'}
                    >
                      {speed}x
                    </button>
                  ))}
                </div>
              </div>
              <div className="ee-replay-strip" role="tablist" aria-label="Incident replay timeline strip">
                {replayTrack.map((step, index) => {
                  const stepIndex = Number.isFinite(step?.index) ? step.index : index;
                  const isActive = stepIndex === replayActiveIndex;
                  const isHotspot = Boolean(step?.hotspot);
                  return (
                    <button
                      key={step?.id || `${step?.frameId || 'frame'}-${stepIndex}`}
                      type="button"
                      className={`ee-replay-node ${isActive ? 'active' : ''} ${isHotspot ? 'hotspot' : ''}`}
                      onClick={() => seekReplayIndex(stepIndex)}
                      title={`${step?.label || `Frame ${stepIndex + 1}`} • ${step?.reason || step?.caption || ''}`}
                      aria-label={`Replay step ${stepIndex + 1}: ${step?.label || 'Temporal step'}`}
                    >
                      <span className="ee-replay-node-dot" />
                    </button>
                  );
                })}
              </div>
              <div className="ee-replay-meta">
                <span>{replayActiveStep?.label || 'Replay frame'}</span>
                <span>{replayActiveStep?.reason || replayActiveStep?.caption || 'Temporal progression synchronized to strategic globe layers'}</span>
              </div>
              {replayHotspots.length > 0 && (
                <div className="ee-hotspot-strip">
                  {replayHotspots.slice(0, 3).map((hotspot, index) => {
                    const stepIndex = Number.isFinite(hotspot?.index)
                      ? hotspot.index
                      : replayActiveIndex;
                    const isActiveHotspot = stepIndex === replayActiveIndex;
                    return (
                      <button
                        key={`ee-hotspot-${hotspot?.id || hotspot?.frameId || index}`}
                        type="button"
                        className={`ee-hotspot-chip ${isActiveHotspot ? 'active' : ''}`}
                        onClick={() => handleReplayHotspotSelection(stepIndex)}
                      >
                        H{index + 1}: {hotspot?.label || `Frame ${stepIndex + 1}`}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="ee-chip-row">
          {provenanceItems.map((item) => (
            <span key={item} className="ee-chip">{item}</span>
          ))}
        </div>

        <div className="ee-signal-grid" aria-label="Confidence and freshness indicators">
          {signalIndicators.map((indicator) => (
            <div key={indicator.key} className={`ee-signal-card ${indicator.tone}`}>
              <span className="ee-signal-label">{indicator.label}</span>
              <span className="ee-signal-value">{indicator.value}</span>
              <span className="ee-signal-detail">{indicator.detail}</span>
            </div>
          ))}
        </div>

        {hasRuntimeDescriptors && (
          <div className="ee-bandwidth-banner">
            Runtime descriptors {runtimeReady ? 'active' : 'connected'}:
            {runtimeLayerCount > 0
              ? ` ${runtimeLayerCount} layer${runtimeLayerCount === 1 ? '' : 's'} ready`
              : ' layer manifest pending'}
            {selectedFrameId ? ` · frame ${selectedFrameId}` : ''}
            {runtimeMode ? ` (${runtimeMode})` : ''}
            {runtimeState?.status ? ` [${runtimeState.status}]` : ''}
            {liveAnalysisTask?.task_id ? ` · task ${liveAnalysisTask.task_id}` : ''}
            {liveAnalysisTask?.state ? ` (${liveAnalysisTask.state})` : ''}.
          </div>
        )}

        {data.lowBandwidth && (
          <div className="ee-bandwidth-banner">
            Low-bandwidth mode active. Temporal previews are prioritized over full-resolution overlays.
          </div>
        )}

        <div className="ee-metrics">
          {METRIC_CONFIG.map(({ key, label, format, critical }) => (
            <div key={key} className={`ee-metric-card ${critical ? 'critical' : ''}`}>
              <span className="ee-metric-label">{label}</span>
              <span className="ee-metric-value">
                {data.metrics?.[key] !== undefined && data.metrics?.[key] !== null
                  ? format(data.metrics[key])
                  : 'N/A'}
              </span>
            </div>
          ))}
        </div>

        <div className="ee-provenance-card">
          <div className="ee-status-header">PROVENANCE</div>
          <div className="ee-provenance-grid">
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Source</span>
              <span className="ee-provenance-value">{data.provenance?.sourceDetail ?? data.provenance?.source ?? 'Unknown'}</span>
            </div>
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Acquisition</span>
              <span className="ee-provenance-value">{data.provenance?.acquisitionWindow ?? 'Unknown'}</span>
            </div>
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Baseline</span>
              <span className="ee-provenance-value">{data.provenance?.baselineWindow ?? 'Unknown'}</span>
            </div>
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Updated</span>
              <span className="ee-provenance-value">{formatTimestamp(data.provenance?.updatedAt)}</span>
            </div>
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Mode</span>
              <span className="ee-provenance-value">{activeComparison}</span>
            </div>
            <div className="ee-provenance-item">
              <span className="ee-provenance-label">Sidecar</span>
              <span className="ee-provenance-value">{data.provenance?.sidecar ?? 'Pending export'}</span>
            </div>
          </div>
        </div>

        <div className="ee-status-header">SYSTEM STATUS</div>
        <div className="ee-status-list">
          {(data.systemStatus || []).map((svc) => (
            <div key={svc.name} className="ee-status-row">
              <StatusDot status={svc.status} />
              <span className="ee-status-name">{svc.name}</span>
              <span className="ee-status-tag">{svc.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
