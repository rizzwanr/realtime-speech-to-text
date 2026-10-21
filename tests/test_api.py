import pytest
from fastapi.testclient import TestClient

from app.db import normalize_database_url, reset_engine
from app.gemini import clean_vocabulary, language_codes_for
from app.schemas import suggest_title


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/speech.db")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reset_engine()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()


def test_normalize_database_url():
    assert normalize_database_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"


def test_language_and_title_helpers():
    assert language_codes_for("auto") == []
    assert language_codes_for("hinglish") == ["hi-IN", "en-IN"]
    assert clean_vocabulary("Vaani, Bangalore, Vaani,  ") == ["Vaani", "Bangalore"]
    assert suggest_title("Kal meeting hai.", "") == "Kal meeting hai."
    with pytest.raises(ValueError):
        language_codes_for("fr")


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"
    assert body["geminiConfigured"] is False


def test_transcription_crud_and_search(client):
    created = client.post(
        "/api/transcriptions",
        json={
            "title": "Standup",
            "transcript": "Kal office jaana hai, पर पहले coffee.",
            "language_mode": "hinglish",
            "detected_languages": ["hi-IN", "en-IN"],
            "source": "manual",
        },
    )
    assert created.status_code == 201
    item = created.json()
    assert item["transcript"].startswith("Kal office")
    assert item["detected_languages"] == ["hi-IN", "en-IN"]

    listing = client.get("/api/transcriptions", params={"q": "coffee"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    hindi = client.get("/api/transcriptions", params={"q": "पहले"})
    assert hindi.json()["total"] == 1

    updated = client.put(
        f"/api/transcriptions/{item['id']}",
        json={"title": "Edited standup", "transcript": "Meeting moved to 5."},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Edited standup"

    fetched = client.get(f"/api/transcriptions/{item['id']}")
    assert fetched.json()["transcript"] == "Meeting moved to 5."

    empty = client.put(f"/api/transcriptions/{item['id']}", json={})
    assert empty.status_code == 400

    removed = client.delete(f"/api/transcriptions/{item['id']}")
    assert removed.status_code == 204
    assert client.get(f"/api/transcriptions/{item['id']}").status_code == 404
    assert client.get("/api/stats").json()["total"] == 0


def test_rejects_blank_transcript(client):
    response = client.post("/api/transcriptions", json={"transcript": "   "})
    assert response.status_code == 422


def test_manual_title_is_taken_from_speech(client):
    response = client.post(
        "/api/transcriptions",
        json={"transcript": "A short spoken line.", "language_mode": "en"},
    )
    assert response.status_code == 201
    assert response.json()["title"] == "A short spoken line."


def test_websocket_requires_gemini_key(client):
    with client.websocket_connect("/ws/transcribe") as socket:
        socket.send_json({"type": "start", "languageMode": "hinglish", "title": "Live"})
        message = socket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "gemini_not_configured"


def test_websocket_rejects_bad_start(client):
    with client.websocket_connect("/ws/transcribe") as socket:
        socket.send_json({"type": "noop"})
        message = socket.receive_json()
        assert message["type"] == "error"
