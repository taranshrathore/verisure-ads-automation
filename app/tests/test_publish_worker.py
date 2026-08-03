"""Tests for the standalone async-publish worker (Phase 4).

Uses monkeypatch/fakes only. Never sleeps for real. Does not exercise
API routes, FastAPI, or HTTP clients.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

from app.orchestration import publish_worker


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_run_once_processes_one_queued_job(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            return True

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    assert publish_worker.run_once() is True


def test_run_once_returns_false_when_queue_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            return False

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    assert publish_worker.run_once() is False


def test_session_closed_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            return True

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    publish_worker.run_once()
    assert session.closed is True


def test_session_closed_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            raise RuntimeError("job failed")

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    with pytest.raises(RuntimeError, match="job failed"):
        publish_worker.run_once()
    assert session.closed is True


def test_worker_constructs_fresh_session_each_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_FakeSession] = []

    def _factory() -> Any:
        def _make() -> _FakeSession:
            session = _FakeSession()
            sessions.append(session)
            return session

        return _make

    monkeypatch.setattr(publish_worker, "SessionFactory", _factory)

    calls = {"n": 0}

    class _JobService:
        def run_once(self) -> bool:
            calls["n"] += 1
            # Two successful claims, then empty queue (triggers sleep).
            return calls["n"] < 3

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    def _sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(publish_worker.time, "sleep", _sleep)

    with pytest.raises(KeyboardInterrupt):
        publish_worker.run_forever()

    assert len(sessions) == 3
    assert all(session.closed for session in sessions)
    assert sessions[0] is not sessions[1]
    assert sessions[1] is not sessions[2]


def test_worker_never_imports_fastapi_or_router_modules() -> None:
    source = Path(publish_worker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.add(node.module)

    forbidden = {
        "fastapi",
        "httpx",
        "requests",
        "starlette",
        "app.main",
        "app.api",
        "app.api.dependencies",
        "app.api.v1",
        "app.api.v1.campaigns",
    }
    assert imported.isdisjoint(forbidden)


def test_worker_never_performs_http_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            return False

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    def _http_forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("worker must not perform HTTP calls")

    for module_name in ("urllib.request", "http.client"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "urlopen"):
            monkeypatch.setattr(module, "urlopen", _http_forbidden, raising=False)
        if module is not None and hasattr(module, "HTTPSConnection"):
            monkeypatch.setattr(
                module, "HTTPSConnection", _http_forbidden, raising=False
            )

    for name in ("httpx", "requests"):
        if name in sys.modules:
            mod = sys.modules[name]
            if hasattr(mod, "get"):
                monkeypatch.setattr(mod, "get", _http_forbidden, raising=False)
            if hasattr(mod, "request"):
                monkeypatch.setattr(mod, "request", _http_forbidden, raising=False)

    assert publish_worker.run_once() is False


def test_worker_continues_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_FakeSession] = []

    def _factory() -> Any:
        def _make() -> _FakeSession:
            session = _FakeSession()
            sessions.append(session)
            return session

        return _make

    monkeypatch.setattr(publish_worker, "SessionFactory", _factory)

    calls = {"n": 0}

    class _JobService:
        def run_once(self) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return False

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish_worker.time, "sleep", _sleep)
    monkeypatch.setattr(
        publish_worker.settings, "publish_job_poll_interval_seconds", 5, raising=False
    )

    with pytest.raises(KeyboardInterrupt):
        publish_worker.run_forever()

    assert calls["n"] == 2
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
    assert sleeps == [5, 5]


def test_dependency_graph_uses_publish_job_service_not_publish_campaign_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    built: dict[str, Any] = {}

    class _TrackingJobService:
        def run_once(self) -> bool:
            built["job_run_once"] = True
            return True

    def _build(_session: Any) -> _TrackingJobService:
        built["builder_called"] = True
        return _TrackingJobService()

    monkeypatch.setattr(publish_worker, "build_publish_job_service", _build)

    def _forbidden_publish(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "worker must invoke PublishJobService, not PublishCampaignService directly"
        )

    monkeypatch.setattr(
        publish_worker.PublishCampaignService,
        "publish_campaign",
        _forbidden_publish,
        raising=False,
    )

    assert publish_worker.run_once() is True
    assert built.get("builder_called") is True
    assert built.get("job_run_once") is True


def test_build_publish_job_service_returns_publish_job_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real constructor wiring returns PublishJobService (graph smoke check)."""
    from app.services.publish_job_service import PublishJobService

    session = object()

    class _Repo:
        def __init__(self, _session: Any) -> None:
            pass

    class _DeploymentService:
        def __init__(self, _repo: Any, _session: Any) -> None:
            pass

    class _ConnectionService:
        def __init__(self, _repo: Any, _encryption: Any, _session: Any) -> None:
            pass

    class _PublishCampaign:
        def __init__(self, *_args: Any) -> None:
            pass

    class _Encryption:
        def __init__(self, _key: Any) -> None:
            pass

    monkeypatch.setattr(publish_worker, "PublishJobRepository", _Repo)
    monkeypatch.setattr(publish_worker, "CampaignRepository", _Repo)
    monkeypatch.setattr(publish_worker, "CampaignDeploymentRepository", _Repo)
    monkeypatch.setattr(publish_worker, "ProviderConnectionRepository", _Repo)
    monkeypatch.setattr(publish_worker, "CredentialEncryptionService", _Encryption)
    monkeypatch.setattr(publish_worker, "CampaignDeploymentService", _DeploymentService)
    monkeypatch.setattr(publish_worker, "ProviderConnectionService", _ConnectionService)
    monkeypatch.setattr(publish_worker, "PublishCampaignService", _PublishCampaign)
    monkeypatch.setattr(publish_worker, "CampaignSpecBuilder", lambda: object())
    monkeypatch.setattr(publish_worker, "ProviderAdapterRegistry", lambda: object())

    service = publish_worker.build_publish_job_service(session)  # type: ignore[arg-type]
    assert isinstance(service, PublishJobService)


