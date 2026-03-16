# HawkEye GCP Service Layer

Four Python modules that wrap GCP APIs. Pure backend — no dependency on FastAPI, ADK, or WebSockets.

## Services

| Module | Class | Backend |
|--------|-------|---------|
| `bigquery_service.py` | `GroundsourceService` | BigQuery (groundsource + infrastructure tables) |
| `firestore_service.py` | `IncidentService` | Firestore (incidents, decisions, sensor_data) |
| `firestore_service.py` | `InfrastructureService` | Firestore (infrastructure collection) + OSM Overpass |
| `maps_service.py` | `MapsService` | Google Maps Platform (Geocoding, Directions, Places, Elevation) |
| `earth_engine_service.py` | `EarthEngineService` | Pre-computed GeoJSON + fallback runtime EE layer descriptors |

## Required Environment Variables

| Variable | Used by |
|----------|---------|
| `GCP_PROJECT_ID` | All services (BigQuery, Firestore, Earth Engine) |
| `GCP_API_KEY` | `MapsService` |
| `GOOGLE_APPLICATION_CREDENTIALS` | BigQuery + Firestore (service account key path) |

## BigQuery Prerequisites (Step 0)

The following tables must exist in the `hawkeye` dataset:

- `hawkeye.groundsource` — optimized table with GEOGRAPHY column, ~2.2M rows
- `hawkeye.groundsource_jakarta` — materialized view, Jakarta-only subset
- `hawkeye.infrastructure` — Jakarta hospitals, schools, shelters, power stations

See `plan/HawkEye_BigQuery_Specification.md` for schema details and loading instructions.

## Running Tests

From the `hawkeye/` directory:

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_API_KEY=your-maps-api-key
export GOOGLE_APPLICATION_CREDENTIALS=credentials/hawkeye-runtime-key.json

pip install -e ".[dev]"
pytest tests/ -v
```

Tests are integration tests that hit real GCP services. They require:
- BigQuery tables loaded (Step 0 complete)
- Firestore database created
- Google Maps API key with Geocoding, Directions, Places, and Elevation APIs enabled

## Notes

- `data/geojson/flood_extent.geojson` is a **synthetic placeholder** covering the Kampung Melayu / Ciliwung River basin. Replace with real Earth Engine output by running `data/compute_flood_extent.py` (requires `earthengine authenticate`).
- `InfrastructureService.load_jakarta_infrastructure()` is a one-time loader. Run it once to populate the Firestore `infrastructure` collection from OpenStreetMap.
- `GroundsourceService` caches `get_monthly_frequency()` and `get_yearly_trend()` results after the first call.
