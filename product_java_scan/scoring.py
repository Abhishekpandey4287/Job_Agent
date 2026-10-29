"""Role match scoring for Java backend and Spring Boot openings."""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_MATCH_SCORE = 70
ACTIVE_SCORE = 85
ACTIVE_ROLES = frozenset(
    {
        "Spring Boot Developer",
        "Java Backend Developer",
        "Java + React Full Stack",
        "Microservices",
    }
)

_EXCLUDE_TITLE = re.compile(
    r"\b("
    r"recruiter|talent acquisition|sales|account executive|designer|"
    r"product manager|data scientist|data analyst|data engineer|ios|android|devops|"
    r"site reliability|\bsre\b|qa engineer|test engineer|sdet|"
    r"quality assurance|technical writer|legal|finance|human resources|"
    r"customer success|marketing|front[\s-]?end|support engineer|"
    r"security engineer|network engineer|machine learning|\bml engineer\b|"
    r"\bmanager\b|\bdirector\b|\bhead\b|\bvp\b"
    r")\b",
    re.I,
)
_INTERN = re.compile(r"\b(intern|internship|new grad)\b", re.I)
_JAVA = re.compile(r"\bjava\b", re.I)
_SPRING_BOOT = re.compile(r"spring\s*boot", re.I)
_SPRING = re.compile(r"\bspring\b", re.I)
_MICRO = re.compile(r"micro[\s-]?services?", re.I)
_REACT = re.compile(r"\breact(?:\.js|js)?\b", re.I)
_FULLSTACK = re.compile(r"full[\s-]?stack", re.I)
_BACKEND = re.compile(r"back[\s-]?end", re.I)
_ENGINEER = re.compile(
    r"software engineer|software developer|\bsde\b|\bdeveloper\b|\bengineer\b",
    re.I,
)
_SDE_TITLE = re.compile(r"\bsde\b|software development engineer|software engineer", re.I)
_GENERIC_ENGINEERING = re.compile(
    r"software engineer|software developer|\bsde\b|back[\s-]?end|"
    r"full[\s-]?stack|platform engineer|server engineer|\bdeveloper\b",
    re.I,
)

# Keep a role when its minimum is under 4 years. A 3–5 year range stays,
# because 4 years falls inside it. A role that starts at 4 or above does not.
MAX_EXPERIENCE_YEARS = 4

_EXPERIENCE = (
    re.compile(
        r"\b(\d+(?:\.\d+)?\s*(?:\+|plus)?\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?\s*(?:\+|plus)?\s*(?:years?|yrs?))\b",
        re.I,
    ),
    re.compile(r"\b(\d+(?:\.\d+)?\s*(?:\+|plus)\s*(?:years?|yrs?))\b", re.I),
    re.compile(
        r"\b((?:minimum|at least|min\.?)\s*\d+(?:\.\d+)?\s*(?:\+|plus)?\s*(?:years?|yrs?))\b",
        re.I,
    ),
    re.compile(
        r"\b((?:up to|maximum|max\.?|less than|under)\s*\d+(?:\.\d+)?\s*(?:years?|yrs?))\b",
        re.I,
    ),
    re.compile(r"\b(\d+(?:\.\d+)?\s*(?:years?|yrs?))\b", re.I),
)
_EXPERIENCE_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)",
    re.I,
)
_EXPERIENCE_PLUS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|plus)", re.I)
_EXPERIENCE_AT_LEAST = re.compile(r"(?:minimum|at least|min\.?)\s*(\d+(?:\.\d+)?)", re.I)
_EXPERIENCE_AT_MOST = re.compile(r"(?:up to|maximum|max\.?|less than|under)\s*(\d+(?:\.\d+)?)", re.I)
_EXPERIENCE_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", re.I)

_SKILLS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Java", re.compile(r"\bjava\b", re.I)),
    ("Spring Boot", re.compile(r"spring\s*boot", re.I)),
    ("Spring", re.compile(r"\bspring\b", re.I)),
    ("Microservices", re.compile(r"micro[\s-]?services?", re.I)),
    ("Hibernate", re.compile(r"\bhibernate\b", re.I)),
    ("JPA", re.compile(r"\bjpa\b", re.I)),
    ("React", re.compile(r"\breact(?:\.js|js)?\b", re.I)),
    ("Kafka", re.compile(r"\bkafka\b", re.I)),
    ("REST", re.compile(r"\brest(?:ful)?\b", re.I)),
    ("GraphQL", re.compile(r"\bgraphql\b", re.I)),
    ("SQL", re.compile(r"\bsql\b", re.I)),
    ("PostgreSQL", re.compile(r"\bpostgres(?:ql)?\b", re.I)),
    ("MySQL", re.compile(r"\bmysql\b", re.I)),
    ("MongoDB", re.compile(r"\bmongodb\b", re.I)),
    ("Redis", re.compile(r"\bredis\b", re.I)),
    ("Docker", re.compile(r"\bdocker\b", re.I)),
    ("Kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.I)),
    ("AWS", re.compile(r"\baws\b", re.I)),
    ("GCP", re.compile(r"\bgcp\b|google cloud", re.I)),
    ("Azure", re.compile(r"\bazure\b", re.I)),
    ("Maven", re.compile(r"\bmaven\b", re.I)),
    ("Gradle", re.compile(r"\bgradle\b", re.I)),
    ("JUnit", re.compile(r"\bjunit\b", re.I)),
    ("Mockito", re.compile(r"\bmockito\b", re.I)),
    ("RabbitMQ", re.compile(r"\brabbitmq\b", re.I)),
)


