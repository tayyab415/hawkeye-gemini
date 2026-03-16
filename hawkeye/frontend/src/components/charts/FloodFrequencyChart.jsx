import { useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';

const MONTH_LABELS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

function barColor(count) {
  if (count > 15000) return '#ff3333';
  if (count > 10000) return '#ff6622';
  if (count > 5000)  return '#ffaa00';
  return '#00d4ff';
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.[0]) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{MONTH_LABELS[(label ?? 1) - 1]} — Month {label}</div>
      <div className="chart-tooltip-value">{payload[0].value?.toLocaleString()} events</div>
    </div>
  );
}

/**
 * Horizontal bar chart: 12 months of flood frequency from BigQuery.
 * Bars colored by intensity — red for peak monsoon, cyan for dry season.
 */
export default function FloodFrequencyChart({ data }) {
  const chartData = useMemo(() => {
    if (!Array.isArray(data) || data.length === 0) return [];
    return data
      .map((d) => ({
        month: d.month,
        flood_count: Number(d.flood_count ?? 0),
      }))
      .sort((a, b) => a.month - b.month);
  }, [data]);

  if (chartData.length === 0) return null;

  const currentMonth = new Date().getMonth() + 1;

  return (
    <ResponsiveContainer width="100%" height={110}>
      <BarChart data={chartData} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
        <XAxis
          dataKey="month"
          tick={{ fill: '#6b7b8d', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}
          tickFormatter={(m) => MONTH_LABELS[m - 1]}
          axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
          tickLine={false}
        />
        <YAxis hide />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(0, 212, 255, 0.06)' }} />
        <Bar dataKey="flood_count" radius={[2, 2, 0, 0]} maxBarSize={24}>
          {chartData.map((entry, i) => (
            <Cell
              key={i}
              fill={barColor(entry.flood_count)}
              stroke={entry.month === currentMonth ? '#ffffff' : 'transparent'}
              strokeWidth={entry.month === currentMonth ? 1.5 : 0}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
