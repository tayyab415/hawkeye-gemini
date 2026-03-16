import { useRef, useEffect, useState, useCallback } from 'react';
import CesiumGlobe from './CesiumGlobe';
import HudOverlay from './strategic/HudOverlay';
import DataLayerPanel from './strategic/DataLayerPanel';
import PipDroneView from './strategic/PipDroneView';
import './StrategicViewPanel.css';

/**
 * StrategicViewPanel — hosts the Cesium 3D globe plus all tactical overlays.
 *
 * Receives from App.jsx:
 *   data.globeRef          – shared ref to CesiumGlobe imperative API
 *   data.layerToggles      – { FLOOD_EXTENT: bool, TRIAGE_ZONES: bool, ... }
 *   data.onToggleLayer     – (layerId) => void
 *   data.cameraMode        – string | null  ('ORBIT', 'BIRD_EYE', etc.)
 *   data.showScanlines     – bool (default true)
 *   data.onToggleScanlines – () => void
 *   data.reconFeed         – recon feed state for PiP
 *   data.showPip           – bool
 *   data.onTogglePip       – () => void
 *   data.sessionStartTime  – number (Date.now() when session started)
 */
export default function StrategicViewPanel({ data }) {
  const globeRef = data.globeRef || useRef(null);
  const activeStep = data.timeWindow?.steps?.[data.timeWindow?.activeIndex ?? 0];

  // Expose globe methods to parent via ref
  useEffect(() => {
    if (data.globeRef) {
      data.globeRef.current = globeRef.current;
    }
  }, [data.globeRef]);

  // ── getViewer callback for child components ─────────────────
  const getViewer = useCallback(() => {
    return globeRef.current?.getViewer?.() ?? null;
  }, []);

  return (
    <div className="panel strategic">
      <div className="panel-header">
        <span>Strategic View</span>
        <div className="strat-header-controls">
          {data.onToggleScanlines && (
            <button
              className={`strat-scanline-btn ${data.showScanlines ? 'strat-scanline-btn--on' : ''}`}
              onClick={data.onToggleScanlines}
              title="Toggle CRT scanlines"
            >
              CRT
            </button>
          )}
          <span className="strat-mode-tag">{data.mode}</span>
        </div>
      </div>
      <div className="strat-viewport">
        {/* Cesium 3D Globe */}
        <div className="strat-globe-container">
          <CesiumGlobe
            ref={globeRef}
            apiKey={import.meta.env.VITE_GOOGLE_MAPS_API_KEY}
          />
        </div>

        {data.hudNotification && (
          <div className="hud-notification">◆ {data.hudNotification}</div>
        )}

        {/* HUD Tactical Overlay */}
        <HudOverlay
          getViewer={getViewer}
          cameraMode={data.cameraMode}
          showScanlines={data.showScanlines !== false}
          sessionStartTime={data.sessionStartTime}
        />

        {/* Data Layer Toggle Panel */}
        <DataLayerPanel
          layers={data.layerToggles || {}}
          onToggle={data.onToggleLayer || (() => {})}
          getViewer={getViewer}
          earthEngineRuntime={data.earthEngineRuntime}
          temporalControl={data.temporalControl}
          incidentReplay={data.incidentReplay}
          onReplayToggle={data.onReplayToggle}
          onReplaySeek={data.onReplaySeek}
          onReplaySpeedChange={data.onReplaySpeedChange}
          onReplayJumpToHotspot={data.onReplayJumpToHotspot}
          lowBandwidth={data.lowBandwidth}
          fieldMode={data.fieldMode}
          bandwidthProfile={data.bandwidthProfile}
        />

        {/* PiP Drone View */}
        <PipDroneView
          reconFeed={data.reconFeed}
          visible={data.showPip || false}
          onToggle={data.onTogglePip || (() => {})}
        />
      </div>
    </div>
  );
}
