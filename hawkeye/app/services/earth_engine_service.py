"""Earth Engine service — reads pre-computed flood extent GeoJSON and derived metrics."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any

import requests
from shapely.geometry import shape
from shapely.ops import transform
import pyproj


_JAKARTA_POP_DENSITY_PER_SQKM = 15_000.0

_BASE_DIR = Path(__file__).resolve().parent.parent.parent  # hawkeye/
_DEFAULT_GEOJSON = _BASE_DIR / "data" / "geojson" / "flood_extent.geojson"
_DEFAULT_METADATA = _BASE_DIR / "data" / "geojson" / "analysis_provenance.json"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONFIDENCE_SCORE_BY_LABEL = {
    "VERY_HIGH": 0.9,
    "HIGH": 0.8,
    "MEDIUM": 0.6,
    "LOW": 0.4,
    "VERY_LOW": 0.2,
    "UNKNOWN": 0.2,
}
_FUSION_SIGNAL_WEIGHTS = {
    "sentinel1": 0.45,
    "sentinel2": 0.15,
    "dem": 0.15,
    "rainfall": 0.15,
    "groundsource_incident": 0.10,
}
_FUSION_PRIMARY_LAYER_ID = "ee_multisensor_fusion"
_FUSION_RUNTIME_LAYER_IDS = (_FUSION_PRIMARY_LAYER_ID, "ee_fused_flood_likelihood")
_LIVE_ANALYSIS_TASK_STATES = {"queued", "running", "complete", "error"}
_LIVE_ANALYSIS_TASK_PREFIX = "ee_live_task"
_LIVE_TILE_HANDLE_PREFIX = "ee_live_tile"
_LIVE_TILE_TASK_CACHE_CONTROL = "public, max-age=300"
_LIVE_TILE_FETCH_TIMEOUT_S = 12
_LIVE_TILE_LAYER_IDS = {
    "ee_baseline_backscatter",
    "ee_event_backscatter",
    "ee_change_detection",
    "ee_multisensor_fusion",
    "ee_fused_flood_likelihood",
}
_LIVE_TILE_ENABLED_VALUES = {"1", "true", "yes", "on"}


class EarthEngineService:
    def __init__(
        self,
        project_id: str,
        geojson_path: str | Path | None = None,
        metadata_path: str | Path | None = None,
    ):
        self.project_id = project_id
        self._geojson_path = Path(geojson_path) if geojson_path else _DEFAULT_GEOJSON
        self._metadata_path = (
            Path(metadata_path)
            if metadata_path
            else self._geojson_path.with_name("analysis_provenance.json")
        )
        self._cached_geojson: dict | None = None
        self._cached_metadata: dict | None = None
        self._live_analysis_tasks: dict[str, dict[str, Any]] = {}
        self._live_analysis_task_order: list[str] = []
        self._live_analysis_task_sequence = 0
        self._live_analysis_task_lock = Lock()
        self._live_tile_registry: dict[str, dict[str, Any]] = {}
        self._live_tile_sequence = 0
        self._live_tile_lock = Lock()
        self._ee_module: Any | None = None
        self._ee_initialized = False

    def _current_utc_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _next_live_analysis_task_id(self) -> str:
        self._live_analysis_task_sequence += 1
        return f"{_LIVE_ANALYSIS_TASK_PREFIX}_{self._live_analysis_task_sequence:04d}"

    def _not_found_live_analysis_task_status(self, task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "state": "error",
            "submitted_at": None,
            "started_at": None,
            "completed_at": None,
            "updated_at": self._current_utc_timestamp(),
            "result_available": False,
            "error": {
                "code": "live_analysis_task_not_found",
                "message": f"Live analysis task '{task_id}' was not found.",
            },
        }

    def _serialize_live_analysis_task_status(self, task: dict[str, Any]) -> dict[str, Any]:
        error_payload = task.get("error")
        if error_payload is not None and not isinstance(error_payload, dict):
            error_payload = {
                "code": "live_analysis_task_failed",
                "message": str(error_payload),
            }
        state = task.get("state")
        if state not in _LIVE_ANALYSIS_TASK_STATES:
            state = "error"
        return {
            "task_id": task.get("task_id"),
            "state": state,
            "submitted_at": task.get("submitted_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "updated_at": task.get("updated_at"),
            "result_available": task.get("result") is not None,
            "error": deepcopy(error_payload) if isinstance(error_payload, dict) else None,
        }

    def submit_live_analysis_task(self, request: dict[str, Any] | None = None) -> str:
        with self._live_analysis_task_lock:
            task_id = self._next_live_analysis_task_id()
            submitted_at = self._current_utc_timestamp()
            self._live_analysis_tasks[task_id] = {
                "task_id": task_id,
                "state": "queued",
                "submitted_at": submitted_at,
                "started_at": None,
                "completed_at": None,
                "updated_at": submitted_at,
                "request": deepcopy(request) if isinstance(request, dict) else {},
                "result": None,
                "error": None,
            }
            self._live_analysis_task_order.append(task_id)
            return task_id

    def create_live_analysis_task(self, request: dict[str, Any] | None = None) -> str:
        return self.submit_live_analysis_task(request=request)

    def start_live_analysis_task(self, task_id: str) -> dict[str, Any]:
        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            if not isinstance(task, dict):
                return self._not_found_live_analysis_task_status(task_id)

            if task.get("state") in {"complete", "error"}:
                return self._serialize_live_analysis_task_status(task)

            started_at = self._current_utc_timestamp()
            task["state"] = "running"
            task["started_at"] = task.get("started_at") or started_at
            task["updated_at"] = started_at
            task["error"] = None
            return self._serialize_live_analysis_task_status(task)

    def complete_live_analysis_task(
        self, task_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            if not isinstance(task, dict):
                return self._not_found_live_analysis_task_status(task_id)

            completed_at = self._current_utc_timestamp()
            task["state"] = "complete"
            task["started_at"] = task.get("started_at") or completed_at
            task["completed_at"] = completed_at
            task["updated_at"] = completed_at
            task["result"] = deepcopy(result) if isinstance(result, dict) else {}
            task["error"] = None
            return self._serialize_live_analysis_task_status(task)

    def fail_live_analysis_task(
        self,
        task_id: str,
        error_message: str,
        *,
        error_code: str = "live_analysis_task_failed",
    ) -> dict[str, Any]:
        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            if not isinstance(task, dict):
                return self._not_found_live_analysis_task_status(task_id)

            failed_at = self._current_utc_timestamp()
            task["state"] = "error"
            task["started_at"] = task.get("started_at") or failed_at
            task["completed_at"] = failed_at
            task["updated_at"] = failed_at
            task["result"] = None
            task["error"] = {
                "code": error_code,
                "message": error_message,
            }
            return self._serialize_live_analysis_task_status(task)

    def get_live_analysis_task_status(self, task_id: str) -> dict[str, Any]:
        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            if not isinstance(task, dict):
                return self._not_found_live_analysis_task_status(task_id)
            return self._serialize_live_analysis_task_status(task)

    def get_latest_live_analysis_task_status(self) -> dict[str, Any] | None:
        with self._live_analysis_task_lock:
            if not self._live_analysis_task_order:
                return None
            latest_task_id = self._live_analysis_task_order[-1]
            task = self._live_analysis_tasks.get(latest_task_id)
            if not isinstance(task, dict):
                return None
            return self._serialize_live_analysis_task_status(task)

    def get_live_analysis_task_result(self, task_id: str) -> dict[str, Any] | None:
        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            if not isinstance(task, dict) or task.get("state") != "complete":
                return None
            result = task.get("result")
            if isinstance(result, dict):
                return deepcopy(result)
            return {"result": deepcopy(result)}

    def _is_live_tile_runtime_enabled(self) -> bool:
        flag = os.getenv("HAWKEYE_ENABLE_EE_LIVE_TILES", "")
        return flag.strip().lower() in _LIVE_TILE_ENABLED_VALUES

    def _resolve_earth_engine_module(self) -> Any:
        if self._ee_module is not None and self._ee_initialized:
            return self._ee_module

        try:
            import ee  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError("earthengine_api_not_available") from exc

        if not self._ee_initialized:
            try:
                if self.project_id:
                    ee.Initialize(project=self.project_id)
                else:
                    ee.Initialize()
                self._ee_initialized = True
            except Exception as exc:
                raise RuntimeError(f"earthengine_initialize_failed: {exc}") from exc

        self._ee_module = ee
        return ee

    def _next_live_tile_handle(self) -> str:
        self._live_tile_sequence += 1
        return f"{_LIVE_TILE_HANDLE_PREFIX}_{self._live_tile_sequence:05d}"

    def _register_live_tile_template(
        self,
        *,
        task_id: str,
        layer_id: str,
        remote_url_template: str,
    ) -> str:
        with self._live_tile_lock:
            tile_handle = self._next_live_tile_handle()
            self._live_tile_registry[tile_handle] = {
                "tile_handle": tile_handle,
                "task_id": task_id,
                "layer_id": layer_id,
                "url_template": remote_url_template,
                "registered_at": self._current_utc_timestamp(),
            }
            return tile_handle

    def _lookup_live_tile_template(self, tile_handle: str) -> dict[str, Any] | None:
        with self._live_tile_lock:
            tile_entry = self._live_tile_registry.get(tile_handle)
            if not isinstance(tile_entry, dict):
                return None
            return dict(tile_entry)

    def fetch_live_tile(
        self,
        tile_handle: str,
        *,
        z: int,
        x: int,
        y: int,
    ) -> dict[str, Any]:
        tile_entry = self._lookup_live_tile_template(tile_handle)
        if not isinstance(tile_entry, dict):
            return {
                "status": "not_found",
                "error": {
                    "code": "live_tile_not_found",
                    "message": f"Live tile handle '{tile_handle}' was not found.",
                },
            }

        url_template = tile_entry.get("url_template")
        if not isinstance(url_template, str) or "{z}" not in url_template:
            return {
                "status": "error",
                "error": {
                    "code": "live_tile_template_invalid",
                    "message": f"Live tile template for '{tile_handle}' is invalid.",
                },
            }

        tile_url = (
            url_template.replace("{z}", str(z))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
        )
        try:
            response = requests.get(tile_url, timeout=_LIVE_TILE_FETCH_TIMEOUT_S)
        except requests.RequestException as exc:
            return {
                "status": "error",
                "error": {
                    "code": "live_tile_fetch_failed",
                    "message": str(exc),
                },
            }

        if response.status_code != 200:
            return {
                "status": "error",
                "http_status": response.status_code,
                "error": {
                    "code": "live_tile_upstream_error",
                    "message": f"Upstream tile endpoint returned HTTP {response.status_code}.",
                },
            }

        return {
            "status": "ok",
            "content": response.content,
            "content_type": response.headers.get("content-type", "image/png"),
            "cache_control": response.headers.get(
                "cache-control", _LIVE_TILE_TASK_CACHE_CONTROL
            ),
            "etag": response.headers.get("etag"),
            "tile_handle": tile_handle,
            "task_id": tile_entry.get("task_id"),
            "layer_id": tile_entry.get("layer_id"),
        }

    def run_live_analysis_task(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        task_id = self.submit_live_analysis_task(request=request)
        status = self.execute_live_analysis_task(task_id)
        result = self.get_live_analysis_task_result(task_id)
        return {
            "task_id": task_id,
            "status": status,
            "result": result,
        }

    def execute_live_analysis_task(self, task_id: str) -> dict[str, Any]:
        status = self.start_live_analysis_task(task_id)
        if status.get("state") == "error":
            return status

        with self._live_analysis_task_lock:
            task = self._live_analysis_tasks.get(task_id)
            task_request = (
                deepcopy(task.get("request"))
                if isinstance(task, dict) and isinstance(task.get("request"), dict)
                else {}
            )

        try:
            runtime_payload = self._build_live_analysis_runtime_payload(
                task_id=task_id, request=task_request
            )
            return self.complete_live_analysis_task(task_id, runtime_payload)
        except Exception as exc:
            return self.fail_live_analysis_task(
                task_id,
                str(exc),
                error_code="live_analysis_task_failed",
            )

    def _first_non_empty(self, *values: Any) -> Any:
        for value in values:
            if isinstance(value, str):
                if value.strip():
                    return value.strip()
            elif value is not None:
                return value
        return None

    def _coerce_numeric(self, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    def _normalize_confidence_label(self, label: Any, default: str = "UNKNOWN") -> str:
        resolved = self._first_non_empty(label, default)
        if not isinstance(resolved, str):
            return default
        normalized = resolved.strip().replace("-", "_").replace(" ", "_").upper()
        if normalized in _CONFIDENCE_SCORE_BY_LABEL:
            return normalized
        if normalized == "VERYHIGH":
            return "VERY_HIGH"
        if normalized == "VERYLOW":
            return "VERY_LOW"
        return default

    def _label_to_score(self, label: Any) -> float:
        normalized = self._normalize_confidence_label(label)
        return _CONFIDENCE_SCORE_BY_LABEL.get(normalized, _CONFIDENCE_SCORE_BY_LABEL["UNKNOWN"])

    def _score_to_label(self, score: float | None) -> str:
        if score is None:
            return "UNKNOWN"
        if score >= 0.85:
            return "VERY_HIGH"
        if score >= 0.70:
            return "HIGH"
        if score >= 0.50:
            return "MEDIUM"
        if score >= 0.30:
            return "LOW"
        return "VERY_LOW"

    def _clamp_score(self, value: Any) -> float | None:
        numeric = self._coerce_numeric(value)
        if numeric is None:
            return None
        return round(max(0.0, min(numeric, 1.0)), 3)

    def _average_scores(self, *values: Any) -> float | None:
        numeric_values: list[float] = []
        for value in values:
            numeric = self._coerce_numeric(value)
            if numeric is not None:
                numeric_values.append(numeric)
        if not numeric_values:
            return None
        return round(sum(numeric_values) / len(numeric_values), 3)

    def _parse_runtime_timestamp(self, value: Any) -> datetime | None:
        normalized = self._normalize_timestamp(value) if isinstance(value, str) else None
        if not isinstance(normalized, str):
            return None
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _compute_window_duration_hours(self, window: str | None) -> float | None:
        window_range = self._parse_window_range(window)
        start = self._parse_runtime_timestamp(window_range.get("start_timestamp"))
        end = self._parse_runtime_timestamp(window_range.get("end_timestamp"))
        if start is None or end is None:
            return None
        duration_hours = (end - start).total_seconds() / 3600.0
        if duration_hours < 0:
            return None
        return round(duration_hours, 2)

    def _build_signal_confidence(
        self,
        metadata: dict[str, Any],
        *,
        label_keys: list[str],
        score_keys: list[str],
        default_label: str = "UNKNOWN",
    ) -> dict[str, Any]:
        raw_label = self._first_non_empty(*(metadata.get(key) for key in label_keys))
        label = self._normalize_confidence_label(raw_label, default=default_label)

        raw_score = self._first_non_empty(*(metadata.get(key) for key in score_keys))
        score = self._coerce_numeric(raw_score)
        score_source = "analysis_metadata"
        if score is None:
            score = self._label_to_score(label)
            score_source = "label_lookup"
        else:
            score = max(0.0, min(score, 1.0))

        return {
            "label": label,
            "score": round(score, 3),
            "source": "analysis_metadata",
            "score_source": score_source,
        }

    def _build_signal_quality(
        self,
        metrics: dict[str, Any],
        tracked_metric_keys: list[str],
        placeholder_note: str,
        *,
        derived_metric_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        available_metric_keys = [
            metric_key
            for metric_key in tracked_metric_keys
            if self._first_non_empty(metrics.get(metric_key)) is not None
        ]
        missing_metric_keys = [
            metric_key
            for metric_key in tracked_metric_keys
            if metric_key not in available_metric_keys
        ]
        runtime_metric_keys = [
            metric_key
            for metric_key in (derived_metric_keys or [])
            if self._first_non_empty(metrics.get(metric_key)) is not None
        ]
        total = len(tracked_metric_keys)
        present = len(available_metric_keys)
        runtime_metric_count = len(runtime_metric_keys)
        coverage_ratio = round((present / total), 3) if total else 0.0
        if total and present == total:
            status = "available"
        elif present > 0:
            status = "partial"
        elif runtime_metric_count > 0:
            status = "derived"
        else:
            status = "placeholder"

        if status == "available":
            note: str | None = None
        elif status == "partial":
            note = (
                f"Partial signal coverage ({present}/{total} tracked metrics present). "
                f"{placeholder_note}"
            )
        else:
            note = placeholder_note

        return {
            "status": status,
            "available_metric_count": present,
            "tracked_metric_count": total,
            "coverage_ratio": coverage_ratio,
            "source": "runtime_signal_fusion_inputs",
            "available_metric_keys": available_metric_keys,
            "missing_metric_keys": missing_metric_keys,
            "derived_metric_count": runtime_metric_count,
            "derived_metric_keys": runtime_metric_keys,
            "note": note,
        }

    def _normalize_timestamp(
        self, value: str | None, *, end_of_day: bool = False
    ) -> str | None:
        if not value or not isinstance(value, str):
            return None

        normalized = value.strip()
        if not normalized:
            return None
        if _DATE_RE.match(normalized):
            return (
                f"{normalized}T23:59:59Z" if end_of_day else f"{normalized}T00:00:00Z"
            )
        return normalized

    def _parse_window_range(self, window: str | None) -> dict[str, str | None]:
        if not window or not isinstance(window, str):
            return {
                "window": None,
                "start_timestamp": None,
                "end_timestamp": None,
            }

        normalized = " ".join(window.strip().split())
        start: str | None = None
        end: str | None = None

        if " to " in normalized:
            start, end = [part.strip() for part in normalized.split(" to ", 1)]
        elif "/" in normalized:
            parts = [part.strip() for part in normalized.split("/", 1)]
            if len(parts) == 2:
                start, end = parts[0], parts[1]

        return {
            "window": normalized,
            "start_timestamp": self._normalize_timestamp(start),
            "end_timestamp": self._normalize_timestamp(end, end_of_day=True),
        }

    def _build_runtime_provenance(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": self._first_non_empty(metadata.get("source"), "earth-engine"),
            "source_dataset": self._first_non_empty(
                metadata.get("source_dataset"), metadata.get("source")
            ),
            "source_dataset_detail": self._first_non_empty(
                metadata.get("source_dataset_detail"), metadata.get("source_detail")
            ),
            "method": self._first_non_empty(metadata.get("method"), "SAR change detection"),
            "threshold_db": metadata.get("threshold_db"),
            "updated_at": self._first_non_empty(
                metadata.get("generated_at"),
                metadata.get("computed_at"),
                metadata.get("updated_at"),
            ),
            "project_id": self._first_non_empty(metadata.get("project_id"), self.project_id),
            "generated_from": self._first_non_empty(
                metadata.get("generated_from"), self._geojson_path.name
            ),
            "sidecar_path": self._first_non_empty(
                metadata.get("sidecar_path"), str(self._metadata_path)
            ),
        }

    def _build_runtime_confidence(self, metadata: dict[str, Any]) -> dict[str, Any]:
        confidence = self._first_non_empty(metadata.get("confidence"), "UNKNOWN")
        label = confidence.upper() if isinstance(confidence, str) else confidence
        return {
            "label": label,
            "score": metadata.get("confidence_score"),
            "source": "analysis_metadata",
        }

    def _build_default_multisensor_fusion(
        self,
        *,
        status: str,
        description: str,
        confidence_source: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        aggregate_confidence = {
            "label": "UNKNOWN",
            "score": None,
            "source": confidence_source,
            "method": "weighted_signal_confidence",
            "method_summary": description,
            "signal_count": 0,
            "available_signal_count": 0,
            "coverage_ratio": 0.0,
            "weight_sum": 0.0,
            "runtime_score": None,
            "runtime_label": "UNKNOWN",
            "runtime_signal_count": 0,
            "runtime_weight_sum": 0.0,
            "blended_score": None,
        }
        payload = {
            "schema_version": "1.0",
            "status": status,
            "fused_layer_id": _FUSION_PRIMARY_LAYER_ID,
            "fused_layer_ids": list(_FUSION_RUNTIME_LAYER_IDS),
            "primary_temporal_frame": "change",
            "source_components": [],
            "signals": {},
            "components": {},
            "component_blocks": {},
            "component_availability": {},
            "runtime_inputs": {},
            "aggregate_confidence": aggregate_confidence,
            "method_summary": {
                "name": "deterministic_multisensor_fusion_scaffold",
                "aggregation": "weighted_signal_confidence",
                "description": description,
                "component_order": [
                    "sentinel1",
                    "sentinel2",
                    "dem",
                    "rainfall",
                    "incidents",
                ],
                "fused_layer_ids": list(_FUSION_RUNTIME_LAYER_IDS),
            },
        }
        if error_code and error_message:
            payload["error"] = {
                "code": error_code,
                "message": error_message,
            }
        return payload

    def _stabilize_runtime_multisensor_fusion(
        self,
        fusion_payload: Any,
        *,
        runtime_status: str,
        runtime_error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        default_fusion = self._build_default_multisensor_fusion(
            status="error" if runtime_status == "error" else "unavailable",
            description=(
                "Multisensor fusion scaffold unavailable due to runtime error."
                if runtime_status == "error"
                else "Deterministic multisensor fusion scaffold is unavailable."
            ),
            confidence_source="runtime_error" if runtime_status == "error" else "runtime_fallback",
            error_code="multisensor_fusion_unavailable" if runtime_status == "error" else None,
            error_message=runtime_error.get("message")
            if runtime_status == "error" and isinstance(runtime_error, dict)
            else None,
        )
        if not isinstance(fusion_payload, dict):
            return default_fusion

        fusion = dict(fusion_payload)

        source_components = fusion.get("source_components")
        if not isinstance(source_components, list):
            source_components = default_fusion["source_components"]

        signals = fusion.get("signals")
        if not isinstance(signals, dict):
            signals = default_fusion["signals"]

        components = fusion.get("components")
        if not isinstance(components, dict):
            components = default_fusion["components"]

        component_blocks = fusion.get("component_blocks")
        if not isinstance(component_blocks, dict):
            component_blocks = components

        component_availability = fusion.get("component_availability")
        if not isinstance(component_availability, dict):
            component_availability = default_fusion["component_availability"]

        runtime_inputs = fusion.get("runtime_inputs")
        if not isinstance(runtime_inputs, dict):
            runtime_inputs = default_fusion["runtime_inputs"]

        aggregate_confidence = fusion.get("aggregate_confidence")
        if not isinstance(aggregate_confidence, dict):
            aggregate_confidence = dict(default_fusion["aggregate_confidence"])
        else:
            aggregate_confidence = {
                **default_fusion["aggregate_confidence"],
                **aggregate_confidence,
            }

        method_summary = fusion.get("method_summary")
        if not isinstance(method_summary, dict):
            method_summary = dict(default_fusion["method_summary"])
        else:
            method_summary = {
                **default_fusion["method_summary"],
                **method_summary,
            }

        fused_layer_id = fusion.get("fused_layer_id")
        if not isinstance(fused_layer_id, str) or not fused_layer_id:
            fused_layer_id = _FUSION_PRIMARY_LAYER_ID

        fused_layer_ids = fusion.get("fused_layer_ids")
        if not isinstance(fused_layer_ids, list):
            fused_layer_ids = []
        normalized_fused_layer_ids: list[str] = []
        for layer_id in [fused_layer_id, *fused_layer_ids, *_FUSION_RUNTIME_LAYER_IDS]:
            if isinstance(layer_id, str) and layer_id and layer_id not in normalized_fused_layer_ids:
                normalized_fused_layer_ids.append(layer_id)

        error = fusion.get("error")
        if error is not None and not isinstance(error, dict):
            error = {"code": "multisensor_fusion_unavailable", "message": str(error)}
        if runtime_status == "error" and not isinstance(error, dict):
            error = {
                "code": "multisensor_fusion_unavailable",
                "message": (
                    runtime_error.get("message")
                    if isinstance(runtime_error, dict)
                    else "Multisensor fusion scaffold unavailable due to runtime error."
                ),
            }

        return {
            "schema_version": fusion.get("schema_version")
            if isinstance(fusion.get("schema_version"), str)
            else "1.0",
            "status": fusion.get("status")
            if isinstance(fusion.get("status"), str)
            else default_fusion["status"],
            "fused_layer_id": fused_layer_id,
            "fused_layer_ids": normalized_fused_layer_ids,
            "primary_temporal_frame": fusion.get("primary_temporal_frame")
            if isinstance(fusion.get("primary_temporal_frame"), str)
            else "change",
            "source_components": source_components,
            "signals": signals,
            "components": components,
            "component_blocks": component_blocks,
            "component_availability": component_availability,
            "runtime_inputs": runtime_inputs,
            "aggregate_confidence": aggregate_confidence,
            "method_summary": method_summary,
            **({"error": error} if isinstance(error, dict) else {}),
        }

    def _stabilize_runtime_flood_product(
        self,
        runtime_product: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata_payload = metadata if isinstance(metadata, dict) else {}
        default_provenance = self._build_runtime_provenance(metadata_payload)
        default_confidence = self._build_runtime_confidence(metadata_payload)
        default_confidence.setdefault("label", "UNKNOWN")
        default_confidence.setdefault("score", None)
        default_confidence.setdefault("source", "analysis_metadata")

        runtime = dict(runtime_product) if isinstance(runtime_product, dict) else {}

        runtime_error = runtime.get("error")
        if runtime_error is not None and not isinstance(runtime_error, dict):
            runtime_error = {
                "code": "ee_runtime_descriptor_unavailable",
                "message": str(runtime_error),
            }

        runtime_status = runtime.get("status")
        if not isinstance(runtime_status, str) or not runtime_status:
            runtime_status = "error" if runtime_error else "fallback"

        runtime_mode = runtime.get("runtime_mode")
        if not isinstance(runtime_mode, str) or not runtime_mode:
            runtime_mode = "error" if runtime_status == "error" else "fallback_descriptor"

        layers = runtime.get("layers")
        if not isinstance(layers, list):
            layers = []

        temporal_frames = runtime.get("temporal_frames")
        if not isinstance(temporal_frames, dict):
            temporal_frames = {}

        temporal_playback = runtime.get("temporal_playback")
        if not isinstance(temporal_playback, dict):
            temporal_playback = {}

        temporal_summary = runtime.get("temporal_summary")
        if not isinstance(temporal_summary, dict):
            temporal_summary = {}

        provenance = dict(default_provenance)
        provenance_update = runtime.get("provenance")
        if isinstance(provenance_update, dict):
            for key, value in provenance_update.items():
                if self._first_non_empty(value) is not None:
                    provenance[key] = value

        confidence = dict(default_confidence)
        confidence_update = runtime.get("confidence")
        if isinstance(confidence_update, dict):
            for key, value in confidence_update.items():
                if self._first_non_empty(value) is not None:
                    confidence[key] = value
        confidence["label"] = self._normalize_confidence_label(
            confidence.get("label"), default="UNKNOWN"
        )
        confidence_score = self._coerce_numeric(confidence.get("score"))
        confidence["score"] = round(confidence_score, 3) if confidence_score is not None else None

        multisensor_fusion = self._stabilize_runtime_multisensor_fusion(
            runtime.get("multisensor_fusion"),
            runtime_status=runtime_status,
            runtime_error=runtime_error if isinstance(runtime_error, dict) else None,
        )
        if not isinstance(confidence.get("fusion_summary"), dict):
            confidence["fusion_summary"] = multisensor_fusion.get("aggregate_confidence")

        return {
            **runtime,
            "runtime_mode": runtime_mode,
            "status": runtime_status,
            "layers": layers,
            "temporal_frames": temporal_frames,
            "temporal_playback": temporal_playback,
            "temporal_summary": temporal_summary,
            "provenance": provenance,
            "confidence": confidence,
            "multisensor_fusion": multisensor_fusion,
            "error": runtime_error if isinstance(runtime_error, dict) else None,
        }

    def _build_temporal_frame_provenance(
        self,
        frame_id: str,
        metadata: dict[str, Any],
        runtime_provenance: dict[str, Any],
    ) -> dict[str, Any]:
        if frame_id == "baseline":
            window = self._first_non_empty(
                metadata.get("baseline_window"), metadata.get("baseline_period")
            )
            scene_count = metadata.get("baseline_scene_count")
        elif frame_id == "event":
            window = self._first_non_empty(
                metadata.get("event_window"),
                metadata.get("acquisition_period"),
                metadata.get("flood_period"),
            )
            scene_count = metadata.get("event_scene_count")
        else:
            window = self._first_non_empty(
                metadata.get("selected_event_window"),
                metadata.get("event_window"),
                metadata.get("acquisition_period"),
            )
            scene_count = metadata.get("event_scene_count")

        return {
            "frame_id": frame_id,
            "source": runtime_provenance.get("source"),
            "source_dataset": runtime_provenance.get("source_dataset"),
            "method": runtime_provenance.get("method"),
            "window": window,
            "scene_count": scene_count,
            "updated_at": runtime_provenance.get("updated_at"),
        }

    def _build_temporal_frame_confidence(
        self,
        frame_id: str,
        metadata: dict[str, Any],
        runtime_confidence: dict[str, Any],
    ) -> dict[str, Any]:
        frame_label = self._first_non_empty(
            metadata.get(f"{frame_id}_confidence"), runtime_confidence.get("label")
        )
        if isinstance(frame_label, str):
            frame_label = frame_label.upper()

        frame_score = self._first_non_empty(
            metadata.get(f"{frame_id}_confidence_score"), runtime_confidence.get("score")
        )

        confidence = dict(runtime_confidence)
        confidence.update(
            {
                "frame_id": frame_id,
                "label": frame_label,
                "score": frame_score,
            }
        )
        return confidence

    def _build_tile_source_descriptor(self, layer_id: str) -> dict[str, Any]:
        return {
            "scheme": "xyz",
            "format": "png",
            "url_template": f"/api/earth-engine/tiles/offline/{layer_id}/{{z}}/{{x}}/{{y}}.png",
            "min_zoom": 0,
            "max_zoom": 18,
            "status": "placeholder",
            "available": False,
            "fallback_type": "geojson_overlay",
            "fallback_path": str(self._geojson_path),
            "reason": (
                "Live Earth Engine tile generation is not configured in this runtime. "
                "Descriptor is deterministic for consumer integration."
            ),
        }

    def _build_live_tile_source_descriptor(
        self,
        *,
        tile_handle: str,
        task_id: str,
        layer_id: str,
    ) -> dict[str, Any]:
        return {
            "scheme": "xyz",
            "format": "png",
            "url_template": f"/api/earth-engine/tiles/live/{tile_handle}/{{z}}/{{x}}/{{y}}.png",
            "min_zoom": 0,
            "max_zoom": 18,
            "status": "live",
            "available": True,
            "task_id": task_id,
            "layer_id": layer_id,
            "tile_handle": tile_handle,
            "cache_control": _LIVE_TILE_TASK_CACHE_CONTROL,
            "source": "earth_engine_mapid_proxy",
        }

    def _extract_geojson_geometry(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload_type = payload.get("type")
        if payload_type == "FeatureCollection":
            features = payload.get("features")
            if isinstance(features, list) and features:
                first = features[0]
                if isinstance(first, dict) and isinstance(first.get("geometry"), dict):
                    return first["geometry"]
            raise ValueError("FeatureCollection does not include a usable geometry.")
        if payload_type == "Feature":
            geometry = payload.get("geometry")
            if isinstance(geometry, dict):
                return geometry
            raise ValueError("Feature payload does not include a usable geometry.")
        if isinstance(payload.get("coordinates"), list):
            return payload
        raise ValueError("GeoJSON payload does not include coordinates.")

    def _build_live_runtime_tile_handles(
        self,
        *,
        task_id: str,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        if not self._is_live_tile_runtime_enabled():
            return {}, {
                "status": "disabled",
                "enabled": False,
                "reason": "env_flag_off",
                "available_layer_count": 0,
                "layer_ids": [],
            }

        ee = self._resolve_earth_engine_module()
        flood_geojson = self.get_flood_extent_geojson()
        geometry_payload = self._extract_geojson_geometry(flood_geojson)
        analysis_geometry = ee.Geometry(geometry_payload)

        baseline_window = self._first_non_empty(
            metadata.get("baseline_window"), metadata.get("baseline_period")
        )
        event_window = self._first_non_empty(
            metadata.get("event_window"),
            metadata.get("acquisition_period"),
            metadata.get("flood_period"),
        )
        baseline_range = self._parse_window_range(baseline_window)
        event_range = self._parse_window_range(event_window)
        baseline_start = baseline_range.get("start_timestamp") or "2025-06-01T00:00:00Z"
        baseline_end = baseline_range.get("end_timestamp") or "2025-10-01T00:00:00Z"
        event_start = event_range.get("start_timestamp") or "2025-11-01T00:00:00Z"
        event_end = event_range.get("end_timestamp") or "2025-12-01T00:00:00Z"

        sentinel1 = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(analysis_geometry)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .select("VV")
        )
        baseline_collection = sentinel1.filterDate(baseline_start, baseline_end)
        event_collection = sentinel1.filterDate(event_start, event_end)

        baseline_image = ee.Image(
            ee.Algorithms.If(
                baseline_collection.size().gt(0),
                baseline_collection.median(),
                ee.Image.constant(-19),
            )
        ).rename("VV")
        event_image = ee.Image(
            ee.Algorithms.If(
                event_collection.size().gt(0),
                event_collection.median(),
                ee.Image.constant(-17),
            )
        ).rename("VV")

        baseline_image = baseline_image.clip(analysis_geometry)
        event_image = event_image.clip(analysis_geometry)
        change_image = event_image.subtract(baseline_image).rename("change").clip(
            analysis_geometry
        )
        fused_image = (
            change_image.multiply(-1)
            .unitScale(0, 6)
            .clamp(0, 1)
            .rename("fused")
            .clip(analysis_geometry)
        )
        flood_mask_image = (
            change_image.lt(-1.5).selfMask().rename("flood_likelihood").clip(analysis_geometry)
        )

        layer_images: dict[str, tuple[Any, dict[str, Any]]] = {
            "ee_baseline_backscatter": (
                baseline_image,
                {
                    "min": -25,
                    "max": -5,
                    "palette": ["001219", "005f73", "0a9396", "94d2bd", "e9d8a6"],
                },
            ),
            "ee_event_backscatter": (
                event_image,
                {
                    "min": -25,
                    "max": -5,
                    "palette": ["001219", "005f73", "0a9396", "94d2bd", "e9d8a6"],
                },
            ),
            "ee_change_detection": (
                change_image,
                {
                    "min": -5,
                    "max": 5,
                    "palette": ["313695", "4575b4", "abd9e9", "ffffbf", "fdae61", "d73027"],
                },
            ),
            "ee_multisensor_fusion": (
                fused_image,
                {
                    "min": 0,
                    "max": 1,
                    "palette": ["0b1d51", "0057e7", "00b4d8", "90e0ef", "f1fa8c"],
                },
            ),
            "ee_fused_flood_likelihood": (
                flood_mask_image,
                {
                    "min": 0,
                    "max": 1,
                    "palette": ["1b1f3b", "26547c", "3fa7d6", "8ac926", "ffca3a", "ff595e"],
                },
            ),
        }

        live_handles: dict[str, str] = {}
        generation_errors: list[dict[str, str]] = []
        for layer_id, (image, vis) in layer_images.items():
            try:
                map_id = image.getMapId(vis)
                tile_fetcher = map_id.get("tile_fetcher")
                remote_url_template = (
                    getattr(tile_fetcher, "url_format", None) if tile_fetcher else None
                )
                if not isinstance(remote_url_template, str) or "{z}" not in remote_url_template:
                    generation_errors.append(
                        {
                            "layer_id": layer_id,
                            "error": "missing_tile_url_template",
                        }
                    )
                    continue
                live_handles[layer_id] = self._register_live_tile_template(
                    task_id=task_id,
                    layer_id=layer_id,
                    remote_url_template=remote_url_template,
                )
            except Exception as exc:
                generation_errors.append(
                    {
                        "layer_id": layer_id,
                        "error": str(exc),
                    }
                )

        status = "live" if live_handles else "fallback"
        reason = None if live_handles else "no_live_layers_generated"
        return live_handles, {
            "status": status,
            "enabled": True,
            "reason": reason,
            "available_layer_count": len(live_handles),
            "layer_ids": sorted(live_handles.keys()),
            "errors": generation_errors,
        }

    def _build_live_analysis_runtime_payload(
        self,
        *,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        runtime_payload = self.get_flood_extent_runtime_payload()
        metadata = (
            runtime_payload.get("metadata")
            if isinstance(runtime_payload.get("metadata"), dict)
            else {}
        )

        runtime_product = self._stabilize_runtime_flood_product(
            runtime_payload.get("ee_runtime"), metadata=metadata
        )
        live_handles, live_tile_status = self._build_live_runtime_tile_handles(
            task_id=task_id, metadata=metadata
        )
        if live_handles:
            runtime_product["layers"] = self.get_runtime_layer_descriptors(
                live_tile_handles=live_handles,
                tile_status="live",
                task_id=task_id,
            )
            runtime_product["runtime_mode"] = "live_earth_engine_tiles"
            runtime_product["status"] = "live"
            runtime_product["error"] = None
        else:
            runtime_product["runtime_mode"] = "fallback_descriptor"
            runtime_product["status"] = "fallback"
        runtime_product["task_id"] = task_id
        runtime_product["task_request"] = deepcopy(request)
        runtime_product["live_tile_status"] = live_tile_status
        runtime_product = self._stabilize_runtime_flood_product(
            runtime_product, metadata=metadata
        )

        runtime_payload["ee_runtime"] = runtime_product
        runtime_payload = self._sync_runtime_payload_runtime_contract(
            runtime_payload,
            metadata=metadata,
        )
        runtime_payload["live_tile_status"] = live_tile_status
        runtime_payload["task_id"] = task_id
        return runtime_payload

    def _load_geojson(self) -> dict:
        if self._cached_geojson is None:
            if not self._geojson_path.exists():
                raise FileNotFoundError(
                    f"Flood extent GeoJSON not found at {self._geojson_path}. "
                    "Run data/compute_flood_extent.py first, or create a "
                    "synthetic placeholder."
                )
            with open(self._geojson_path) as f:
                self._cached_geojson = json.load(f)
        return self._cached_geojson

    def _load_metadata(self) -> dict:
        if self._cached_metadata is not None:
            return self._cached_metadata

        if self._metadata_path.exists():
            with open(self._metadata_path) as f:
                self._cached_metadata = json.load(f)
            return self._cached_metadata

        geojson = self._load_geojson()
        props = geojson.get("properties", {})
        self._cached_metadata = {
            "source": props.get("source", "earth-engine"),
            "source_dataset": props.get("source_dataset", "COPERNICUS/S1_GRD"),
            "baseline_window": props.get("baseline_period"),
            "event_window": props.get("flood_period"),
            "method": props.get("method", "SAR change detection"),
            "threshold_db": props.get("threshold_db"),
            "confidence": props.get("confidence", "MEDIUM"),
            "generated_from": str(self._geojson_path.name),
            "project_id": self.project_id,
        }
        return self._cached_metadata

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_flood_extent_geojson(self) -> dict:
        return self._load_geojson()

    def get_flood_extent_metadata(self) -> dict:
        metadata = dict(self._load_metadata())
        metadata.setdefault("project_id", self.project_id)
        metadata.setdefault("generated_from", self._geojson_path.name)
        metadata.setdefault("sidecar_path", str(self._metadata_path))
        return metadata

    def get_runtime_temporal_frames(self) -> dict[str, dict[str, Any]]:
        metadata = self.get_flood_extent_metadata()
        baseline_window = self._first_non_empty(
            metadata.get("baseline_window"), metadata.get("baseline_period")
        )
        event_window = self._first_non_empty(
            metadata.get("event_window"),
            metadata.get("acquisition_period"),
            metadata.get("flood_period"),
        )
        updated_at = self._first_non_empty(
            metadata.get("generated_at"),
            metadata.get("computed_at"),
            metadata.get("updated_at"),
        )
        runtime_provenance = self._build_runtime_provenance(metadata)
        runtime_confidence = self._build_runtime_confidence(metadata)

        baseline_range = self._parse_window_range(baseline_window)
        event_range = self._parse_window_range(event_window)

        baseline_frame = {
            "id": "baseline",
            "frame_id": "baseline",
            "index": 0,
            "name": "Baseline",
            "window": baseline_range["window"],
            "start_timestamp": baseline_range["start_timestamp"],
            "end_timestamp": baseline_range["end_timestamp"],
            "timestamp": baseline_range["end_timestamp"] or baseline_range["start_timestamp"],
            "provenance": self._build_temporal_frame_provenance(
                "baseline", metadata, runtime_provenance
            ),
            "confidence": self._build_temporal_frame_confidence(
                "baseline", metadata, runtime_confidence
            ),
        }
        event_frame = {
            "id": "event",
            "frame_id": "event",
            "index": 1,
            "name": "Event",
            "window": event_range["window"],
            "start_timestamp": event_range["start_timestamp"],
            "end_timestamp": event_range["end_timestamp"],
            "timestamp": event_range["end_timestamp"] or event_range["start_timestamp"],
            "provenance": self._build_temporal_frame_provenance(
                "event", metadata, runtime_provenance
            ),
            "confidence": self._build_temporal_frame_confidence(
                "event", metadata, runtime_confidence
            ),
        }
        change_frame = {
            "id": "change",
            "frame_id": "change",
            "index": 2,
            "name": "Change",
            "window": None,
            "start_timestamp": baseline_frame["start_timestamp"],
            "end_timestamp": event_frame["end_timestamp"],
            "timestamp": (
                self._normalize_timestamp(updated_at)
                or event_frame["timestamp"]
                or baseline_frame["timestamp"]
            ),
            "derived_from": ["baseline", "event"],
            "provenance": self._build_temporal_frame_provenance(
                "change", metadata, runtime_provenance
            ),
            "confidence": self._build_temporal_frame_confidence(
                "change", metadata, runtime_confidence
            ),
        }
        return {
            "baseline": baseline_frame,
            "event": event_frame,
            "change": change_frame,
        }

    def get_runtime_temporal_playback(self) -> dict[str, Any]:
        metadata = self.get_flood_extent_metadata()
        runtime_provenance = self._build_runtime_provenance(metadata)
        runtime_confidence = self._build_runtime_confidence(metadata)
        temporal_frames = self.get_runtime_temporal_frames()
        ordered_frame_ids = [
            frame_id
            for frame_id in ("baseline", "event", "change")
            if temporal_frames.get(frame_id)
        ]

        playback_frames: list[dict[str, Any]] = []
        for frame_id in ordered_frame_ids:
            frame = temporal_frames.get(frame_id, {})
            playback_frames.append(
                {
                    "id": frame.get("id", frame_id),
                    "frame_id": frame.get("frame_id", frame_id),
                    "name": frame.get("name", frame_id.title()),
                    "index": frame.get("index"),
                    "window": frame.get("window"),
                    "start_timestamp": frame.get("start_timestamp"),
                    "end_timestamp": frame.get("end_timestamp"),
                    "timestamp": frame.get("timestamp"),
                }
            )

        progression_frames: list[dict[str, Any]] = []
        raw_candidates = metadata.get("event_window_candidates")
        if isinstance(raw_candidates, list):
            for i, candidate_window in enumerate(raw_candidates):
                if not isinstance(candidate_window, str):
                    continue
                candidate_range = self._parse_window_range(candidate_window)
                progression_frames.append(
                    {
                        "id": f"progression_{i + 1}",
                        "frame_id": f"progression_{i + 1}",
                        "name": f"Progression {i + 1}",
                        "index": len(playback_frames) + i,
                        "window": candidate_range["window"],
                        "start_timestamp": candidate_range["start_timestamp"],
                        "end_timestamp": candidate_range["end_timestamp"],
                        "timestamp": candidate_range["end_timestamp"]
                        or candidate_range["start_timestamp"],
                        "provenance": self._build_temporal_frame_provenance(
                            "event", metadata, runtime_provenance
                        ),
                        "confidence": self._build_temporal_frame_confidence(
                            "event", metadata, runtime_confidence
                        ),
                    }
                )

        default_frame_id = "change" if "change" in temporal_frames else None
        if default_frame_id is None and ordered_frame_ids:
            default_frame_id = ordered_frame_ids[-1]

        return {
            "ordered_frame_ids": ordered_frame_ids,
            "default_frame_id": default_frame_id,
            "frames": playback_frames,
            "progression_frames": progression_frames,
        }

    def get_runtime_temporal_summary(self) -> dict[str, Any]:
        temporal_frames = self.get_runtime_temporal_frames()
        playback = self.get_runtime_temporal_playback()
        ordered_frame_ids = playback.get("ordered_frame_ids", [])

        first_frame_id = ordered_frame_ids[0] if ordered_frame_ids else None
        latest_frame_id = playback.get("default_frame_id")

        first_frame = temporal_frames.get(first_frame_id, {}) if first_frame_id else {}
        latest_frame = temporal_frames.get(latest_frame_id, {}) if latest_frame_id else {}

        return {
            "frame_count": len(ordered_frame_ids),
            "frame_ids": ordered_frame_ids,
            "default_frame_id": latest_frame_id,
            "start_timestamp": first_frame.get("start_timestamp"),
            "end_timestamp": latest_frame.get("end_timestamp")
            or latest_frame.get("timestamp"),
            "latest_frame_id": latest_frame.get("frame_id"),
            "latest_frame_timestamp": latest_frame.get("timestamp"),
            "progression_frame_count": len(playback.get("progression_frames", [])),
        }

    def get_runtime_multisensor_fusion(self) -> dict[str, Any]:
        metadata = self.get_flood_extent_metadata()
        runtime_provenance = self._build_runtime_provenance(metadata)
        baseline_window = self._first_non_empty(
            metadata.get("baseline_window"), metadata.get("baseline_period")
        )
        event_window = self._first_non_empty(
            metadata.get("event_window"),
            metadata.get("acquisition_period"),
            metadata.get("flood_period"),
        )
        global_confidence = self._normalize_confidence_label(
            metadata.get("confidence"), default="MEDIUM"
        )

        baseline_window_hours = self._compute_window_duration_hours(baseline_window)
        event_window_hours = self._compute_window_duration_hours(event_window)

        sentinel1_threshold_db = self._coerce_numeric(metadata.get("threshold_db"))
        sentinel1_change_metric_db = self._coerce_numeric(metadata.get("sar_change_db"))
        sentinel1_baseline_scene_count = self._coerce_numeric(metadata.get("baseline_scene_count"))
        sentinel1_event_scene_count = self._coerce_numeric(metadata.get("event_scene_count"))
        sentinel1_scene_pair_count = (
            min(sentinel1_baseline_scene_count, sentinel1_event_scene_count)
            if sentinel1_baseline_scene_count is not None
            and sentinel1_event_scene_count is not None
            else None
        )
        sentinel1_scene_balance_ratio = (
            round(
                sentinel1_scene_pair_count
                / max(sentinel1_baseline_scene_count, sentinel1_event_scene_count),
                3,
            )
            if sentinel1_scene_pair_count is not None
            and max(sentinel1_baseline_scene_count, sentinel1_event_scene_count) > 0
            else None
        )
        sentinel1_change_to_threshold_ratio = (
            round(
                abs(sentinel1_change_metric_db) / abs(sentinel1_threshold_db),
                3,
            )
            if sentinel1_change_metric_db is not None
            and sentinel1_threshold_db is not None
            and sentinel1_threshold_db != 0
            else None
        )
        sentinel1_change_detected = (
            sentinel1_change_to_threshold_ratio >= 1.0
            if sentinel1_change_to_threshold_ratio is not None
            else None
        )
        sentinel1_scene_support_score = (
            self._clamp_score(sentinel1_scene_pair_count / 6.0)
            if sentinel1_scene_pair_count is not None
            else None
        )
        sentinel1_change_intensity_score = (
            self._clamp_score(sentinel1_change_to_threshold_ratio)
            if sentinel1_change_to_threshold_ratio is not None
            else None
        )
        sentinel1_runtime_signal_score = self._average_scores(
            sentinel1_scene_support_score,
            sentinel1_change_intensity_score,
        )
        sentinel1_metrics = {
            "baseline_window": baseline_window,
            "event_window": event_window,
            "threshold_db": metadata.get("threshold_db"),
            "change_metric_db": metadata.get("sar_change_db"),
            "baseline_scene_count": metadata.get("baseline_scene_count"),
            "event_scene_count": metadata.get("event_scene_count"),
            "baseline_window_hours": baseline_window_hours,
            "event_window_hours": event_window_hours,
            "scene_pair_count": sentinel1_scene_pair_count,
            "scene_balance_ratio": sentinel1_scene_balance_ratio,
            "change_to_threshold_ratio": sentinel1_change_to_threshold_ratio,
            "change_detected": sentinel1_change_detected,
            "runtime_signal_score": sentinel1_runtime_signal_score,
        }

        sentinel2_ndwi_mean = self._coerce_numeric(metadata.get("sentinel2_ndwi_mean"))
        sentinel2_mndwi_mean = self._coerce_numeric(metadata.get("sentinel2_mndwi_mean"))
        sentinel2_cloud_cover_pct = self._coerce_numeric(metadata.get("sentinel2_cloud_cover_pct"))
        sentinel2_water_index_mean = self._average_scores(
            sentinel2_ndwi_mean,
            sentinel2_mndwi_mean,
        )
        sentinel2_water_presence_score = (
            self._clamp_score((sentinel2_water_index_mean + 1.0) / 2.0)
            if sentinel2_water_index_mean is not None
            else None
        )
        sentinel2_cloud_free_fraction = (
            self._clamp_score(1.0 - (sentinel2_cloud_cover_pct / 100.0))
            if sentinel2_cloud_cover_pct is not None
            else None
        )
        sentinel2_optical_support_score = self._average_scores(
            sentinel2_water_presence_score,
            sentinel2_cloud_free_fraction,
        )
        sentinel2_metrics = {
            "event_window": self._first_non_empty(metadata.get("sentinel2_window"), event_window),
            "ndwi_mean": sentinel2_ndwi_mean,
            "mndwi_mean": sentinel2_mndwi_mean,
            "cloud_cover_pct": sentinel2_cloud_cover_pct,
            "water_index_mean": sentinel2_water_index_mean,
            "cloud_free_fraction": sentinel2_cloud_free_fraction,
            "water_presence_score": sentinel2_water_presence_score,
            "runtime_signal_score": sentinel2_optical_support_score,
        }

        dem_mean_elevation_m = self._coerce_numeric(metadata.get("dem_mean_elevation_m"))
        dem_min_elevation_m = self._coerce_numeric(metadata.get("dem_min_elevation_m"))
        dem_max_elevation_m = self._coerce_numeric(metadata.get("dem_max_elevation_m"))
        dem_mean_slope_deg = self._coerce_numeric(metadata.get("dem_mean_slope_deg"))
        dem_elevation_range_m = (
            round(dem_max_elevation_m - dem_min_elevation_m, 3)
            if dem_min_elevation_m is not None and dem_max_elevation_m is not None
            else None
        )
        dem_lowland_score = (
            self._clamp_score((15.0 - dem_mean_elevation_m) / 15.0)
            if dem_mean_elevation_m is not None
            else None
        )
        dem_slope_score = (
            self._clamp_score((5.0 - dem_mean_slope_deg) / 5.0)
            if dem_mean_slope_deg is not None
            else None
        )
        dem_runtime_signal_score = self._average_scores(dem_lowland_score, dem_slope_score)
        dem_metrics = {
            "mean_elevation_m": dem_mean_elevation_m,
            "min_elevation_m": dem_min_elevation_m,
            "max_elevation_m": dem_max_elevation_m,
            "mean_slope_deg": dem_mean_slope_deg,
            "elevation_range_m": dem_elevation_range_m,
            "lowland_score": dem_lowland_score,
            "slope_susceptibility_score": dem_slope_score,
            "runtime_signal_score": dem_runtime_signal_score,
        }

        rainfall_24h_mm = self._coerce_numeric(metadata.get("rainfall_24h_mm"))
        rainfall_72h_mm = self._coerce_numeric(metadata.get("rainfall_72h_mm"))
        rainfall_anomaly_pct = self._coerce_numeric(metadata.get("rainfall_anomaly_pct"))
        rainfall_short_burst_ratio = (
            round(rainfall_24h_mm / rainfall_72h_mm, 3)
            if rainfall_24h_mm is not None
            and rainfall_72h_mm is not None
            and rainfall_72h_mm > 0
            else None
        )
        rainfall_intensity_score = (
            self._clamp_score(rainfall_72h_mm / 150.0) if rainfall_72h_mm is not None else None
        )
        rainfall_anomaly_score = (
            self._clamp_score((rainfall_anomaly_pct + 100.0) / 200.0)
            if rainfall_anomaly_pct is not None
            else None
        )
        rainfall_short_burst_score = (
            self._clamp_score(rainfall_short_burst_ratio)
            if rainfall_short_burst_ratio is not None
            else None
        )
        rainfall_runtime_signal_score = self._average_scores(
            rainfall_intensity_score,
            rainfall_anomaly_score,
            rainfall_short_burst_score,
        )
        rainfall_metrics = {
            "measurement_window": self._first_non_empty(
                metadata.get("rainfall_window"), event_window
            ),
            "accumulation_24h_mm": rainfall_24h_mm,
            "accumulation_72h_mm": rainfall_72h_mm,
            "anomaly_pct": rainfall_anomaly_pct,
            "short_burst_ratio": rainfall_short_burst_ratio,
            "intensity_score": rainfall_intensity_score,
            "anomaly_score": rainfall_anomaly_score,
            "runtime_signal_score": rainfall_runtime_signal_score,
        }

        groundsource_historical_event_count = self._coerce_numeric(
            metadata.get("groundsource_event_count")
        )
        groundsource_recent_incident_count = self._coerce_numeric(metadata.get("incident_count_recent"))
        groundsource_incident_severity_index = self._coerce_numeric(
            metadata.get("incident_severity_index")
        )
        groundsource_incident_pressure_ratio = (
            round(
                groundsource_recent_incident_count
                / max(groundsource_historical_event_count, 1.0),
                3,
            )
            if groundsource_recent_incident_count is not None
            and groundsource_historical_event_count is not None
            else None
        )
        groundsource_historical_score = (
            self._clamp_score(groundsource_historical_event_count / 100.0)
            if groundsource_historical_event_count is not None
            else None
        )
        groundsource_recent_score = (
            self._clamp_score(groundsource_recent_incident_count / 10.0)
            if groundsource_recent_incident_count is not None
            else None
        )
        groundsource_severity_score = self._clamp_score(groundsource_incident_severity_index)
        groundsource_pressure_score = self._clamp_score(groundsource_incident_pressure_ratio)
        groundsource_runtime_signal_score = self._average_scores(
            groundsource_historical_score,
            groundsource_recent_score,
            groundsource_severity_score,
            groundsource_pressure_score,
        )
        groundsource_metrics = {
            "query_window": self._first_non_empty(
                metadata.get("groundsource_window"), metadata.get("incident_window"), event_window
            ),
            "historical_event_count": metadata.get("groundsource_event_count"),
            "recent_incident_count": metadata.get("incident_count_recent"),
            "incident_severity_index": groundsource_incident_severity_index,
            "latest_incident_timestamp": self._first_non_empty(
                metadata.get("incident_last_timestamp"), metadata.get("incident_last_updated")
            ),
            "incident_pressure_ratio": groundsource_incident_pressure_ratio,
            "historical_signal_score": groundsource_historical_score,
            "recent_signal_score": groundsource_recent_score,
            "severity_signal_score": groundsource_severity_score,
            "runtime_signal_score": groundsource_runtime_signal_score,
        }

        signals = {
            "sentinel1": {
                "signal_id": "sentinel1",
                "name": "Sentinel-1 SAR change/backscatter",
                "source_dataset": self._first_non_empty(
                    metadata.get("sentinel1_dataset"), runtime_provenance.get("source_dataset")
                ),
                "contribution_weight": _FUSION_SIGNAL_WEIGHTS["sentinel1"],
                "contribution_role": "primary_flood_extent_detection",
                "metrics": sentinel1_metrics,
                "quality": self._build_signal_quality(
                    sentinel1_metrics,
                    ["threshold_db", "change_metric_db", "baseline_scene_count", "event_scene_count"],
                    "Sentinel-1 runtime signal is partial until live SAR differencing is connected.",
                    derived_metric_keys=[
                        "baseline_window_hours",
                        "event_window_hours",
                        "scene_pair_count",
                        "scene_balance_ratio",
                        "change_to_threshold_ratio",
                        "runtime_signal_score",
                    ],
                ),
                "confidence": self._build_signal_confidence(
                    metadata,
                    label_keys=[
                        "sentinel1_confidence",
                        "sar_confidence",
                        "change_confidence",
                        "confidence",
                    ],
                    score_keys=[
                        "sentinel1_confidence_score",
                        "sar_confidence_score",
                        "change_confidence_score",
                        "confidence_score",
                    ],
                    default_label=global_confidence,
                ),
                "runtime_signal": {
                    "score": sentinel1_runtime_signal_score,
                    "score_source": (
                        "runtime_derived_metrics"
                        if sentinel1_runtime_signal_score is not None
                        else "unavailable"
                    ),
                    "derived_metric_keys": [
                        "scene_pair_count",
                        "scene_balance_ratio",
                        "change_to_threshold_ratio",
                    ],
                },
            },
            "sentinel2": {
                "signal_id": "sentinel2",
                "name": "Sentinel-2 optical water indices",
                "source_dataset": self._first_non_empty(
                    metadata.get("sentinel2_dataset"), "COPERNICUS/S2_SR_HARMONIZED"
                ),
                "contribution_weight": _FUSION_SIGNAL_WEIGHTS["sentinel2"],
                "contribution_role": "optical_water_index_context",
                "metrics": sentinel2_metrics,
                "quality": self._build_signal_quality(
                    sentinel2_metrics,
                    ["ndwi_mean", "mndwi_mean", "cloud_cover_pct"],
                    "Sentinel-2 optical support is derived from metadata and cloud coverage heuristics.",
                    derived_metric_keys=[
                        "water_index_mean",
                        "cloud_free_fraction",
                        "water_presence_score",
                        "runtime_signal_score",
                    ],
                ),
                "confidence": self._build_signal_confidence(
                    metadata,
                    label_keys=[
                        "sentinel2_confidence",
                        "optical_confidence",
                        "confidence",
                    ],
                    score_keys=[
                        "sentinel2_confidence_score",
                        "optical_confidence_score",
                        "confidence_score",
                    ],
                    default_label="LOW",
                ),
                "runtime_signal": {
                    "score": sentinel2_optical_support_score,
                    "score_source": (
                        "runtime_derived_metrics"
                        if sentinel2_optical_support_score is not None
                        else "unavailable"
                    ),
                    "derived_metric_keys": [
                        "water_index_mean",
                        "cloud_free_fraction",
                        "water_presence_score",
                    ],
                },
            },
            "dem": {
                "signal_id": "dem",
                "name": "DEM/elevation context",
                "source_dataset": self._first_non_empty(
                    metadata.get("dem_dataset"), "USGS/SRTMGL1_003"
                ),
                "contribution_weight": _FUSION_SIGNAL_WEIGHTS["dem"],
                "contribution_role": "topographic_susceptibility",
                "metrics": dem_metrics,
                "quality": self._build_signal_quality(
                    dem_metrics,
                    ["mean_elevation_m", "min_elevation_m", "max_elevation_m", "mean_slope_deg"],
                    "DEM susceptibility signal is estimated from local elevation/slope proxies.",
                    derived_metric_keys=[
                        "elevation_range_m",
                        "lowland_score",
                        "slope_susceptibility_score",
                        "runtime_signal_score",
                    ],
                ),
                "confidence": self._build_signal_confidence(
                    metadata,
                    label_keys=["dem_confidence", "elevation_confidence", "confidence"],
                    score_keys=["dem_confidence_score", "elevation_confidence_score", "confidence_score"],
                    default_label="MEDIUM",
                ),
                "runtime_signal": {
                    "score": dem_runtime_signal_score,
                    "score_source": (
                        "runtime_derived_metrics"
                        if dem_runtime_signal_score is not None
                        else "unavailable"
                    ),
                    "derived_metric_keys": [
                        "elevation_range_m",
                        "lowland_score",
                        "slope_susceptibility_score",
                    ],
                },
            },
            "rainfall": {
                "signal_id": "rainfall",
                "name": "Rainfall forcing context",
                "source_dataset": self._first_non_empty(
                    metadata.get("rainfall_dataset"), "NASA/GPM_L3/IMERG_V06"
                ),
                "contribution_weight": _FUSION_SIGNAL_WEIGHTS["rainfall"],
                "contribution_role": "hydrometeorological_driver",
                "metrics": rainfall_metrics,
                "quality": self._build_signal_quality(
                    rainfall_metrics,
                    ["accumulation_24h_mm", "accumulation_72h_mm", "anomaly_pct"],
                    "Rainfall forcing is approximated with 24h/72h accumulation and anomaly priors.",
                    derived_metric_keys=[
                        "short_burst_ratio",
                        "intensity_score",
                        "anomaly_score",
                        "runtime_signal_score",
                    ],
                ),
                "confidence": self._build_signal_confidence(
                    metadata,
                    label_keys=["rainfall_confidence", "precipitation_confidence", "confidence"],
                    score_keys=[
                        "rainfall_confidence_score",
                        "precipitation_confidence_score",
                        "confidence_score",
                    ],
                    default_label="MEDIUM",
                ),
                "runtime_signal": {
                    "score": rainfall_runtime_signal_score,
                    "score_source": (
                        "runtime_derived_metrics"
                        if rainfall_runtime_signal_score is not None
                        else "unavailable"
                    ),
                    "derived_metric_keys": [
                        "short_burst_ratio",
                        "intensity_score",
                        "anomaly_score",
                    ],
                },
            },
            "groundsource_incident": {
                "signal_id": "groundsource_incident",
                "name": "Groundsource and incident signal",
                "source_dataset": self._first_non_empty(
                    metadata.get("groundsource_dataset"),
                    "hawkeye.groundsource_jakarta + firestore.incidents",
                ),
                "contribution_weight": _FUSION_SIGNAL_WEIGHTS["groundsource_incident"],
                "contribution_role": "historical_and_incident_prior",
                "metrics": groundsource_metrics,
                "quality": self._build_signal_quality(
                    groundsource_metrics,
                    ["historical_event_count", "recent_incident_count", "incident_severity_index"],
                    "Groundsource/incident prior is estimated from historical activity and recent incidents.",
                    derived_metric_keys=[
                        "incident_pressure_ratio",
                        "historical_signal_score",
                        "recent_signal_score",
                        "severity_signal_score",
                        "runtime_signal_score",
                    ],
                ),
                "confidence": self._build_signal_confidence(
                    metadata,
                    label_keys=[
                        "groundsource_confidence",
                        "incident_confidence",
                        "confidence",
                    ],
                    score_keys=[
                        "groundsource_confidence_score",
                        "incident_confidence_score",
                        "confidence_score",
                    ],
                    default_label="MEDIUM",
                ),
                "runtime_signal": {
                    "score": groundsource_runtime_signal_score,
                    "score_source": (
                        "runtime_derived_metrics"
                        if groundsource_runtime_signal_score is not None
                        else "unavailable"
                    ),
                    "derived_metric_keys": [
                        "incident_pressure_ratio",
                        "historical_signal_score",
                        "recent_signal_score",
                        "severity_signal_score",
                    ],
                },
            },
        }

        component_layer_mapping = {
            "sentinel1": "ee_change_detection",
            "sentinel2": "ee_sentinel2_water_indices",
            "dem": "ee_dem_context",
            "rainfall": "ee_rainfall_context",
            "groundsource_incident": "ee_groundsource_incident_context",
        }
        component_signal_mapping = {
            "sentinel1": "sentinel1",
            "sentinel2": "sentinel2",
            "dem": "dem",
            "rainfall": "rainfall",
            "incidents": "groundsource_incident",
        }
        source_components: list[dict[str, Any]] = []
        metadata_weighted_sum = 0.0
        metadata_weight_total = 0.0
        runtime_weighted_sum = 0.0
        runtime_weight_total = 0.0
        available_count = 0
        runtime_signal_count = 0
        runtime_component_scores: dict[str, float | None] = {}
        runtime_component_status: dict[str, str] = {}

        for signal_id, signal in signals.items():
            confidence = signal.get("confidence", {})
            confidence_score = self._coerce_numeric(confidence.get("score"))
            weight = self._coerce_numeric(signal.get("contribution_weight")) or 0.0
            quality = signal.get("quality", {})
            quality_status = quality.get("status")
            if quality_status in {"available", "partial", "derived"}:
                available_count += 1

            runtime_signal = signal.get("runtime_signal")
            runtime_score = (
                self._coerce_numeric(runtime_signal.get("score"))
                if isinstance(runtime_signal, dict)
                else None
            )
            runtime_component_scores[signal_id] = runtime_score
            runtime_component_status[signal_id] = (
                quality_status if isinstance(quality_status, str) else "placeholder"
            )

            if confidence_score is not None:
                metadata_weighted_sum += confidence_score * weight
                metadata_weight_total += weight
            if runtime_score is not None:
                runtime_signal_count += 1
                runtime_weighted_sum += runtime_score * weight
                runtime_weight_total += weight

            source_components.append(
                {
                    "signal_id": signal_id,
                    "layer_id": component_layer_mapping.get(signal_id),
                    "dataset": signal.get("source_dataset"),
                    "weight": weight,
                    "confidence_label": confidence.get("label"),
                    "confidence_score": confidence_score,
                    "quality_status": quality_status,
                    "availability_status": quality_status,
                    "coverage_ratio": quality.get("coverage_ratio"),
                    "available_metric_count": quality.get("available_metric_count"),
                    "tracked_metric_count": quality.get("tracked_metric_count"),
                    "derived_metric_count": quality.get("derived_metric_count"),
                    "runtime_signal_score": runtime_score,
                }
            )

        aggregate_score = (
            round(metadata_weighted_sum / metadata_weight_total, 3)
            if metadata_weight_total
            else None
        )
        runtime_aggregate_score = (
            round(runtime_weighted_sum / runtime_weight_total, 3)
            if runtime_weight_total
            else None
        )
        blended_score = self._average_scores(aggregate_score, runtime_aggregate_score)
        aggregate_confidence = {
            "label": self._score_to_label(aggregate_score),
            "score": aggregate_score,
            "source": "multisensor_fusion_scaffold",
            "method": "weighted_signal_confidence",
            "method_summary": (
                "Weighted fusion of metadata confidence with runtime-derived Sentinel-1, "
                "Sentinel-2, DEM, rainfall, and incident priors."
            ),
            "signal_count": len(signals),
            "available_signal_count": available_count,
            "coverage_ratio": round((available_count / len(signals)), 3) if signals else 0.0,
            "weight_sum": round(metadata_weight_total, 3),
            "runtime_score": runtime_aggregate_score,
            "runtime_label": self._score_to_label(runtime_aggregate_score),
            "runtime_signal_count": runtime_signal_count,
            "runtime_weight_sum": round(runtime_weight_total, 3),
            "blended_score": blended_score,
        }

        components: dict[str, dict[str, Any]] = {}
        component_availability: dict[str, dict[str, Any]] = {}
        for component_id, signal_id in component_signal_mapping.items():
            signal_payload = signals.get(signal_id, {})
            signal_confidence = signal_payload.get("confidence", {})
            signal_quality = signal_payload.get("quality", {})
            availability_status = signal_quality.get("status")
            component_block = {
                "source": signal_payload.get("source_dataset"),
                "signal": signal_payload.get("name"),
                "confidence_label": signal_confidence.get("label"),
                "availability_status": availability_status,
                "coverage_ratio": signal_quality.get("coverage_ratio"),
            }
            confidence_score = self._coerce_numeric(signal_confidence.get("score"))
            if confidence_score is not None:
                component_block["score"] = round(confidence_score, 3)
            runtime_signal_score = self._coerce_numeric(
                signal_payload.get("runtime_signal", {}).get("score")
                if isinstance(signal_payload.get("runtime_signal"), dict)
                else None
            )
            if runtime_signal_score is not None:
                component_block["runtime_score"] = round(runtime_signal_score, 3)
            quality_note = signal_quality.get("note")
            if quality_note:
                component_block["notes"] = quality_note
            components[component_id] = component_block
            component_availability[component_id] = {
                "signal_id": signal_id,
                "status": availability_status,
                "coverage_ratio": signal_quality.get("coverage_ratio"),
                "available_metric_count": signal_quality.get("available_metric_count"),
                "tracked_metric_count": signal_quality.get("tracked_metric_count"),
                "derived_metric_count": signal_quality.get("derived_metric_count"),
            }

        method_summary = {
            "name": "deterministic_multisensor_fusion_scaffold",
            "aggregation": "weighted_signal_confidence",
            "description": aggregate_confidence["method_summary"],
            "component_order": list(component_signal_mapping.keys()),
            "fused_layer_ids": list(_FUSION_RUNTIME_LAYER_IDS),
            "runtime_input_source": "runtime_derived_signal_metrics",
        }

        runtime_inputs = {
            "source": "runtime_derived_signal_metrics",
            "updated_at": runtime_provenance.get("updated_at"),
            "temporal_windows": {
                "baseline": baseline_window,
                "event": event_window,
            },
            "component_scores": runtime_component_scores,
            "component_status": runtime_component_status,
            "available_component_count": available_count,
            "tracked_component_count": len(signals),
        }

        return {
            "schema_version": "1.0",
            "status": "deterministic_scaffold",
            "fused_layer_id": _FUSION_PRIMARY_LAYER_ID,
            "fused_layer_ids": list(_FUSION_RUNTIME_LAYER_IDS),
            "primary_temporal_frame": "change",
            "source_components": source_components,
            "signals": signals,
            "components": components,
            "component_blocks": components,
            "component_availability": component_availability,
            "runtime_inputs": runtime_inputs,
            "aggregate_confidence": aggregate_confidence,
            "method_summary": method_summary,
        }

    def get_runtime_layer_descriptors(
        self,
        live_tile_handles: dict[str, str] | None = None,
        *,
        tile_status: str = "placeholder",
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        metadata = self.get_flood_extent_metadata()
        temporal_frames = self.get_runtime_temporal_frames()
        provenance = self._build_runtime_provenance(metadata)
        confidence = self._build_runtime_confidence(metadata)
        multisensor_fusion = self.get_runtime_multisensor_fusion()
        resolved_live_handles = (
            live_tile_handles
            if isinstance(live_tile_handles, dict)
            else {}
        )
        fused_layer_id = multisensor_fusion.get("fused_layer_id", _FUSION_PRIMARY_LAYER_ID)
        fused_layer_ids = [fused_layer_id]
        configured_fused_layer_ids = multisensor_fusion.get("fused_layer_ids", [])
        if isinstance(configured_fused_layer_ids, list):
            for layer_id in configured_fused_layer_ids:
                if (
                    isinstance(layer_id, str)
                    and layer_id
                    and layer_id not in fused_layer_ids
                ):
                    fused_layer_ids.append(layer_id)
        for layer_id in _FUSION_RUNTIME_LAYER_IDS:
            if layer_id not in fused_layer_ids:
                fused_layer_ids.append(layer_id)
        method_summary = multisensor_fusion.get("method_summary", {})

        layer_specs = [
            ("ee_baseline_backscatter", "EE Baseline Backscatter", "raster", "baseline"),
            ("ee_event_backscatter", "EE Event Backscatter", "raster", "event"),
            ("ee_change_detection", "EE Flood Change Detection", "raster", "change"),
        ]

        descriptors: list[dict[str, Any]] = []
        for layer_id, layer_name, layer_type, frame_id in layer_specs:
            frame = temporal_frames.get(frame_id, {})
            tile_handle = resolved_live_handles.get(layer_id)
            tile_source = (
                self._build_live_tile_source_descriptor(
                    tile_handle=tile_handle,
                    task_id=task_id or "",
                    layer_id=layer_id,
                )
                if isinstance(tile_handle, str) and tile_handle
                else self._build_tile_source_descriptor(layer_id)
            )
            descriptors.append(
                {
                    "id": layer_id,
                    "name": layer_name,
                    "type": layer_type,
                    "temporal_frame": frame_id,
                    "tile_source": tile_source,
                    "timestamps": {
                        "frame_timestamp": frame.get("timestamp"),
                        "updated_at": provenance.get("updated_at"),
                    },
                    "provenance": provenance,
                    "confidence": confidence,
                    "fused_into": fused_layer_id,
                }
            )
        for layer_id in fused_layer_ids:
            layer_name = (
                "EE Fused Flood Likelihood"
                if layer_id == "ee_fused_flood_likelihood"
                else "EE Multisensor Flood Fusion"
            )
            tile_handle = resolved_live_handles.get(layer_id)
            tile_source = (
                self._build_live_tile_source_descriptor(
                    tile_handle=tile_handle,
                    task_id=task_id or "",
                    layer_id=layer_id,
                )
                if isinstance(tile_handle, str) and tile_handle
                else self._build_tile_source_descriptor(layer_id)
            )
            descriptors.append(
                {
                    "id": layer_id,
                    "name": layer_name,
                    "type": "raster",
                    "temporal_frame": "change",
                    "tile_source": tile_source,
                    "timestamps": {
                        "frame_timestamp": temporal_frames.get("change", {}).get("timestamp"),
                        "updated_at": provenance.get("updated_at"),
                    },
                    "provenance": {
                        **provenance,
                        "method": "deterministic_multisensor_fusion",
                        "method_summary": method_summary.get("description"),
                    },
                    "confidence": multisensor_fusion.get("aggregate_confidence", confidence),
                    "fusion": {
                        "is_fused": True,
                        "fused_layer_id": fused_layer_id,
                        "source_components": multisensor_fusion.get("source_components", []),
                        "component_blocks": multisensor_fusion.get("components", {}),
                        "signal_ids": list(multisensor_fusion.get("signals", {}).keys()),
                    },
                }
            )
        if tile_status != "placeholder":
            for descriptor in descriptors:
                tile_source = descriptor.get("tile_source")
                if isinstance(tile_source, dict):
                    tile_source["status"] = tile_status
        return descriptors

    def get_runtime_flood_product(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            metadata = self.get_flood_extent_metadata()
            temporal_frames = self.get_runtime_temporal_frames()
            temporal_playback = self.get_runtime_temporal_playback()
            multisensor_fusion = self.get_runtime_multisensor_fusion()
            runtime_confidence = self._build_runtime_confidence(metadata)
            runtime_confidence["fusion_summary"] = multisensor_fusion.get(
                "aggregate_confidence"
            )
            return self._stabilize_runtime_flood_product(
                {
                    "runtime_mode": "fallback_descriptor",
                    "status": "fallback",
                    "layers": self.get_runtime_layer_descriptors(),
                    "temporal_frames": temporal_frames,
                    "temporal_playback": temporal_playback,
                    "temporal_summary": self.get_runtime_temporal_summary(),
                    "provenance": self._build_runtime_provenance(metadata),
                    "confidence": runtime_confidence,
                    "multisensor_fusion": multisensor_fusion,
                    "error": None,
                },
                metadata=metadata,
            )
        except Exception as exc:
            if not metadata:
                try:
                    metadata = self.get_flood_extent_metadata()
                except Exception:
                    metadata = {}
            return self._stabilize_runtime_flood_product(
                {
                    "runtime_mode": "error",
                    "status": "error",
                    "multisensor_fusion": self._build_default_multisensor_fusion(
                        status="error",
                        description="Multisensor fusion scaffold unavailable due to runtime error.",
                        confidence_source="runtime_error",
                        error_code="multisensor_fusion_unavailable",
                        error_message=str(exc),
                    ),
                    "error": {
                        "code": "ee_runtime_descriptor_unavailable",
                        "message": str(exc),
                    },
                },
                metadata=metadata,
            )

    def _sync_runtime_payload_runtime_contract(
        self,
        payload: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime_payload = dict(payload) if isinstance(payload, dict) else {}
        payload_metadata = runtime_payload.get("metadata")
        if not isinstance(payload_metadata, dict):
            payload_metadata = metadata if isinstance(metadata, dict) else {}
        runtime_product = self._stabilize_runtime_flood_product(
            runtime_payload.get("ee_runtime"), metadata=payload_metadata
        )
        runtime_payload["metadata"] = payload_metadata
        runtime_payload["ee_runtime"] = runtime_product
        runtime_payload["runtime_layers"] = runtime_product.get("layers", [])
        runtime_payload["temporal_frames"] = runtime_product.get("temporal_frames", {})
        runtime_payload["temporal_playback"] = runtime_product.get("temporal_playback", {})
        runtime_payload["temporal_summary"] = runtime_product.get("temporal_summary", {})
        runtime_payload["multisensor_fusion"] = runtime_product.get(
            "multisensor_fusion",
            self._build_default_multisensor_fusion(
                status="unavailable",
                description="Deterministic multisensor fusion scaffold is unavailable.",
                confidence_source="runtime_fallback",
            ),
        )
        return runtime_payload

    def get_flood_extent_runtime_payload(self) -> dict[str, Any]:
        try:
            metadata = self.get_flood_extent_metadata()
        except Exception:
            metadata = {}

        latest_status = self.get_latest_live_analysis_task_status()
        if isinstance(latest_status, dict) and latest_status.get("state") == "complete":
            task_id = latest_status.get("task_id")
            if isinstance(task_id, str) and task_id:
                latest_result = self.get_live_analysis_task_result(task_id)
                if isinstance(latest_result, dict):
                    runtime_payload = latest_result.get("runtime_payload", latest_result)
                    if (
                        isinstance(runtime_payload, dict)
                        and isinstance(runtime_payload.get("ee_runtime"), dict)
                    ):
                        return self._sync_runtime_payload_runtime_contract(
                            deepcopy(runtime_payload),
                            metadata=metadata,
                        )

        try:
            runtime_product_raw = self.get_runtime_flood_product()
        except Exception as exc:
            runtime_product_raw = {
                "runtime_mode": "error",
                "status": "error",
                "error": {
                    "code": "ee_runtime_descriptor_unavailable",
                    "message": str(exc),
                },
            }

        runtime_product = self._stabilize_runtime_flood_product(
            runtime_product_raw, metadata=metadata
        )

        if not metadata:
            runtime_provenance = runtime_product.get("provenance")
            metadata = runtime_provenance if isinstance(runtime_provenance, dict) else {}

        try:
            area_sqkm = float(self.get_flood_area_sqkm())
        except Exception:
            area_sqkm = 0.0

        try:
            growth_rate_pct = self.get_flood_growth_rate()
        except Exception:
            growth_rate_pct = {
                "rate_pct_per_hour": 0.0,
                "source": "runtime_error",
                "note": "Growth rate unavailable due to runtime error.",
            }
        if not isinstance(growth_rate_pct, dict):
            growth_rate_pct = {
                "rate_pct_per_hour": 0.0,
                "source": "runtime_error",
                "note": "Growth rate unavailable due to invalid runtime payload.",
            }

        payload = {
            "area_sqkm": round(area_sqkm, 2),
            "growth_rate_pct": growth_rate_pct,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "ee_runtime": runtime_product,
            "runtime_layers": runtime_product.get("layers", []),
            "temporal_frames": runtime_product.get("temporal_frames", {}),
            "temporal_playback": runtime_product.get("temporal_playback", {}),
            "temporal_summary": runtime_product.get("temporal_summary", {}),
            "multisensor_fusion": runtime_product.get("multisensor_fusion", {}),
        }
        return self._sync_runtime_payload_runtime_contract(payload, metadata=metadata)

    def get_flood_area_sqkm(self) -> float:
        geojson = self._load_geojson()
        geom = geojson.get("geometry", geojson)
        poly = shape(geom)

        project_to_meters = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:32748", always_xy=True  # UTM 48S covers Jakarta
        ).transform
        poly_m = transform(project_to_meters, poly)
        return round(poly_m.area / 1_000_000, 2)

    def get_flood_growth_rate(self) -> dict[str, Any]:
        return {
            "rate_pct_per_hour": 12.0,
            "source": "pre-computed",
            "note": "Replace with live EE computation when available.",
        }

    def get_population_at_risk(self, flood_geojson: str | dict | None = None) -> dict:
        if flood_geojson is not None:
            geom = (
                json.loads(flood_geojson)
                if isinstance(flood_geojson, str)
                else flood_geojson
            )
            geom = geom.get("geometry", geom)
        else:
            geom = self._load_geojson().get(
                "geometry", self._load_geojson()
            )

        poly = shape(geom)
        project_to_meters = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:32748", always_xy=True
        ).transform
        area_sqkm = transform(project_to_meters, poly).area / 1_000_000

        total = int(area_sqkm * _JAKARTA_POP_DENSITY_PER_SQKM)
        return {
            "total": total,
            "children_under_5": int(total * 0.085),
            "elderly_over_65": int(total * 0.057),
            "flood_area_sqkm": round(area_sqkm, 2),
            "density_used": _JAKARTA_POP_DENSITY_PER_SQKM,
        }
