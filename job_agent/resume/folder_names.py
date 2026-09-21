"""Resume folder and file naming — synced with Excel Role Category."""

import re
from typing import Dict, Set

ROLE_LABELS = {
    "ANDROID": "Android",
    "BACKEND": "Backend",
    "HYBRID": "Hybrid",
    "FLUTTER": "Flutter",
    "IOS": "iOS",
    "FULLSTACK": "Fullstack",
    "DEVOPS": "DevOps",
}


def format_role_label(role_category: str) -> str:
    """Excel Role Category label — known tracks + any dynamic profile id."""
    key = (role_category or "").strip().upper()
    if key in ROLE_LABELS:
        return ROLE_LABELS[key]
    cleaned = (role_category or "").strip()
    return cleaned.replace("_", " ").title() if cleaned else "Android"


def company_folder_name(company: str, max_len: int = 60) -> str:
    """Company subfolder under resumes/ — matches Excel Company column."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "", company.strip())
    return cleaned[:max_len] or "Unknown"


def _job_title_slug(job_title: str, max_len: int = 40) -> str:
    short = job_title.split(" - ")[0].split("|")[0].strip()
    slug = re.sub(r"[^\w\s-]", "", short)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:max_len] or "Role"


def resume_file_basename(
    role_category: str,
    job_title: str,
    used_in_company: Set[str],
) -> str:
    """Abhishek-Pandey_<Role>.pdf/.tex; title suffix only on collision."""
    label = format_role_label(role_category)
    base = f"Abhishek-Pandey_{label}"
    if base not in used_in_company:
        used_in_company.add(base)
        return base
    slug = _job_title_slug(job_title)
    candidate = f"{base}_{slug}"
    if candidate not in used_in_company:
        used_in_company.add(candidate)
        return candidate
    n = 2
    while f"{candidate}_{n}" in used_in_company:
        n += 1
    name = f"{candidate}_{n}"
    used_in_company.add(name)
    return name


def new_company_basename_tracker() -> Dict[str, Set[str]]:
    return {}


def new_company_cover_letter_tracker() -> Dict[str, Set[str]]:
    return {}


def basenames_for_company(
    tracker: Dict[str, Set[str]],
    company: str,
    profile_folder: str = "",
) -> Set[str]:
    folder = (
        f"{profile_folder}/{company_folder_name(company)}"
        if profile_folder
        else company_folder_name(company)
    )
    if folder not in tracker:
        tracker[folder] = set()
    return tracker[folder]


def cover_letters_for_company(
    tracker: Dict[str, Set[str]],
    company: str,
    profile_folder: str = "",
) -> Set[str]:
    folder = (
        f"{profile_folder}/{company_folder_name(company)}"
        if profile_folder
        else company_folder_name(company)
    )
    if folder not in tracker:
        tracker[folder] = set()
    return tracker[folder]


def cover_letter_filename(
    role_category: str,
    job_title: str,
    used_in_company: Set[str],
) -> str:
    """cover_letter.txt, or cover_letter_backend.txt / title suffix on collision."""
    if "cover_letter.txt" not in used_in_company:
        used_in_company.add("cover_letter.txt")
        return "cover_letter.txt"

    role = format_role_label(role_category).lower()
    candidate = f"cover_letter_{role}.txt"
    if candidate not in used_in_company:
        used_in_company.add(candidate)
        return candidate

    slug = _job_title_slug(job_title).lower()
    candidate = f"cover_letter_{role}_{slug}.txt"
    if candidate not in used_in_company:
        used_in_company.add(candidate)
        return candidate

    n = 2
    while f"cover_letter_{role}_{slug}_{n}.txt" in used_in_company:
        n += 1
    name = f"cover_letter_{role}_{slug}_{n}.txt"
    used_in_company.add(name)
    return name
