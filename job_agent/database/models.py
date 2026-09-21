"""SQLAlchemy database models."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from job_agent.config import get_settings


class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(128), nullable=False, index=True)
    company = Column(String(256), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    location = Column(String(256), default="")
    location_tier = Column(Integer, default=0)
    source = Column(String(64), nullable=False)
    source_job_id = Column(String(256), default="")
    job_url = Column(Text, nullable=False)
    application_url = Column(Text, default="")
    date_posted = Column(DateTime, nullable=True)
    date_posted_verified = Column(String(16), default="unknown")
    date_discovered = Column(DateTime, default=datetime.utcnow, index=True)
    employment_type = Column(String(32), default="Unknown")
    seniority = Column(String(32), default="Unknown")
    salary = Column(String(128), default="")
    job_description = Column(Text, default="")
    is_remote = Column(Boolean, default=False)
    remote_india_eligible = Column(Boolean, default=False)
    is_android_relevant = Column(Boolean, default=False)

    # Employer-side identifier resolved from the company's own ATS, kept
    # separate from source_job_id (which stays the job-board identifier).
    career_portal = Column(String(64), default="")
    career_portal_job_id = Column(String(128), default="", index=True)
    career_portal_url = Column(Text, default="")
    career_job_id_type = Column(String(32), default="")

    match_score = Column(Float, nullable=True, index=True)
    match_category = Column(String(32), nullable=True)
    match_reason = Column(Text, default="")
    matching_skills = Column(Text, default="")  # JSON comma-separated
    missing_requirements = Column(Text, default="")
    score_breakdown = Column(Text, default="")  # JSON

    resume_path = Column(Text, default="")
    resume_tex_path = Column(Text, default="")
    cover_letter_path = Column(Text, default="")

    application_status = Column(String(32), default="DISCOVERED", index=True)
    applied_at = Column(DateTime, nullable=True)
    notes = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    sources_searched = Column(Integer, default=0)
    jobs_discovered = Column(Integer, default=0)
    jobs_deduplicated = Column(Integer, default=0)
    relevant_jobs = Column(Integer, default=0)
    jobs_within_recency = Column(Integer, default=0)
    strong_matches = Column(Integer, default=0)
    resumes_generated = Column(Integer, default=0)
    errors = Column(Text, default="")
    output_path = Column(Text, default="")


class AppConfig(Base):
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(128), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(settings.database_url, connect_args=connect_args)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def _add_missing_columns(engine) -> None:
    """Add newly declared columns without rebuilding the table (keeps PK intact)."""
    additive_columns = {
        "jobs": {
            "career_portal": "VARCHAR(64) DEFAULT ''",
            "career_portal_job_id": "VARCHAR(128) DEFAULT ''",
            "career_portal_url": "TEXT DEFAULT ''",
            "career_job_id_type": "VARCHAR(32) DEFAULT ''",
        }
    }
    inspector = inspect(engine)
    for table, columns in additive_columns.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in existing:
                continue
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _add_missing_columns(engine)


def get_db_session() -> Session:
    factory = get_session_factory()
    return factory()
