"""Records produced by one company scan."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Board:
    kind: str
    slug: str = ""
    tenant: str = ""
    cluster: str = ""
    site: str = ""
    host: str = ""

    @property
    def label(self) -> str:
        if self.kind == "workday":
            return f"workday:{self.tenant}/{self.site}"
        if self.kind == "oracle":
            return f"oracle:{self.site}"
        return f"{self.kind}:{self.slug}"


@dataclass
class RawPosting:
    title: str
    location: str
    url: str
    description: str = ""
    posting_date: str = ""
    verified_via: str = ""


@dataclass
class BoardFetch:
    label: str
    postings: list[RawPosting] = field(default_factory=list)
    complete: bool = False
    error: str = ""


@dataclass(frozen=True)
class FoundJob:
    company: str
    title: str
    location: str
    experience: str
    skills: str
    url: str
    posting_date: str
    match_score: int
    role_match: str
    verified_via: str
    location_check: str


@dataclass
class CompanyResult:
    company: str
    target_profiles: str
    portal_url: str
    final_url: str
    hiring_status: str
    scanning_failure: str
    needs_rescan: bool
    notes: str
    ats_checked: str
    jobs: list[FoundJob]
    portal_loaded: bool
    listing_complete: bool
    actively_hiring: bool
