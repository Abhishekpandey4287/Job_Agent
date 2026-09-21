"""Prepare LinkedIn connection requests — never click Send."""

import re
from typing import Optional

from job_agent.discovery.browser.brave_session import BraveSession
from job_agent.logging_config import get_logger
from job_agent.outreach.schemas import LinkedInContact, OutreachStatus

logger = get_logger(__name__)

# Selectors we must NEVER click
SEND_PATTERNS = re.compile(
    r"send invitation|send now|send$|done",
    re.I,
)


class LinkedInConnectWorkflow:
    """Opens Connect → Add a note → fills message. Does NOT send."""

    def __init__(self, session: BraveSession):
        self.session = session

    async def prepare_connection(
        self,
        contact: LinkedInContact,
        note: str,
    ) -> tuple[OutreachStatus, bool]:
        page = self.session.page

        try:
            await page.goto(contact.profile_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
        except Exception as e:
            logger.warning("profile_navigation_failed", url=contact.profile_url, error=str(e))
            return OutreachStatus.BLOCKED, False

        blocker = await self.session.detect_blocker()
        if blocker:
            return OutreachStatus.BLOCKED, False

        connect = page.locator(
            "main button:has-text('Connect'), "
            "button[aria-label*='Invite'][aria-label*='connect' i], "
            ".pvs-profile-actions button:has-text('Connect')"
        ).first

        if await connect.count() == 0:
            # Already connected or no connect option
            pending = page.locator("button:has-text('Pending')")
            if await pending.count() > 0:
                return OutreachStatus.SKIPPED, False
            return OutreachStatus.NO_CONNECT, False

        try:
            await connect.click(timeout=5000)
            await page.wait_for_timeout(1500)
        except Exception:
            return OutreachStatus.NO_CONNECT, False

        add_note = page.locator(
            "button:has-text('Add a note'), "
            "button[aria-label='Add a note']"
        ).first

        if await add_note.count() == 0:
            # LinkedIn did not offer Add a note — dismiss modal safely
            await self._dismiss_modal(page)
            return OutreachStatus.ADD_NOTE_UNAVAILABLE, False

        try:
            await add_note.click(timeout=5000)
            await page.wait_for_timeout(800)
        except Exception:
            await self._dismiss_modal(page)
            return OutreachStatus.ADD_NOTE_UNAVAILABLE, False

        textarea = page.locator(
            "textarea[name='message'], textarea#custom-message, "
            "textarea.connect-button-send-invite__custom-message"
        ).first

        if await textarea.count() == 0:
            await self._dismiss_modal(page)
            return OutreachStatus.ADD_NOTE_UNAVAILABLE, False

        await textarea.fill(note)
        logger.info(
            "linkedin_note_entered",
            contact=contact.name,
            note_length=len(note),
            action="STOPPED_BEFORE_SEND",
        )

        # Deliberately do NOT click Send — leave modal open for user review
        return OutreachStatus.UI_PREPARED, True

    async def _dismiss_modal(self, page) -> None:
        """Close invitation modal without sending."""
        dismiss = page.locator(
            "button[aria-label='Dismiss'], button:has-text('Cancel'), "
            "button.artdeco-modal__dismiss"
        ).first
        if await dismiss.count() > 0:
            try:
                await dismiss.click(timeout=3000)
            except Exception:
                await page.keyboard.press("Escape")
        else:
            await page.keyboard.press("Escape")
