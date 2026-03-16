import { useMemo, useRef, useEffect, useState } from 'react';

const RISK_COLORS = {
  CRITICAL: 'var(--critical)',
  HIGH: '#ff6b35',
  MODERATE: 'var(--warning)',
  LOW: 'var(--accent)',
  MINIMAL: 'var(--success)',
};

const RISK_BG = {
  CRITICAL: 'var(--critical-dim)',
  HIGH: 'rgba(255, 107, 53, 0.15)',
  MODERATE: 'var(--warning-dim)',
  LOW: 'var(--accent-dim)',
  MINIMAL: 'var(--success-dim)',
};

const RISK_FILL = {
  CRITICAL: '#ff4444',
  HIGH: '#ff6b35',
  MODERATE: '#ffaa00',
  LOW: '#00d4ff',
  MINIMAL: '#00ff88',
};

/**
 * Animated gauge bar — shows a fill percentage with a smooth width transition.
 * `value` is the raw number, `max` is the reference maximum for 100% fill.
 */
function GaugeBar({ value, max, color, label, suffix = '' }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const displayVal = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);

  return (
    <div className="vp-gauge">
      <div className="vp-gauge-header">
        <span className="vp-gauge-label">{label}</span>
        <span className="vp-gauge-value" style={{ color }}>{displayVal}{suffix}</span>
      </div>
      <div className="vp-gauge-track">
        <div
          className="vp-gauge-fill"
          style={{ width: `${pct}%`, background: color }}
        />
        {/* Moving scan marker */}
        <div
          className="vp-gauge-marker"
          style={{ left: `${pct}%`, borderColor: color }}
        />
      </div>
    </div>
  );
}

/**
 * PointIntelCard — dynamic viewport intelligence panel.
 *
 * Shows animated gauge bars for flood frequency stats, infrastructure counts,
 * and risk level for the current camera position. All values change as the
 * user pans/zooms the map.
 *
 * Props:
 *  - pointAnalytics: { lat, lng, loading, data, error, radius, source }
 *    where data = { flood_frequency, infrastructure, risk_level }
 */
