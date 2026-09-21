"""Test LaTeX generation."""

import re
from pathlib import Path

import pytest

from job_agent.resume.latex_generator import (
    LatexGenerator,
    _plain_to_latex_profile,
    safe_filename,
)
from job_agent.schemas import TailoredResume


def test_safe_filename():
    assert safe_filename("Google Inc.") == "Google_Inc"
    assert " " not in safe_filename("Test Company Name!")


def test_latex_generation(tmp_path, resume):
    skills = dict(resume.technical_skills)
    tailored = TailoredResume(
        job_id="test123",
        company="TestCorp",
        job_title="Android Engineer",
        profile_summary="Tailored paragraph that must not reach the document.",
        experience=resume.experience,
        projects=resume.projects,
        technical_skills=skills,
        education=resume.education,
        certifications=resume.certifications,
    )

    gen = LatexGenerator()
    tex_path, pdf_path = gen.generate(tailored, "TestCorp", "Android Engineer", tmp_path, role_category="ANDROID")

    assert tex_path.exists()
    assert tex_path.name == "Abhishek-Pandey_Android.tex"
    content = tex_path.read_text()
    assert "\\documentclass" in content
    assert "ABHISHEK PANDEY" in content
    assert "\\section{Technical Skills}" in content
    # \bn{...} highlighting must survive as a command, never be escaped into text
    assert "\\bn{" in content
    assert r"\bn\{" not in content
    assert "TestCorp" in content or "Android Engineer" in content
    if pdf_path is not None:
        assert pdf_path.exists()
        assert pdf_path.suffix == ".pdf"


def _profile_section(text: str) -> str:
    match = re.search(r"\\section\{Profile\}(.*?)(?=\\section\{)", text, re.S)
    assert match, "no Profile section found"
    return " ".join(match.group(1).split())


@pytest.mark.parametrize("profile_id,track", [("android", "ANDROID"), ("backend", "BACKEND")])
def test_profile_summary_always_matches_master(tmp_path, request, profile_id, track):
    """The Profile section is fixed per resume profile.

    Even when the LLM returns a tailored paragraph, the generated resume must carry
    the master's summary verbatim — an Android resume always reads like the Android
    master, a Backend resume like the Backend master.
    """
    profile = request.getfixturevalue(f"{profile_id}_profile")
    resume = profile.resume
    master_text = _profile_section(profile.tex_path.read_text(encoding="utf-8"))

    tailored = TailoredResume(
        job_id="t",
        company="Acme",
        job_title="Senior Engineer",
        profile_summary="LLM rewrote this and it must be ignored.",
        experience=resume.experience,
        projects=resume.projects,
        technical_skills=dict(resume.technical_skills),
        education=resume.education,
        certifications=resume.certifications,
        keywords_used=["Kotlin", "Coroutines"],
    )

    gen = LatexGenerator()
    gen.master_path = profile.tex_path
    tex_path, _ = gen.generate(
        tailored, "Acme", "Senior Engineer", tmp_path, role_category=track
    )
    content = tex_path.read_text(encoding="utf-8")

    assert _profile_section(content) == master_text
    assert "LLM rewrote this" not in content


def _header(text: str) -> str:
    return text[text.find("\\begin{center}") : text.find("\\end{center}")]


def test_header_tagline_always_matches_master(tmp_path, android_profile):
    """The header role line is fixed per profile, like the Profile section.

    It must not absorb the target job title — a JPMorgan posting once produced
    "Software Engineer III - Java Full Stack..." in the header.
    """
    resume = android_profile.resume
    master_header = _header(android_profile.tex_path.read_text(encoding="utf-8"))

    tailored = TailoredResume(
        job_id="t",
        company="Acme",
        job_title="Staff Android Platform Engineer",
        profile_summary="Summary.",
        experience=resume.experience,
        projects=resume.projects,
        technical_skills=dict(resume.technical_skills),
        education=resume.education,
        certifications=resume.certifications,
        keywords_used=["Kotlin", "Coroutines"],
    )

    gen = LatexGenerator()
    gen.master_path = android_profile.tex_path
    tex_path, _ = gen.generate(
        tailored, "Acme", "Staff Android Platform Engineer", tmp_path, role_category="ANDROID"
    )

    generated_header = _header(tex_path.read_text(encoding="utf-8"))
    assert generated_header == master_header
    assert "Staff Android Platform Engineer" not in generated_header


def test_profile_highlights_keep_bn_command():
    latex = _plain_to_latex_profile(
        "Android Engineer with \\bn{Kotlin} and **MVVM** plus \\bJava\\b."
    )
    assert "\\bn{Kotlin}" in latex
    assert "\\bn{MVVM}" in latex
    assert "\\bn{Java}" in latex
    assert r"\bn\{" not in latex


