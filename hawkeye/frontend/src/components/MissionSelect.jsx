import { useState } from 'react';
import './MissionSelect.css';

/**
 * MissionSelect — full-screen region selection before mission start.
 * Two cards: Jakarta (active) and Washington DC (coming soon / disabled).
 */

// Inline SVG: simplified Indonesia flag silhouette with map pin
function JakartaGraphic() {
  return (
    <svg className="ms-card-graphic" viewBox="0 0 320 200" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Indonesian flag bands */}
      <rect x="0" y="0" width="320" height="100" fill="#CE1126" opacity="0.25" />
      <rect x="0" y="100" width="320" height="100" fill="#FFFFFF" opacity="0.08" />
      {/* Grid overlay */}
      {Array.from({ length: 9 }, (_, i) => (
        <line key={`vg-${i}`} x1={i * 40} y1="0" x2={i * 40} y2="200" stroke="rgba(0,212,255,0.06)" strokeWidth="0.5" />
      ))}
      {Array.from({ length: 6 }, (_, i) => (
        <line key={`hg-${i}`} x1="0" y1={i * 40} x2="320" y2={i * 40} stroke="rgba(0,212,255,0.06)" strokeWidth="0.5" />
      ))}
      {/* Target crosshair */}
      <circle cx="160" cy="100" r="32" stroke="#CE1126" strokeWidth="1" fill="none" opacity="0.6" />
      <circle cx="160" cy="100" r="20" stroke="#CE1126" strokeWidth="0.5" fill="none" opacity="0.4" />
      <circle cx="160" cy="100" r="4" fill="#CE1126" opacity="0.8" />
      <line x1="160" y1="60" x2="160" y2="80" stroke="#CE1126" strokeWidth="1" opacity="0.5" />
      <line x1="160" y1="120" x2="160" y2="140" stroke="#CE1126" strokeWidth="1" opacity="0.5" />
      <line x1="120" y1="100" x2="140" y2="100" stroke="#CE1126" strokeWidth="1" opacity="0.5" />
      <line x1="180" y1="100" x2="200" y2="100" stroke="#CE1126" strokeWidth="1" opacity="0.5" />
      {/* Coordinate labels */}
      <text x="12" y="190" fill="rgba(206,17,38,0.5)" fontSize="9" fontFamily="JetBrains Mono, monospace">6.2°S 106.8°E</text>
      <text x="230" y="16" fill="rgba(206,17,38,0.4)" fontSize="8" fontFamily="JetBrains Mono, monospace">SOUTHEAST ASIA</text>
    </svg>
  );
}

function WashingtonGraphic() {
  return (
    <svg className="ms-card-graphic" viewBox="0 0 320 200" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* US flag inspired bands */}
      {Array.from({ length: 7 }, (_, i) => (
        <rect key={`stripe-${i}`} x="0" y={i * 28.5} width="320" height="14.25" fill={i % 2 === 0 ? '#B31942' : '#FFFFFF'} opacity={i % 2 === 0 ? '0.15' : '0.04'} />
      ))}
      {/* Blue canton area */}
      <rect x="0" y="0" width="130" height="85" fill="#0A3161" opacity="0.3" />
      {/* Stars hint */}
      {[
        [22, 18], [50, 18], [78, 18], [106, 18],
        [36, 36], [64, 36], [92, 36],
        [22, 54], [50, 54], [78, 54], [106, 54],
        [36, 72], [64, 72], [92, 72],
      ].map(([cx, cy], i) => (
        <circle key={`star-${i}`} cx={cx} cy={cy} r="2" fill="#FFFFFF" opacity="0.15" />
      ))}
      {/* Grid overlay */}
      {Array.from({ length: 9 }, (_, i) => (
        <line key={`vg-${i}`} x1={i * 40} y1="0" x2={i * 40} y2="200" stroke="rgba(0,212,255,0.06)" strokeWidth="0.5" />
      ))}
      {Array.from({ length: 6 }, (_, i) => (
        <line key={`hg-${i}`} x1="0" y1={i * 40} x2="320" y2={i * 40} stroke="rgba(0,212,255,0.06)" strokeWidth="0.5" />
      ))}
      {/* Target crosshair */}
      <circle cx="160" cy="100" r="32" stroke="#0A3161" strokeWidth="1" fill="none" opacity="0.5" />
      <circle cx="160" cy="100" r="20" stroke="#0A3161" strokeWidth="0.5" fill="none" opacity="0.3" />
      <circle cx="160" cy="100" r="4" fill="#0A3161" opacity="0.6" />
      <line x1="160" y1="60" x2="160" y2="80" stroke="#0A3161" strokeWidth="1" opacity="0.4" />
      <line x1="160" y1="120" x2="160" y2="140" stroke="#0A3161" strokeWidth="1" opacity="0.4" />
      <line x1="120" y1="100" x2="140" y2="100" stroke="#0A3161" strokeWidth="1" opacity="0.4" />
      <line x1="180" y1="100" x2="200" y2="100" stroke="#0A3161" strokeWidth="1" opacity="0.4" />
      {/* Coordinate labels */}
      <text x="12" y="190" fill="rgba(10,49,97,0.5)" fontSize="9" fontFamily="JetBrains Mono, monospace">38.9°N 77.0°W</text>
      <text x="230" y="16" fill="rgba(10,49,97,0.4)" fontSize="8" fontFamily="JetBrains Mono, monospace">NORTH AMERICA</text>
    </svg>
  );
}