export default function PointIntelCard({ pointAnalytics }) {
  const { lat, lng, loading, data, error, radius, source } = pointAnalytics || {};
  const [prevData, setPrevData] = useState(null);
  const [flashKey, setFlashKey] = useState(0);
  const prevDataRef = useRef(null);

  const riskColor = RISK_COLORS[data?.risk_level] || 'var(--text-secondary)';
  const riskBg = RISK_BG[data?.risk_level] || 'transparent';
  const riskFill = RISK_FILL[data?.risk_level] || '#555';

  // Flash animation when data changes
  useEffect(() => {
    if (data && data !== prevDataRef.current) {
      prevDataRef.current = data;
      setPrevData(data);
      setFlashKey((k) => k + 1);
    }
  }, [data]);

  const coordStr = useMemo(() => {
    if (lat == null || lng == null) return '—';
    const latDir = lat >= 0 ? 'N' : 'S';
    const lngDir = lng >= 0 ? 'E' : 'W';
    return `${Math.abs(lat).toFixed(4)}\u00B0${latDir}  ${Math.abs(lng).toFixed(4)}\u00B0${lngDir}`;
  }, [lat, lng]);

  const flood = data?.flood_frequency;
  const infra = data?.infrastructure;

  // Risk level gauge percentage
  const riskPct = { CRITICAL: 100, HIGH: 80, MODERATE: 55, LOW: 30, MINIMAL: 10 };

  const isViewport = source === 'viewport';
  const scopeLabel = isViewport ? `${radius || 3}km VIEWPORT` : 'CLICK QUERY';

  if (loading && !data) {
    return (
      <div className="point-intel-card point-intel-card--loading">
        <div className="point-intel-header">
          <span className="point-intel-icon">&#9678;</span>
          <span className="point-intel-title">VIEWPORT ANALYSIS</span>
          <span className="point-intel-coord">{coordStr}</span>
        </div>
        <div className="point-intel-loading">
          <div className="point-intel-spinner" />
          <span>Querying BigQuery...</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="point-intel-card point-intel-card--error">
        <div className="point-intel-header">
          <span className="point-intel-icon">&#9678;</span>
          <span className="point-intel-title">VIEWPORT ANALYSIS</span>
          <span className="point-intel-coord">{coordStr}</span>
        </div>
        <div className="point-intel-error">Query failed: {error}</div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className={`point-intel-card${loading ? ' point-intel-card--refreshing' : ''}`} key={flashKey}>
      {/* Scan line animation on refresh */}
      {loading && <div className="point-intel-scan-line" />}

      {/* Header row */}
      <div className="point-intel-header">
        <span className="point-intel-icon">&#9678;</span>
        <span className="point-intel-title">VIEWPORT ANALYSIS</span>
        <span className="vp-scope-badge">{scopeLabel}</span>
        <span className="point-intel-coord">{coordStr}</span>
      </div>

      {/* Risk level bar — full-width prominent gauge */}
      <div className="vp-risk-row">
        <div className="vp-risk-level-bar">
          <div className="vp-risk-level-track">
            <div
              className="vp-risk-level-fill"
              style={{
                width: `${riskPct[data.risk_level] || 10}%`,
                background: `linear-gradient(90deg, ${riskFill}88, ${riskFill})`,
                boxShadow: `0 0 12px ${riskFill}44`,
              }}
            />
          </div>
          <span className="vp-risk-label">RISK</span>
        </div>
        <span
          className="point-intel-risk-badge"
          style={{ color: riskColor, background: riskBg, borderColor: riskColor }}
        >
          {data.risk_level}
        </span>
      </div>

      {/* Gauge bars — these animate on each camera move */}
      <div className="vp-gauges">
        <GaugeBar
          label="FLOOD EVENTS"
          value={flood?.total_events ?? 0}
          max={120}
          color="#00d4ff"
          suffix=""
        />
        <GaugeBar
          label="AVG DURATION"
          value={flood?.avg_duration_days ?? 0}
          max={30}
          color="#ffaa00"
          suffix="d"
        />
        <GaugeBar
          label="MAX AREA"
          value={flood?.max_area_km2 ?? 0}
          max={60}
          color="#ff6b35"
          suffix=" km²"
        />
        <GaugeBar
          label="AVG AREA"
          value={flood?.avg_area_km2 ?? 0}
          max={40}
          color="#00ff88"
          suffix=" km²"
        />
      </div>

      {/* Last flood date */}
      {flood?.most_recent_date && (
        <div className="vp-last-event">
          <span className="vp-last-event-label">LAST EVENT</span>
          <span className="vp-last-event-value">{flood.most_recent_date}</span>
        </div>
      )}

      {/* Infrastructure breakdown — inline mini-bars */}
      {infra && infra.total > 0 && (
        <div className="vp-infra-section">
          <div className="vp-infra-header">
            INFRASTRUCTURE — {infra.total} within {radius || 3}km
          </div>
          <div className="vp-infra-bars">
            {infra.hospitals > 0 && (
              <div className="vp-infra-bar-row">
                <span className="vp-infra-bar-icon">🏥</span>
                <span className="vp-infra-bar-name">Hospitals</span>
                <div className="vp-infra-bar-track">
                  <div
                    className="vp-infra-bar-fill"
                    style={{
                      width: `${Math.min((infra.hospitals / Math.max(infra.total, 1)) * 100, 100)}%`,
                      background: '#ff4444',
                    }}
                  />
                </div>
                <span className="vp-infra-bar-count" style={{ color: '#ff4444' }}>
                  {infra.hospitals}
                </span>
              </div>
            )}
            {infra.schools > 0 && (
              <div className="vp-infra-bar-row">
                <span className="vp-infra-bar-icon">🏫</span>
                <span className="vp-infra-bar-name">Schools</span>
                <div className="vp-infra-bar-track">
                  <div
                    className="vp-infra-bar-fill"
                    style={{
                      width: `${Math.min((infra.schools / Math.max(infra.total, 1)) * 100, 100)}%`,
                      background: '#ffcc00',
                    }}
                  />
                </div>
                <span className="vp-infra-bar-count" style={{ color: '#ffcc00' }}>
                  {infra.schools}
                </span>
              </div>
            )}
            {infra.power_stations > 0 && (
              <div className="vp-infra-bar-row">
                <span className="vp-infra-bar-icon">⚡</span>
                <span className="vp-infra-bar-name">Power</span>
                <div className="vp-infra-bar-track">
                  <div
                    className="vp-infra-bar-fill"
                    style={{
                      width: `${Math.min((infra.power_stations / Math.max(infra.total, 1)) * 100, 100)}%`,
                      background: '#ff8800',
                    }}
                  />
                </div>
                <span className="vp-infra-bar-count" style={{ color: '#ff8800' }}>
                  {infra.power_stations}
                </span>
              </div>
            )}
            {infra.shelters > 0 && (
              <div className="vp-infra-bar-row">
                <span className="vp-infra-bar-icon">🏕</span>
                <span className="vp-infra-bar-name">Shelters</span>
                <div className="vp-infra-bar-track">
                  <div
                    className="vp-infra-bar-fill"
                    style={{
                      width: `${Math.min((infra.shelters / Math.max(infra.total, 1)) * 100, 100)}%`,
                      background: '#00ff88',
                    }}
                  />
                </div>
                <span className="vp-infra-bar-count" style={{ color: '#00ff88' }}>
                  {infra.shelters}
                </span>
              </div>
            )}
          </div>
          {infra.hospital_names?.length > 0 && (
            <div className="point-intel-names">
              {infra.hospital_names.map((n, i) => (
                <span key={i} className="point-intel-name">{n}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