@dataclass(frozen=True)
class RoleMatch:
    score: int
    role: str


def is_excluded_title(title: str) -> bool:
    return bool(_EXCLUDE_TITLE.search(title or ""))


def is_generic_engineering_title(title: str) -> bool:
    return bool(_GENERIC_ENGINEERING.search(title or "")) and not is_excluded_title(title)


def classify_role(title: str, description: str = "") -> RoleMatch | None:
    title_text = title or ""
    if is_excluded_title(title_text):
        return None
    description_text = description or ""
    blob = f"{title_text}\n{description_text[:5000]}"
    java = bool(_JAVA.search(blob))
    spring_boot = bool(_SPRING_BOOT.search(blob))
    spring = spring_boot or bool(_SPRING.search(blob))
    if not java and not spring:
        return None

    java_title = bool(_JAVA.search(title_text))
    spring_boot_title = bool(_SPRING_BOOT.search(title_text))
    spring_title = spring_boot_title or bool(_SPRING.search(title_text))
    backend_title = bool(_BACKEND.search(title_text))
    engineer_title = bool(_ENGINEER.search(title_text))
    micro_title = bool(_MICRO.search(title_text))
    role = ""
    score = 0

    if _FULLSTACK.search(title_text) and java and bool(_REACT.search(blob)):
        role = "Java + React Full Stack"
        score = 93 if java_title or _REACT.search(title_text) else 84
    elif spring_boot_title or (
        spring_boot and (backend_title or java_title or engineer_title or spring_title)
    ):
        role = "Spring Boot Developer"
        score = 97 if spring_boot_title else 90
    elif java_title and backend_title:
        role = "Java Backend Developer"
        score = 95
    elif micro_title and (java or spring):
        role = "Microservices"
        score = 91
    elif backend_title and _SDE_TITLE.search(title_text) and (java or spring):
        role = "Backend SDE"
        score = 88 if java_title or spring_title else 76
    elif java and backend_title:
        role = "Java Backend Developer"
        score = 82
    elif (java_title or spring_title) and engineer_title:
        if spring_title and not java_title:
            role = "Spring Boot Developer"
            score = 92 if spring_boot_title else 84
        else:
            role = "Java Software Engineer"
            score = 86
    elif _MICRO.search(description_text) and (java or spring) and engineer_title:
        role = "Microservices"
        score = 80
    elif java and engineer_title:
        role = "Java Software Engineer"
        score = 74
    else:
        return None

    if _INTERN.search(title_text):
        score -= 30
    if score < MIN_MATCH_SCORE:
        return None
    return RoleMatch(score=min(score, 100), role=role)


def actively_hiring(matches: list[RoleMatch]) -> bool:
    return any(match.score >= ACTIVE_SCORE and match.role in ACTIVE_ROLES for match in matches)


def extract_experience(*parts: str) -> str:
    text = "\n".join(part for part in parts if part)
    for pattern in _EXPERIENCE:
        found = pattern.search(text)
        if found:
            return re.sub(r"\s+", " ", found.group(1)).strip()
    return ""


def experience_acceptable(phrase: str) -> bool | None:
    """True when the role can consider someone with under 4 years.

    ``3-5 years`` is kept because its minimum is 3 and 4 sits inside the range.
    ``5-8 years`` and ``4+ years`` are left out because they start at 4 or above.
    None means the posting did not state a requirement.
    """
    text = (phrase or "").strip()
    if not text:
        return None
    ranged = _EXPERIENCE_RANGE.search(text)
    if ranged:
        low = float(ranged.group(1))
        high = float(ranged.group(2))
        if high < low:
            low, high = high, low
        return low < MAX_EXPERIENCE_YEARS
    plus = _EXPERIENCE_PLUS.search(text)
    if plus:
        return float(plus.group(1)) < MAX_EXPERIENCE_YEARS
    at_least = _EXPERIENCE_AT_LEAST.search(text)
    if at_least:
        return float(at_least.group(1)) < MAX_EXPERIENCE_YEARS
    at_most = _EXPERIENCE_AT_MOST.search(text)
    if at_most:
        # "Up to 5 years" starts at 0, so 4 years is inside the range.
        return float(at_most.group(1)) > 0
    single = _EXPERIENCE_SINGLE.search(text)
    if single:
        return float(single.group(1)) < MAX_EXPERIENCE_YEARS
    return None


def extract_skills(*parts: str) -> str:
    text = "\n".join(part for part in parts if part)
    found = [label for label, pattern in _SKILLS if pattern.search(text)]
    return ", ".join(found[:15])
