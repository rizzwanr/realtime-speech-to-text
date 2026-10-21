"""PostgreSQL persistence for transcription history."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, create_engine, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.schemas import encode_languages, suggest_title

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql+psycopg://stt:stt@localhost:5432/speech"

_engine = None
_session_factory = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and postgresql:// URLs from hosted Postgres."""
    cleaned = url.strip()
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned[len("postgres://") :]
    if cleaned.startswith("postgresql://"):
        cleaned = "postgresql+psycopg://" + cleaned[len("postgresql://") :]
    return cleaned


def database_kind(url: str | None = None) -> str:
    resolved = normalize_database_url(url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    if resolved.startswith("postgresql"):
        return "postgresql"
    if resolved.startswith("sqlite"):
        return "sqlite"
    return "other"


class Base(DeclarativeBase):
    pass


class Transcription(Base):
    __tablename__ = "transcriptions"
    __table_args__ = (Index("ix_transcriptions_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    language_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    detected_languages: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        url = normalize_database_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def _sessions():
    get_engine()
    return _session_factory


@contextmanager
def session_scope():
    session = _sessions()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    import time

    last_error: Exception | None = None
    for attempt in range(8):
        try:
            Base.metadata.create_all(get_engine())
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Database not ready (attempt %s): %s", attempt + 1, exc)
            time.sleep(2)
    raise RuntimeError("Could not initialize the database") from last_error


def create_transcription(
    *,
    title: str,
    transcript: str,
    language_mode: str,
    detected_languages: list[str],
    source: str,
) -> Transcription:
    with session_scope() as db:
        now = utcnow()
        row = Transcription(
            title=suggest_title(transcript, title),
            transcript=transcript,
            language_mode=language_mode,
            detected_languages=encode_languages(detected_languages),
            source=source,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def list_transcriptions(q: str, limit: int, offset: int) -> tuple[list[Transcription], int]:
    with session_scope() as db:
        filters = []
        query = q.strip()
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            filters.append(
                or_(
                    Transcription.title.ilike(pattern, escape="\\"),
                    Transcription.transcript.ilike(pattern, escape="\\"),
                )
            )
        count_stmt = select(func.count()).select_from(Transcription)
        list_stmt = select(Transcription).order_by(Transcription.created_at.desc(), Transcription.id.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = db.scalar(count_stmt) or 0
        rows = list(db.scalars(list_stmt.limit(limit).offset(offset)).all())
        return rows, int(total)


def get_transcription(transcription_id: int) -> Transcription | None:
    with session_scope() as db:
        return db.get(Transcription, transcription_id)


def update_transcription(
    transcription_id: int,
    *,
    title: str | None,
    transcript: str | None,
    language_mode: str | None,
) -> Transcription | None:
    with session_scope() as db:
        row = db.get(Transcription, transcription_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if transcript is not None:
            row.transcript = transcript
        if language_mode is not None:
            row.language_mode = language_mode
        row.updated_at = utcnow()
        db.flush()
        db.refresh(row)
        return row


def delete_transcription(transcription_id: int) -> bool:
    with session_scope() as db:
        row = db.get(Transcription, transcription_id)
        if row is None:
            return False
        db.delete(row)
        return True


def transcription_stats() -> dict:
    with session_scope() as db:
        total = int(db.scalar(select(func.count()).select_from(Transcription)) or 0)
        stored = db.scalars(select(Transcription.detected_languages)).all()
    counts: dict[str, int] = {}
    for raw in stored:
        for code in decode_safe(raw):
            counts[code] = counts.get(code, 0) + 1
    return {"total": total, "languages": counts}


def decode_safe(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in raw.split(",") if part]
