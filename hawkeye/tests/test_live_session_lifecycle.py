"""Unit tests for Live API session lifecycle decisions."""

from __future__ import annotations

import pytest

pytest.importorskip("google.adk")

import app.main as main_module


class _FakeSession:
    def __init__(self, state: dict | None = None):
        self.state = state


class _FakeSessionService:
    def __init__(
        self,
        *,
        existing_session: _FakeSession | None = None,
        fail_get: bool = False,
    ) -> None:
        self._session = existing_session
        self._fail_get = fail_get
        self.calls: list[tuple] = []

    async def get_session(self, *, app_name: str, user_id: str, session_id: str):
        self.calls.append(("get", app_name, user_id, session_id))
        if self._fail_get:
            raise RuntimeError("lookup failed")
        return self._session

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str):
        self.calls.append(("delete", app_name, user_id, session_id))
        self._session = None

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict,
    ):
        self.calls.append(("create", app_name, user_id, session_id, dict(state)))
        self._session = _FakeSession(state=dict(state))
        return self._session


@pytest.mark.asyncio
async def test_prepare_live_session_reuses_existing_session_by_default() -> None:
    existing_session = _FakeSession(state={"operational_mode": "BRIEF"})
    service = _FakeSessionService(existing_session=existing_session)

    session, lifecycle = await main_module._prepare_live_session_with_service(
        service=service,
        user_id="user-1",
        session_id="session-1",
        strategy=main_module.LIVE_SESSION_STRATEGY_RESUME_PREFER,
    )

    assert session is existing_session
    assert lifecycle["decision"] == "reused_existing"
    assert lifecycle["reused_existing_session"] is True
    assert lifecycle["fallback_to_fresh"] is False
    assert session.state["operational_mode"] == "BRIEF"
    assert session.state["water_level_m"] == 0.0
    assert session.state["active_threats"] == []
    assert session.state["session_lifecycle"]["decision"] == "reused_existing"
    assert [call[0] for call in service.calls] == ["get"]


@pytest.mark.asyncio
async def test_prepare_live_session_forced_fresh_recreates_existing_session() -> None:
    existing_session = _FakeSession(
        state={"operational_mode": "BRIEF", "water_level_m": 2.8, "active_threats": ["x"]}
    )
    service = _FakeSessionService(existing_session=existing_session)

    session, lifecycle = await main_module._prepare_live_session_with_service(
        service=service,
        user_id="user-1",
        session_id="session-1",
        strategy=main_module.LIVE_SESSION_STRATEGY_FRESH,
    )

    assert session is not existing_session
    assert lifecycle["decision"] == "created_fresh"
    assert lifecycle["reason"] == "strategy_forced_fresh"
    assert lifecycle["fallback_to_fresh"] is True
    assert lifecycle["reused_existing_session"] is False
    assert session.state["operational_mode"] == "ALERT"
    assert session.state["water_level_m"] == 0.0
    assert session.state["active_threats"] == []
    assert [call[0] for call in service.calls] == ["get", "delete", "create"]


@pytest.mark.asyncio
async def test_prepare_live_session_falls_back_to_fresh_on_lookup_error() -> None:
    service = _FakeSessionService(fail_get=True)

    session, lifecycle = await main_module._prepare_live_session_with_service(
        service=service,
        user_id="user-1",
        session_id="session-1",
        strategy=main_module.LIVE_SESSION_STRATEGY_RESUME_PREFER,
    )

    assert isinstance(session, _FakeSession)
    assert lifecycle["decision"] == "created_fresh"
    assert lifecycle["reason"] == "resume_lookup_failed"
    assert lifecycle["fallback_to_fresh"] is True
    assert [call[0] for call in service.calls] == ["get", "create"]


def test_is_stale_live_session_error_classifier() -> None:
    stale_error = RuntimeError(
        "Gemini Live API 1007 invalid argument: session resumption handle expired"
    )
    unrelated_error = RuntimeError("tool timeout while fetching map tiles")

    assert main_module._is_stale_live_session_error(stale_error) is True
    assert main_module._is_stale_live_session_error(unrelated_error) is False
