"""Tests for resume folder and file naming."""

from job_agent.resume.folder_names import (
    company_folder_name,
    format_role_label,
    resume_file_basename,
)


def test_company_folder_matches_excel():
    assert company_folder_name("Google") == "Google"
    assert company_folder_name("ACL Digital") == "ACL Digital"


def test_role_label_for_excel_and_filename():
    assert format_role_label("ANDROID") == "Android"
    assert format_role_label("BACKEND") == "Backend"
    assert format_role_label("HYBRID") == "Hybrid"


def test_resume_basename_clean():
    used: set[str] = set()
    assert resume_file_basename("ANDROID", "Android Engineer", used) == "Abhishek-Pandey_Android"
    assert "Abhishek-Pandey_Android" in used


def test_resume_basename_collision_uses_title():
    used: set[str] = set()
    first = resume_file_basename("ANDROID", "Android Engineer", used)
    second = resume_file_basename("ANDROID", "Senior Android Engineer", used)
    assert first == "Abhishek-Pandey_Android"
    assert second == "Abhishek-Pandey_Android_Senior-Android-Engineer"
    assert "Custom" not in second


def test_cover_letter_filename_default_and_collision():
    from job_agent.resume.folder_names import cover_letter_filename

    used: set[str] = set()
    assert cover_letter_filename("ANDROID", "Android Engineer", used) == "cover_letter.txt"
    assert cover_letter_filename("BACKEND", "Java Developer", used) == "cover_letter_backend.txt"
    third = cover_letter_filename("ANDROID", "Senior Android Engineer", used)
    assert third == "cover_letter_android.txt"
    fourth = cover_letter_filename("ANDROID", "Staff Android Engineer", used)
    assert fourth.startswith("cover_letter_android_")
