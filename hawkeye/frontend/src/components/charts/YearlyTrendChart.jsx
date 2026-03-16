import { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.[0]) return null;
  const d = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{d.year}</div>
      <div className="chart-tooltip-value">{d.flood_count?.toLocaleString()} events</div>
    </div>
  );
}

/**
 * Small area chart showing year-over-year flood trend from BigQuery.
 * Gradient fill from cyan. Includes trend direction indicator.
 */
export default function YearlyTrendChart({ data }) {
  const { chartData, trendPct, trendDir } = useMemo(() => {
    if (!Array.isArray(data) || data.length === 0) {
      return { chartData: [], trendPct: 0, trendDir: '—' };
    }
    const sorted = data
      .map((d) => ({ year: Number(d.year), flood_count: Number(d.flood_count ?? 0) }))
      .sort((a, b) => a.year - b.year);

    let pct = 0;
    let dir = '—';
    if (sorted.length >= 2) {
      const prev = sorted[sorted.length - 2].flood_count;
      const curr = sorted[sorted.length - 1].flood_count;
      if (prev > 0) {
        pct = Math.round(((curr - prev) / prev) * 100);
        dir = pct > 0 ? '+' : pct < 0 ? '' : '—';
      }
    }
    return { chartData: sorted, trendPct: pct, trendDir: dir };
  }, [data]);

  if (chartData.length === 0) return null;

  const trendColor = trendPct > 0 ? '#ff4444' : trendPct < 0 ? '#00ff88' : '#6b7b8d';

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 4,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '1.5px',
          textTransform: 'uppercase', color: 'var(--text-secondary)',
        }}>
          5-Year Trend
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
          color: trendColor,
        }}>
          {trendDir}{Math.abs(trendPct)}%
          <span style={{ fontSize: 10, marginLeft: 2 }}>
            {trendPct > 0 ? '↑' : trendPct < 0 ? '↓' : ''}
          </span>
        </span>
      </div>
      <ResponsiveContainer width="100%" height={72}>
        <AreaChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <defs>
            <linearGradient id="yearlyTrendGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#00d4ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="year"
            tick={{ fill: '#6b7b8d', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
            tickLine={false}
          />
          <YAxis hide />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'rgba(0, 212, 255, 0.2)' }} />
          <Area
            type="monotone"
            dataKey="flood_count"
            stroke="#00d4ff"
            fill="url(#yearlyTrendGrad)"
            strokeWidth={2}
            dot={{ r: 3, fill: '#0a0e17', stroke: '#00d4ff', strokeWidth: 1.5 }}
            activeDot={{ r: 4, fill: '#00d4ff', stroke: '#0a0e17', strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