export default function MissionSelect({ onSelect }) {
  const [hoveredCard, setHoveredCard] = useState(null);

  return (
    <div className="ms-overlay">
      {/* Scanline effect */}
      <div className="ms-scanlines" />

      <div className="ms-container">
        {/* Header */}
        <div className="ms-header">
          <div className="ms-logo-mark">
            <span className="ms-diamond">&#9670;</span>
            <span className="ms-diamond ms-diamond--echo">&#9670;</span>
          </div>
          <h1 className="ms-title">HAWK EYE</h1>
          <div className="ms-subtitle-row">
            <span className="ms-rule" />
            <span className="ms-subtitle">SELECT OPERATION THEATRE</span>
            <span className="ms-rule" />
          </div>
        </div>

        {/* Cards */}
        <div className="ms-cards">
          {/* Jakarta card — active */}
          <button
            className={`ms-card ms-card--jakarta ${hoveredCard === 'jakarta' ? 'ms-card--hovered' : ''}`}
            onMouseEnter={() => setHoveredCard('jakarta')}
            onMouseLeave={() => setHoveredCard(null)}
            onClick={() => onSelect('jakarta')}
          >
            <div className="ms-card-graphic-wrap">
              <JakartaGraphic />
              <div className="ms-card-graphic-fade" />
            </div>
            <div className="ms-card-body">
              <span className="ms-card-status ms-card-status--live">
                <span className="ms-status-dot" />
                LIVE
              </span>
              <h2 className="ms-card-title">Analyse Jakarta Floods</h2>
              <p className="ms-card-desc">
                Real-time flood analytics, BigQuery-powered infrastructure risk assessment, and Gemini AI voice briefings across the Greater Jakarta metropolitan area.
              </p>
              <div className="ms-card-meta">
                <span className="ms-card-tag">INDONESIA</span>
                <span className="ms-card-tag">FLOOD RISK</span>
                <span className="ms-card-tag">BIGQUERY</span>
              </div>
            </div>
            <div className="ms-card-action">
              <span className="ms-card-arrow">&#8594;</span>
            </div>
            {/* Corner accents */}
            <span className="ms-corner ms-corner--tl" />
            <span className="ms-corner ms-corner--tr" />
            <span className="ms-corner ms-corner--bl" />
            <span className="ms-corner ms-corner--br" />
          </button>

          {/* Washington card — active */}
          <button
            className={`ms-card ms-card--washington ${hoveredCard === 'washington' ? 'ms-card--hovered' : ''}`}
            onMouseEnter={() => setHoveredCard('washington')}
            onMouseLeave={() => setHoveredCard(null)}
            onClick={() => onSelect('washington')}
          >
            <div className="ms-card-graphic-wrap">
              <WashingtonGraphic />
              <div className="ms-card-graphic-fade" />
            </div>
            <div className="ms-card-body">
              <span className="ms-card-status ms-card-status--preview">
                <span className="ms-status-dot" />
                PREVIEW
              </span>
              <h2 className="ms-card-title">Predict Washington Floods</h2>
              <p className="ms-card-desc">
                Predictive flood modeling for the Washington D.C. metropolitan area with real-time sensor integration and evacuation route planning.
              </p>
              <div className="ms-card-meta">
                <span className="ms-card-tag">UNITED STATES</span>
                <span className="ms-card-tag">PREDICTIVE</span>
                <span className="ms-card-tag">PREVIEW</span>
              </div>
            </div>
            <div className="ms-card-action">
              <span className="ms-card-arrow">&#8594;</span>
            </div>
            {/* Corner accents */}
            <span className="ms-corner ms-corner--tl" />
            <span className="ms-corner ms-corner--tr" />
            <span className="ms-corner ms-corner--bl" />
            <span className="ms-corner ms-corner--br" />
          </button>
        </div>

        {/* Footer */}
        <div className="ms-footer">
          <span className="ms-footer-text">DISASTER RESPONSE COMMAND</span>
          <span className="ms-footer-sep">&#x2022;</span>
          <span className="ms-footer-text">GEMINI AI</span>
          <span className="ms-footer-sep">&#x2022;</span>
          <span className="ms-footer-text">BIGQUERY ANALYTICS</span>
        </div>
      </div>
    </div>
  );
}
