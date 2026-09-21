"""Tests for JD-driven skill intelligence."""

import pytest

from job_agent.matching.scorer import MatchScorer
from job_agent.matching.skill_intelligence import SkillAnalyzer
from job_agent.schemas import RawJob
from job_agent.normalization.filters import normalize_job


def test_view_binding_is_implied_not_missing(android_resume):
    analyzer = SkillAnalyzer(android_resume, role_track="ANDROID")
    jd = (
        "Android Engineer with Kotlin, Jetpack Compose, View Binding, "
        "MVVM, REST APIs, Android SDK."
    )
    analysis = analyzer.analyze(jd.lower())

    assert "View Binding" in analysis.implied or "View Binding" in analysis.explicit
    assert "View Binding" not in analysis.unsupported


def test_flutter_is_unsupported(android_resume):
    analyzer = SkillAnalyzer(android_resume, role_track="ANDROID")
    jd = "Kotlin Android developer with Flutter and React Native experience required."
    analysis = analyzer.analyze(jd.lower())

    assert "Flutter" in analysis.unsupported
    assert "React Native" in analysis.unsupported


def test_dynamodb_implied_for_backend_aws_jd(backend_resume):
    analyzer = SkillAnalyzer(backend_resume, role_track="BACKEND")
    jd = "Java Spring Boot backend on AWS — EC2, RDS, DynamoDB, security groups."
    analysis = analyzer.analyze(jd.lower())

    assert "DynamoDB" in analysis.implied or "DynamoDB" in analysis.explicit
    assert "Security Groups" in analysis.implied or "Security Groups" in analysis.explicit


def test_tailor_adds_implied_skills_to_technical_skills(android_resume):
    analyzer = SkillAnalyzer(android_resume, role_track="ANDROID")
    jd = "Requires View Binding and Jetpack Compose."
    analysis = analyzer.analyze(jd.lower())
    tailored = analyzer.tailor_technical_skills(analysis)

    android_skills = " ".join(tailored.get("Android", [])).lower()
    assert "view binding" in android_skills


def test_scorer_does_not_mark_view_binding_missing(android_resume):
    raw = RawJob(
        source="test",
        title="Android Engineer",
        company="Google",
        location="Mumbai, India",
        description=(
            "Requirements: Kotlin, Android SDK, Jetpack Compose, View Binding, "
            "REST APIs, MVVM."
        ),
        job_url="https://example.com/job/vb",
    )
    job = normalize_job(raw)
    match = MatchScorer(android_resume, role_track="ANDROID").score(job)

    assert "View Binding" not in match.missing_requirements
    assert any("view binding" in s.lower() for s in match.matching_skills + match.implied_skills)


def test_scorer_marks_flutter_as_missing(android_resume):
    raw = RawJob(
        source="test",
        title="Cross-platform Mobile Engineer",
        company="Startup",
        location="Remote, India",
        description="Must have Flutter, Kotlin, and Kubernetes in production.",
        job_url="https://example.com/job/x",
    )
    job = normalize_job(raw)
    match = MatchScorer(android_resume, role_track="ANDROID").score(job)

    assert "Flutter" in match.missing_requirements
    assert "Kubernetes" in match.missing_requirements
