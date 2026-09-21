"""Shared test fixtures.

Resumes are loaded through the profile system (data/resume/profiles/<n>-<Name>/)
rather than a fixed path, so adding or renaming a profile folder cannot silently
break the suite the way the flat data/resume/master_resume.json layout did.
"""

from __future__ import annotations

import pytest

from job_agent.profiles import ResumeProfile, load_profiles


def _require_profile(profile_id: str) -> ResumeProfile:
    for profile in load_profiles():
        if profile.id.lower() == profile_id:
            return profile
    available = ", ".join(p.id for p in load_profiles()) or "none"
    pytest.skip(f"profile '{profile_id}' not found (available: {available})")


@pytest.fixture(scope="session")
def android_profile() -> ResumeProfile:
    return _require_profile("android")


@pytest.fixture(scope="session")
def backend_profile() -> ResumeProfile:
    return _require_profile("backend")


@pytest.fixture(scope="session")
def android_resume(android_profile: ResumeProfile):
    return android_profile.resume


@pytest.fixture(scope="session")
def backend_resume(backend_profile: ResumeProfile):
    return backend_profile.resume


@pytest.fixture(scope="session")
def resume(android_resume):
    """Default resume for tests written before the multi-profile split."""
    return android_resume
