"""Endpoint smoke tests for insights — router mounted on a bare app with a fake
service (no lifespan, no workers). Empty results must 200, never 500."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse import insights_outcomes as io
from muse.models import TimelineResponse
from muse.routers import insights


class FakeService:
    def get_insights(self, days):
        return io.compute_outcomes(
            days=days, now=datetime(2026, 6, 16, tzinfo=timezone.utc),
            summaries=[], rollup={}, windows={}, health_rows={}, commits_by_sid={},
            history_rows=[], error_times=[], commit_times=[],
            scan=type("S", (), {"events": []})(),
        )

    def get_insights_timeline(self, project, days):
        return TimelineResponse(project=project)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(insights.router)
    app.state.service = FakeService()
    return TestClient(app)


def test_insights_empty_ok(client):
    r = client.get("/api/insights?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["outcomes"] == []
    assert body["most_productive"] == [] and body["most_wasteful"] == []
    assert len(body["matrix"]) == 7 * 24  # always a full grid
    assert body["confidence_policy"] == "high+medium"


def test_insights_range_validation(client):
    assert client.get("/api/insights?days=5").status_code == 400
    for d in (0, 7, 30, 90):
        assert client.get(f"/api/insights?days={d}").status_code == 200


def test_timeline_empty_ok(client):
    r = client.get("/api/insights/timeline?project=/nope&days=30")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


def test_timeline_requires_project(client):
    assert client.get("/api/insights/timeline?days=30").status_code == 422
