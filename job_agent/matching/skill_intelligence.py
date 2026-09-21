"""JD-driven skill classification beyond the master resume inventory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from job_agent.schemas import MasterResume

SKILL_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "resume" / "skill_knowledge.json"
)

# Core skills checked in matching (shared with scorer)
CORE_JD_SKILLS: Dict[str, List[str]] = {
    "kotlin": ["kotlin"],
    "java": ["java"],
    "android sdk": ["android sdk", "android development", "native android"],
    "jetpack": ["jetpack", "android jetpack"],
    "jetpack compose": ["jetpack compose", "compose ui", "compose"],
    "mvvm": ["mvvm", "model-view-viewmodel"],
    "clean architecture": ["clean architecture", "clean arch"],
    "hilt": ["hilt", "dagger hilt", "dagger/hilt"],
    "dagger": ["dagger", "dagger hilt", "dagger/hilt", "dependency injection"],
    "coroutines": ["coroutines", "kotlin coroutines"],
    "flow": ["flow", "stateflow", "sharedflow"],
    "retrofit": ["retrofit"],
    "okhttp": ["okhttp", "ok http"],
    "room": ["room", "room database"],
    "navigation": ["navigation component", "navigation"],
    "rest api": ["rest api", "rest apis", "restful"],
    "firebase": ["firebase", "fcm", "firebase crashlytics"],
    "testing": ["unit test", "testing", "junit", "espresso", "mockito"],
    "ci/cd": ["ci/cd", "github actions", "jenkins", "azure devops", "continuous integration"],
    "git": ["git", "github", "gitlab"],
    "spring boot": ["spring boot"],
    "aws": ["aws", "amazon web services"],
    "docker": ["docker", "containerization"],
    "ml kit": ["ml kit", "machine learning kit"],
    "webrtc": ["webrtc"],
    "exoplayer": ["exoplayer"],
    "camerax": ["camerax", "camera x"],
    "redis": ["redis"],
    "postgresql": ["postgresql", "postgres"],
    "mysql": ["mysql"],
    "xml": ["xml"],
}


class SkillClassification(str, Enum):
    EXPLICIT = "explicit"
    IMPLIED = "implied"
    ADJACENT = "adjacent"
    UNSUPPORTED = "unsupported"


@dataclass
class ClassifiedSkill:
    name: str
    classification: SkillClassification


@dataclass
class SkillAnalysis:
    explicit: List[str] = field(default_factory=list)
    implied: List[str] = field(default_factory=list)
    adjacent: List[str] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)

    @property
    def matching_skills(self) -> List[str]:
        return self.explicit + self.implied

    @property
    def missing_requirements(self) -> List[str]:
        return self.unsupported


def _load_knowledge() -> dict:
    if SKILL_KNOWLEDGE_PATH.exists():
        return json.loads(SKILL_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    return {}


def _collect_resume_skills(resume: MasterResume) -> Set[str]:
    skills: Set[str] = set()
    for category in resume.technical_skills.values():
        for skill in category:
            skills.add(skill.lower())
    for exp in resume.experience:
        for tech in exp.technologies:
            skills.add(tech.lower())
    for proj in resume.projects:
        for tech in proj.technologies:
            skills.add(tech.lower())
    return skills


def _skill_in_resume(resume_skills: Set[str], aliases: List[str]) -> bool:
    joined = " ".join(resume_skills)
    return any(alias in joined or any(alias in rs for rs in resume_skills) for alias in aliases)


def _in_jd(jd_text: str, aliases: List[str]) -> bool:
    return any(alias in jd_text for alias in aliases)


def _display_name(key: str) -> str:
    if key in CORE_JD_SKILLS:
        return key.title()
    return key


class SkillAnalyzer:
    """Classify JD requirements against documented + inferable candidate skills."""

    def __init__(self, resume: MasterResume, role_track: str = "ANDROID"):
        self.resume = resume
        self.role_track = role_track.upper()
        self.resume_skills = _collect_resume_skills(resume)
        self.knowledge = _load_knowledge()
        self._android_implied: Dict[str, List[str]] = self.knowledge.get("android_implied", {})
        self._backend_implied: Dict[str, List[str]] = self.knowledge.get("backend_implied", {})
        self._blocked: Dict[str, List[str]] = self.knowledge.get("blocked_inference", {})
        self._adjacent: Dict[str, dict] = self.knowledge.get("adjacent", {})
        self._categories: Dict[str, str] = self.knowledge.get("skill_categories", {})

    def analyze(self, jd_text: str) -> SkillAnalysis:
        jd_lower = jd_text.lower()
        seen: Set[str] = set()
        analysis = SkillAnalysis()

        for skill_key, aliases in CORE_JD_SKILLS.items():
            if not _in_jd(jd_lower, aliases):
                continue
            display = _display_name(skill_key)
            if display.lower() in seen:
                continue
            seen.add(display.lower())
            classification = self._classify_core_skill(skill_key, aliases, jd_lower)
            self._append(analysis, display, classification)

        for name, aliases in self._blocked.items():
            if not _in_jd(jd_lower, aliases):
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            if _skill_in_resume(self.resume_skills, aliases):
                analysis.explicit.append(name)
            else:
                analysis.unsupported.append(name)

        implied_pool = self._implied_pool()
        for name, aliases in implied_pool.items():
            if not _in_jd(jd_lower, aliases):
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            if _skill_in_resume(self.resume_skills, aliases):
                analysis.explicit.append(name)
            else:
                analysis.implied.append(name)

        for name, cfg in self._adjacent.items():
            aliases = cfg.get("aliases", [])
            if not _in_jd(jd_lower, aliases):
                continue
            if name.lower() in seen:
                continue
            tracks = [t.upper() for t in cfg.get("tracks", [])]
            if self.role_track not in tracks and self.role_track != "HYBRID":
                continue
            seen.add(name.lower())
            if _skill_in_resume(self.resume_skills, aliases):
                analysis.explicit.append(name)
            elif self._adjacent_prerequisites_met(cfg):
                analysis.adjacent.append(name)
            else:
                analysis.unsupported.append(name)

        return analysis

    def _implied_pool(self) -> Dict[str, List[str]]:
        if self.role_track == "BACKEND":
            return dict(self._backend_implied)
        if self.role_track == "HYBRID":
            merged = dict(self._android_implied)
            merged.update(self._backend_implied)
            return merged
        return dict(self._android_implied)

    def _classify_core_skill(
        self, skill_key: str, aliases: List[str], jd_lower: str
    ) -> SkillClassification:
        if _skill_in_resume(self.resume_skills, aliases):
            return SkillClassification.EXPLICIT

        for blocked_name, blocked_aliases in self._blocked.items():
            if skill_key in blocked_name.lower() or any(a in aliases for a in blocked_aliases):
                return SkillClassification.UNSUPPORTED

        implied = self._implied_pool()
        for name, imp_aliases in implied.items():
            if any(a in aliases for a in imp_aliases) or skill_key in name.lower():
                return SkillClassification.IMPLIED

        return SkillClassification.UNSUPPORTED

    def _adjacent_prerequisites_met(self, cfg: dict) -> bool:
        requires = cfg.get("requires_any", [])
        joined = " ".join(self.resume_skills)
        return any(req.lower() in joined for req in requires)

    @staticmethod
    def _append(analysis: SkillAnalysis, name: str, classification: SkillClassification) -> None:
        bucket = {
            SkillClassification.EXPLICIT: analysis.explicit,
            SkillClassification.IMPLIED: analysis.implied,
            SkillClassification.ADJACENT: analysis.adjacent,
            SkillClassification.UNSUPPORTED: analysis.unsupported,
        }[classification]
        if name not in bucket:
            bucket.append(name)

    def tailor_technical_skills(self, analysis: SkillAnalysis) -> Dict[str, List[str]]:
        """Prioritize JD-matched skills and add implied ones to a copy of master skills."""
        tailored = {cat: list(skills) for cat, skills in self.resume.technical_skills.items()}
        existing = _collect_resume_skills(self.resume)

        # Move matching / explicit skills to the front of their categories
        priority = [
            s.lower()
            for s in (analysis.matching_skills + analysis.explicit + analysis.implied)
            if s
        ]
        for cat, skills in list(tailored.items()):
            if not skills or not priority:
                continue
            front, rest = [], []
            for sk in skills:
                if any(p in sk.lower() or sk.lower() in p for p in priority):
                    front.append(sk)
                else:
                    rest.append(sk)
            tailored[cat] = front + rest

        for skill_name in analysis.implied:
            if any(skill_name.lower() in e for e in existing):
                continue
            category = self._resolve_category(skill_name)
            if category not in tailored:
                tailored[category] = []
            if not any(skill_name.lower() in s.lower() for s in tailored[category]):
                tailored[category].insert(0, skill_name)

        return tailored

    def _resolve_category(self, skill_name: str) -> str:
        if skill_name in self._categories:
            cat = self._categories[skill_name]
            if cat in self.resume.technical_skills:
                return cat
        track = self.role_track
        if track in ("ANDROID", "HYBRID") and "Android" in self.resume.technical_skills:
            android_hints = {
                "view", "data binding", "fragment", "activity", "recycler",
                "material", "gradle", "espresso", "junit", "mockito", "proguard", "xml",
            }
            if any(h in skill_name.lower() for h in android_hints):
                return "Android"
        if "Cloud & DevOps" in self.resume.technical_skills:
            cloud_hints = {"aws", "ec2", "rds", "dynamo", "docker", "jenkins", "ci/cd", "cloud", "s3", "iam"}
            if any(h in skill_name.lower() for h in cloud_hints):
                return "Cloud & DevOps"
        if "Cloud" in self.resume.technical_skills:
            return "Cloud"
        if "Build & Tools" in self.resume.technical_skills:
            return "Build & Tools"
        if "Integration" in self.resume.technical_skills:
            return "Integration"
        if "Architecture" in self.resume.technical_skills:
            return "Architecture"
        return next(iter(self.resume.technical_skills.keys()), "Languages")

    def build_explanation(self, analysis: SkillAnalysis, score: float, category_label: str) -> str:
        parts: List[str] = []
        if analysis.matching_skills:
            parts.append(f"Strong alignment on: {', '.join(analysis.matching_skills[:8])}.")
        if analysis.implied:
            parts.append(
                f"Standard domain skills also relevant: {', '.join(analysis.implied[:5])}."
            )
        if analysis.adjacent:
            parts.append(
                f"Transferable/adjacent exposure: {', '.join(analysis.adjacent[:4])}."
            )
        if analysis.unsupported:
            parts.append(f"Unsupported gaps: {', '.join(analysis.unsupported[:5])}.")
        parts.append(f"Overall score: {score:.0f}/100 ({category_label}).")
        return " ".join(parts)


RESUME_INTELLIGENCE_RULES = """
RESUME INTELLIGENCE — Skills Beyond the Master Resume

