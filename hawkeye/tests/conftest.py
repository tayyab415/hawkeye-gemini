"""Shared fixtures for HawkEye service tests.

These are integration tests that hit real GCP services.
Required env vars: GCP_PROJECT_ID, GCP_MAPS_API_KEY (or GCP_API_KEY).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services.bigquery_service import GroundsourceService
from app.services.earth_engine_service import EarthEngineService
from app.services.firestore_service import IncidentService, InfrastructureService
from app.services.maps_service import MapsService


_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geojson"


@pytest.fixture(scope="session")
def project_id() -> str:
    pid = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not pid:
        pytest.skip("GCP_PROJECT_ID not set")
    return pid


@pytest.fixture(scope="session")
def maps_api_key() -> str:
    key = os.environ.get("GCP_MAPS_API_KEY") or os.environ.get("GCP_API_KEY")
    if not key:
        pytest.skip("GCP_MAPS_API_KEY not set")
    return key


@pytest.fixture(scope="session")
def jakarta_flood_geojson() -> str:
    path = _DATA_DIR / "flood_extent.geojson"
    if not path.exists():
        pytest.skip("data/geojson/flood_extent.geojson not found")
    with open(path) as f:
        data = json.load(f)
    return json.dumps(data["geometry"])


@pytest.fixture(scope="session")
def bigquery_service(project_id: str) -> GroundsourceService:
    return GroundsourceService(project_id=project_id)


@pytest.fixture(scope="session")
def incident_service(project_id: str) -> IncidentService:
    return IncidentService(project_id=project_id)


@pytest.fixture(scope="session")
def infrastructure_service(project_id: str) -> InfrastructureService:
    return InfrastructureService(project_id=project_id)


@pytest.fixture(scope="session")
def maps_service(maps_api_key: str) -> MapsService:
    return MapsService(api_key=maps_api_key)


@pytest.fixture(scope="session")
def earth_engine_service(project_id: str) -> EarthEngineService:
    return EarthEngineService(project_id=project_id)
