"""Tests for LinkedIn connection note generation."""

from job_agent.outreach.note_generator import template_connection_note, _trim_note
from job_agent.outreach.schemas import ContactPriority, LinkedInContact
from job_agent.matching.scorer import MatchScorer
from job_agent.normalization.filters import normalize_job
from job_agent.schemas import RawJob


def test_note_under_300_chars(resume):
    raw = RawJob(
        source="test",
        title="Android Engineer",
        company="TestCorp",
        location="Mumbai",
        description="Kotlin, Jetpack Compose, MVVM",
        job_url="https://example.com/job",
    )
    job = normalize_job(raw)
    match = MatchScorer(resume).score(job)
    contact = LinkedInContact(
        name="Jane Smith",
        title="Engineering Manager - Android",
        profile_url="https://linkedin.com/in/jane",
        priority=ContactPriority.HIRING_MANAGER,
    )
    note = template_connection_note(contact, match, resume, "ANDROID")
    assert len(note) <= 250
    assert "Jane" in note
    assert "TestCorp" in note
    assert "refer" in note.lower()


def test_note_no_fake_connection(resume, backend_resume):
    raw = RawJob(
        source="test",
        title="Java Backend Developer",
        company="ACL Digital",
        location="Mumbai",
        description="Java Spring Boot",
        job_url="https://example.com/job",
    )
    job = normalize_job(raw)
    match = MatchScorer(backend_resume, role_track="BACKEND").score(job)
    contact = LinkedInContact(
        name="Raj Patel",
        title="Technical Recruiter",
        profile_url="https://linkedin.com/in/raj",
        priority=ContactPriority.RECRUITER,
    )
    note = template_connection_note(contact, match, resume, "BACKEND")
    assert "we worked together" not in note.lower()
    assert "know each other" not in note.lower()