The master resume is a documented representation of experience, NOT an exhaustive skill inventory.

Skill classification for each JD requirement:
1. EXPLICIT — clearly present in the master resume
2. IMPLIED — standard domain skill reasonably inferable from Android/Backend work (e.g. View Binding for Android dev)
3. ADJACENT — transferable but not deep experience; never claim years/projects
4. UNSUPPORTED — genuinely outside background (e.g. Flutter, React Native, iOS Swift, Kubernetes, Terraform)

Rules:
- Do NOT treat "not on master resume" as "candidate does not know it" for standard domain skills
- Include IMPLIED skills naturally in Technical Skills or existing bullets — never invent projects, metrics, or years
- NEVER claim professional experience duration for skills not explicitly documented
- NEVER fabricate companies, projects, achievements, certifications, or titles
- Do NOT copy the JD verbatim — emphasize the strongest truthful profile for this role
- Docker, Jenkins, AWS (EC2, ALB, CloudFormation, RDS, DynamoDB, security groups) may be emphasized when JD-relevant
- Block over-inference: do not claim Flutter, React Native, iOS, Kubernetes, Terraform without evidence
"""


def merge_skill_analysis_for_scoring(
    analysis: SkillAnalysis,
) -> Tuple[List[str], List[str], Optional[float]]:
    """Return matching list, missing list, and effective match ratio for scoring.

    A ratio of None means the JD carried no recognizable skill signal (usually an
    empty description), so the caller must score it as unknown rather than perfect.
    """
    jd_total = (
        len(analysis.explicit)
        + len(analysis.implied)
        + len(analysis.adjacent)
        + len(analysis.unsupported)
    )
    if jd_total == 0:
        return analysis.matching_skills, analysis.missing_requirements, None

    effective_matches = len(analysis.explicit) + len(analysis.implied) + 0.5 * len(analysis.adjacent)
    ratio = effective_matches / jd_total
    matching = analysis.matching_skills + [f"{s} (adjacent)" for s in analysis.adjacent]
    return matching, analysis.missing_requirements, ratio
