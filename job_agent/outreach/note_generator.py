"""Generate truthful LinkedIn connection notes (max 300 chars)."""

from job_agent.ai.llm_service import LLMService
from job_agent.outreach.schemas import ContactPriority, LinkedInContact
from job_agent.schemas import JobMatch, MasterResume

MAX_NOTE_LENGTH = 250


def _skills_for_track(match: JobMatch, resume: MasterResume, role_track: str) -> str:
    skills = match.matching_skills[:4]
    if skills:
        return ", ".join(skills)
    if role_track.upper() in ("BACKEND", "HYBRID"):
        backend = resume.technical_skills.get("Backend", [])[:3]
        langs = resume.technical_skills.get("Languages", [])[:2]
        return ", ".join(backend + langs) or "Java, Spring Boot"
    android = resume.technical_skills.get("Android", [])[:3]
    langs = resume.technical_skills.get("Languages", [])[:2]
    return ", ".join(android + langs) or "Kotlin, Android"


def _team_hint(contact: LinkedInContact, role_track: str) -> str:
    title_l = contact.title.lower()
    if contact.priority == ContactPriority.RECRUITER:
        return "talent team"
    if contact.priority == ContactPriority.HIRING_MANAGER:
        if "android" in title_l or "mobile" in title_l:
            return "mobile engineering team"
        if "backend" in title_l or "java" in title_l:
            return "backend engineering team"
        return "engineering team"
    if "android" in title_l or "mobile" in title_l or role_track.upper() == "ANDROID":
        return "Android/mobile team"
    if role_track.upper() == "BACKEND":
        return "backend/Java team"
    return "engineering team"


def template_connection_note(
    contact: LinkedInContact,
    match: JobMatch,
    resume: MasterResume,
    role_track: str = "ANDROID",
) -> str:
    first_name = contact.name.split()[0] if contact.name else "there"
    skills = _skills_for_track(match, resume, role_track)
    team = _team_hint(contact, role_track)
    job_title = match.job.title
    company = match.job.company

    if role_track.upper() == "BACKEND":
        exp_line = f"I work with {skills}."
    elif role_track.upper() == "HYBRID":
        exp_line = f"I work across mobile & backend ({skills})."
    else:
        exp_line = f"I work with {skills}."

    note = (
        f"Hi {first_name}, I saw the {job_title} role at {company} and noticed your work "
        f"with the {team}. {exp_line} "
        f"Would you be open to referring me if my profile seems like a fit? Thanks!"
    )
    return _trim_note(note)


def _trim_note(note: str) -> str:
    note = " ".join(note.split())
    if len(note) <= MAX_NOTE_LENGTH:
        return note
    return note[: MAX_NOTE_LENGTH - 3].rsplit(" ", 1)[0] + "..."


async def generate_connection_note(
    contact: LinkedInContact,
    match: JobMatch,
    resume: MasterResume,
    role_track: str = "ANDROID",
    llm: LLMService | None = None,
) -> str:
    llm = llm or LLMService()
    if llm.available:
        note = await _llm_note(contact, match, resume, role_track, llm)
        if note:
            return _trim_note(note)
    return template_connection_note(contact, match, resume, role_track)


async def _llm_note(
    contact: LinkedInContact,
    match: JobMatch,
    resume: MasterResume,
    role_track: str,
    llm: LLMService,
) -> str:
    skills = _skills_for_track(match, resume, role_track)
    prompt = f"""Write a LinkedIn connection request note (MAX 250 characters).

CRITICAL RULES:
- Never claim the recipient knows the candidate.
- Never fabricate shared connections, schools, or experience.
- Only mention skills that exist: {skills}
- Candidate has ~{resume.total_experience_years} years experience.
- Polite referral ask, not pushy.
- Use recipient's first name: {contact.name.split()[0]}

Recipient: {contact.name}, {contact.title}
Job: {match.job.title} at {match.job.company}
Role track: {role_track}

Return ONLY the note text, no quotes."""

    try:
        response = await llm.client.chat.completions.create(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return ""
