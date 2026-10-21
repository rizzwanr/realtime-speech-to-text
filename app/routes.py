"""CRUD routes for stored transcriptions."""

from fastapi import APIRouter, HTTPException, Query

from app import db
from app.schemas import TranscriptionCreate, TranscriptionOut, TranscriptionUpdate, to_out

router = APIRouter(prefix="/api")


@router.get("/transcriptions")
def list_transcriptions(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows, total = db.list_transcriptions(q, limit, offset)
    return {
        "items": [to_out(row).model_dump(mode="json") for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/transcriptions", status_code=201)
def create_transcription(payload: TranscriptionCreate) -> TranscriptionOut:
    row = db.create_transcription(
        title=payload.title,
        transcript=payload.transcript,
        language_mode=payload.language_mode,
        detected_languages=payload.detected_languages,
        source=payload.source,
    )
    return to_out(row)


@router.get("/transcriptions/{transcription_id}")
def read_transcription(transcription_id: int) -> TranscriptionOut:
    row = db.get_transcription(transcription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    return to_out(row)


@router.put("/transcriptions/{transcription_id}")
def update_transcription(transcription_id: int, payload: TranscriptionUpdate) -> TranscriptionOut:
    if payload.title is None and payload.transcript is None and payload.language_mode is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = db.update_transcription(
        transcription_id,
        title=payload.title,
        transcript=payload.transcript,
        language_mode=payload.language_mode,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    return to_out(row)


@router.delete("/transcriptions/{transcription_id}", status_code=204)
def remove_transcription(transcription_id: int) -> None:
    if not db.delete_transcription(transcription_id):
        raise HTTPException(status_code=404, detail="Transcription not found")


@router.get("/stats")
def stats() -> dict:
    return db.transcription_stats()
