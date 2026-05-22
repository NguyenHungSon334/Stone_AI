"""
Integration tests for FastAPI endpoints.
External calls (Supabase, orchestrator) are mocked.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /ready
# ---------------------------------------------------------------------------

def test_ready_db_ok(client):
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock()
    with patch("app.db.supabase.get_client", return_value=mock_db):
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] is True
    assert body["status"] == "ok"


def test_ready_db_degraded(client):
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("conn failed")
    with patch("app.db.supabase.get_client", return_value=mock_db):
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] is False
    assert body["status"] == "degraded"


# ---------------------------------------------------------------------------
# GET /webhook  (Facebook verification)
# ---------------------------------------------------------------------------

def test_webhook_verify_success(client):
    resp = client.get("/webhook", params={
        "hub_mode": "subscribe",
        "hub_verify_token": settings.messenger_verify_token,
        "hub_challenge": "99999",
    })
    assert resp.status_code == 200
    assert resp.json() == 99999


def test_webhook_verify_wrong_token(client):
    resp = client.get("/webhook", params={
        "hub_mode": "subscribe",
        "hub_verify_token": "wrong-token",
        "hub_challenge": "99999",
    })
    assert resp.status_code == 403


def test_webhook_verify_wrong_mode(client):
    resp = client.get("/webhook", params={
        "hub_mode": "unsubscribe",
        "hub_verify_token": settings.messenger_verify_token,
        "hub_challenge": "99999",
    })
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /webhook  (message receive)
# ---------------------------------------------------------------------------

def _page_body(sender_id: str = "u1", text: str = "hello") -> dict:
    return {
        "object": "page",
        "entry": [{"messaging": [{"sender": {"id": sender_id}, "message": {"text": text}}]}],
    }


def test_webhook_post_queues_message(client):
    with (
        patch("app.main.extract_messages", return_value=[{"sender_id": "u1", "text": "xin chào", "timestamp": 1}]),
        patch("app.main.is_rate_limited", return_value=False),
        patch("app.orchestrator.run", new_callable=AsyncMock),
    ):
        resp = client.post("/webhook", json=_page_body())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["queued"] == 1


def test_webhook_post_rate_limited_skips(client):
    with (
        patch("app.main.extract_messages", return_value=[{"sender_id": "u1", "text": "hello"}]),
        patch("app.main.is_rate_limited", return_value=True),
        patch("app.orchestrator.run", new_callable=AsyncMock) as mock_run,
    ):
        resp = client.post("/webhook", json=_page_body())

    assert resp.status_code == 200
    assert resp.json()["queued"] == 0
    mock_run.assert_not_called()


def test_webhook_post_not_page_event(client):
    resp = client.post("/webhook", json={"object": "user"})
    assert resp.status_code == 400


def test_webhook_post_multiple_events(client):
    events = [
        {"sender_id": "u10", "text": "hello", "timestamp": 1001},
        {"sender_id": "u11", "text": "hi", "timestamp": 1002},
    ]
    with (
        patch("app.main.extract_messages", return_value=events),
        patch("app.main.is_rate_limited", return_value=False),
        patch("app.orchestrator.run", new_callable=AsyncMock),
    ):
        resp = client.post("/webhook", json=_page_body())

    assert resp.json()["queued"] == 2
