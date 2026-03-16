import { useEffect, useRef } from 'react';
import './NeuralLinkPanel.css';

function formatTimestamp(ts) {
  if (!ts && ts !== 0) return '';
  if (typeof ts === 'string' && !/^\d{10,}$/.test(ts)) return ts;
  const d = new Date(typeof ts === 'string' ? Number(ts) : ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

function roleColor(role) {
  if (role === 'agent') return 'var(--accent)';
  if (role === 'user') return 'var(--text-primary)';
  return 'var(--text-secondary)';
}

function riskColor(level) {
  if (level === 'CRITICAL') return 'var(--critical)';
  if (level === 'HIGH') return 'var(--critical)';
  if (level === 'MEDIUM') return 'var(--warning)';
  return 'var(--success)';
}

function formatCadenceLabel(value) {
  const cadence = Number(value);
  if (!Number.isFinite(cadence) || cadence <= 0) return '1.00';
  return cadence.toFixed(2);
}

function normalizeToolState(state) {
  if (typeof state !== 'string') return 'pending';
  const normalized = state.trim().toLowerCase();
  if (['pending', 'running', 'complete', 'error'].includes(normalized)) {
    return normalized;
  }
  return 'pending';
}

function formatToolDuration(durationMs) {
  const parsed = Number(durationMs);
  if (!Number.isFinite(parsed) || parsed < 0) return '';
  if (parsed < 1000) return `${Math.round(parsed)}ms`;
  return `${(parsed / 1000).toFixed(1)}s`;
}

function formatConnectionState(state) {
  if (typeof state !== 'string' || state.trim().length === 0) {
    return 'UNAVAILABLE';
  }
  return state.replace(/_/g, ' ').toUpperCase();
}

export default function NeuralLinkPanel({ data }) {
  const scrollRef = useRef(null);
  const missionRailItems = Array.isArray(data.toolMissionRail)
    ? [...data.toolMissionRail].slice(-6).reverse()
    : [];
  const connectionReady = !data.connectionStatus || data.connectionStatus === 'connected';
  const micTelemetryMessage = data.audioPipelineError || data.micStatus?.message || null;
  const micTelemetryTone = data.audioPipelineError
    ? 'error'
    : (data.micStatus?.tone || 'info');
  const activeMissionToolCount = missionRailItems.filter((entry) => {
    const state = normalizeToolState(entry?.state);
    return state === 'pending' || state === 'running';
  }).length;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [data.transcript]);

  return (
    <div className="panel neural">
      <div className="panel-header">
        <span>Neural Link</span>
        <span className="nl-agent-status">
          {data.isPlaying ? '● SPEAKING' : data.isRecording ? '● ARMED' : '○ STANDBY'}
        </span>
      </div>

      <div className="nl-body">
        <div className="nl-transcript" ref={scrollRef}>
          {data.transcript.length === 0 && (
            <div className="nl-init-state">
              <span className="nl-init-dot" />
              <span className="nl-init-label">INITIALIZING NEURAL LINK...</span>
            </div>
          )}
          {data.transcript.map((msg, idx) => {
            const role = msg.role || msg.speaker || 'system';
            return (
              <div key={msg.id || `msg-${idx}`} className={`nl-message nl-role-${role}`}>
                <div className="nl-msg-header">
                  <span className="nl-msg-role" style={{ color: roleColor(role) }}>
                    {role === 'agent' ? 'HAWK EYE' : role === 'user' ? 'COMMANDER' : 'SYSTEM'}
                  </span>
                  <span className="nl-msg-ts">{formatTimestamp(msg.timestamp)}</span>
                  {msg.confidence != null && (
                    <span className="nl-msg-conf">{(msg.confidence * 100).toFixed(0)}%</span>
                  )}
                </div>
                <div className="nl-msg-text">{msg.text}</div>
                {Array.isArray(msg.citations) && msg.citations.length > 0 && (
                  <div className="nl-msg-citations">
                    {msg.citations.map((citation, citationIndex) => {
                      const citationLabel = citation?.title || citation?.source || `Source ${citationIndex + 1}`;
                      const citationUrl = citation?.url;
                      return citationUrl ? (
                        <a
                          key={`${msg.id || idx}-citation-${citationIndex}`}
                          className="nl-msg-citation-link"
                          href={citationUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {citationLabel}
                        </a>
                      ) : (
                        <span
                          key={`${msg.id || idx}-citation-${citationIndex}`}
                          className="nl-msg-citation-label"
                        >
                          {citationLabel}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="nl-controls">
          <div className="nl-tool-rail">
            <div className="nl-tool-rail-header">
              <span>Mission Rail</span>
              <span className={`nl-tool-rail-count ${activeMissionToolCount > 0 ? 'active' : ''}`}>
                {activeMissionToolCount > 0 ? `${activeMissionToolCount} active` : 'idle'}
              </span>
            </div>
            <div className="nl-tool-rail-list">
              {missionRailItems.length === 0 && (
                <div className="nl-tool-rail-empty">Awaiting live tool telemetry…</div>
              )}
              {missionRailItems.map((entry, index) => {
                const state = normalizeToolState(entry?.state);
                const durationLabel = formatToolDuration(entry?.durationMs);
                const timeLabel = durationLabel || formatTimestamp(entry?.updatedAt ?? entry?.timestamp);
                return (
                  <div key={entry.id || `rail-${index}`} className={`nl-tool-rail-item ${state}`}>
                    <span className={`nl-tool-state-dot ${state}`} />
                    <div className="nl-tool-rail-meta">
                      <span className="nl-tool-rail-tool">{entry.tool || 'Tool'}</span>
                      <span className={`nl-tool-rail-state ${state}`}>{state.toUpperCase()}</span>
                    </div>
                    <span className="nl-tool-rail-time">{timeLabel}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="nl-controls-spacer" />

          <div className="nl-agent-status">
            {!connectionReady && !data.isRecording ? (
              <span style={{ color: 'var(--warning)' }}>
                ◌ Voice link not ready ({formatConnectionState(data.connectionStatus)})
              </span>
            ) : data.isPlaying ? (
              <span style={{ color: 'var(--accent)' }}>▶ Agent reply - mic muted</span>
            ) : data.isRecording && data.isInputMuted ? (
              <span style={{ color: 'var(--warning)' }}>◌ Input gate closed</span>
            ) : data.isRecording ? (
              <span style={{ color: 'var(--critical)' }}>● Armed and listening</span>
            ) : (
              <span>Mic disarmed</span>
            )}
          </div>
          {micTelemetryMessage && (
            <div className={`nl-mic-telemetry nl-mic-telemetry--${micTelemetryTone}`}>
              {micTelemetryMessage}
            </div>
          )}
          <div className="nl-confidence-bar">
            <div
              className={`nl-confidence-fill ${data.overallConfidence < 0.5 ? 'pulse-risk' : ''}`}
              style={{
                width: `${data.overallConfidence * 100}%`,
                background: riskColor(data.riskLevel),
              }}
            />
          </div>
          <div className="nl-confidence-meta">
            <span className="nl-conf-pct">{(data.overallConfidence * 100).toFixed(0)}%</span>
            <span className="nl-conf-divider">|</span>
            <span className="nl-conf-risk" style={{ color: riskColor(data.riskLevel) }}>
              {data.riskLevel} RISK
            </span>
            <span className="nl-conf-divider">|</span>
            <span className="nl-conf-sources">{data.sourceCount} sources</span>
          </div>

          <div className="nl-video-controls">
            <div className="nl-video-status">{data.videoStatusLabel || 'VIDEO OFFLINE'}</div>
            <div className="nl-video-actions">
              <button
                className={`nl-video-btn ${data.videoStreamingEnabled ? 'active' : ''}`}
                onClick={() => data.onToggleVideoStreaming?.()}
                title={data.videoStreamingEnabled ? 'Stop video stream' : 'Start video stream'}
              >
                {data.videoStreamingEnabled ? 'VIDEO ON' : 'VIDEO OFF'}
              </button>
              <select
                className="nl-video-cadence"
                value={String(data.videoCadenceFps ?? 1)}
                onChange={(event) => data.onSetVideoCadenceFps?.(Number(event.target.value))}
                title="Video cadence"
              >
                {(Array.isArray(data.videoCadenceOptions) && data.videoCadenceOptions.length > 0
                  ? data.videoCadenceOptions
                  : [0.25, 0.5, 1]
                ).map((cadence) => (
                  <option key={`cadence-${cadence}`} value={cadence}>
                    {formatCadenceLabel(cadence)} FPS
                  </option>
                ))}
              </select>
            </div>
          </div>

          <button
            className={`nl-mic-btn ${data.isRecording ? 'recording' : ''} ${data.isPlaying ? 'playing' : ''} ${data.isInputMuted ? 'muted' : ''} ${!connectionReady ? 'unavailable' : ''}`}
            onClick={() => {
              console.log('[AUDIO] Mic button clicked');
              data.onToggleRecording?.();
            }}
            title={
              data.isRecording
                ? 'Disarm microphone session'
                : connectionReady
                  ? 'Arm microphone session'
                  : `Voice link not ready (${formatConnectionState(data.connectionStatus)})`
            }
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
              <line x1="8" y1="23" x2="16" y2="23" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
