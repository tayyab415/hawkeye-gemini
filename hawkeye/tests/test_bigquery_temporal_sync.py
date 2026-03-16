"""Unit tests for GroundsourceService EE temporal summary sync hook."""

from __future__ import annotations

from typing import Any

from app.services.bigquery_service import GroundsourceService


class _DummyBigQueryClient:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[dict]:
        self.insert_calls.append((table, rows))
        return []


def _sample_summary() -> dict[str, Any]:
    return {
        "project_id": "demo-project",
        "runtime_mode": "fallback_descriptor",
        "source": "earth-engine",
        "confidence_label": "MEDIUM",
        "area_sqkm": 1.56,
        "growth_rate_pct": {"rate_pct_per_hour": 12.0},
        "frame_count": 3,
        "frame_ids": ["baseline", "event", "change"],
        "latest_frame_id": "change",
        "latest_frame_timestamp": "2026-03-15T18:17:01Z",
        "start_timestamp": "2025-06-01T00:00:00Z",
        "end_timestamp": "2026-02-28T23:59:59Z",
        "updated_at": "2026-03-15T18:17:01Z",
    }


def test_sync_ee_temporal_summary_returns_dry_run_without_table(
    monkeypatch,
) -> None:
    monkeypatch.delenv("HAWKEYE_EE_TEMPORAL_SUMMARY_TABLE", raising=False)

    client = _DummyBigQueryClient()
    service = GroundsourceService(project_id="demo-project", client=client)
    result = service.sync_ee_temporal_summary(_sample_summary())

    assert result["status"] == "dry_run"
    assert result["mode"] == "local"
    assert result["record"]["growth_rate_pct_per_hour"] == 12.0
    assert result["record"]["frame_ids"] == ["baseline", "event", "change"]
    assert client.insert_calls == []


def test_sync_ee_temporal_summary_uses_forward_hook(monkeypatch) -> None:
    monkeypatch.delenv("HAWKEYE_EE_TEMPORAL_SUMMARY_TABLE", raising=False)

    captured: dict[str, Any] = {}

    def _hook(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = payload
        return {"accepted": True}

    service = GroundsourceService(
        project_id="demo-project",
        client=_DummyBigQueryClient(),
        ee_temporal_summary_hook=_hook,
    )
    result = service.sync_ee_temporal_summary(_sample_summary())

    assert result["status"] == "forwarded"
    assert result["mode"] == "hook"
    assert captured["payload"]["latest_frame_id"] == "change"
    assert captured["payload"]["growth_rate_pct_per_hour"] == 12.0


def test_sync_ee_temporal_summary_persists_when_table_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "HAWKEYE_EE_TEMPORAL_SUMMARY_TABLE", "hawkeye.ee_temporal_summary"
    )

    client = _DummyBigQueryClient()
    service = GroundsourceService(project_id="demo-project", client=client)
    result = service.sync_ee_temporal_summary(_sample_summary())

    assert result["status"] == "persisted"
    assert result["mode"] == "bigquery"
    assert result["table"] == "hawkeye.ee_temporal_summary"
    assert len(client.insert_calls) == 1
    table_name, rows = client.insert_calls[0]
    assert table_name == "hawkeye.ee_temporal_summary"
    assert rows[0]["frame_count"] == 3
    assert rows[0]["latest_frame_timestamp"] == "2026-03-15T18:17:01Z"
