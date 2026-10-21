"""Bridge microphone audio to Gemini Live transcription.

Audio contract: raw 16-bit little-endian PCM, 16 kHz, mono.
Docs: https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Awaitable, Callable

import websockets

logger = logging.getLogger(__name__)

WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

LANGUAGE_MODES = {
    "auto": [],
    "hinglish": ["hi-IN", "en-IN"],
    "hi": ["hi-IN"],
    "en": ["en-IN"],
}

SendEvent = Callable[[dict], Awaitable[None]]


def language_codes_for(mode: str) -> list[str]:
    if mode not in LANGUAGE_MODES:
        raise ValueError(f"Unsupported language mode: {mode}")
    return list(LANGUAGE_MODES[mode])


def clean_vocabulary(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, list):
        parts = raw
    else:
        return []
    cleaned: list[str] = []
    for part in parts:
        text = " ".join(str(part).split())[:40]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= 50:
            break
    return cleaned


def _pick(data: dict | None, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


class GeminiLiveTranscriber:
    def __init__(self, language_mode: str, custom_vocabulary=None) -> None:
        self.language_mode = language_mode
        self.language_codes = language_codes_for(language_mode)
        self.custom_vocabulary = clean_vocabulary(custom_vocabulary)
        self.model = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live").strip()
        self.final_parts: list[str] = []
        self.interim = ""
        self.detected: list[str] = []
        self._ws = None
        self._ready = asyncio.Event()
        self._closed = False

    async def connect(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        transcription: dict = {"languageCodes": self.language_codes}
        if self.custom_vocabulary:
            transcription["customVocabulary"] = self.custom_vocabulary
        setup = {
            "setup": {
                "model": f"models/{self.model}",
                "generationConfig": {"responseModalities": ["TEXT"]},
                "inputAudioTranscription": transcription,
            }
        }
        self._ws = await websockets.connect(
            f"{WS_URL}?key={api_key}",
            max_size=8_000_000,
            ping_interval=20,
        )
        try:
            await self._ws.send(json.dumps(setup))
        except Exception:
            await self.close()
            raise
        logger.info("Opened Gemini Live session for model %s", self.model)

    async def pump_server(self, send: SendEvent) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Ignoring non-JSON Gemini message")
                    continue
                if _pick(message, "setupComplete", "setup_complete") is not None:
                    self._ready.set()
                    await send({"type": "ready", "engine": "gemini", "model": self.model})
                    continue
                error = message.get("error")
                if error:
                    text = error.get("message") if isinstance(error, dict) else str(error)
                    await send({"type": "error", "message": text or "Gemini Live error"})
                    continue
                content = _pick(message, "serverContent", "server_content") or {}
                interim = _pick(content, "interimInputTranscription", "interim_input_transcription")
                final = _pick(content, "inputTranscription", "input_transcription")
                if isinstance(interim, dict) and interim.get("text"):
                    self.interim = str(interim["text"])
                    code = str(_pick(interim, "languageCode", "language_code") or "")
                    self._remember(code)
                    await send({"type": "interim", "text": self.interim, "languageCode": code})
                if isinstance(final, dict) and final.get("text"):
                    text = str(final["text"]).strip()
                    if not text:
                        continue
                    self.final_parts.append(text)
                    self.interim = ""
                    code = str(_pick(final, "languageCode", "language_code") or "")
                    self._remember(code)
                    await send({"type": "final", "text": text, "languageCode": code})
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            logger.info("Gemini socket closed: %s", exc.reason or exc.code)
            if not self._ready.is_set():
                reason = exc.reason or "connection closed before setup completed"
                await send({"type": "error", "message": f"Gemini Live closed: {reason}"})
        except Exception:
            logger.exception("Gemini transcription stream failed")
            if not self._ready.is_set():
                await send({"type": "error", "message": "Gemini Live connection failed."})

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or self._ws is None or not pcm:
            return
        await asyncio.wait_for(self._ready.wait(), timeout=10)
        if self._closed or self._ws is None:
            return
        payload = {
            "realtimeInput": {
                "audio": {
                    "data": base64.b64encode(pcm).decode("ascii"),
                    "mimeType": "audio/pcm;rate=16000",
                }
            }
        }
        await self._ws.send(json.dumps(payload))

    async def end_audio(self) -> None:
        self._closed = True
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
        except Exception:
            logger.info("Could not send audioStreamEnd")

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("Gemini socket close failed", exc_info=True)
            self._ws = None

    def transcript(self) -> str:
        parts = [part.strip() for part in self.final_parts if part.strip()]
        if self.interim.strip():
            parts.append(self.interim.strip())
        return " ".join(parts).strip()

    def _remember(self, code: str) -> None:
        if code and code not in self.detected:
            self.detected.append(code)
