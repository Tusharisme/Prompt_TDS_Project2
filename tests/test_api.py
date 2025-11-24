import pytest
from fastapi.testclient import TestClient
from fastapi import status

from app.main import app
from app.config import settings

client = TestClient(app)

def test_quiz_ok():
    payload = {
        "email": str(settings.STUDENT_EMAIL),
        "secret": str(settings.STUDENT_SECRET),
        "url": str("https://example.com/quiz-123"),
    }
    resp = client.post("/quiz", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["ok"] is True
    assert "Phase 1 OK" in data["message"]


def test_quiz_forbidden():
    payload = {
        "email": str(settings.STUDENT_EMAIL),
        "secret": "WRONG",
        "url": str("https://example.com/quiz-123"),
    }
    resp = client.post("/quiz", json=payload)
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    data = resp.json()
    assert data["ok"] is False
    assert "Invalid secret" == data["error"]


def test_quiz_bad_request():
    # Missing fields to trigger 400
    resp = client.post("/quiz", json={"email": "x@y.com"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    data = resp.json()
    assert data["ok"] is False
