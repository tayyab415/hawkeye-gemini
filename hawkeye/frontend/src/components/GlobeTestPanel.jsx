import { useState } from "react";

const TEST_FLOOD_GEOJSON = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [106.85, -6.22],
            [106.86, -6.22],
            [106.86, -6.23],
            [106.85, -6.23],
            [106.85, -6.22],
          ],
        ],
      },
      properties: { name: "Test Flood Zone" },
    },
  ],
};

const FLOOD_STYLE = {
  fillColor: "#00d4ff",
  opacity: 0.35,
  outlineColor: "#00d4ff",
  outlineWidth: 2,
};

const LOCATIONS = [
  { label: "Kampung Melayu", lat: -6.225, lng: 106.855, alt: 1500 },
  { label: "Monas", lat: -6.1754, lng: 106.8272, alt: 2000 },
  { label: "Jakarta Overview", lat: -6.2, lng: 106.845, alt: 15000 },
];

export default function GlobeTestPanel({ globeRef, status }) {
  const [screenshotSrc, setScreenshotSrc] = useState(null);
  const [log, setLog] = useState([]);

  function addLog(msg) {
    setLog((prev) => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev].slice(0, 20));
  }

  function handleFlyTo(loc) {
    globeRef.current?.flyTo(loc.lat, loc.lng, loc.alt, 2);
    addLog(`flyTo → ${loc.label}`);
  }

  async function handleAddOverlay() {
    await globeRef.current?.addGeoJsonOverlay("test-flood", TEST_FLOOD_GEOJSON, FLOOD_STYLE);
    addLog("addGeoJsonOverlay → test-flood");
  }

  function handleRemoveOverlay() {
    globeRef.current?.removeOverlay("test-flood");
    addLog("removeOverlay → test-flood");
  }

  function handleAddMarker() {
    globeRef.current?.addPulsingMarker("emergency-1", -6.225, 106.855, "#ff4444", "EMERGENCY");
    addLog("addPulsingMarker → emergency-1");
  }

  function handleRemoveMarker() {
    globeRef.current?.removeMarker("emergency-1");
    addLog("removeMarker → emergency-1");
  }

  async function handleScreenshot() {
    try {
      const base64 = await globeRef.current?.captureScreenshot();
      if (base64) {
        setScreenshotSrc(`data:image/jpeg;base64,${base64}`);
        addLog(`captureScreenshot → ${Math.round(base64.length / 1024)}KB`);
      }
    } catch (err) {
      addLog(`captureScreenshot FAILED: ${err.message}`);
    }
  }

  return (
    <div style={styles.panel}>
      <h2 style={styles.title}>Globe Test Panel</h2>

      <Section label="Viewer Status">
        <div style={styles.statusCard}>
          <div style={styles.statusPhase(status?.phase)}>{status?.phase ?? "idle"}</div>
          <div style={styles.statusMessage}>{status?.message ?? "Viewer not started"}</div>
          <div style={styles.helperText}>
            Console smoke test: <code>window.globeRef.flyTo(-6.225, 106.855, 3000, 2)</code>
          </div>
        </div>
      </Section>

      <Section label="Camera">
        {LOCATIONS.map((loc) => (
          <Btn key={loc.label} onClick={() => handleFlyTo(loc)}>
            Fly to {loc.label}
          </Btn>
        ))}
      </Section>

      <Section label="GeoJSON Overlay">
        <Btn onClick={handleAddOverlay}>Add Flood Polygon</Btn>
        <Btn onClick={handleRemoveOverlay} variant="danger">
          Remove Flood Polygon
        </Btn>
      </Section>

      <Section label="Markers">
        <Btn onClick={handleAddMarker}>Add Emergency Marker</Btn>
        <Btn onClick={handleRemoveMarker} variant="danger">
          Remove Marker
        </Btn>
      </Section>

      <Section label="Screenshot">
        <Btn onClick={handleScreenshot}>Capture Screenshot</Btn>
        {screenshotSrc && (
          <img
            src={screenshotSrc}
            alt="Screenshot"
            style={styles.screenshot}
          />
        )}
      </Section>

      <Section label="Log">
        <div style={styles.log}>
          {log.map((entry, i) => (
            <div key={i} style={styles.logEntry}>
              {entry}
            </div>
          ))}
          {log.length === 0 && (
            <div style={styles.logEmpty}>No actions yet</div>
          )}
        </div>
      </Section>
    </div>
  );
}

function Section({ label, children }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionLabel}>{label}</div>
      {children}
    </div>
  );
}

function Btn({ children, onClick, variant }) {
  const bg = variant === "danger" ? "#ff4444" : "#1a2332";
  const hoverBg = variant === "danger" ? "#cc3333" : "#253448";
  return (
    <button
      onClick={onClick}
      style={{ ...styles.btn, backgroundColor: bg }}
      onMouseEnter={(e) => (e.target.style.backgroundColor = hoverBg)}
      onMouseLeave={(e) => (e.target.style.backgroundColor = bg)}
    >
      {children}
    </button>
  );
}

const styles = {
  panel: {
    position: "absolute",
    top: 12,
    right: 12,
    width: 280,
    maxHeight: "calc(100vh - 24px)",
    overflowY: "auto",
    backgroundColor: "rgba(10, 14, 23, 0.92)",
    borderRadius: 8,
    border: "1px solid #1a2332",
    padding: 16,
    zIndex: 1000,
    fontFamily: "'Inter', system-ui, sans-serif",
    color: "#e0e0e0",
  },
  title: {
    margin: "0 0 12px",
    fontSize: 15,
    fontWeight: 600,
    color: "#00d4ff",
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  section: {
    marginBottom: 14,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#6b7a8d",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 6,
  },
  btn: {
    display: "block",
    width: "100%",
    padding: "8px 12px",
    marginBottom: 4,
    border: "1px solid #2a3a4e",
    borderRadius: 4,
    color: "#e0e0e0",
    fontSize: 13,
    cursor: "pointer",
    textAlign: "left",
    transition: "background-color 0.15s",
  },
  screenshot: {
    width: "100%",
    marginTop: 8,
    borderRadius: 4,
    border: "1px solid #2a3a4e",
  },
  log: {
    maxHeight: 140,
    overflowY: "auto",
    fontSize: 11,
    fontFamily: "'SF Mono', 'Fira Code', monospace",
  },
  logEntry: {
    padding: "2px 0",
    color: "#8899aa",
    borderBottom: "1px solid #1a2332",
  },
  logEmpty: {
    color: "#4a5568",
    fontStyle: "italic",
  },
  statusCard: {
    padding: 10,
    borderRadius: 6,
    border: "1px solid #23364a",
    backgroundColor: "rgba(16, 22, 35, 0.85)",
    marginBottom: 4,
  },
  statusPhase: (phase) => ({
    marginBottom: 6,
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: 1,
    textTransform: "uppercase",
    color: phase === "error" ? "#ff4444" : phase === "ready" ? "#00ff88" : "#00d4ff",
  }),
  statusMessage: {
    fontSize: 12,
    lineHeight: 1.45,
    color: "#d2dbe7",
  },
  helperText: {
    marginTop: 8,
    fontSize: 11,
    color: "#7f90a5",
    lineHeight: 1.4,
  },
};
