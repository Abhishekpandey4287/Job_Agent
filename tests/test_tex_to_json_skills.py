"""Regression: Backend master.tex skills must not pick up macro docs or split parens."""

from pathlib import Path

from job_agent.resume.tex_to_json import _extract_skills, _split_tech, tex_to_master_resume

BACKEND_TEX = Path(__file__).resolve().parents[1] / "data/resume/profiles/2-Backend/master.tex"


def test_split_tech_keeps_parentheses():
    items = _split_tech(
        "PostgreSQL, MySQL, Redis (Streams, caching, rate limiting), Liquibase"
    )
    assert items == [
        "PostgreSQL",
        "MySQL",
        "Redis (Streams, caching, rate limiting)",
        "Liquibase",
    ]


def test_backend_skills_ignore_macro_docs():
    tex = BACKEND_TEX.read_text(encoding="utf-8")
    skills = _extract_skills(tex)
    assert "Label" not in skills
    assert list(skills.keys()) == [
        "Languages",
        "Frameworks",
        "APIs & Integration",
        "Databases",
        "Cloud & DevOps",
        "Testing & Build",
        "Core Concepts",
    ]
    assert skills["Databases"][2] == "Redis (Streams, caching, rate limiting)"
    assert skills["Cloud & DevOps"][-1] == "GitHub Actions"


def test_backend_master_resume_roundtrip_skills():
    resume = tex_to_master_resume(BACKEND_TEX.read_text(encoding="utf-8"))
    assert "Label" not in resume.technical_skills
    assert "Redis (Streams, caching, rate limiting)" in resume.technical_skills["Databases"]
