"""Env toggles that turn resume-profile search on or off."""

from job_agent.profiles import _profile_allowed, load_profiles, profile_search_flags
from job_agent.resume.tex_to_json import tex_to_master_resume


def test_flag_file_parses_true_and_false(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PROFILE_BASED_SEARCH=false\n"
        "PROFILE_ANDROID=true\n"
        "PROFILE_BACKEND=false\n",
        encoding="utf-8",
    )
    master, flags = profile_search_flags(env)
    assert master is False
    assert flags["android"] is True
    assert flags["backend"] is False


def test_profile_allowed_respects_master_and_per_profile_flag():
    flags = {"android": True, "backend": False}
    assert _profile_allowed({"android", "1-android"}, True, flags) is True
    assert _profile_allowed({"backend", "2-backend"}, True, flags) is False
    assert _profile_allowed({"android"}, False, flags) is False
    # A profile with no line stays on.
    assert _profile_allowed({"flutter", "3-flutter"}, True, flags) is True


def test_load_profiles_skips_flagged_off(monkeypatch):
    monkeypatch.setattr(
        "job_agent.profiles.profile_search_flags",
        lambda env_path=None: (True, {"backend": False, "android": True}),
    )
    ids = [p.id for p in load_profiles()]
    assert "android" in ids
    assert "backend" not in ids


def test_load_profiles_master_switch_off(monkeypatch):
    monkeypatch.setattr(
        "job_agent.profiles.profile_search_flags",
        lambda env_path=None: (False, {"android": True, "backend": True}),
    )
    assert load_profiles() == []


def test_load_profiles_ignores_search_flags_for_resume_templates(monkeypatch):
    """Resume agent needs masters even when PROFILE_ANDROID=false."""
    monkeypatch.setattr(
        "job_agent.profiles.profile_search_flags",
        lambda env_path=None: (True, {"android": False, "backend": True}),
    )
    search_ids = [p.id for p in load_profiles()]
    assert "android" not in search_ids
    assert "backend" in search_ids

    template_ids = [p.id for p in load_profiles(respect_search_flags=False)]
    assert "android" in template_ids
    assert "backend" in template_ids


def test_backend_tex_builds_resume_json():
    from pathlib import Path

    tex = Path("data/resume/profiles/2-Backend/master.tex").read_text(encoding="utf-8")
    resume = tex_to_master_resume(tex)
    assert "Java Backend" in resume.profile_summary
    assert resume.total_experience_years == 2
    assert len(resume.experience) == 3
    assert resume.experience[0].company == "Percipere"
    assert "Spring Boot" in resume.technical_skills["Frameworks"]
    assert any(c.name == "Java Programming" for c in resume.certifications)
    assert resume.projects[0].name == "Social Platform API"
