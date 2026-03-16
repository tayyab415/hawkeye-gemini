# HawkEye Monorepo

This folder follows the ADK-oriented monorepo layout defined in the implementation plan.

- Backend ADK app: `app/`
- Frontend React app: `frontend/`
- Data scripts and GeoJSON assets: `data/`
- Deployment scripts: `infra/`

Environment configuration is centralized in the project root `.env`:

- `/Users/tayyabkhan/Downloads/gemini-agent/.env`

No additional `.env` files are used in subdirectories.

## Earth Engine live runtime endpoints

The backend now exposes runtime-oriented Earth Engine APIs:

- `POST /api/earth-engine/live-analysis`
- `GET /api/earth-engine/live-analysis/latest`
- `GET /api/earth-engine/live-analysis/{task_id}`
- `GET /api/earth-engine/live-analysis/{task_id}/result`
- `GET /api/earth-engine/tiles/live/{tile_handle}/{z}/{x}/{y}.png`

Live EE map-tile generation is feature-gated with:

- `HAWKEYE_ENABLE_EE_LIVE_TILES=true`

If the flag is disabled or Earth Engine initialization fails, HawkEye falls back to deterministic descriptor/offline tile behavior so frontend contracts remain stable.
