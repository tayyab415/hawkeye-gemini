"""Backend tests for deterministic offline Earth Engine tile delivery."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("google.adk")

import app.main as main_module
from app.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_offline_tile_route_shape_registered() -> None:
    route_paths = {route.path for route in app.routes}
    assert "/api/earth-engine/tiles/offline/{layer_id}/{z}/{x}/{y}.png" in route_paths
    assert "/api/earth-engine/tiles/live/{tile_handle}/{z}/{x}/{y}.png" in route_paths
    assert "/api/earth-engine/live-analysis" in route_paths
    assert "/api/earth-engine/live-analysis/latest" in route_paths
    assert "/api/earth-engine/live-analysis/{task_id}" in route_paths
    assert "/api/earth-engine/live-analysis/{task_id}/result" in route_paths


def test_offline_tile_endpoint_returns_png(client: TestClient) -> None:
    response = client.get("/api/earth-engine/tiles/offline/ee_change_detection/2/1/1.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers.get("cache-control")
    assert response.headers.get("etag")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_offline_tile_endpoint_is_deterministic(client: TestClient) -> None:
    path = "/api/earth-engine/tiles/offline/ee_change_detection/3/4/2.png"
    first = client.get(path)
    second = client.get(path)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == second.content
    assert first.headers.get("etag") == second.headers.get("etag")


def test_offline_tile_endpoint_rejects_unsafe_inputs(client: TestClient) -> None:
    bad_layer = client.get("/api/earth-engine/tiles/offline/ee$bad/1/0/0.png")
    bad_bounds = client.get("/api/earth-engine/tiles/offline/ee_change_detection/2/5/1.png")

    assert bad_layer.status_code == 400
    assert bad_bounds.status_code == 400


def test_live_tile_endpoint_rejects_unsafe_inputs(client: TestClient) -> None:
    bad_layer = client.get("/api/earth-engine/tiles/live/ee$bad/1/0/0.png")
    bad_bounds = client.get("/api/earth-engine/tiles/live/ee_live_tile_00001/2/9/1.png")

    assert bad_layer.status_code == 400
    assert bad_bounds.status_code == 400


def test_live_tile_endpoint_proxies_success_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def fetch_live_tile(self, tile_handle: str, *, z: int, x: int, y: int) -> dict:
            assert tile_handle == "ee_live_tile_00001"
            assert (z, x, y) == (2, 1, 1)
            return {
                "status": "ok",
                "content": main_module.EE_TILE_PLACEHOLDER_PNG,
                "content_type": "image/png",
                "cache_control": "public, max-age=60",
                "etag": "\"fake-etag\"",
            }

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/tiles/live/ee_live_tile_00001/2/1/1.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers.get("etag") == "\"fake-etag\""
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_live_tile_endpoint_returns_not_found_for_unknown_handle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def fetch_live_tile(self, tile_handle: str, *, z: int, x: int, y: int) -> dict:
            return {
                "status": "not_found",
                "error": {
                    "code": "live_tile_not_found",
                    "message": f"Live tile handle '{tile_handle}' was not found.",
                },
            }

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/tiles/live/ee_live_tile_99999/2/1/1.png")
    assert response.status_code == 404
    assert "Live tile handle" in response.json()["error"]


def test_live_tile_endpoint_surfaces_upstream_error_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def fetch_live_tile(self, tile_handle: str, *, z: int, x: int, y: int) -> dict:
            return {
                "status": "error",
                "http_status": 503,
                "error": {
                    "code": "live_tile_upstream_error",
                    "message": f"Upstream outage for '{tile_handle}'",
                },
            }

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/tiles/live/ee_live_tile_00001/2/1/1.png")
    assert response.status_code == 503
    assert response.json()["error"] == "Upstream outage for 'ee_live_tile_00001'"


def test_live_tile_endpoint_rejects_invalid_tile_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def fetch_live_tile(self, tile_handle: str, *, z: int, x: int, y: int) -> dict:
            return {
                "status": "ok",
                "content": "not-bytes",
                "content_type": "image/png",
            }

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/tiles/live/ee_live_tile_00001/2/1/1.png")
    assert response.status_code == 502
    assert response.json()["error"] == "Live tile fetch returned invalid content payload"


def test_live_tile_endpoint_handles_runtime_service_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_runtime_error():
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(main_module, "_get_earth_engine_runtime_service", _raise_runtime_error)

    response = client.get("/api/earth-engine/tiles/live/ee_live_tile_00001/2/1/1.png")
    assert response.status_code == 500
    assert response.json()["error"] == "Failed to resolve live tile descriptor"
    assert "runtime unavailable" in response.json()["detail"]


def test_live_analysis_endpoint_uses_runtime_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def run_live_analysis_task(self, request: dict | None = None) -> dict:
            return {
                "task_id": "ee_live_task_0009",
                "status": {
                    "task_id": "ee_live_task_0009",
                    "state": "complete",
                    "result_available": True,
                },
                "result": {"ee_runtime": {"status": "live"}},
            }

        def get_latest_live_analysis_task_status(self) -> dict:
            return {
                "task_id": "ee_live_task_0009",
                "state": "complete",
                "result_available": True,
            }

        def get_live_analysis_task_status(self, task_id: str) -> dict:
            return {
                "task_id": task_id,
                "state": "complete",
                "result_available": True,
                "error": None,
            }

        def get_live_analysis_task_result(self, task_id: str) -> dict:
            return {"runtime_payload": {"ee_runtime": {"status": "live"}}, "task_id": task_id}

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    run_response = client.post(
        "/api/earth-engine/live-analysis",
        json={"analysis": "flood_extent"},
    )
    latest_response = client.get("/api/earth-engine/live-analysis/latest")
    status_response = client.get("/api/earth-engine/live-analysis/ee_live_task_0009")
    result_response = client.get("/api/earth-engine/live-analysis/ee_live_task_0009/result")

    assert run_response.status_code == 200
    assert run_response.json()["task_id"] == "ee_live_task_0009"
    assert latest_response.status_code == 200
    assert latest_response.json()["task_id"] == "ee_live_task_0009"
    assert latest_response.json()["result"]["runtime_payload"]["ee_runtime"]["status"] == "live"
    assert status_response.status_code == 200
    assert status_response.json()["task_id"] == "ee_live_task_0009"
    assert result_response.status_code == 200
    assert result_response.json()["runtime_payload"]["ee_runtime"]["status"] == "live"


def test_live_analysis_latest_endpoint_returns_not_found_when_no_tasks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def get_latest_live_analysis_task_status(self) -> None:
            return None

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/live-analysis/latest")
    assert response.status_code == 404
    assert response.json()["error"] == "No live analysis task has been submitted."


def test_live_analysis_status_endpoint_returns_not_found_for_unknown_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def get_live_analysis_task_status(self, task_id: str) -> dict:
            return {
                "task_id": task_id,
                "state": "error",
                "result_available": False,
                "error": {
                    "code": "live_analysis_task_not_found",
                    "message": f"Live analysis task '{task_id}' was not found.",
                },
            }

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/live-analysis/ee_live_task_404")
    assert response.status_code == 404
    payload = response.json()
    assert payload["task_id"] == "ee_live_task_404"
    assert payload["error"]["code"] == "live_analysis_task_not_found"


def test_live_analysis_result_endpoint_returns_not_found_when_result_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeEarthEngineService:
        def get_live_analysis_task_result(self, task_id: str) -> None:
            return None

    monkeypatch.setattr(
        main_module,
        "_get_earth_engine_runtime_service",
        lambda: _FakeEarthEngineService(),
    )

    response = client.get("/api/earth-engine/live-analysis/ee_live_task_0010/result")
    assert response.status_code == 404
    assert "No completed result found for task" in response.json()["error"]
