# BigQuery Inspection Results
- Generated at: `2026-03-15T00:31:53.795535+00:00`
- Project ID: `gen-lang-client-0261050164`
- GOOGLE_APPLICATION_CREDENTIALS: `credentials/hawkeye-runtime-key.json`
- Flood GeoJSON source: `/Users/tayyabkhan/Downloads/gemini-agent/hawkeye/data/geojson/flood_extent.geojson`

### query_floods_intersecting_polygon
- Time: `3.78s`
- Intersecting historical floods: `50`
- Sample event: `start_date=2026-01-29`, `duration_days=0`, `overlap_sqkm=0.01`

### get_flood_frequency(-6.225, 106.855, 10)
- Time: `2.19s`

### raw_worst_case_event_date_query
- Time: `2.00s`
- total_events: `35796`
- avg_duration_days: `0.7`
- worst_case_event_date: `2026-01-18` (duration_days=6)

### get_infrastructure_at_risk(flood_geojson)
- Time: `1.87s`
- hospitals_in_zone: `11`
- hospital_names: ["RSUD MAMPANG PRAPATAN", "RS TRIA DIPA", "RS METROPOLITAN MEDICAL CENTER (MMC) JAKARTA", "RSIA Tambak", "RS MEDISTRA", "RS AGUNG", "RS HERMINA JATINEGARA", "RS TEBET", "Ocean Dental Tebet", "RS BRAWIJAYA SAHARJO", "RSUD TEBET"]
- schools_in_zone: `9`
- shelters_in_zone: `1`
- power_stations_in_zone: `2`

### get_infrastructure_at_expanded_level(flood_geojson, 1000)
- Time: `2.37s`
- newly_at_risk_hospitals: `6`
- newly_at_risk_hospital_names: ["Klinik SouthernUK", "RS RADJAK SALEMBA", "RS SILOAM ASRI", "RS ST. CAROLUS", "RSUD BUDHI ASIH", "RSUD MATRAMAN"]
- total_newly_at_risk: `12`

### find_pattern_match(15.0, 4)
- Time: `1.80s`
- top_3_pattern_matches:
  1. start_date=2019-12-31, end_date=2020-01-04, area_sqkm=15.023803237035287, duration_days=4
  2. start_date=2021-12-03, end_date=2021-12-07, area_sqkm=15.023803237035287, duration_days=4
  3. start_date=2020-02-22, end_date=2020-02-26, area_sqkm=15.023803237035287, duration_days=4

### get_monthly_frequency()
- Time: `1.79s`
- monthly_flood_counts:
  - month=01: flood_count=21242
  - month=02: flood_count=20766
  - month=03: flood_count=8178
  - month=04: flood_count=5568
  - month=05: flood_count=4068
  - month=06: flood_count=2026
  - month=07: flood_count=4592
  - month=08: flood_count=2734
  - month=09: flood_count=2490
  - month=10: flood_count=5812
  - month=11: flood_count=7954
  - month=12: flood_count=6816
- highest_month: month=1, flood_count=21242

### get_yearly_trend()
- Time: `2.14s`
- last_5_years:
  - year=2022: flood_count=8126, avg_area=44.11
  - year=2023: flood_count=5010, avg_area=39.0
  - year=2024: flood_count=8386, avg_area=36.76
  - year=2025: flood_count=15250, avg_area=37.98
  - year=2026: flood_count=5660, avg_area=10.0
- trend_direction: `decreasing`

### raw_st_buffer_cascade_query
- Time: `2.24s`
- original_area_sqkm: `25.01`
- expanded_area_sqkm: `46.03`
- expanded_vs_original_ratio: `1.84x`
- additional_infrastructure_points_in_expanded_zone: `12`

## Timing Summary
| Query | Time (s) | Under 3s? |
|---|---:|:---:|
| `query_floods_intersecting_polygon` | 3.78 | NO |
| `get_flood_frequency(-6.225, 106.855, 10)` | 2.19 | YES |
| `raw_worst_case_event_date_query` | 2.00 | YES |
| `get_infrastructure_at_risk(flood_geojson)` | 1.87 | YES |
| `get_infrastructure_at_expanded_level(flood_geojson, 1000)` | 2.37 | YES |
| `find_pattern_match(15.0, 4)` | 1.80 | YES |
| `get_monthly_frequency()` | 1.79 | YES |
| `get_yearly_trend()` | 2.14 | YES |
| `raw_st_buffer_cascade_query` | 2.24 | YES |
