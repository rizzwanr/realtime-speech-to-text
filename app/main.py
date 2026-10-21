"""Speech-to-text web service with live transcription and history."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import db
from app.gemini import GeminiLiveTranscriber
from app.routes import router
from app.schemas import to_out

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSION_SECONDS = 600
MAX_AUDIO_BYTES = 16_000 * 2 * MAX_SESSION_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(db.init_db)
    yield


app = FastAPI(title="Vaani Speech to Text", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health() -> dict:
    database_ok = True
    try:
        with db.session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check could not reach the database")
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "database": db.database_kind(),
        "geminiConfigured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "model": os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live"),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws/transcribe")
async def transcribe_socket(websocket: WebSocket) -> None:
    await websocket.accept()

    async def safe_send(payload: dict) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:
            logger.debug("Could not write to the client socket", exc_info=True)

    try:
        start = await asyncio.wait_for(websocket.receive_json(), timeout=20)
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        await websocket.close()
        return

    if start.get("type") != "start":
        await safe_send({"type": "error", "message": "Expected a start message."})
        await websocket.close()
        return

    mode = str(start.get("languageMode") or "auto")
    title = str(start.get("title") or "")
    transcriber: GeminiLiveTranscriber | None = None
    try:
        transcriber = GeminiLiveTranscriber(mode, start.get("customVocabulary"))
        await transcriber.connect()
    except ValueError as exc:
        await safe_send({"type": "error", "code": "bad_mode", "message": str(exc)})
        await websocket.close()
        return
    except RuntimeError as exc:
        await safe_send({"type": "error", "code": "gemini_not_configured", "message": str(exc)})
        await websocket.close()
        return
    except Exception:
        logger.exception("Could not connect to Gemini Live")
        await safe_send({"type": "error", "message": "Could not reach Gemini Live."})
        if transcriber is not None:
            await transcriber.close()
        await websocket.close()
        return

    pump = asyncio.create_task(transcriber.pump_server(safe_send))
    save = True
    audio_bytes = 0
    started = asyncio.get_running_loop().time()
    try:
        while True:
            if asyncio.get_running_loop().time() - started > MAX_SESSION_SECONDS:
                await safe_send({"type": "error", "message": "Session limit reached (10 minutes)."})
                break
            try:
                incoming = await websocket.receive()
            except WebSocketDisconnect:
                break
            if incoming.get("type") == "websocket.disconnect":
                break
            chunk = incoming.get("bytes")
            if chunk:
                audio_bytes += len(chunk)
                if audio_bytes > MAX_AUDIO_BYTES:
                    await safe_send({"type": "error", "message": "Audio limit reached."})
                    break
                try:
                    await transcriber.send_audio(chunk)
                except asyncio.TimeoutError:
                    await safe_send({"type": "error", "message": "Gemini Live did not become ready."})
                    break
                continue
            if incoming.get("text"):
                try:
                    message = json.loads(incoming["text"])
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "stop":
                    save = bool(message.get("save", True))
                    break
    finally:
        await transcriber.end_audio()
        if not pump.done():
            try:
                await asyncio.wait_for(pump, timeout=2)
            except Exception:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
        transcript_text = transcriber.transcript()
        detected = list(transcriber.detected)
        model_name = transcriber.model[:80]
        await transcriber.close()
        try:
            if save and transcript_text:
                row = await asyncio.to_thread(
                    db.create_transcription,
                    title=title,
                    transcript=transcript_text,
                    language_mode=mode if mode in {"auto", "hinglish", "hi", "en"} else "auto",
                    detected_languages=detected,
                    source=model_name,
                )
                await safe_send({"type": "saved", "transcription": to_out(row).model_dump(mode="json")})
            elif save:
                await safe_send({"type": "empty"})
            else:
                await safe_send({"type": "discarded"})
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()
