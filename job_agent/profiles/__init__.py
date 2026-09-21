"""Dynamic resume profile registry — auto-discovers folders under data/resume/profiles/.

Folder naming (priority prefix optional but recommended):
  profiles/1-Android/   → priority 1 (Excel rank prefers these when scores tie)
  profiles/2-Backend/   → priority 2
  profiles/flutter/     → priority 999 (unnumbered loads last)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.resume.parser import ResumeParser
from job_agent.schemas import MasterResume

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROFILES_DIR = PROJECT_ROOT / "data" / "resume" / "profiles"

# "1-Android", "2_Backend", "3 Backend"
_PRIORITY_FOLDER_RE = re.compile(r"^(\d+)[-_\s]+(.+)$")


def parse_profile_folder_name(folder_name: str) -> Tuple[int, str, str]:
    """Return (priority, canonical_id, display_label_guess) from a folder name."""
    name = (folder_name or "").strip()
    m = _PRIORITY_FOLDER_RE.match(name)
    if m:
        priority = int(m.group(1))
        rest = m.group(2).strip()
    else:
        priority = 999
        rest = name
    label_guess = rest.replace("_", " ").replace("-", " ").strip() or name
    # Title-case multi-word; keep simple ids lower for canonical form
    canonical = re.sub(r"[^a-z0-9]+", "-", label_guess.lower()).strip("-") or name.lower()
    display = " ".join(w.capitalize() if w.islower() else w for w in label_guess.split())
    if display.lower() == "android":
        display = "Android"
    elif display.lower() == "backend":
        display = "Backend"
    return priority, canonical, display or name


@dataclass
class ResumeProfile:
    """One searchable resume track (android, backend, flutter, …)."""

    id: str
    label: str
    path: Path
    resume: MasterResume
    tex_path: Path
    aliases: List[str] = field(default_factory=list)
    title_hints: List[str] = field(default_factory=list)
    priority: int = 999  # lower = higher priority in Excel / job list

    @property
    def role_track_label(self) -> str:
        """Legacy RoleTrack-compatible label for Excel / snapshot."""
        upper = self.id.upper()
        if upper in ("ANDROID", "BACKEND", "HYBRID"):
            return upper
        return upper

    @property
    def output_folder_name(self) -> str:
        """Folder used under output/.../resumes/ — e.g. 1-Android."""
        return f"{self.priority}-{self.label}"


def _legacy_fallback_profiles() -> List[ResumeProfile]:
    """If profiles/ is empty, fall back to classic master JSON paths."""
    settings = get_settings()
    profiles: List[ResumeProfile] = []
    android_json = PROJECT_ROOT / "data/resume/master_resume.json"
    backend_json = PROJECT_ROOT / "data/resume/master_backend.json"
    android_tex = PROJECT_ROOT / "data/resume/master_android.tex"
    backend_tex = PROJECT_ROOT / "data/resume/master_backend.tex"
    if android_json.exists():
        profiles.append(
            ResumeProfile(
                id="android",
                label="Android",
                path=android_json.parent,
                resume=ResumeParser(json_path=str(android_json)).load(),
                tex_path=android_tex if android_tex.exists() else Path(settings.master_resume_tex),
                aliases=["ANDROID", "Mobile"],
                priority=1,
            )
        )
    if backend_json.exists():
        profiles.append(
            ResumeProfile(
                id="backend",
                label="Backend",
                path=backend_json.parent,
                resume=ResumeParser(json_path=str(backend_json)).load(),
                tex_path=backend_tex if backend_tex.exists() else android_tex,
                aliases=["BACKEND", "Java"],
                priority=2,
            )
        )
    return profiles


def _ensure_profile_json(child: Path, settings) -> Path | None:
    """Return path to master_resume.json, generating it from master.tex if needed.

    Valid profile folder needs either:
      - master_resume.json, or
      - master.tex (JSON is auto-created next to it)
    """
    json_path = child / "master_resume.json"
    tex = child / "master.tex"
    if not tex.exists():
        legacy = PROJECT_ROOT / f"data/resume/master_{child.name}.tex"
        if legacy.exists():
            tex = legacy

    if json_path.exists():
        return json_path

    if not tex.exists():
        logger.warning(
            "profile_missing_json_and_tex",
            profile=child.name,
            hint="Add master.tex or master_resume.json",
        )
        return None

    try:
        from job_agent.resume.tex_to_json import convert_tex_file

        convert_tex_file(tex, json_path)
        logger.info(
            "profile_json_generated_from_tex",
            profile=child.name,
            tex=str(tex),
            json=str(json_path),
        )
        print(f"  ✓ Generated {child.name}/master_resume.json from {tex.name}")
        return json_path
    except Exception as e:
        logger.error(
            "profile_json_generate_failed",
            profile=child.name,
            error=str(e)[:200],
        )
        return None


def load_profiles(
    profiles_dir: Path | None = None,
    active: Optional[List[str]] = None,
) -> List[ResumeProfile]:
    """Discover every profile folder under data/resume/profiles/.

    A folder is active if it has master_resume.json, OR master.tex (JSON is
    auto-generated from the .tex when missing). No code changes needed to add
    flutter/, devops/, etc.
    """
    settings = get_settings()
    root = profiles_dir or Path(getattr(settings, "resume_profiles_dir", "") or DEFAULT_PROFILES_DIR)
    if not root.is_absolute():
        root = PROJECT_ROOT / root

    active_set = {a.strip().lower() for a in (active or []) if a.strip()}
    if not active_set:
        raw = (getattr(settings, "active_profiles", "") or "").strip()
        if raw:
            active_set = {a.strip().lower() for a in raw.split(",") if a.strip()}

    found: List[ResumeProfile] = []
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue

            folder_priority, folder_id, folder_label = parse_profile_folder_name(child.name)

            # active_profiles may list "android", "1-android", or "1-Android"
            if active_set:
                names = {
                    child.name.lower(),
                    folder_id,
                    f"{folder_priority}-{folder_id}",
                    f"{folder_priority}-{folder_label}".lower(),
                }
                if not (names & active_set):
                    continue

            json_path = _ensure_profile_json(child, settings)
            if not json_path:
                continue

            meta: Dict = {}
            meta_path = child / "profile.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning("profile_meta_invalid", profile=child.name, error=str(e))

            tex = child / "master.tex"
            if not tex.exists():
                # Prefer canonical id for legacy master_android.tex etc.
                legacy = PROJECT_ROOT / f"data/resume/master_{folder_id}.tex"
                if not legacy.exists():
                    legacy = PROJECT_ROOT / f"data/resume/master_{child.name}.tex"
                tex = legacy if legacy.exists() else Path(settings.master_resume_tex)

            try:
                resume = ResumeParser(json_path=str(json_path)).load()
            except Exception as e:
                logger.error("profile_load_failed", profile=child.name, error=str(e))
                continue

            pid = str(meta.get("id") or folder_id).strip().lower() or folder_id
            try:
                priority = int(meta.get("priority", folder_priority))
            except (TypeError, ValueError):
                priority = folder_priority

            found.append(
                ResumeProfile(
                    id=pid,
                    label=str(meta.get("label") or folder_label or child.name.title()),
                    path=child,
                    resume=resume,
                    tex_path=tex,
                    aliases=[str(a) for a in meta.get("aliases", [])],
                    title_hints=[str(t) for t in meta.get("title_hints", [])],
                    priority=priority,
                )
            )

    if not found:
        logger.warning("profiles_dir_empty_using_legacy", path=str(root))
        found = _legacy_fallback_profiles()

    # Lower number = higher priority (1 before 2); stable by id
    found.sort(key=lambda p: (p.priority, p.id))

    logger.info(
        "profiles_loaded",
        count=len(found),
        ids=[p.id for p in found],
        priorities={p.id: p.priority for p in found},
    )
    return found


def profiles_by_id(profiles: List[ResumeProfile]) -> Dict[str, ResumeProfile]:
    return {p.id: p for p in profiles}


def resolve_profile(
    profiles: List[ResumeProfile],
    profile_id: str | None = None,
    resume_used: str | None = None,
) -> ResumeProfile | None:
    """Resolve snapshot resume_used / profile id to a ResumeProfile."""
    by_id = profiles_by_id(profiles)
    key = (profile_id or resume_used or "").strip()
    if not key:
        return profiles[0] if profiles else None
    lower = key.lower()
    # "Hybrid" is a role track, not a profile — caller should re-score; don't force Android
    if lower == "hybrid":
        return None
    if lower in by_id:
        return by_id[lower]
    # Legacy "Android" / "Backend" / "Flutter" labels
    for p in profiles:
        if p.label.lower() == lower or lower in {a.lower() for a in p.aliases}:
            return p
        if p.id == lower:
            return p
    return None
