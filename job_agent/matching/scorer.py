"""Deterministic match scoring engine."""

import re
from typing import List

from job_agent.matching.skill_intelligence import (
    SkillAnalyzer,
    merge_skill_analysis_for_scoring,
)
from job_agent.schemas import (
    JobMatch,
    MatchCategory,
    MatchScoreBreakdown,
    MasterResume,
    NormalizedJob,
    SeniorityLevel,
)

MOBILE_TRACKS = {"ANDROID", "FLUTTER", "IOS", "MOBILE", "HYBRID"}

# Neutral technical score for a JD with no recognizable skill signal. An empty
# description is an absence of evidence, so it must not outrank a real JD that
# was actually assessed and fell short.
UNKNOWN_TECHNICAL_SCORE = 17.5

# Title/description signals that a job belongs to a given resume track
ROLE_TITLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ANDROID": ("android", "kotlin", "mobile", "jetpack", "compose"),
    "BACKEND": (
        "backend", "back-end", "back end", "server-side", "server side",
        "java", "spring", "microservice", "api", "node", "nest",
        "python", "django", "fastapi", "golang", "php", "laravel",
        "full stack", "full-stack", "fullstack",
    ),
    "JAVA": ("java", "spring", "jvm", "backend"),
    "FLUTTER": ("flutter", "dart", "cross-platform", "cross platform", "mobile"),
    "IOS": ("ios", "swift", "objective-c", "mobile"),
    "MOBILE": ("mobile", "android", "ios", "flutter", "react native"),
    "DEVOPS": ("devops", "sre", "kubernetes", "ci/cd", "infrastructure", "platform"),
    "CLOUD": ("cloud", "aws", "gcp", "azure", "infrastructure"),
    "FULLSTACK": ("full stack", "full-stack", "fullstack", "frontend", "backend"),
    "HYBRID": (
        "android", "kotlin", "mobile", "backend", "spring", "java",
        "full stack", "full-stack", "fullstack",
    ),
}

SENIORITY_ORDER = {
    SeniorityLevel.INTERN: 0,
    SeniorityLevel.JUNIOR: 1,
    SeniorityLevel.MID: 2,
    SeniorityLevel.SENIOR: 3,
    SeniorityLevel.LEAD: 4,
    SeniorityLevel.STAFF: 5,
    SeniorityLevel.UNKNOWN: 2,
}


class MatchScorer:
    def __init__(self, resume: MasterResume, role_track: str = "ANDROID"):
        self.resume = resume
        self.role_track = role_track.upper()
        self.analyzer = SkillAnalyzer(resume, role_track=self.role_track)

    def score(self, job: NormalizedJob) -> JobMatch:
        jd_text = f"{job.title} {job.description}".lower()
        analysis = self.analyzer.analyze(jd_text)
        matching, missing, tech_ratio = merge_skill_analysis_for_scoring(analysis)

        tech_score = self._score_technical(tech_ratio)
        exp_score = self._score_experience(jd_text)
        role_score = self._score_role_alignment(job)
        seniority_score = self._score_seniority(job.seniority)
        location_score = self._score_location(job)
        industry_score = self._score_industry(jd_text)

        breakdown = MatchScoreBreakdown(
            technical_skills=tech_score,
            experience=exp_score,
            role_alignment=role_score,
            seniority=seniority_score,
            location=location_score,
            industry=industry_score,
            total=round(tech_score + exp_score + role_score + seniority_score + location_score + industry_score, 1),
        )

        category = self._categorize(breakdown.total)
        explanation = self.analyzer.build_explanation(analysis, breakdown.total, category.value)

        return JobMatch(
            job=job,
            match_score=breakdown.total,
            match_category=category,
            matching_skills=matching,
            implied_skills=analysis.implied,
            adjacent_skills=analysis.adjacent,
            missing_requirements=missing,
            match_explanation=explanation,
            score_breakdown=breakdown,
        )

    def _score_technical(self, ratio: float | None) -> float:
        if ratio is None:
            return UNKNOWN_TECHNICAL_SCORE
        if ratio >= 1.0:
            return 35.0
        return min(35.0, ratio * 35.0)

    def _score_experience(self, jd_text: str) -> float:
        years_required = self._extract_years_required(jd_text)
        candidate_years = self.resume.total_experience_years

        if years_required is None:
            return 15.0

        if candidate_years >= years_required:
            return 20.0
        if candidate_years >= years_required - 1:
            return 16.0
        if candidate_years >= years_required - 2:
            return 12.0
        return max(0.0, 20.0 * (candidate_years / years_required))

    def _extract_years_required(self, text: str) -> float | None:
        patterns = [
            r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)",
            r"(?:minimum|min|at least)\s*(\d+)\s*(?:years?|yrs?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return float(match.group(1))
        return None

    def _score_role_alignment(self, job: NormalizedJob) -> float:
        """Reward titles that name the track this resume targets.

        Every profile needs its own keywords — keying only off
        is_android_relevant left Backend/Flutter roles permanently at 0/20.
        """
        title_lower = (job.title or "").lower()
        keywords = ROLE_TITLE_KEYWORDS.get(
            self.role_track, ROLE_TITLE_KEYWORDS["ANDROID"]
        )

        if any(k in title_lower for k in keywords):
            return 20.0

        # Track-relevant but the title doesn't say so (matched via description)
        desc_lower = (job.description or "")[:3000].lower()
        if any(k in desc_lower for k in keywords):
            return 14.0

        if job.is_android_relevant and self.role_track in MOBILE_TRACKS:
            return 14.0

        return 0.0

    def _score_seniority(self, job_seniority: SeniorityLevel) -> float:
        candidate = self.resume.seniority_level
        job_level = SENIORITY_ORDER.get(job_seniority, 2)
        cand_level = SENIORITY_ORDER.get(candidate, 2)

        diff = abs(job_level - cand_level)
        if diff == 0:
            return 10.0
        if diff == 1:
            return 7.0
        if diff == 2:
            return 4.0
        return 1.0

    def _score_location(self, job: NormalizedJob) -> float:
        tier_scores = {1: 10.0, 2: 8.0, 3: 7.0, 0: 0.0}
        return tier_scores.get(job.location_tier, 0.0)

    def _score_industry(self, jd_text: str) -> float:
        domains = {
            "fintech": ["fintech", "banking", "financial", "payment"],
            "enterprise": ["enterprise", "sap", "warehouse", "b2b"],
            "social": ["social media", "social network", "messaging"],
            "e-commerce": ["e-commerce", "ecommerce", "retail", "shopping"],
        }
        resume_text = " ".join(
            [self.resume.profile_summary]
            + [b for e in self.resume.experience for b in e.bullets]
            + [b for p in self.resume.projects for b in p.bullets]
        ).lower()

        for domain, keywords in domains.items():
            if any(k in jd_text for k in keywords) and any(k in resume_text for k in keywords):
                return 5.0
        return 2.0

    def _categorize(self, score: float) -> MatchCategory:
        if score >= 90:
            return MatchCategory.EXCELLENT
        if score >= 80:
            return MatchCategory.STRONG
        if score >= 70:
            return MatchCategory.GOOD
        if score >= 60:
            return MatchCategory.MODERATE
        return MatchCategory.WEAK
