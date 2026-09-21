"""Pydantic schemas for resume, jobs, matching, and API."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ApplicationStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    ANALYZING = "ANALYZING"
    MATCHED = "MATCHED"
    RESUME_GENERATED = "RESUME_GENERATED"
    READY_TO_APPLY = "READY_TO_APPLY"
    APPLICATION_STARTED = "APPLICATION_STARTED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"


class MatchCategory(str, Enum):
    EXCELLENT = "Excellent Match"
    STRONG = "Strong Match"
    GOOD = "Good Match"
    MODERATE = "Moderate Match"
    WEAK = "Weak Match"


class EmploymentType(str, Enum):
    FULL_TIME = "Full-time"
    PART_TIME = "Part-time"
    CONTRACT = "Contract"
    INTERNSHIP = "Internship"
    UNKNOWN = "Unknown"


class SeniorityLevel(str, Enum):
    INTERN = "Intern"
    JUNIOR = "Junior"
    MID = "Mid-level"
    SENIOR = "Senior"
    LEAD = "Lead"
    STAFF = "Staff"
    UNKNOWN = "Unknown"


class DateVerification(str, Enum):
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


# --- Resume Schemas ---

class ContactInfo(BaseModel):
    name: str
    email: str
    phone: str
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    location: str = ""


class WorkExperience(BaseModel):
    company: str
    title: str
    location: str = ""
    start_date: str
    end_date: str
    is_current: bool = False
    technologies: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    description: str = ""
    technologies: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)
    year: str = ""


class Education(BaseModel):
    institution: str
    degree: str
    field: str = ""
    start_year: str = ""
    end_year: str = ""
    gpa: str = ""


class Certification(BaseModel):
    name: str
    issuer: str = ""
    year: str = ""


class MasterResume(BaseModel):
    contact: ContactInfo
    profile_summary: str
    total_experience_years: float
    experience: List[WorkExperience]
    projects: List[Project]
    technical_skills: Dict[str, List[str]]
    education: List[Education]
    certifications: List[Certification]
    seniority_level: SeniorityLevel = SeniorityLevel.MID
    raw_text: str = ""


# --- Job Schemas ---

class RawJob(BaseModel):
    """Job as returned from a source adapter before normalization."""
    source: str
    source_job_id: str = ""
    title: str
    company: str
    location: str
    description: str = ""
    job_url: str
    application_url: str = ""
    date_posted: Optional[datetime] = None
    date_posted_verified: DateVerification = DateVerification.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    seniority: SeniorityLevel = SeniorityLevel.UNKNOWN
    salary: str = ""
    is_remote: bool = False
    remote_india_eligible: Optional[bool] = None
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class NormalizedJob(BaseModel):
    job_id: str
    source: str
    source_job_id: str = ""
    title: str
    company: str
    location: str
    location_tier: int = 0  # 1=Mumbai, 2=India, 3=Remote India eligible
    description: str = ""
    job_url: str
    application_url: str = ""
    date_posted: Optional[datetime] = None
    date_posted_verified: DateVerification = DateVerification.UNKNOWN
    date_discovered: datetime = Field(default_factory=datetime.utcnow)
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    seniority: SeniorityLevel = SeniorityLevel.UNKNOWN
    salary: str = ""
    is_remote: bool = False
    remote_india_eligible: bool = False
    is_android_relevant: bool = False
    relevance_reason: str = ""
    company_url: str = ""
    matched_profiles: List[str] = Field(default_factory=list)
    # Employer-side identifier from the company's own ATS. Blank when not
    # established — source_job_id above stays the job-board identifier.
    career_portal: str = ""
    career_portal_job_id: str = ""
    career_portal_url: str = ""
    career_job_id_type: str = ""


class MatchScoreBreakdown(BaseModel):
    technical_skills: float = 0.0
    experience: float = 0.0
    role_alignment: float = 0.0
    seniority: float = 0.0
    location: float = 0.0
    industry: float = 0.0
    total: float = 0.0


class JobMatch(BaseModel):
    job: NormalizedJob
    match_score: float
    match_category: MatchCategory
    matching_skills: List[str] = Field(default_factory=list)
    implied_skills: List[str] = Field(default_factory=list)
    adjacent_skills: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    match_explanation: str = ""
    score_breakdown: MatchScoreBreakdown = Field(default_factory=MatchScoreBreakdown)


class TailoredResume(BaseModel):
    job_id: str
    company: str
    job_title: str
    profile_summary: str
    experience: List[WorkExperience]
    projects: List[Project]
    technical_skills: Dict[str, List[str]]
    education: List[Education]
    certifications: List[Certification]
    keywords_used: List[str] = Field(default_factory=list)


class ApplicationQuestion(BaseModel):
    question: str
    answer: str
    requires_user_input: bool = False


class PipelineSummary(BaseModel):
    search_started: datetime
    search_completed: Optional[datetime] = None
    sources_searched: int = 0
    jobs_discovered: int = 0
    jobs_after_deduplication: int = 0
    relevant_android_jobs: int = 0
    jobs_within_recency: int = 0
    strong_matches: int = 0
    resumes_generated: int = 0
    application_ready: int = 0
    errors: List[str] = Field(default_factory=list)
    output_path: str = ""


# --- API Schemas ---

class JobResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    job_id: str
    company: str
    title: str
    location: str
    match_score: Optional[float]
    match_category: Optional[str]
    application_status: str
    date_posted: Optional[datetime]
    date_discovered: datetime
    salary: str
    source: str
    job_url: str
    application_url: str


class DashboardStats(BaseModel):
    jobs_found_today: int = 0
    strong_matches: int = 0
    applications_ready: int = 0
    applications_submitted: int = 0
    interviews: int = 0
    rejections: int = 0
    offers: int = 0


class ConfigUpdate(BaseModel):
    target_roles: Optional[List[str]] = None
    target_locations: Optional[List[str]] = None
    resume_generation_threshold: Optional[int] = None
    top_jobs_count: Optional[int] = None
    job_recency_hours: Optional[int] = None
    daily_search_time: Optional[str] = None
    auto_apply_enabled: Optional[bool] = None
    notification_enabled: Optional[bool] = None
