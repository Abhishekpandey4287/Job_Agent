"""LinkedIn referral outreach orchestrator."""

import json
import re
from pathlib import Path
from typing import List, Optional

from job_agent.ai.llm_service import LLMService
from job_agent.config import get_settings
from job_agent.discovery.browser.brave_session import BraveSession
from job_agent.logging_config import get_logger
from job_agent.outreach.linkedin_finder import LinkedInContactFinder
from job_agent.outreach.note_generator import MAX_NOTE_LENGTH, generate_connection_note
from job_agent.outreach.schemas import JobOutreachSummary, LinkedInOutreachRecord, OutreachStatus
from job_agent.resume.folder_names import company_folder_name
from job_agent.schemas import JobMatch, MasterResume

logger = get_logger(__name__)


class LinkedInOutreachService:
    def __init__(self, llm: Optional[LLMService] = None):
        self.llm = llm or LLMService()
        self.settings = get_settings()

    async def run_for_matches(
        self,
        matches: List[tuple[JobMatch, str, str]],
        output_dir: Path,
        prepare_ui: bool = False,
    ) -> tuple[List[JobOutreachSummary], List[str]]:
        """Find 3 contacts per company; save profile link + referral note txt files."""
        summaries: List[JobOutreachSummary] = []
        errors: List[str] = []
        max_contacts = self.settings.linkedin_max_contacts_per_job

        if not matches:
            logger.info("linkedin_outreach_no_matches")
            return summaries, errors

        outreach_root = output_dir / "outreach"
        outreach_root.mkdir(parents=True, exist_ok=True)

        try:
            async with BraveSession() as session:
                finder = LinkedInContactFinder(session)

                for match, role_track, resume_used in matches:
                    m = match
                    company_dir = outreach_root / company_folder_name(m.job.company)
                    company_dir.mkdir(parents=True, exist_ok=True)

                    resume = self._load_resume(resume_used)

                    try:
                        contacts = await finder.find_contacts(
                            m.job.company, m.job.title, max_contacts=max_contacts
                        )
                    except Exception as e:
                        errors.append(f"{m.job.company}: contact search failed: {e}")
                        continue

                    if not contacts:
                        errors.append(f"{m.job.company}: no contacts found on LinkedIn")
                        (company_dir / "_no_contacts.txt").write_text(
                            f"No LinkedIn contacts found for {m.job.company}.\n"
                            f"Job: {m.job.title}\n",
                            encoding="utf-8",
                        )
                        continue

                    records: List[LinkedInOutreachRecord] = []
                    for contact in contacts:
                        note = await generate_connection_note(
                            contact, m, resume, role_track, self.llm
                        )
                        safe_name = re.sub(r"[^\w-]", "_", contact.name.strip())[:40]
                        note_path = company_dir / f"{safe_name}_referral.txt"
                        note_path.write_text(
                            self._format_referral_file(contact, note),
                            encoding="utf-8",
                        )

                        records.append(LinkedInOutreachRecord(
                            job_id=m.job.job_id,
                            company=m.job.company,
                            job_title=m.job.title,
                            contact=contact,
                            connection_note=note,
                            status=OutreachStatus.NOTE_SAVED,
                            note_file_path=str(note_path),
                        ))

                    summaries.append(JobOutreachSummary(
                        job_id=m.job.job_id,
                        company=m.job.company,
                        job_title=m.job.title,
                        match_score=m.match_score,
                        contacts=records,
                    ))

                    summary_path = company_dir / "contacts_summary.txt"
                    summary_path.write_text(
                        self._format_company_summary(m, records),
                        encoding="utf-8",
                    )

        except RuntimeError as e:
            errors.append(f"Brave session: {e}")
            logger.error("linkedin_outreach_session_failed", error=str(e))

        master_summary = outreach_root / "linkedin_outreach_summary.json"
        master_summary.write_text(
            json.dumps([s.model_dump() for s in summaries], indent=2, default=str),
            encoding="utf-8",
        )
        return summaries, errors

    def _load_resume(self, resume_used: str) -> MasterResume:
        from job_agent.resume.parser import ResumeParser

        android_resume_path = Path(self.settings.master_resume_json)
        backend_path = android_resume_path.parent / "master_backend.json"
        if resume_used == "Backend" and backend_path.exists():
            return ResumeParser(json_path=str(backend_path)).load()
        return ResumeParser(json_path=str(android_resume_path)).load()

    def _format_referral_file(self, contact, note: str) -> str:
        return (
            f"Name: {contact.name}\n"
            f"Title: {contact.title}\n"
            f"Profile: {contact.profile_url}\n"
            f"\n"
            f"Referral message ({len(note)}/{MAX_NOTE_LENGTH} chars):\n"
            f"{note}\n"
        )

    def _format_company_summary(self, match: JobMatch, records: List[LinkedInOutreachRecord]) -> str:
        lines = [
            f"Company: {match.job.company}",
            f"Job: {match.job.title}",
            f"Match score: {match.match_score}/100",
            f"Contacts found: {len(records)}",
            "",
        ]
        for i, rec in enumerate(records, 1):
            lines.extend([
                f"{i}. {rec.contact.name} — {rec.contact.title}",
                f"   Profile: {rec.contact.profile_url}",
                f"   Note file: {Path(rec.note_file_path).name}",
                "",
            ])
        return "\n".join(lines)
