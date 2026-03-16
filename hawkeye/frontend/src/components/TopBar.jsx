import { useState, useEffect, useRef, useCallback } from 'react';
import './TopBar.css';

const MODES = ['SILENT', 'ALERT', 'BRIEF', 'ACTION'];

function formatTime(totalSeconds) {
  const h = String(Math.floor(totalSeconds / 3600)).padStart(2, '0');
  const m = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, '0');
  const s = String(totalSeconds % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function formatPopulation(n) {
  return Math.round(n).toLocaleString();
}

function waterLevelColor(meters) {
  if (meters >= 4.0) return 'var(--critical)';
  if (meters >= 3.0) return 'var(--warning)';
  return 'var(--success)';
}

function usagePressureClass(pressure) {
  if (typeof pressure !== 'string') return '';
  const normalized = pressure.trim().toUpperCase();
  if (normalized === 'CRITICAL' || normalized === 'HIGH') return 'critical';
  if (normalized === 'MODERATE') return 'warning';
  if (normalized === 'LOW') return 'healthy';
  return '';
}

function confidenceClass(confidencePct) {
  const parsed = Number(confidencePct);
  if (!Number.isFinite(parsed)) return '';
  if (parsed >= 80) return 'healthy';
  if (parsed >= 60) return 'warning';
  return 'critical';
}

function freshnessClass(value) {
  if (value === 'fresh') return 'healthy';
  if (value === 'aging') return 'warning';
  if (value === 'stale') return 'critical';
  return '';
}

/**
 * useCountUp — animates a numeric value from 0 to target using
 * requestAnimationFrame with easeOutQuart easing.
 */
function useCountUp(target, duration = 1500, decimals = 2) {
  const [display, setDisplay] = useState(0);
  const prevTarget = useRef(0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (target === null || target === undefined || !Number.isFinite(target)) {
      setDisplay(0);
      return;
    }
    const startValue = prevTarget.current;
    const delta = target - startValue;
    if (Math.abs(delta) < 0.001) {
      setDisplay(target);
      prevTarget.current = target;
      return;
    }
    const startTime = performance.now();

    const animate = (now) => {
      const elapsed = now - startTime;
      const progress = Math.min(1, elapsed / duration);
      // easeOutQuart: 1 - (1 - t)^4
      const eased = 1 - Math.pow(1 - progress, 4);
      const current = startValue + delta * eased;
      setDisplay(Number(current.toFixed(decimals)));

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate);
      } else {
        setDisplay(target);
        prevTarget.current = target;
      }
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration, decimals]);

  return display;
}

