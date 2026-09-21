"""LinkedIn outreach schemas."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ContactPriority(str, Enum):
    HIRING_MANAGER = "hiring_manager"
    RECRUITER = "recruiter"
    ENGINEER = "engineer"


class LinkedInContact(BaseModel):
    name: str
    title: str
    profile_url: str
    priority: ContactPriority
    relevance_reason: str = ""


class OutreachStatus(str, Enum):
    NOTE_SAVED = "note_saved"
    UI_PREPARED = "ui_prepared"  # note entered in LinkedIn modal; user must click Send
    NO_CONNECT = "no_connect_button"
    ADD_NOTE_UNAVAILABLE = "add_note_unavailable"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class LinkedInOutreachRecord(BaseModel):
    job_id: str
    company: str
    job_title: str
    contact: LinkedInContact
    connection_note: str
    status: OutreachStatus
    note_entered_in_ui: bool = False
    note_file_path: str = ""


class JobOutreachSummary(BaseModel):
    job_id: str
    company: str
    job_title: str
    match_score: float
    contacts: List[LinkedInOutreachRecord] = Field(default_factory=list)
