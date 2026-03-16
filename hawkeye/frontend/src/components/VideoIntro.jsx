import { useRef, useEffect, useState } from 'react';
import './VideoIntro.css';

/**
 * VideoIntro — Full-screen mission briefing video with "Activate HawkEye" overlay.
 *
 * Props:
 *   mission   — 'jakarta' | 'washington'
 *   onActivate — called when user clicks the activate button
 *   shrunk    — if true, video is in shrunk/minimised state (after activation)
 */

const VIDEO_MAP = {
  jakarta: '/videos/indonesia-video.mp4',
  washington: '/videos/washingotn-vid.mp4',
};

const MISSION_LABELS = {
  jakarta: { region: 'JAKARTA', country: 'INDONESIA', op: 'FLOOD ANALYSIS' },
  washington: { region: 'WASHINGTON D.C.', country: 'UNITED STATES', op: 'FLOOD PREDICTION' },
};

export default function VideoIntro({ mission, onActivate, shrunk }) {
  const videoRef = useRef(null);
  const [videoEnded, setVideoEnded] = useState(false);
  const [showControls, setShowControls] = useState(false);

  const src = VIDEO_MAP[mission];
  const labels = MISSION_LABELS[mission] || MISSION_LABELS.jakarta;

  // Auto-play on mount
  useEffect(() => {
    const vid = videoRef.current;
    if (!vid) return;
    vid.currentTime = 0;
    setVideoEnded(false);
    setShowControls(false);
    vid.play().catch(() => {});

    // Show activate button after 2s
    const timer = setTimeout(() => setShowControls(true), 2000);
    return () => clearTimeout(timer);
  }, [mission]);

  const handleEnded = () => {
    setVideoEnded(true);
  };

  return (
    <div className={`vi-container ${shrunk ? 'vi-container--shrunk' : ''}`}>
      {/* Video element */}
      <video
        ref={videoRef}
        className="vi-video"
        src={src}
        onEnded={handleEnded}
        muted
        playsInline
        preload="auto"
      />

      {/* Dark gradient vignette */}
      <div className="vi-vignette" />

      {/* Scanline overlay */}
      <div className="vi-scanlines" />

      {/* Top-left mission tag */}
      {!shrunk && (
        <div className="vi-mission-tag">
          <div className="vi-mission-tag-line">
            <span className="vi-tag-dot" />
            <span className="vi-tag-label">OPERATION THEATRE</span>
          </div>
          <div className="vi-tag-region">{labels.region}</div>
          <div className="vi-tag-detail">{labels.country} &mdash; {labels.op}</div>
        </div>
      )}

      {/* Bottom-right: video progress indicator */}
      {!shrunk && (
        <div className="vi-progress-tag">
          {videoEnded ? 'BRIEFING COMPLETE' : 'BRIEFING IN PROGRESS'}
        </div>
      )}

      {/* Center: Activate button */}
      {!shrunk && showControls && (
        <div className="vi-activate-wrap">
          <button className="vi-activate-btn" onClick={onActivate}>
            <span className="vi-activate-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 8v8M8 12h8" />
              </svg>
            </span>
            <span className="vi-activate-text">ACTIVATE HAWKEYE</span>
          </button>
          {/* Corner brackets around button */}
          <span className="vi-bracket vi-bracket--tl" />
          <span className="vi-bracket vi-bracket--tr" />
          <span className="vi-bracket vi-bracket--bl" />
          <span className="vi-bracket vi-bracket--br" />
        </div>
      )}
    </div>
  );
}
