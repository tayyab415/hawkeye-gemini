import { useMemo } from 'react';

const INFRA_TYPES = [
  { key: 'hospitals',       label: 'Hospitals',  icon: '🏥', color: '#ff3333' },
  { key: 'schools',         label: 'Schools',    icon: '🏫', color: '#ffcc00' },
  { key: 'power_stations',  label: 'Power',      icon: '⚡', color: '#ff8800' },
  { key: 'shelters',        label: 'Shelters',   icon: '🏠', color: '#00ff88' },
];

function countItems(obj, key) {
  if (!obj) return 0;
  const v = obj[key];
  if (Array.isArray(v)) return v.length;
  if (typeof v === 'number') return v;
  return 0;
}

/**
 * 2x2 grid showing infrastructure counts with before/after cascade deltas.
 * Shows current count and +N delta from expanded flood scenario.
 */
export default function InfrastructureRiskChart({ current, expanded }) {
  const items = useMemo(() => {
    return INFRA_TYPES.map((type) => {
      const cur = countItems(current, type.key);
      const exp = countItems(expanded, type.key);
      const delta = exp > cur ? exp - cur : 0;
      return { ...type, current: cur, expanded: exp, delta };
    });
  }, [current, expanded]);

  const hasAny = items.some((item) => item.current > 0 || item.expanded > 0);
  if (!hasAny) return null;

  return (
    <div className="infra-risk-grid">
      {items.map((item) => (
        <div className="infra-risk-item" key={item.key}>
          <span className="infra-risk-icon">{item.icon}</span>
          <div className="infra-risk-meta">
            <span className="infra-risk-label">{item.label}</span>
            <span className="infra-risk-value">{item.current}</span>
          </div>
          {item.delta > 0 && (
            <span className="infra-risk-delta infra-risk-delta--up">+{item.delta}</span>
          )}
          {item.delta === 0 && item.current > 0 && (
            <span className="infra-risk-delta infra-risk-delta--same">—</span>
          )}
        </div>
      ))}
    </div>
  );
}
