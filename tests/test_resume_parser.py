"""Test resume parsing."""

import json
from pathlib import Path

import pytest

from job_agent.resume.parser import ResumeParser
from job_agent.schemas import MasterResume


@pytest.fixture
def master_resume_path(android_profile):
    src = android_profile.path / "master_resume.json"
    if src.exists():
        return str(src)
    pytest.skip(f"master_resume.json not found in {android_profile.path}")


def test_load_master_resume(master_resume_path):
    parser = ResumeParser(json_path=master_resume_path)
    resume = parser.load()
    assert isinstance(resume, MasterResume)
    assert resume.contact.name == "Abhishek Pandey"
    assert resume.contact.email == "abhishekvpandeyxyz@gmail.com"
    assert len(resume.experience) >= 2
    assert len(resume.projects) >= 1


def test_get_all_skills(master_resume_path):
    parser = ResumeParser(json_path=master_resume_path)
    resume = parser.load()
    skills = parser.get_all_skills(resume)
    assert "kotlin" in skills
    assert "jetpack compose" in skills or any("compose" in s for s in skills)


def test_no_fabricated_data(master_resume_path):
    parser = ResumeParser(json_path=master_resume_path)
    resume = parser.load()
    companies = [e.company for e in resume.experience]
    assert "Percipere" in companies
    assert "Google" in companies
    assert resume.total_experience_years <= 5
