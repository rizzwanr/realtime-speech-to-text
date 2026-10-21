"""Request and response models for transcription records."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

LANGUAGE_MODES = {"auto", "hinglish", "hi", "en"}


def suggest_title(transcript: str, title: str) -> str:
    cleaned = " ".join(title.split())
    if cleaned:
        return cleaned[:200]
    compact = " ".join(transcript.split())
    if len(compact) <= 72:
        return compact or "Untitled session"
    return compact[:69].rstrip() + "..."


def encode_languages(codes: list[str]) -> str:
    cleaned: list[str] = []
    for code in codes:
        item = str(code).strip().replace(",", "")[:16]
        if item and item not in cleaned:
            cleaned.append(item)
    return ",".join(cleaned)


def decode_languages(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in raw.split(",") if part]


class TranscriptionCreate(BaseModel):
    title: str = ""
    transcript: str
    language_mode: str = "auto"
    detected_languages: list[str] = Field(default_factory=list)
    source: str = "manual"

    @field_validator("transcript")
    @classmethod
    def transcript_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Transcript cannot be empty")
        if len(value) > 20000:
            raise ValueError("Transcript is too long")
        return value

    @field_validator("title")
    @classmethod
    def title_length(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) > 200:
            raise ValueError("Title is too long")
        return value

    @field_validator("language_mode")
    @classmethod
    def mode_known(cls, value: str) -> str:
        value = (value or "auto").strip()
        if value not in LANGUAGE_MODES:
            raise ValueError("Unsupported language mode")
        return value

    @field_validator("source")
    @classmethod
    def source_length(cls, value: str) -> str:
        value = (value or "manual").strip()
        if not value or len(value) > 80:
            raise ValueError("Source must be 1 to 80 characters")
        return value

    @field_validator("detected_languages")
    @classmethod
    def languages_bounded(cls, value: list[str]) -> list[str]:
        return decode_languages(encode_languages(value))[:12]


class TranscriptionUpdate(BaseModel):
    title: str | None = None
    transcript: str | None = None
    language_mode: str | None = None

    @field_validator("title")
    @classmethod
    def title_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("Title cannot be empty")
        if len(value) > 200:
            raise ValueError("Title is too long")
        return value

    @field_validator("transcript")
    @classmethod
    def transcript_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Transcript cannot be empty")
        if len(value) > 20000:
            raise ValueError("Transcript is too long")
        return value

    @field_validator("language_mode")
    @classmethod
    def mode_known(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value not in LANGUAGE_MODES:
            raise ValueError("Unsupported language mode")
        return value


class TranscriptionOut(BaseModel):
    id: int
    title: str
    transcript: str
    language_mode: str
    detected_languages: list[str]
    source: str
    created_at: datetime
    updated_at: datetime


def to_out(row) -> TranscriptionOut:
    return TranscriptionOut(
        id=row.id,
        title=row.title,
        transcript=row.transcript,
        language_mode=row.language_mode,
        detected_languages=decode_languages(row.detected_languages),
        source=row.source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
