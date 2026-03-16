import { useState, useEffect, useRef, useMemo } from 'react';
import EarthEnginePanel from '../EarthEnginePanel';
import FloodFrequencyChart from './FloodFrequencyChart';
import YearlyTrendChart from './YearlyTrendChart';
import CascadeFlowChart from './CascadeFlowChart';
import InfrastructureRiskChart from './InfrastructureRiskChart';
import VulnerabilityRankingChart from './VulnerabilityRankingChart';
import PointIntelCard from './PointIntelCard';
import './charts.css';

const DATA_FRESH_DURATION_MS = 2500;

/**
 * Tracks whether a data value has freshly changed.
 * Returns true for DATA_FRESH_DURATION_MS after the value changes,
 * then automatically resets to false.
 */
function useDataFresh(value) {
  const [fresh, setFresh] = useState(false);
  const prevRef = useRef(undefined);
  const timerRef = useRef(null);

  useEffect(() => {
    // Skip the initial mount (when prevRef is still undefined)
    if (prevRef.current === undefined) {
      prevRef.current = value;
      return;
    }
    // Only fire when value transitions from falsy to truthy, or identity changes
    if (value && value !== prevRef.current) {
      prevRef.current = value;
      setFresh(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setFresh(false);
      }, DATA_FRESH_DURATION_MS);
    }
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [value]);

  return fresh;
}

/**
 * AnalyticsDashboard — merged analytics panel (columns 7–10).
 *
 * Two-tab layout:
 *   INTEL     — Recon & Agent Feed + Earth Engine Analysis (existing panels)
 *   BIGQUERY  — Reactive BigQuery charts that animate in when data arrives
 *
 * Props:
 *  - chartData        : accumulated chart_update payloads keyed by chart name
 *  - transcripts      : array of { role, text, timestamp } for mirror
 *  - reconFeed        : existing recon feed data (SAR provenance source)
 *  - earthEngineData  : full data prop passed through to EarthEnginePanel
 *  - onComparisonChange, onTimeSliderChange, replay, onReplayToggle,
 *    onReplaySeek, onReplaySpeedChange, onReplayJumpToHotspot
 *    — forwarded to EarthEnginePanel
 */
