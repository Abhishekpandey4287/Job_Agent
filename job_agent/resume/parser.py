"""Resume parsing and loading from PDF or structured JSON."""

import json
import re
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.schemas import MasterResume, SeniorityLevel

logger = get_logger(__name__)


class ResumeParser:
    """Load and validate master resume from JSON (source of truth) or PDF."""

    def __init__(self, json_path: Optional[str] = None, pdf_path: Optional[str] = None):
        settings = get_settings()
        self.json_path = Path(json_path or settings.master_resume_json)
        self.pdf_path = Path(pdf_path or settings.resume_path)

    def load(self) -> MasterResume:
        if self.json_path.exists():
            return self._load_from_json()
        if self.pdf_path.exists():
            return self._load_from_pdf()
        raise FileNotFoundError(
            f"No resume found at {self.json_path} or {self.pdf_path}. "
            "Place master_resume.json or your PDF in data/resume/."
        )

    def _load_from_json(self) -> MasterResume:
        with open(self.json_path, encoding="utf-8") as f:
            data = json.load(f)
        resume = MasterResume.model_validate(data)
        logger.info("resume_loaded", source=str(self.json_path), name=resume.contact.name)
        return resume

    def _load_from_pdf(self) -> MasterResume:
        reader = PdfReader(str(self.pdf_path))
        raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        logger.warning(
            "resume_pdf_fallback",
            message="Loading from PDF — structured JSON is preferred as source of truth",
        )
        return MasterResume(
            contact=_parse_contact(raw_text),
            profile_summary=_extract_section(raw_text, "PROFILE", "EXPERIENCE"),
            total_experience_years=_estimate_experience_years(raw_text),
            experience=[],
            projects=[],
            technical_skills={},
            education=[],
            certifications=[],
            seniority_level=SeniorityLevel.MID,
            raw_text=raw_text,
        )

    def get_all_skills(self, resume: MasterResume) -> set[str]:
        skills: set[str] = set()
        for category_skills in resume.technical_skills.values():
            skills.update(s.lower().strip() for s in category_skills)
        for exp in resume.experience:
            skills.update(t.lower().strip() for t in exp.technologies)
        for proj in resume.projects:
            skills.update(t.lower().strip() for t in proj.technologies)
        return skills

    def save_json(self, resume: MasterResume, path: Optional[Path] = None) -> Path:
        target = path or self.json_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(resume.model_dump(), f, indent=2, ensure_ascii=False)
        return target


def _extract_section(text: str, start: str, end: str) -> str:
    pattern = rf"{start}\s*(.*?)\s*{end}"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parse_contact(text: str) -> dict:
    lines = text.strip().split("\n")
    name = lines[0].strip() if lines else "Unknown"
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone_match = re.search(r"\+?\d[\d\s-]{8,}\d", text)
    return {
        "name": name,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0).strip() if phone_match else "",
        "linkedin": "",
        "github": "",
        "portfolio": "",
        "location": "Mumbai, India",
    }


def _estimate_experience_years(text: str) -> float:
    if "Oct 2024" in text and "Present" in text:
        return 2.0
    return 1.5