export default function TopBar({ data, onModeChange }) {
  const [elapsed, setElapsed] = useState(data.elapsedSeconds || 0);
  const [activeMode, setActiveMode] = useState(data.mode || 'ALERT');
  const targetPopulation = data.populationAtRisk ?? data.population ?? 47000;
  const [displayPopulation, setDisplayPopulation] = useState(targetPopulation);
  const [popJump, setPopJump] = useState(false);
  const prevPopulation = useRef(targetPopulation);

  const floodAreaTarget = data.floodAreaSqKm ?? null;
  const displayFloodArea = useCountUp(floodAreaTarget, 2000, 2);

  useEffect(() => {
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Sync mode from parent (e.g. WebSocket status_update)
  useEffect(() => {
    if (data.mode && data.mode !== activeMode) {
      setActiveMode(data.mode);
    }
  }, [data.mode]);

  useEffect(() => {
    if (targetPopulation === prevPopulation.current) return;

    const isLargeJump =
      prevPopulation.current > 0 &&
      (targetPopulation - prevPopulation.current) / prevPopulation.current >= 0.2;

    if (isLargeJump) {
      setPopJump(true);
    } else {
      setPopJump(false);
    }

    const frames = 30;
    const frameDurationMs = 33;
    const startValue = displayPopulation;
    const delta = targetPopulation - startValue;
    let currentFrame = 0;
    let jumpTimeoutId;

    const intervalId = setInterval(() => {
      currentFrame += 1;

      if (currentFrame >= frames) {
        setDisplayPopulation(targetPopulation);
        clearInterval(intervalId);
        if (isLargeJump) {
          jumpTimeoutId = setTimeout(() => setPopJump(false), 500);
        }
        return;
      }

      const nextValue = Math.round(startValue + (delta * currentFrame) / frames);
      setDisplayPopulation(nextValue);
    }, frameDurationMs);

    prevPopulation.current = targetPopulation;

    return () => {
      clearInterval(intervalId);
      if (jumpTimeoutId) clearTimeout(jumpTimeoutId);
    };
  }, [targetPopulation]);

  const wlColor = waterLevelColor(data.waterLevelMeters);
  const wlPct = Math.min((data.waterLevelMeters / 6) * 100, 100);
  const isWlRed = data.waterLevelMeters >= 4.0;
  const normalizedConnectionStatus = typeof data.connectionStatus === 'string'
    ? data.connectionStatus.toLowerCase()
    : '';
  const connectionDotClass = normalizedConnectionStatus === 'connected'
    ? 'online'
    : (normalizedConnectionStatus === 'connecting' || normalizedConnectionStatus === 'reconnecting')
      ? 'amber'
      : 'offline';
  const trustCockpit = data.trustCockpit && typeof data.trustCockpit === 'object'
    ? data.trustCockpit
    : null;
  const pressureToneClass = usagePressureClass(trustCockpit?.pressure);
  const confidenceToneClass = confidenceClass(trustCockpit?.confidencePct);
  const freshnessToneClass = freshnessClass(trustCockpit?.freshnessState);
  const directorButtonLabel = data.directorFallbackActive
    ? 'DIR AUTO'
    : data.directorModeEnabled
      ? 'DIR LIVE'
      : 'DIRECTOR';
  const confidenceLabel = trustCockpit
    ? Number.isFinite(trustCockpit.confidencePct)
      ? `${Math.round(trustCockpit.confidencePct)}%`
      : trustCockpit.confidenceLabel || '--'
    : '--';
  const citationLabel = trustCockpit
    ? Number.isFinite(trustCockpit.citationCount)
      ? `${Math.max(0, Math.round(trustCockpit.citationCount))}`
      : '--'
    : '--';
  const freshnessLabel = trustCockpit?.freshnessLabel || '--';

  return (
    <div className="topbar">
      {/* Brand */}
      <div className="topbar-brand">
        <span className="topbar-logo">&#9670;</span>
        <span className="topbar-title">HAWK EYE</span>
        <span className="topbar-subtitle">{data.incidentName}</span>
      </div>

      {/* Timer */}
      <div className="topbar-timer">
        <span className="topbar-timer-label">INCIDENT</span>
        <span className="topbar-timer-value">{formatTime(elapsed)}</span>
      </div>

      {/* Mode buttons */}
      <div className="topbar-modes">
        {MODES.map((mode) => (
          <button
            key={mode}
            className={`topbar-mode-btn ${activeMode === mode ? 'active' : ''} ${mode === 'ACTION' ? 'mode-action' : ''}`}
            onClick={() => { setActiveMode(mode); onModeChange?.(mode); }}
          >
            {mode}
          </button>
        ))}
        <button
          className={`topbar-mode-btn topbar-director-btn ${data.directorModeEnabled ? 'active' : ''} ${data.directorFallbackActive ? 'fallback' : ''}`}
          onClick={() => data.onToggleDirectorMode?.()}
          title={data.directorModeEnabled ? 'Disable director mode' : 'Enable director mode'}
        >
          {directorButtonLabel}
        </button>
        <button
          className={`topbar-mode-btn topbar-demo-btn ${data.demoRunning ? 'active' : ''}`}
          onClick={data.onStartDemo}
          disabled={Boolean(data.demoLocked)}
          title={data.demoLocked ? 'Demo playback is controlled by Director fallback' : 'Run demo timeline'}
        >
          {data.demoRunning ? 'STOP' : 'DEMO'}
        </button>
      </div>

      {/* Stats */}
      <div className="topbar-stats">
        <div className="topbar-stat">
          <span className="topbar-stat-label">POPULATION</span>
          <span className={`topbar-stat-value ${popJump ? 'pop-jump' : ''} critical`}>
            {formatPopulation(displayPopulation)}
          </span>
        </div>

        <div className="topbar-stat">
          <span className="topbar-stat-label">WATER LEVEL</span>
          <div className={`topbar-gauge ${isWlRed ? 'pulse-red' : ''}`}>
            <div
              className={`topbar-gauge-fill ${isWlRed ? 'bg-red' : ''}`}
              style={{ width: `${wlPct}%`, background: isWlRed ? 'var(--critical)' : wlColor }}
            />
          </div>
          <span className="topbar-stat-value" style={{ color: wlColor }}>
            {data.waterLevelMeters.toFixed(1)}m
          </span>
        </div>

        {floodAreaTarget !== null && (
          <div className="topbar-stat">
            <span className="topbar-stat-label">FLOOD AREA</span>
            <span className="topbar-stat-value topbar-stat-flood">
              {displayFloodArea} km&sup2;
            </span>
          </div>
        )}
      </div>

      {trustCockpit && (
        <div className="topbar-trust-cockpit">
          <div className="topbar-trust-chip">
            <span className="topbar-trust-label">PRESS</span>
            <span className={`topbar-trust-value ${pressureToneClass}`}>
              {trustCockpit.pressure || 'NOMINAL'}
            </span>
          </div>
          <div className="topbar-trust-chip">
            <span className="topbar-trust-label">CITES</span>
            <span className="topbar-trust-value">{citationLabel}</span>
          </div>
          <div className="topbar-trust-chip">
            <span className="topbar-trust-label">CONF</span>
            <span className={`topbar-trust-value ${confidenceToneClass}`}>
              {confidenceLabel}
            </span>
          </div>
          <div className="topbar-trust-chip">
            <span className="topbar-trust-label">FRESH</span>
            <span className={`topbar-trust-value ${freshnessToneClass}`}>
              {freshnessLabel}
            </span>
          </div>
        </div>
      )}

      {/* Connection */}
      <div className="topbar-connection">
        <span
          className={`topbar-dot ${connectionDotClass}`}
        />
        <div className="topbar-connection-meta">
          <span className="topbar-connection-text">
            {data.connectionStatus ? data.connectionStatus.toUpperCase() : 'OFFLINE'}
          </span>
          {data.connectionStatusDetail && (
            <span className="topbar-usage-text">{data.connectionStatusDetail}</span>
          )}
          {data.usageSummary && (
            <span className={`topbar-usage-text ${usagePressureClass(data.usagePressure)}`}>
              {data.usageSummary}
            </span>
          )}
          {data.videoStatusLabel && (
            <span className="topbar-video-text">{data.videoStatusLabel}</span>
          )}
          {data.directorStatus && (
            <span className={`topbar-director-text ${data.directorFallbackActive ? 'fallback' : data.directorModeEnabled ? 'live' : ''}`}>
              {data.directorStatus}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