def test_session_closed_after_constructor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    def _boom(_session: Any) -> None:
        raise RuntimeError("graph wiring failed")

    monkeypatch.setattr(publish_worker, "build_publish_job_service", _boom)

    with pytest.raises(RuntimeError, match="graph wiring failed"):
        publish_worker.run_once()
    assert session.closed is True


def test_run_forever_continues_after_dependency_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_FakeSession] = []

    def _factory() -> Any:
        def _make() -> _FakeSession:
            session = _FakeSession()
            sessions.append(session)
            return session

        return _make

    monkeypatch.setattr(publish_worker, "SessionFactory", _factory)

    builds = {"n": 0}

    def _build(_session: Any) -> Any:
        builds["n"] += 1
        if builds["n"] == 1:
            raise RuntimeError("construct boom")

        class _JobService:
            def run_once(self) -> bool:
                return False

        return _JobService()

    monkeypatch.setattr(publish_worker, "build_publish_job_service", _build)

    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish_worker.time, "sleep", _sleep)
    monkeypatch.setattr(
        publish_worker.settings, "publish_job_poll_interval_seconds", 5, raising=False
    )

    with pytest.raises(KeyboardInterrupt):
        publish_worker.run_forever()

    assert builds["n"] == 2
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
    assert sleeps == [5, 5]


def test_keyboard_interrupt_from_run_once_stops_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    with pytest.raises(KeyboardInterrupt):
        publish_worker.run_forever()
    assert session.closed is True


def test_system_exit_from_run_once_stops_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: session)
    )

    class _JobService:
        def run_once(self) -> bool:
            raise SystemExit(2)

    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _session: _JobService()
    )

    with pytest.raises(SystemExit) as exc_info:
        publish_worker.run_forever()
    assert exc_info.value.code == 2
    assert session.closed is True


@pytest.mark.parametrize("invalid_interval", [0, -1, -100, "abc", 1.5])
def test_invalid_poll_interval_is_rejected(invalid_interval: Any) -> None:
    from pydantic import ValidationError

    from app.core.settings import Settings

    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key="test-secret-key-for-settings-validation",
            publish_job_poll_interval_seconds=invalid_interval,
        )


def test_poll_interval_minimum_one_is_accepted() -> None:
    from app.core.settings import Settings

    loaded = Settings(
        jwt_secret_key="test-secret-key-for-settings-validation",
        publish_job_poll_interval_seconds=1,
    )
    assert loaded.publish_job_poll_interval_seconds == 1
