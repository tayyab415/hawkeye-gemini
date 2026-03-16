import { useEffect, useRef } from 'react';
import './IncidentLogPanel.css';

function formatTimestamp(ts) {
  if (!ts && ts !== 0) return '';
  if (typeof ts === 'string' && !/^\d{10,}$/.test(ts)) return ts;
  const d = new Date(typeof ts === 'string' ? Number(ts) : ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

function severityColor(severity) {
  if (severity === 'CRITICAL') return 'var(--critical)';
  if (severity === 'WARNING') return 'var(--warning)';
  return 'var(--accent)';
}

export default function IncidentLogPanel({ data }) {
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [data.entries]);

  return (
    <div className="panel incident">
      <div className="panel-header">
        <span>Incident Log</span>
        <span className="il-count">{data.entries.length} events</span>
      </div>

      <div className="panel-body" ref={scrollRef}>
        {data.entries.length === 0 && (
          <div className="il-init-state">
            <span className="il-init-dot" />
            <span className="il-init-label">AWAITING EVENTS...</span>
          </div>
        )}
        {data.entries.map((entry) => (
          <div key={entry.id} className={`il-entry ${entry.severity ? entry.severity.toLowerCase() : 'info'}`}>
            <span className="il-ts">{formatTimestamp(entry.timestamp)}</span>
            <span
              className="il-severity"
              style={{ color: severityColor(entry.severity) }}
            >
              {entry.severity}
            </span>
            <span className="il-text">{entry.text || entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
