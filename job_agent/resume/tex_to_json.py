"""Convert this project's master LaTeX resume (.tex) into master_resume.json.

Prefer .tex over PDF — the custom \\role / \\sk / \\bn macros parse cleanly.
PDF text extraction is lossy and should only be a last resort.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from job_agent.schemas import (
    Certification,
    ContactInfo,
    Education,
    MasterResume,
    Project,
    SeniorityLevel,
    WorkExperience,
)


def _strip_latex(text: str) -> str:
    """Remove common LaTeX commands while keeping readable text."""
    t = text
    t = t.replace(r"\%", "%")
    t = t.replace(r"\,", " ")
    t = t.replace(r"\&", "&")
    t = t.replace(r"\-", "-")
    t = re.sub(r"\$\\to\$", "→", t)
    t = re.sub(r"\$\\leftrightarrow\$", "↔", t)
    t = re.sub(r"\$\\cdot\$", "·", t)
    t = t.replace("–", "-").replace("—", "-")
    t = re.sub(r"\\bn\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", t)
    t = re.sub(
        r"\\(?:textbf|textit|emph|small|footnotesize|bfseries|color)\{([^{}]*)\}",
        r"\1",
        t,
    )
    t = re.sub(r"\\(?:textbf|textit|emph|small|footnotesize|bfseries|color)\b", "", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", t)
    t = t.replace("{", " ").replace("}", " ")
    t = re.sub(r"%.*$", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _split_tech(raw: str) -> List[str]:
    parts = re.split(r",|;", raw)
    out: List[str] = []
    for p in parts:
        s = _strip_latex(p).strip(" ·|")
        if s:
            out.append(s)
    return out


def _parse_dates(date_str: str) -> Tuple[str, str, bool]:
    raw = _strip_latex(date_str).replace("–", "-").replace("—", "-")
    raw = re.sub(r"\s+", " ", raw).strip()
    if " - " in raw:
        bits = [x.strip() for x in raw.split(" - ")]
        start, end = bits[0], bits[-1]
        if len(bits) >= 2 and not re.search(r"\d{4}", start):
            year = re.search(r"(\d{4})", end)
            if year:
                start = f"{start} {year.group(1)}"
    else:
        start, end = raw, ""
    is_current = "present" in end.lower() or end == ""
    if not end:
        end = "Present"
    return start, end, is_current


def _next_brace_args(text: str, start: int, count: int) -> Tuple[List[str], int]:
    """Read the next `count` {...} groups, skipping whitespace and % comments."""
    args: List[str] = []
    pos = start
    while len(args) < count and pos < len(text):
        while pos < len(text) and text[pos] in " \t\n\r%":
            if text[pos] == "%":
                nl = text.find("\n", pos)
                pos = len(text) if nl < 0 else nl + 1
            else:
                pos += 1
        if pos >= len(text) or text[pos] != "{":
            break
        depth = 0
        i = pos
        inner_start = pos + 1
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    args.append(text[inner_start:i])
                    pos = i + 1
                    break
            i += 1
        else:
            break
    return args, pos


def _extract_contact(tex: str) -> ContactInfo:
    email = ""
    m = re.search(r"mailto:([^}]+)", tex)
    if m:
        email = m.group(1).strip()

    phone = "+91 70459 33043"
    m = re.search(r"\+91[\\,\s]*\d[\d\\,\s]{8,}", tex)
    if m:
        phone = re.sub(r"[\\\s,]+", " ", m.group(0)).strip()
        phone = re.sub(r"\s+", " ", phone)

    linkedin = ""
    m = re.search(r"linkedin\.com/in/[^\s}]+", tex)
    if m:
        linkedin = m.group(0).rstrip("/")

    github = ""
    m = re.search(r"github\.com/[A-Za-z0-9_-]+", tex)
    if m:
        github = m.group(0)

    portfolio = ""
    m = re.search(r"(abhishek-pandey-dev\.vercel\.app)", tex, re.I)
    if m:
        portfolio = m.group(1)

    name = "Abhishek Pandey"
    m = re.search(r"\\bfseries\\color\{NavyTx\}\s*([A-Z][A-Z\s]+)\\", tex)
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip().title()

    return ContactInfo(
        name=name,
        email=email or "abhishekvpandeyxyz@gmail.com",
        phone=phone,
        linkedin=linkedin,
        github=github,
        portfolio=portfolio,
        location="Mumbai, India",
    )


def _extract_profile(tex: str) -> str:
    m = re.search(r"%%PROFILE_START%%(.*?)%%PROFILE_END%%", tex, re.S)
    block = m.group(1) if m else ""
    if not block:
        m = re.search(r"\\section\{Profile\}(.*?)\\section\{", tex, re.S)
        block = m.group(1) if m else ""
    block = re.sub(r"\\section\{Profile\}", "", block)
    return _strip_latex(block)


def _extract_roles_and_lists(tex: str, section: str) -> List[dict]:
    m = re.search(
        rf"\\section\{{{re.escape(section)}\}}(.*?)(?=\\section\{{|\Z)",
        tex,
        re.S,
    )
    if not m:
        return []
    body = m.group(1)
    items: List[dict] = []

    for rm in re.finditer(r"\\role\b", body):
        args, pos = _next_brace_args(body, rm.end(), 4)
        if len(args) < 4:
            continue

        company = _strip_latex(args[0])
        meta = _strip_latex(args[2])
        dates = _strip_latex(args[3])

        next_role = body.find(r"\role", rm.end())
        chunk = body[pos : next_role if next_role > 0 else len(body)]
        bullets: List[str] = []
        for bm in re.finditer(r"\\item\s+(.*?)(?=\\item\s+|\\end\{blist\}|\Z)", chunk, re.S):
            b = _strip_latex(bm.group(1))
            if b:
                bullets.append(b)

        title = meta
        techs: List[str] = []
        if "·" in meta:
            title, tech_part = meta.split("·", 1)
            title = title.strip()
            techs = _split_tech(tech_part)
        elif section.lower().startswith("project"):
            techs = _split_tech(meta)
            title = company

        items.append(
            {
                "company_or_name": company,
                "title_or_meta": title.strip(),
                "technologies": techs,
                "dates": dates,
                "bullets": bullets,
            }
        )
    return items


def _estimate_years(profile: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*years?\s+of\s+experience", profile, re.I)
    if m:
        return float(m.group(1))
    return 2.0


def tex_to_master_resume(tex: str) -> MasterResume:
    contact = _extract_contact(tex)
    profile = _extract_profile(tex)

    experience: List[WorkExperience] = []
    for e in _extract_roles_and_lists(tex, "Experience"):
        start, end, is_current = _parse_dates(e["dates"])
        experience.append(
            WorkExperience(
                company=e["company_or_name"],
                title=e["title_or_meta"],
                location="",
                start_date=start,
                end_date=end,
                is_current=is_current,
                technologies=e["technologies"],
                bullets=e["bullets"],
            )
        )

    projects: List[Project] = []
    for p in _extract_roles_and_lists(tex, "Projects"):
        projects.append(
            Project(
                name=p["company_or_name"],
                description=p["company_or_name"],
                technologies=p["technologies"],
                bullets=p["bullets"],
                year=_strip_latex(p["dates"]),
            )
        )

    skills: Dict[str, List[str]] = {}
    for sm in re.finditer(r"\\sk\{([^{}]*)\}\{([^{}]*)\}", tex):
        cat = _strip_latex(sm.group(1)).rstrip(":")
        skills[cat] = _split_tech(sm.group(2))

    certs: List[Certification] = []
    cert_section = re.search(
        r"\\section\{Certifications.*?\}(.*?)(?=\\end\{document\}|\Z)",
        tex,
        re.S,
    )
    if cert_section:
        for cm in re.finditer(
            r"\\href\{[^{}]*\}\{\\color\{BlueAc\}([^{}]*)\}",
            cert_section.group(1),
        ):
            label = _strip_latex(cm.group(1))
            issuer = ""
            name = label
            if "—" in label:
                name, issuer = [x.strip() for x in label.split("—", 1)]
            elif " - " in label:
                name, issuer = [x.strip() for x in label.split(" - ", 1)]
            certs.append(Certification(name=name, issuer=issuer, year=""))

    education = [
        Education(
            institution="Dr. JJ Magdum College of Engineering",
            degree="B.Tech",
            field="Computer Science & Engineering",
            start_year="2020",
            end_year="2024",
            gpa="8.54 / 10",
        )
    ]
    em = re.search(
        r"\\bn\{(Dr\.?.*?College[^}]*)\}.*?B\.Tech,\s*([^\\|]+).*?\\bn\{([^}]*)\}.*?(\d{4})\s*[-–]\s*(\d{4})",
        tex,
        re.S,
    )
    if em:
        education = [
            Education(
                institution=_strip_latex(em.group(1)),
                degree="B.Tech",
                field=_strip_latex(em.group(2)).strip(),
                start_year=em.group(4),
                end_year=em.group(5),
                gpa=_strip_latex(em.group(3)).replace("/ ", " / "),
            )
        ]

    return MasterResume(
        contact=contact,
        profile_summary=profile,
        total_experience_years=_estimate_years(profile),
        experience=experience,
        projects=projects,
        technical_skills=skills,
        education=education,
        certifications=certs,
        seniority_level=SeniorityLevel.MID,
        raw_text="",
    )


def convert_tex_file(tex_path: Path, json_path: Path) -> MasterResume:
    tex = tex_path.read_text(encoding="utf-8")
    resume = tex_to_master_resume(tex)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = resume.model_dump(mode="json")
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return resume
