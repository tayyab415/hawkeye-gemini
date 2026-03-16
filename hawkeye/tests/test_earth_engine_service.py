"""Integration tests for EarthEngineService (pre-computed GeoJSON)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app.services.earth_engine_service import EarthEngineService


class TestFloodExtentGeoJSON:
    def test_returns_valid_geojson(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        data = earth_engine_service.get_flood_extent_geojson()
        assert data["type"] == "Feature"
        assert "geometry" in data
        assert data["geometry"]["type"] == "Polygon"

    def test_geometry_in_jakarta(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        data = earth_engine_service.get_flood_extent_geojson()
        coords = data["geometry"]["coordinates"][0]
        lngs = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        assert 106.0 < min(lngs) and max(lngs) < 107.5, "Longitude outside Jakarta"
        assert -7.0 < min(lats) and max(lats) < -5.5, "Latitude outside Jakarta"


class TestFloodArea:
    def test_reasonable_range(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        area = earth_engine_service.get_flood_area_sqkm()
        assert 1.0 < area < 100.0, f"Area {area} km² outside expected 1-100 range"


class TestFloodMetadata:
    def test_returns_metadata_from_sidecar(self, tmp_path: Path) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        metadata_path = tmp_path / "analysis_provenance.json"

        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "source": "earth-engine",
                    "source_dataset": "COPERNICUS/S1_GRD",
                    "method": "SAR change detection",
                    "confidence": "HIGH",
                    "generated_at": "2026-03-14T08:21:17Z",
                }
            )
        )

        service = EarthEngineService(
            project_id="demo-project",
            geojson_path=geojson_path,
            metadata_path=metadata_path,
        )

        metadata = service.get_flood_extent_metadata()
        assert metadata["source_dataset"] == "COPERNICUS/S1_GRD"
        assert metadata["method"] == "SAR change detection"
        assert metadata["project_id"] == "demo-project"
        assert metadata["generated_from"] == "flood_extent.geojson"

    def test_falls_back_to_geojson_properties(self, tmp_path: Path) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "source_dataset": "COPERNICUS/S1_GRD",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "threshold_db": -3.0,
                        "confidence": "HIGH",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        metadata = service.get_flood_extent_metadata()
        assert metadata["baseline_window"] == "2025-06-01 to 2025-09-30"
        assert metadata["event_window"] == "2025-11-01 to 2025-11-30"
        assert metadata["threshold_db"] == -3.0
        assert metadata["confidence"] == "HIGH"


class TestPopulationAtRisk:
    def test_returns_demographics(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        pop = earth_engine_service.get_population_at_risk()
        assert pop["total"] > 0
        assert pop["children_under_5"] > 0
        assert pop["elderly_over_65"] > 0
        assert pop["flood_area_sqkm"] > 0

    def test_accepts_custom_geojson(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        geom = {
            "type": "Polygon",
            "coordinates": [
                [
                    [106.84, -6.22],
                    [106.86, -6.22],
                    [106.86, -6.24],
                    [106.84, -6.24],
                    [106.84, -6.22],
                ]
            ],
        }
        pop = earth_engine_service.get_population_at_risk(json.dumps(geom))
        assert pop["total"] > 0


class TestFloodGrowthRate:
    def test_returns_rate(
        self, earth_engine_service: EarthEngineService
    ) -> None:
        rate = earth_engine_service.get_flood_growth_rate()
        assert "rate_pct_per_hour" in rate
        assert rate["rate_pct_per_hour"] > 0


class TestRuntimeFloodProduct:
    def test_runtime_layer_descriptors_include_tile_contract(self, tmp_path: Path) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        metadata_path = tmp_path / "analysis_provenance.json"

        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "confidence": "HIGH",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "source": "earth-engine",
                    "source_dataset": "COPERNICUS/S1_GRD",
                    "method": "SAR change detection",
                    "confidence": "HIGH",
                    "baseline_window": "2025-06-01 to 2025-09-30",
                    "event_window": "2025-11-01 to 2025-11-30",
                    "generated_at": "2026-03-14T08:21:17Z",
                }
            )
        )

        service = EarthEngineService(
            project_id="demo-project",
            geojson_path=geojson_path,
            metadata_path=metadata_path,
        )
        runtime = service.get_runtime_flood_product()

        assert runtime["runtime_mode"] == "fallback_descriptor"
        assert runtime["status"] == "fallback"
        assert runtime["error"] is None

        layers = runtime["layers"]
        layer_ids = {layer["id"] for layer in layers}
        assert {
            "ee_baseline_backscatter",
            "ee_event_backscatter",
            "ee_change_detection",
            "ee_multisensor_fusion",
        }.issubset(layer_ids)
        for layer in layers:
            assert "id" in layer and layer["id"].startswith("ee_")
            assert isinstance(layer.get("name"), str) and layer["name"]
            assert layer.get("type") == "raster"
            tile_source = layer.get("tile_source", {})
            assert "{z}" in tile_source.get("url_template", "")
            assert tile_source.get("status") == "placeholder"
            assert tile_source.get("available") is False

        fused_layer = next(layer for layer in layers if layer["id"] == "ee_multisensor_fusion")
        assert fused_layer["fusion"]["is_fused"] is True
        assert fused_layer["fusion"]["fused_layer_id"] == "ee_multisensor_fusion"
        assert len(fused_layer["fusion"]["source_components"]) == 5

        temporal_frames = runtime["temporal_frames"]
        assert set(temporal_frames.keys()) == {"baseline", "event", "change"}
        assert (
            temporal_frames["baseline"]["start_timestamp"] == "2025-06-01T00:00:00Z"
        )
        assert temporal_frames["event"]["end_timestamp"] == "2025-11-30T23:59:59Z"
        assert temporal_frames["baseline"]["frame_id"] == "baseline"
        assert temporal_frames["event"]["frame_id"] == "event"
        assert temporal_frames["change"]["frame_id"] == "change"
        assert temporal_frames["change"]["derived_from"] == ["baseline", "event"]
        assert temporal_frames["baseline"]["provenance"]["source"] == "earth-engine"
        assert temporal_frames["event"]["confidence"]["label"] == "HIGH"

        temporal_playback = runtime["temporal_playback"]
        assert temporal_playback["default_frame_id"] == "change"
        assert temporal_playback["ordered_frame_ids"] == ["baseline", "event", "change"]

        temporal_summary = runtime["temporal_summary"]
        assert temporal_summary["frame_count"] == 3
        assert temporal_summary["latest_frame_id"] == "change"
        assert runtime["confidence"]["label"] == "HIGH"
        assert runtime["confidence"]["fusion_summary"]["signal_count"] == 5

    def test_runtime_product_includes_multisensor_fusion_confidence(
        self, tmp_path: Path
    ) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        metadata_path = tmp_path / "analysis_provenance.json"

        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "confidence": "HIGH",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "source": "earth-engine",
                    "source_dataset": "COPERNICUS/S1_GRD",
                    "method": "SAR change detection",
                    "confidence": "HIGH",
                    "confidence_score": 0.82,
                    "baseline_window": "2025-06-01 to 2025-09-30",
                    "event_window": "2025-11-01 to 2025-11-30",
                    "generated_at": "2026-03-14T08:21:17Z",
                    "threshold_db": -3.0,
                    "baseline_scene_count": 6,
                    "event_scene_count": 5,
                    "sentinel1_confidence_score": 0.9,
                    "sentinel2_ndwi_mean": 0.37,
                    "sentinel2_mndwi_mean": 0.31,
                    "sentinel2_cloud_cover_pct": 12.5,
                    "sentinel2_confidence_score": 0.7,
                    "dem_mean_elevation_m": 6.2,
                    "dem_min_elevation_m": 1.1,
                    "dem_max_elevation_m": 13.8,
                    "dem_mean_slope_deg": 1.4,
                    "dem_confidence_score": 0.6,
                    "rainfall_24h_mm": 44.0,
                    "rainfall_72h_mm": 126.0,
                    "rainfall_anomaly_pct": 18.0,
                    "rainfall_confidence_score": 0.8,
                    "groundsource_event_count": 53,
                    "incident_count_recent": 4,
                    "incident_severity_index": 0.65,
                    "groundsource_confidence_score": 0.5,
                }
            )
        )

        service = EarthEngineService(
            project_id="demo-project",
            geojson_path=geojson_path,
            metadata_path=metadata_path,
        )
        runtime = service.get_runtime_flood_product()
        fusion = runtime["multisensor_fusion"]

        assert fusion["status"] == "deterministic_scaffold"
        assert fusion["fused_layer_id"] == "ee_multisensor_fusion"
        assert fusion["primary_temporal_frame"] == "change"
        assert set(fusion["signals"].keys()) == {
            "sentinel1",
            "sentinel2",
            "dem",
            "rainfall",
            "groundsource_incident",
        }

        for signal in fusion["signals"].values():
            assert "confidence" in signal
            assert "quality" in signal
            assert "metrics" in signal

        for signal in fusion["signals"].values():
            assert signal["quality"]["status"] in {"available", "partial", "derived"}
            assert signal["quality"]["status"] != "placeholder"
        assert set(fusion["component_availability"].keys()) == {
            "sentinel1",
            "sentinel2",
            "dem",
            "rainfall",
            "incidents",
        }
        assert fusion["runtime_inputs"]["tracked_component_count"] == 5
        assert set(fusion["runtime_inputs"]["component_status"].keys()) == set(
            fusion["signals"].keys()
        )
        assert set(fusion["runtime_inputs"]["component_scores"].keys()) == set(
            fusion["signals"].keys()
        )
        for source_component in fusion["source_components"]:
            assert "availability_status" in source_component
            assert "runtime_signal_score" in source_component

        aggregate = fusion["aggregate_confidence"]
        assert aggregate["signal_count"] == 5
        assert aggregate["available_signal_count"] == 5
        assert aggregate["score"] == pytest.approx(0.77)
        assert aggregate["label"] == "HIGH"
        assert aggregate["runtime_score"] is not None
        assert aggregate["runtime_signal_count"] == 5
        assert aggregate["blended_score"] is not None
        assert runtime["confidence"]["fusion_summary"] == aggregate

    def test_temporal_playback_includes_progression_frames_from_metadata_candidates(
        self, tmp_path: Path
    ) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        metadata_path = tmp_path / "analysis_provenance.json"

        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "source": "earth-engine",
                    "source_dataset": "COPERNICUS/S1_GRD",
                    "method": "SAR change detection",
                    "confidence": "MEDIUM",
                    "baseline_window": "2025-06-01 to 2025-09-30",
                    "event_window": "2025-11-01 to 2025-11-30",
                    "event_window_candidates": [
                        "2025-10-24 to 2025-10-31",
                        "2025-11-01/2025-11-07",
                        42,
                    ],
                }
            )
        )

        service = EarthEngineService(
            project_id="demo-project",
            geojson_path=geojson_path,
            metadata_path=metadata_path,
        )
        playback = service.get_runtime_temporal_playback()
        progression_frames = playback["progression_frames"]

        assert [frame["frame_id"] for frame in progression_frames] == [
            "progression_1",
            "progression_2",
        ]
        assert progression_frames[0]["index"] == 3
        assert progression_frames[0]["start_timestamp"] == "2025-10-24T00:00:00Z"
        assert progression_frames[0]["end_timestamp"] == "2025-10-31T23:59:59Z"
        assert progression_frames[1]["index"] == 4
        assert progression_frames[1]["start_timestamp"] == "2025-11-01T00:00:00Z"
        assert progression_frames[1]["end_timestamp"] == "2025-11-07T23:59:59Z"

        summary = service.get_runtime_temporal_summary()
        assert summary["progression_frame_count"] == 2

    def test_runtime_payload_preserves_legacy_metric_keys(self, tmp_path: Path) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        payload = service.get_flood_extent_runtime_payload()

        required_keys = {
            "area_sqkm",
            "growth_rate_pct",
            "metadata",
            "ee_runtime",
            "runtime_layers",
            "temporal_frames",
            "temporal_playback",
            "temporal_summary",
            "multisensor_fusion",
        }
        assert required_keys.issubset(payload.keys())
        assert payload["area_sqkm"] > 0
        assert "rate_pct_per_hour" in payload["growth_rate_pct"]
        assert payload["runtime_layers"] == payload["ee_runtime"]["layers"]
        assert payload["temporal_frames"] == payload["ee_runtime"]["temporal_frames"]
        assert payload["temporal_playback"] == payload["ee_runtime"]["temporal_playback"]
        assert payload["temporal_summary"] == payload["ee_runtime"]["temporal_summary"]
        assert payload["multisensor_fusion"] == payload["ee_runtime"]["multisensor_fusion"]
        assert "component_availability" in payload["multisensor_fusion"]
        assert "runtime_inputs" in payload["multisensor_fusion"]
        assert payload["ee_runtime"]["runtime_mode"] == "fallback_descriptor"
        assert payload["ee_runtime"]["status"] == "fallback"
        assert payload["ee_runtime"]["error"] is None
        assert isinstance(payload["ee_runtime"]["provenance"], dict)
        assert "source" in payload["ee_runtime"]["provenance"]
        assert isinstance(payload["ee_runtime"]["confidence"], dict)
        assert "label" in payload["ee_runtime"]["confidence"]
        assert "fusion_summary" in payload["ee_runtime"]["confidence"]

    def test_runtime_payload_returns_stable_error_envelope_for_missing_geojson(
        self, tmp_path: Path
    ) -> None:
        service = EarthEngineService(
            project_id="demo-project",
            geojson_path=tmp_path / "missing_flood_extent.geojson",
            metadata_path=tmp_path / "missing_analysis_provenance.json",
        )

        payload = service.get_flood_extent_runtime_payload()
        runtime = payload["ee_runtime"]

        assert {
            "area_sqkm",
            "growth_rate_pct",
            "metadata",
            "ee_runtime",
            "runtime_layers",
            "temporal_frames",
            "temporal_playback",
            "temporal_summary",
            "multisensor_fusion",
        }.issubset(payload.keys())
        assert payload["area_sqkm"] == 0.0
        assert isinstance(payload["growth_rate_pct"], dict)
        assert isinstance(payload["metadata"], dict)
        assert isinstance(payload["runtime_layers"], list)
        assert isinstance(payload["temporal_frames"], dict)
        assert isinstance(payload["temporal_playback"], dict)
        assert isinstance(payload["temporal_summary"], dict)
        assert isinstance(payload["multisensor_fusion"], dict)

        assert runtime["runtime_mode"] == "error"
        assert runtime["status"] == "error"
        assert isinstance(runtime["error"], dict)
        assert runtime["error"]["code"] == "ee_runtime_descriptor_unavailable"
        assert isinstance(runtime["provenance"], dict)
        assert {"source", "method", "project_id"}.issubset(runtime["provenance"].keys())
        assert isinstance(runtime["confidence"], dict)
        assert {"label", "score", "source", "fusion_summary"}.issubset(
            runtime["confidence"].keys()
        )


class TestLiveAnalysisTaskLifecycle:
    def test_lifecycle_state_transitions_and_result_are_stable(self) -> None:
        service = EarthEngineService(project_id="demo-project")

        task_id = service.submit_live_analysis_task({"analysis": "flood_extent"})
        assert task_id == "ee_live_task_0001"

        queued_status = service.get_live_analysis_task_status(task_id)
        assert queued_status["task_id"] == task_id
        assert queued_status["state"] == "queued"
        assert queued_status["submitted_at"] == queued_status["updated_at"]
        assert queued_status["started_at"] is None
        assert queued_status["completed_at"] is None
        assert queued_status["result_available"] is False
        assert queued_status["error"] is None
        assert service.get_live_analysis_task_result(task_id) is None

        running_status = service.start_live_analysis_task(task_id)
        assert running_status["state"] == "running"
        assert running_status["started_at"] is not None
        assert running_status["completed_at"] is None
        assert running_status["result_available"] is False

        completed_status = service.complete_live_analysis_task(
            task_id,
            {"analysis": "flood_extent", "status": "ok", "area_sqkm": 12.34},
        )
        assert completed_status["state"] == "complete"
        assert completed_status["started_at"] is not None
        assert completed_status["completed_at"] is not None
        assert completed_status["result_available"] is True
        assert completed_status["error"] is None

        result = service.get_live_analysis_task_result(task_id)
        assert result == {"analysis": "flood_extent", "status": "ok", "area_sqkm": 12.34}

    def test_latest_status_tracks_most_recent_task_and_result_visibility(self) -> None:
        service = EarthEngineService(project_id="demo-project")

        first_task_id = service.submit_live_analysis_task({"analysis": "flood_extent"})
        second_task_id = service.submit_live_analysis_task(
            {"analysis": "flood_extent", "window": "event"}
        )

        latest_queued_status = service.get_latest_live_analysis_task_status()
        assert latest_queued_status is not None
        assert latest_queued_status["task_id"] == second_task_id
        assert latest_queued_status["state"] == "queued"
        assert latest_queued_status["result_available"] is False

        service.start_live_analysis_task(second_task_id)
        service.complete_live_analysis_task(
            second_task_id,
            {"analysis": "flood_extent", "status": "ok", "window": "event"},
        )

        latest_complete_status = service.get_latest_live_analysis_task_status()
        assert latest_complete_status is not None
        assert latest_complete_status["task_id"] == second_task_id
        assert latest_complete_status["state"] == "complete"
        assert latest_complete_status["result_available"] is True

        latest_result = service.get_live_analysis_task_result(second_task_id)
        assert latest_result is not None
        assert latest_result["window"] == "event"
        assert service.get_live_analysis_task_result(first_task_id) is None

    def test_not_found_and_error_state_contract_is_deterministic(self) -> None:
        service = EarthEngineService(project_id="demo-project")

        not_found = service.get_live_analysis_task_status("ee_live_task_9999")
        assert not_found["task_id"] == "ee_live_task_9999"
        assert not_found["state"] == "error"
        assert not_found["submitted_at"] is None
        assert not_found["started_at"] is None
        assert not_found["completed_at"] is None
        assert not_found["result_available"] is False
        assert not_found["error"] == {
            "code": "live_analysis_task_not_found",
            "message": "Live analysis task 'ee_live_task_9999' was not found.",
        }
        assert service.get_live_analysis_task_result("ee_live_task_9999") is None

        task_id = service.create_live_analysis_task({"analysis": "flood_extent"})
        failure = service.fail_live_analysis_task(task_id, "Synthetic EE execution failure.")
        assert failure["state"] == "error"
        assert failure["result_available"] is False
        assert failure["error"] == {
            "code": "live_analysis_task_failed",
            "message": "Synthetic EE execution failure.",
        }
        assert service.get_live_analysis_task_result(task_id) is None

    def test_runtime_payload_fallback_contract_is_unchanged_when_tasking_unused(
        self, tmp_path: Path
    ) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        service.submit_live_analysis_task({"analysis": "flood_extent"})
        payload = service.get_flood_extent_runtime_payload()

        assert {
            "area_sqkm",
            "growth_rate_pct",
            "metadata",
            "ee_runtime",
            "runtime_layers",
            "temporal_frames",
            "temporal_playback",
            "temporal_summary",
            "multisensor_fusion",
        }.issubset(payload.keys())
        assert payload["runtime_layers"] == payload["ee_runtime"]["layers"]
        assert payload["temporal_frames"] == payload["ee_runtime"]["temporal_frames"]
        assert payload["temporal_playback"] == payload["ee_runtime"]["temporal_playback"]
        assert payload["temporal_summary"] == payload["ee_runtime"]["temporal_summary"]
        assert payload["multisensor_fusion"] == payload["ee_runtime"]["multisensor_fusion"]
        assert payload["ee_runtime"]["runtime_mode"] == "fallback_descriptor"
        assert payload["ee_runtime"]["status"] == "fallback"
        assert payload["ee_runtime"]["error"] is None

    def test_run_live_analysis_task_returns_runtime_payload_when_live_tiles_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HAWKEYE_ENABLE_EE_LIVE_TILES", raising=False)

        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        run_result = service.run_live_analysis_task({"analysis": "flood_extent"})

        assert run_result["task_id"].startswith("ee_live_task_")
        assert run_result["status"]["state"] == "complete"
        payload = run_result["result"]
        assert isinstance(payload, dict)
        assert payload["task_id"] == run_result["task_id"]
        assert payload["ee_runtime"]["status"] == "fallback"
        assert payload["ee_runtime"]["runtime_mode"] == "fallback_descriptor"
        assert payload["live_tile_status"]["status"] == "disabled"
        assert payload["runtime_layers"] == payload["ee_runtime"]["layers"]
        assert payload["multisensor_fusion"] == payload["ee_runtime"]["multisensor_fusion"]
        assert "component_availability" in payload["multisensor_fusion"]
        assert "runtime_inputs" in payload["multisensor_fusion"]

    def test_run_live_analysis_task_includes_task_observability_in_runtime_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HAWKEYE_ENABLE_EE_LIVE_TILES", raising=False)

        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        request_payload = {
            "analysis": "flood_extent",
            "requested_by": "ops_lead",
            "session_id": "demo-session",
        }

        run_result = service.run_live_analysis_task(request_payload)
        runtime_payload = run_result["result"]
        ee_runtime = runtime_payload["ee_runtime"]

        assert run_result["status"]["state"] == "complete"
        assert runtime_payload["task_id"] == run_result["task_id"]
        assert ee_runtime["task_id"] == run_result["task_id"]
        assert ee_runtime["task_request"] == request_payload
        assert runtime_payload["live_tile_status"]["status"] == "disabled"
        assert ee_runtime["live_tile_status"]["status"] == "disabled"

    def test_runtime_payload_normalizes_completed_task_fusion_contract(
        self, tmp_path: Path
    ) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        task_id = service.submit_live_analysis_task({"analysis": "flood_extent"})
        service.start_live_analysis_task(task_id)
        service.complete_live_analysis_task(
            task_id,
            {
                "runtime_payload": {
                    "area_sqkm": 9.1,
                    "growth_rate_pct": {"rate_pct_per_hour": 4.2, "source": "test"},
                    "metadata": {"source": "earth-engine"},
                    "ee_runtime": {
                        "runtime_mode": "fallback_descriptor",
                        "status": "fallback",
                        "layers": [],
                        "temporal_frames": {},
                        "temporal_playback": {},
                        "temporal_summary": {},
                        "multisensor_fusion": {
                            "status": "deterministic_scaffold",
                            "signals": {
                                "sentinel1": {
                                    "signal_id": "sentinel1",
                                    "name": "Sentinel-1 SAR change/backscatter",
                                }
                            },
                        },
                    },
                }
            },
        )

        payload = service.get_flood_extent_runtime_payload()
        assert payload["multisensor_fusion"] == payload["ee_runtime"]["multisensor_fusion"]
        assert "component_availability" in payload["multisensor_fusion"]
        assert "runtime_inputs" in payload["multisensor_fusion"]
        assert isinstance(payload["multisensor_fusion"]["aggregate_confidence"], dict)

    def test_run_live_analysis_task_uses_live_tile_handles_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "HIGH",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        live_tile_handles = {
            "ee_baseline_backscatter": "ee_live_tile_00001",
            "ee_event_backscatter": "ee_live_tile_00002",
            "ee_change_detection": "ee_live_tile_00003",
            "ee_multisensor_fusion": "ee_live_tile_00004",
            "ee_fused_flood_likelihood": "ee_live_tile_00005",
        }
        live_tile_status = {
            "status": "live",
            "enabled": True,
            "reason": None,
            "available_layer_count": len(live_tile_handles),
            "layer_ids": sorted(live_tile_handles.keys()),
            "errors": [],
        }

        monkeypatch.setattr(
            service,
            "_build_live_runtime_tile_handles",
            lambda **_kwargs: (live_tile_handles, live_tile_status),
        )

        run_result = service.run_live_analysis_task({"analysis": "flood_extent"})
        runtime_payload = run_result["result"]
        ee_runtime = runtime_payload["ee_runtime"]

        assert run_result["status"]["state"] == "complete"
        assert ee_runtime["status"] == "live"
        assert ee_runtime["runtime_mode"] == "live_earth_engine_tiles"
        assert ee_runtime["task_id"] == run_result["task_id"]
        assert ee_runtime["live_tile_status"] == live_tile_status
        assert runtime_payload["live_tile_status"] == live_tile_status
        assert runtime_payload["runtime_layers"] == ee_runtime["layers"]

        layers_by_id = {layer["id"]: layer for layer in ee_runtime["layers"]}
        assert set(live_tile_handles.keys()).issubset(layers_by_id.keys())
        for layer_id, tile_handle in live_tile_handles.items():
            tile_source = layers_by_id[layer_id]["tile_source"]
            assert tile_source["status"] == "live"
            assert tile_source["available"] is True
            assert tile_source["task_id"] == run_result["task_id"]
            assert tile_source["tile_handle"] == tile_handle
            assert "/api/earth-engine/tiles/live/" in tile_source["url_template"]

    def test_fetch_live_tile_returns_not_found_for_unknown_handle(self) -> None:
        service = EarthEngineService(project_id="demo-project")
        result = service.fetch_live_tile("ee_live_tile_99999", z=1, x=0, y=0)

        assert result["status"] == "not_found"
        assert result["error"]["code"] == "live_tile_not_found"

    def test_fetch_live_tile_returns_error_for_invalid_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = EarthEngineService(project_id="demo-project")
        monkeypatch.setattr(
            service,
            "_lookup_live_tile_template",
            lambda _tile_handle: {
                "task_id": "ee_live_task_0001",
                "layer_id": "ee_change_detection",
                "url_template": "https://tiles.example.com/static.png",
            },
        )

        result = service.fetch_live_tile("ee_live_tile_00001", z=2, x=1, y=1)
        assert result["status"] == "error"
        assert result["error"]["code"] == "live_tile_template_invalid"

    def test_fetch_live_tile_returns_error_for_request_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = EarthEngineService(project_id="demo-project")
        monkeypatch.setattr(
            service,
            "_lookup_live_tile_template",
            lambda _tile_handle: {
                "task_id": "ee_live_task_0001",
                "layer_id": "ee_change_detection",
                "url_template": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
        )

        def _raise_request_exception(*_args, **_kwargs):
            raise requests.RequestException("network timeout")

        monkeypatch.setattr(
            "app.services.earth_engine_service.requests.get",
            _raise_request_exception,
        )

        result = service.fetch_live_tile("ee_live_tile_00001", z=2, x=1, y=1)
        assert result["status"] == "error"
        assert result["error"]["code"] == "live_tile_fetch_failed"
        assert "network timeout" in result["error"]["message"]

    def test_fetch_live_tile_surfaces_upstream_http_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = EarthEngineService(project_id="demo-project")
        monkeypatch.setattr(
            service,
            "_lookup_live_tile_template",
            lambda _tile_handle: {
                "task_id": "ee_live_task_0001",
                "layer_id": "ee_change_detection",
                "url_template": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
        )
        monkeypatch.setattr(
            "app.services.earth_engine_service.requests.get",
            lambda *_args, **_kwargs: SimpleNamespace(
                status_code=503,
                content=b"",
                headers={},
            ),
        )

        result = service.fetch_live_tile("ee_live_tile_00001", z=2, x=1, y=1)
        assert result["status"] == "error"
        assert result["http_status"] == 503
        assert result["error"]["code"] == "live_tile_upstream_error"

    def test_fetch_live_tile_returns_content_and_observability_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = EarthEngineService(project_id="demo-project")
        monkeypatch.setattr(
            service,
            "_lookup_live_tile_template",
            lambda _tile_handle: {
                "task_id": "ee_live_task_0042",
                "layer_id": "ee_multisensor_fusion",
                "url_template": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
        )
        monkeypatch.setattr(
            "app.services.earth_engine_service.requests.get",
            lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200,
                content=b"\x89PNG\r\n\x1a\n",
                headers={
                    "content-type": "image/png",
                    "cache-control": "public, max-age=60",
                    "etag": "\"ee-live\"",
                },
            ),
        )

        result = service.fetch_live_tile("ee_live_tile_00042", z=2, x=1, y=1)
        assert result["status"] == "ok"
        assert result["content"].startswith(b"\x89PNG")
        assert result["content_type"] == "image/png"
        assert result["cache_control"] == "public, max-age=60"
        assert result["etag"] == "\"ee-live\""
        assert result["task_id"] == "ee_live_task_0042"
        assert result["layer_id"] == "ee_multisensor_fusion"


class TestAnalystLiveTaskMetadata:
    def test_get_flood_extent_exposes_live_task_metadata_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("google.genai")
        pytest.importorskip("google.adk")
        from app.hawkeye_agent.tools import analyst

        geojson_path = tmp_path / "flood_extent.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "earth-engine",
                        "baseline_period": "2025-06-01 to 2025-09-30",
                        "flood_period": "2025-11-01 to 2025-11-30",
                        "method": "SAR change detection",
                        "confidence": "MEDIUM",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.84, -6.22],
                                [106.86, -6.22],
                                [106.86, -6.24],
                                [106.84, -6.24],
                                [106.84, -6.22],
                            ]
                        ],
                    },
                }
            )
        )

        service = EarthEngineService(project_id="demo-project", geojson_path=geojson_path)
        task_id = service.submit_live_analysis_task({"analysis": "flood_extent"})
        service.start_live_analysis_task(task_id)
        service.complete_live_analysis_task(task_id, {"status": "complete"})

        monkeypatch.setattr(analyst, "_earth_engine", service)
        result = analyst.get_flood_extent()

        assert "geojson" in result
        assert "area_sqkm" in result
        assert "ee_runtime" in result
        assert result["live_analysis_task"]["task_id"] == task_id
        assert result["live_analysis_task"]["state"] == "complete"
        assert result["live_analysis_task"]["result"] == {"status": "complete"}
        assert result["live_task"] == result["live_analysis_task"]
