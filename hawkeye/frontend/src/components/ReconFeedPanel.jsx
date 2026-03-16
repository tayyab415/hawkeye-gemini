import { useState } from 'react';
import './ReconFeedPanel.css';

const MODES = ['DRONE', 'SAR', 'PREDICTION'];

export default function ReconFeedPanel({ data }) {
  const [activeMode, setActiveMode] = useState(data.activeMode || 'DRONE');

  const frame =
    activeMode === 'DRONE' ? data.currentFrame :
    activeMode === 'SAR' ? data.sarImage :
    data.predictionImage;

  return (
    <div className="panel recon">
      <div className="panel-header">
        <span>Recon Feed</span>
        <span className="recon-ts">
          {new Date(data.timestamp).toLocaleTimeString([], { hour12: false })}
        </span>
      </div>

      <div className="recon-viewport">
        {frame ? (
          <>
            <img
              key={frame}
              className={`recon-frame ${activeMode === 'PREDICTION' ? 'prediction-pulse' : ''}`}
              src={frame.startsWith('http') || frame.startsWith('data:') ? frame : `data:image/jpeg;base64,${frame}`}
              alt={`${activeMode} feed`}
            />
            {activeMode === 'PREDICTION' && (
              <div className="recon-prediction-overlay">
                RISK PROJECTION — WORST CASE
              </div>
            )}
          </>
        ) : (
          <div className="recon-placeholder">
            <div className="recon-icon">&#9681;</div>
            <span className="recon-placeholder-label">{activeMode} FEED</span>
            <span className="recon-placeholder-sub">Awaiting signal</span>
          </div>
        )}

        <div className="recon-overlay-badge">{activeMode}</div>
      </div>

      <div className="recon-modes">
        {MODES.map((mode) => (
          <button
            key={mode}
            className={`recon-mode-btn ${activeMode === mode ? 'active' : ''}`}
            onClick={() => setActiveMode(mode)}
          >
            {mode}
          </button>
        ))}
      </div>
    </div>
  );
}