export default function AnalyticsDashboard({
  chartData,
  pointAnalytics,
  transcripts,
  reconFeed,
  earthEngineData,
  onComparisonChange,
  onTimeSliderChange,
  replay,
  onReplayToggle,
  onReplaySeek,
  onReplaySpeedChange,
  onReplayJumpToHotspot,
}) {
  const [activeTab, setActiveTab] = useState('intel');
  const [hasUnseenCharts, setHasUnseenCharts] = useState(false);
  const mirrorRef = useRef(null);

  // Auto-scroll transcript mirror to bottom on new entries
  useEffect(() => {
    if (mirrorRef.current) {
      mirrorRef.current.scrollTop = mirrorRef.current.scrollHeight;
    }
  }, [transcripts]);

  // ── Derive chart data ──────────────────────────────────────
  const floodAnalytics = chartData?.flood_analytics ?? null;
  const cascadeAnalysis = chartData?.cascade_analysis ?? null;
  const vulnerabilityRanking = chartData?.vulnerability_ranking ?? null;

  // ── Data-fresh glow on arrival ─────────────────────────────
  const floodFresh = useDataFresh(floodAnalytics);
  const cascadeFresh = useDataFresh(cascadeAnalysis);
  const vulnFresh = useDataFresh(vulnerabilityRanking);
  const viewportFresh = useDataFresh(pointAnalytics?.data);

  // ── Viewport-scoped derived values ─────────────────────────
  const vpFlood = pointAnalytics?.data?.flood_frequency;
  const vpInfra = pointAnalytics?.data?.infrastructure;
  const vpRisk = pointAnalytics?.data?.risk_level;
  const vpRadius = pointAnalytics?.radius;

  // Infrastructure Impact: prefer viewport-local counts when available
  const displayInfraCurrent = useMemo(() => {
    if (vpInfra && vpInfra.total > 0) {
      return {
        hospitals: vpInfra.hospitals,
        schools: vpInfra.schools,
        power_stations: vpInfra.power_stations,
        shelters: vpInfra.shelters,
      };
    }
    return cascadeAnalysis?.infrastructure_current ?? null;
  }, [vpInfra, cascadeAnalysis]);

  const displayInfraExpanded = useMemo(() => {
    if (vpInfra && vpInfra.total > 0) {
      // When showing viewport data, use the region current as "expanded"
      // so the delta shows how many more exist region-wide
      return cascadeAnalysis?.infrastructure_current ?? null;
    }
    return cascadeAnalysis?.infrastructure_expanded ?? null;
  }, [vpInfra, cascadeAnalysis]);

  const hasAnyCharts = Boolean(
    floodAnalytics || cascadeAnalysis || vulnerabilityRanking,
  );

  const chartCount = [floodAnalytics, cascadeAnalysis, vulnerabilityRanking]
    .filter(Boolean).length;

  // ── Unseen chart indicator ─────────────────────────────────
  // When new chart data arrives while viewing INTEL, pulse the BIGQUERY tab
  useEffect(() => {
    if (hasAnyCharts && activeTab !== 'analytics') {
      setHasUnseenCharts(true);
    }
  }, [hasAnyCharts, chartData]); // eslint-disable-line react-hooks/exhaustive-deps

  // When point analytics arrives, switch to BIGQUERY tab
  useEffect(() => {
    if (pointAnalytics?.data && activeTab !== 'analytics') {
      setActiveTab('analytics');
      setHasUnseenCharts(false);
    }
  }, [pointAnalytics?.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeTab === 'analytics') {
      setHasUnseenCharts(false);
    }
  }, [activeTab]);

  // ── Auto-switch to BIGQUERY tab on first chart arrival ─────
  const hasEverSwitched = useRef(false);
  useEffect(() => {
    if (hasAnyCharts && !hasEverSwitched.current) {
      hasEverSwitched.current = true;
      setActiveTab('analytics');
      setHasUnseenCharts(false);
    }
  }, [hasAnyCharts]);

  // Most recent agent transcript line for teleprompter
  const latestAgentText = useMemo(() => {
    if (!Array.isArray(transcripts) || transcripts.length === 0) return null;
    for (let i = transcripts.length - 1; i >= 0; i--) {
      if (transcripts[i].role === 'agent' && transcripts[i].text) {
        return transcripts[i].text;
      }
    }
    return null;
  }, [transcripts]);

  return (
    <div className="panel analytics">
      {/* ── Panel header ──────────────────────────────────────── */}
      <div className="panel-header">
        <span>Analytics</span>
        {hasAnyCharts && (
          <span className="chart-badge chart-badge--cyan">LIVE</span>
        )}
      </div>

      {/* ── Tab bar — tactical mode switch ────────────────────── */}
      <div className="analytics-tab-bar">
        <button
          className={`analytics-tab ${activeTab === 'intel' ? 'analytics-tab--active' : ''}`}
          onClick={() => setActiveTab('intel')}
        >
          <span className="analytics-tab-icon">&#9670;</span>
          INTEL
        </button>
        <button
          className={`analytics-tab ${activeTab === 'analytics' ? 'analytics-tab--active' : ''}`}
          onClick={() => setActiveTab('analytics')}
        >
          <span className="analytics-tab-icon">&#9632;</span>
          BIGQUERY
          {hasUnseenCharts && (
            <span className="analytics-tab-indicator" />
          )}
          {hasAnyCharts && activeTab === 'analytics' && (
            <span className="analytics-tab-count">{chartCount}</span>
          )}
        </button>
      </div>

      {/* ── Tab content ───────────────────────────────────────── */}
      <div className="analytics-tab-content">

        {/* ══ INTEL TAB ═══════════════════════════════════════ */}
        {activeTab === 'intel' && (
          <div className="analytics-dashboard">
            {/* SAR Provenance + Transcript Mirror */}
            <div className="analytics-section" style={{ animationDelay: '0s' }}>
              <div className="analytics-section-header">Recon &amp; Agent Feed</div>

              <div className="sar-provenance-card">
                <div className="sar-provenance-item">
                  <span className="sar-provenance-label">Mode</span>
                  <span className="sar-provenance-value">
                    {reconFeed?.activeMode ?? 'SAR'}
                  </span>
                </div>
                <div className="sar-provenance-item">
                  <span className="sar-provenance-label">Last Update</span>
                  <span className="sar-provenance-value">
                    {reconFeed?.timestamp
                      ? new Date(reconFeed.timestamp).toLocaleTimeString([], { hour12: false })
                      : '—'}
                  </span>
                </div>
              </div>

              <div className="transcript-mirror" ref={mirrorRef}>
                {latestAgentText ? (
                  <div className="transcript-mirror-text">{latestAgentText}</div>
                ) : (
                  <div className="transcript-mirror-empty">
                    Agent transcript pending
                  </div>
                )}
              </div>
            </div>

            {/* Earth Engine Analysis (delegated) */}
            <EarthEnginePanel
              data={earthEngineData}
              onComparisonChange={onComparisonChange}
              onTimeSliderChange={onTimeSliderChange}
              replay={replay}
              onReplayToggle={onReplayToggle}
              onReplaySeek={onReplaySeek}
              onReplaySpeedChange={onReplaySpeedChange}
              onReplayJumpToHotspot={onReplayJumpToHotspot}
            />
          </div>
        )}

        {/* ══ BIGQUERY TAB ════════════════════════════════════ */}
        {activeTab === 'analytics' && (
          <div className="analytics-dashboard">
            {hasAnyCharts ? (
              <>
                {/* ── Point Intel (click-to-query) ───────────── */}
                {pointAnalytics && (
                  <PointIntelCard pointAnalytics={pointAnalytics} />
                )}

                {/* ── Flood History ─────────────────────────── */}
                {floodAnalytics && (
                  <div className={`analytics-section${floodFresh || viewportFresh ? ' data-fresh' : ''}`} style={{ animationDelay: '0s' }}>
                    <div className="analytics-section-header">
                      Flood History
                      {vpFlood ? (
                        <span className="chart-badge-group" style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
                          <span className="chart-badge chart-badge--green viewport-num-transition">
                            {Number(vpFlood.total_events).toLocaleString()} LOCAL
                          </span>
                          {vpRadius && (
                            <span className="chart-badge chart-badge--cyan viewport-scope-tag">
                              {vpRadius}km
                            </span>
                          )}
                        </span>
                      ) : floodAnalytics.total_events != null ? (
                        <span className="chart-badge chart-badge--cyan" style={{ marginLeft: 'auto' }}>
                          {Number(floodAnalytics.total_events).toLocaleString()} events
                        </span>
                      ) : null}
                    </div>

                    {floodAnalytics.monthly_frequency && (
                      <div style={{ marginBottom: 14 }}>
                        <div className="analytics-subsection-label">Monthly Frequency</div>
                        <FloodFrequencyChart data={floodAnalytics.monthly_frequency} />
                      </div>
                    )}

                    {floodAnalytics.yearly_trend && (
                      <div>
                        <div className="analytics-subsection-label">Yearly Trend</div>
                        <YearlyTrendChart data={floodAnalytics.yearly_trend} />
                      </div>
                    )}
                  </div>
                )}

                {/* ── Consequence Cascade ───────────────────── */}
                {cascadeAnalysis && (
                  <div className={`analytics-section${cascadeFresh || viewportFresh ? ' data-fresh' : ''}`} style={{ animationDelay: '0.15s' }}>
                    <div className="analytics-section-header">
                      Consequence Cascade
                      {vpRisk ? (
                        <span className="chart-badge-group" style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
                          <span className={`chart-badge viewport-num-transition ${
                            vpRisk === 'CRITICAL' ? 'chart-badge--red' :
                            vpRisk === 'HIGH' ? 'chart-badge--orange' :
                            vpRisk === 'MODERATE' ? 'chart-badge--cyan' :
                            'chart-badge--green'
                          }`}>
                            {vpRisk}
                          </span>
                          {vpRadius && (
                            <span className="chart-badge chart-badge--cyan viewport-scope-tag">
                              {vpRadius}km
                            </span>
                          )}
                        </span>
                      ) : cascadeAnalysis.population_at_risk != null ? (
                        <span className="chart-badge chart-badge--red" style={{ marginLeft: 'auto' }}>
                          {Number(cascadeAnalysis.population_at_risk).toLocaleString()} at risk
                        </span>
                      ) : null}
                    </div>
                    <CascadeFlowChart data={cascadeAnalysis} />

                    {(displayInfraCurrent || displayInfraExpanded) && (
                      <div style={{ marginTop: 10 }}>
                        <div className="analytics-subsection-label">
                          Infrastructure Impact
                          {vpInfra && vpInfra.total > 0 && vpRadius && (
                            <span className="viewport-scope-inline"> IN VIEW ({vpRadius}km)</span>
                          )}
                        </div>
                        <InfrastructureRiskChart
                          current={displayInfraCurrent}
                          expanded={displayInfraExpanded}
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* ── Vulnerability Ranking ─────────────────── */}
                {vulnerabilityRanking?.ranking && (
                  <div className={`analytics-section${vulnFresh ? ' data-fresh' : ''}`} style={{ animationDelay: '0.3s' }}>
                    <div className="analytics-section-header">
                      Vulnerability Ranking
                      <span className="chart-badge chart-badge--orange" style={{ marginLeft: 'auto' }}>
                        {vulnerabilityRanking.ranking.length} facilities
                      </span>
                    </div>
                    <VulnerabilityRankingChart data={vulnerabilityRanking.ranking} />
                  </div>
                )}
              </>
            ) : (
              /* ── Empty state ───────────────────────────── */
              <div className="analytics-empty-state">
                {pointAnalytics && (
                  <PointIntelCard pointAnalytics={pointAnalytics} />
                )}
                <div className="analytics-empty-icon">&#9632;</div>
                <div className="analytics-empty-grid">
                  <div className="analytics-empty-cell" />
                  <div className="analytics-empty-cell" />
                  <div className="analytics-empty-cell" />
                  <div className="analytics-empty-cell" />
                </div>
                <div className="analytics-empty-title">AWAITING BIGQUERY DATA</div>
                <div className="analytics-empty-text">
                  Charts will populate when the agent queries flood history,
                  cascade analysis, or vulnerability data
                </div>
                <div className="analytics-empty-hint">
                  Try: &ldquo;What&rsquo;s the flood risk cascade for Jakarta?&rdquo;
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
