# Vaani — Speech to Text

Real-time speech transcription for Hindi, English, and mixed Hindi–English speech, with the transcript history stored in PostgreSQL and managed from a dashboard.

This is a hiring assignment for Procucev. The live path uses Google’s free Gemini Live transcription API (`gemini-3.5-transcribe-live`), which is the current speech-to-text model in the Gemini Live API. The assignment brief mentions “Gemini Live 3.1”; Google’s live transcription docs now publish `gemini-3.5-transcribe-live`, and that model id is what this app calls. It can be changed with `GEMINI_TRANSCRIBE_MODEL`.

If `GEMINI_API_KEY` is not set, the page falls back to the browser’s speech recognition (Chrome or Edge) so the dashboard can still be tried. Mixed Hindi and English in one sentence is the Gemini path.

## What it does

- Listens from the microphone and shows words while you are still speaking.
- Language modes: auto-detect, Hindi + English, Hindi, or Indian English.
- Optional bias words for names such as Procucev or Bangalore.
- Saves each session in PostgreSQL.
- History dashboard with search, create, edit, and delete.

Audio sent to Gemini is raw 16-bit PCM at 16 kHz. Only the text is stored. The recording itself is not kept.

## Stack

- Python, FastAPI, WebSockets
- PostgreSQL (SQLAlchemy and psycopg)
- Static browser UI, no frontend build step
- Gemini Live transcription over WebSocket

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d db
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

Put a free key from [Google AI Studio](https://aistudio.google.com/apikey) in `.env` as `GEMINI_API_KEY`, then restart the server. Use Chrome or Edge if you want the browser fallback.

`DATABASE_URL` defaults to `postgresql+psycopg://stt:stt@localhost:5432/speech`. Hosted Postgres URLs that start with `postgres://` or `postgresql://` are rewritten for psycopg.

## Tests

```bash
pytest -q
```

The tests use a temporary SQLite file so they do not need a running Postgres server. The application path used in Docker and on a host is PostgreSQL.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Database and speech-engine status |
| GET | `/api/stats` | Saved session count |
| GET | `/api/transcriptions?q=&limit=&offset=` | Search history |
| POST | `/api/transcriptions` | Create a transcript |
| GET | `/api/transcriptions/{id}` | Read one |
| PUT | `/api/transcriptions/{id}` | Update title, text, or language |
| DELETE | `/api/transcriptions/{id}` | Delete one |
| WebSocket | `/ws/transcribe` | Stream PCM audio and receive live text |

Interactive docs are at `/docs`.

## Deploy

The container listens on `$PORT`.

1. Create a project on Railway or Render.
2. Add a PostgreSQL database and set `DATABASE_URL` to its connection string.
3. Set `GEMINI_API_KEY`.
4. Deploy this repo with the included `Dockerfile`.

`docker compose up --build` runs the app and Postgres together on port 8000.

## Project layout

```
app/main.py          HTTP server, health, and the live audio socket
app/gemini.py        Gemini Live transcription client
app/db.py            PostgreSQL model and queries
app/routes.py        History CRUD
app/static/          Dashboard
tests/test_api.py    CRUD, search, and socket checks
```
