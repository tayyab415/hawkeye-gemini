import './PipDroneView.css';

/**
 * Picture-in-Picture Drone View — miniature recon feed
 * shown in the bottom-left corner of the Strategic View.
 */
export default function PipDroneView({ reconFeed, visible, onToggle }) {
  if (!visible) return null;

  const mode = reconFeed?.activeMode || 'DRONE';
  const frame = mode === 'DRONE'
    ? reconFeed?.currentFrame
    : mode === 'SAR'
      ? reconFeed?.sarImage
      : reconFeed?.predictionImage;

  const modeLabels = {
    DRONE: 'DRONE CAM',
    SAR: 'SAR IMAGE',
    PREDICTION: 'PREDICTION',
  };

  return (
    <div className="pip-drone" onClick={onToggle}>
      <div className="pip-drone-label">{modeLabels[mode] || mode}</div>
      {frame ? (
        <img
          className="pip-drone-frame"
          src={frame.startsWith('data:') || frame.startsWith('http') ? frame : `data:image/jpeg;base64,${frame}`}
          alt={mode}
        />
      ) : (
        <div className="pip-drone-placeholder">
          <div className="pip-drone-icon">&#9681;</div>
          <div className="pip-drone-waiting">AWAITING SIGNAL</div>
        </div>
      )}
    </div>
  );
}
