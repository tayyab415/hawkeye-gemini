import { useEffect, useRef, useState, useCallback } from 'react';
import './HudOverlay.css';

/**
 * HUD Tactical Overlay — pure CSS/HTML command-center decoration
 * layered on top of the Cesium globe. pointer-events:none throughout.
 *
 * Reads live camera state (heading, altitude, lat/lng) via the Cesium
 * viewer reference and updates the readouts at ~4 FPS.
 */
export default function HudOverlay({ getViewer, cameraMode, showScanlines, sessionStartTime }) {
  const [heading, setHeading] = useState(0);
  const [altitude, setAltitude] = useState(0);
  const [lat, setLat] = useState(-6.225);
  const [lng, setLng] = useState(106.855);
  const [clock, setClock] = useState('');
  const [duration, setDuration] = useState('00:00:00');
  const rafRef = useRef(null);
  const lastUpdateRef = useRef(0);

  // ── Camera state polling (throttled to ~4 FPS) ────────────────
  const pollCamera = useCallback(() => {
    const now = performance.now();
    if (now - lastUpdateRef.current > 250) {
      lastUpdateRef.current = now;
      try {
        const viewer = typeof getViewer === 'function' ? getViewer() : null;
        if (viewer && !viewer.isDestroyed()) {
          const cam = viewer.camera;
          const carto = cam.positionCartographic;
          if (carto) {
            const CesiumMath = window.Cesium?.Math;
            const toDeg = CesiumMath
              ? CesiumMath.toDegrees
              : (r) => (r * 180) / Math.PI;
            setHeading(toDeg(cam.heading));
            setAltitude(Math.round(carto.height));
            setLat(toDeg(carto.latitude));
            setLng(toDeg(carto.longitude));
          }
        }
      } catch {
        /* viewer not ready yet — ignore */
      }
    }
    rafRef.current = requestAnimationFrame(pollCamera);
  }, [getViewer]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(pollCamera);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [pollCamera]);

  // ── Clock + duration ticker (1 Hz) ────────────────────────────
  useEffect(() => {
    function tick() {
      const now = new Date();
      const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
      const dd = String(now.getUTCDate()).padStart(2, '0');
      const mon = months[now.getUTCMonth()];
      const yyyy = now.getUTCFullYear();
      const hh = String(now.getUTCHours()).padStart(2, '0');
      const mm = String(now.getUTCMinutes()).padStart(2, '0');
      const ss = String(now.getUTCSeconds()).padStart(2, '0');
      setClock(`${dd} ${mon} ${yyyy} ${hh}:${mm}:${ss} UTC`);

      if (sessionStartTime) {
        const elapsed = Math.max(0, Math.floor((now.getTime() - sessionStartTime) / 1000));
        const eh = String(Math.floor(elapsed / 3600)).padStart(2, '0');
        const em = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
        const es = String(elapsed % 60).padStart(2, '0');
        setDuration(`${eh}:${em}:${es}`);
      }
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [sessionStartTime]);

  // ── Derived display values ────────────────────────────────────
  const compassRotation = -heading; // rotate needle opposite to heading
  const altDisplay = altitude >= 1000
    ? `${(altitude / 1000).toFixed(1)}km`
    : `${altitude}m`;
  const latDir = lat >= 0 ? 'N' : 'S';
  const lngDir = lng >= 0 ? 'E' : 'W';
  const latDisplay = `${Math.abs(lat).toFixed(3)}\u00B0${latDir}`;
  const lngDisplay = `${Math.abs(lng).toFixed(3)}\u00B0${lngDir}`;

  const cardinalFromHeading = (h) => {
    const norm = ((h % 360) + 360) % 360;
    if (norm < 22.5 || norm >= 337.5) return 'N';
    if (norm < 67.5) return 'NE';
    if (norm < 112.5) return 'E';
    if (norm < 157.5) return 'SE';
    if (norm < 202.5) return 'S';
    if (norm < 247.5) return 'SW';
    if (norm < 292.5) return 'W';
    return 'NW';
  };

  return (
    <div className="hud-overlay">
      {/* Corner brackets */}
      <div className="hud-corner hud-corner--tl" />
      <div className="hud-corner hud-corner--tr" />
      <div className="hud-corner hud-corner--bl" />
      <div className="hud-corner hud-corner--br" />

      {/* Subtle crosshair */}
      <div className="hud-crosshair-h" />
      <div className="hud-crosshair-v" />

      {/* Top center: compass + altitude + coords */}
      <div className="hud-top-bar">
        <div className="hud-compass">
          <div
            className="hud-compass-needle"
            style={{ transform: `rotate(${compassRotation}deg)` }}
          />
          <span className="hud-compass-label">{cardinalFromHeading(heading)}</span>
        </div>
        <span className="hud-altitude">
          ALT:<span className="hud-altitude-value">{altDisplay}</span>
        </span>
        <span className="hud-coords">
          <span className="hud-coords-value">{latDisplay} {lngDisplay}</span>
        </span>
      </div>

      {/* Top right: REC + timestamp + duration */}
      <div className="hud-top-right">
        <div className="hud-rec">
          <span className="hud-rec-dot" />
          REC
        </div>
        <div className="hud-timestamp">{clock}</div>
        <div className="hud-duration">T+ {duration}</div>
      </div>

      {/* Bottom status bar */}
      <div className="hud-bottom-bar">
        HAWK EYE v1.0 &nbsp;|&nbsp; STRATEGIC VIEW &nbsp;|&nbsp; 17 SERVICES ACTIVE &nbsp;|&nbsp; GROUNDSOURCE: 2.6M EVENTS
      </div>

      {/* Camera mode badge */}
      {cameraMode && (
        <div className="hud-camera-badge">{cameraMode}</div>
      )}

      {/* Scanline CRT effect */}
      {showScanlines && <div className="hud-scanlines" />}
    </div>
  );
}
