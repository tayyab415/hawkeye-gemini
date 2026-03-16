/**
 * Camera Mode Indicator — small badge in bottom-right of globe.
 * Shows current camera behavior: ORBIT, BIRD EYE, STREET, OVERVIEW, TRACKING [entity].
 */
export default function CameraModeIndicator({ mode }) {
  if (!mode) return null;

  return (
    <div style={{
      position: 'absolute',
      bottom: 24,
      right: 12,
      padding: '3px 8px',
      border: '1px solid rgba(0, 212, 255, 0.25)',
      borderRadius: 2,
      background: 'rgba(10, 14, 23, 0.75)',
      fontFamily: 'var(--font-mono)',
      fontSize: 9,
      fontWeight: 700,
      letterSpacing: 1.5,
      color: 'var(--accent)',
      textTransform: 'uppercase',
      pointerEvents: 'none',
      zIndex: 7,
    }}>
      {mode}
    </div>
  );
}
