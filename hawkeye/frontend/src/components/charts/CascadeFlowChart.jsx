import { useMemo } from 'react';

function fmt(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString();
}

/**
 * Vertical consequence chain — styled cards, not a Recharts chart.
 * Each cascade order animates in with a 0.25s stagger.
 * Data from compute_cascade BigQuery tool.
 */
export default function CascadeFlowChart({ data }) {
  const orders = useMemo(() => {
    if (!data) return [];
    const { first_order: o1, second_order: o2, third_order: o3, fourth_order: o4 } = data;

    const result = [];

    if (o1 && Object.keys(o1).length > 0) {
      result.push({
        label: '1st Order — Direct Flood',
        icon: '◆',
        stats: [
          o1.population_at_risk != null && `Population: ${fmt(o1.population_at_risk)}`,
          o1.water_level_delta_m != null && `Water level +${o1.water_level_delta_m}m`,
          o1.flood_area_expanded && 'Flood area expanded',
        ].filter(Boolean),
      });
    }

    if (o2 && Object.keys(o2).length > 0) {
      result.push({
        label: '2nd Order — Infrastructure',
        icon: '◆',
        stats: [
          o2.hospitals_at_risk != null && `${fmt(o2.hospitals_at_risk)} hospitals at risk`,
          o2.newly_isolated_hospitals?.length > 0 &&
            `${o2.newly_isolated_hospitals.length} newly isolated`,
          o2.schools_at_risk != null && `${fmt(o2.schools_at_risk)} schools affected`,
        ].filter(Boolean),
      });
    }

    if (o3 && Object.keys(o3).length > 0) {
      result.push({
        label: '3rd Order — Utilities',
        icon: '◆',
        stats: [
          o3.power_stations_at_risk != null &&
            `${fmt(o3.power_stations_at_risk)} power substations`,
          o3.estimated_residents_without_power != null &&
            `${fmt(o3.estimated_residents_without_power)} without power`,
        ].filter(Boolean),
      });
    }

    if (o4 && Object.keys(o4).length > 0) {
      result.push({
        label: '4th Order — Humanitarian',
        icon: '◆',
        stats: [
          o4.children_under_5 != null && `${fmt(o4.children_under_5)} children under 5`,
          o4.elderly_over_65 != null && `${fmt(o4.elderly_over_65)} elderly over 65`,
          o4.hospital_patients_needing_evac != null &&
            `${fmt(o4.hospital_patients_needing_evac)} patients to evacuate`,
        ].filter(Boolean),
      });
    }

    return result;
  }, [data]);

  if (orders.length === 0) return null;

  return (
    <div className="cascade-flow">
      {orders.map((order, i) => (
        <div className="cascade-order" key={i}>
          <div className="cascade-order-label">
            <span className="cascade-icon">{order.icon}</span>
            {order.label}
          </div>
          {order.stats.map((stat, j) => (
            <div className="cascade-stat-row" key={j}>
              <div className="cascade-stat-marker" />
              <span
                className="cascade-stat-text"
                dangerouslySetInnerHTML={{
                  __html: stat.replace(
                    /(\d[\d,]*)/g,
                    '<strong>$1</strong>'
                  ),
                }}
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
