"""Brave browser session manager using Playwright.

Connects to the user's logged-in Brave 'Switch' profile for read-only job discovery.
Does NOT submit applications, modify profiles, or export session tokens.
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from job_agent.config import get_settings
from job_agent.logging_config import get_logger

logger = get_logger(__name__)

BRAVE_EXECUTABLE = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
DEFAULT_USER_DATA = os.path.expanduser(
    "~/Library/Application Support/BraveSoftware/Brave-Browser"
)


class BraveSession:
    """Manages a read-only Playwright session against Brave browser."""

    def __init__(self):
        settings = get_settings()
        self.executable = settings.brave_executable or BRAVE_EXECUTABLE
        self.user_data_dir = os.path.expanduser(settings.brave_user_data_dir or DEFAULT_USER_DATA)
        self.profile_directory = settings.brave_profile_directory or "Profile 2"
        self.cdp_url = settings.brave_cdp_url
        self.headless = settings.brave_headless
        self.captcha_wait_seconds = max(0, int(getattr(settings, "brave_captcha_wait_seconds", 0) or 0))
        self._playwright = None
        self._context = None
        self._browser = None
        self._page = None
        self._owned = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        cdp_urls = []
        if self.cdp_url:
            cdp_urls.append(self.cdp_url)
        for port in range(9222, 9231):
            cdp_urls.append(f"http://127.0.0.1:{port}")

        for url in cdp_urls:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(url)
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    pages = self._context.pages
                    self._page = pages[0] if pages else await self._context.new_page()
                else:
                    self._context = await self._browser.new_context()
                    self._page = await self._context.new_page()
                logger.info("brave_cdp_connected", url=url)
                return
            except Exception:
                continue

        if not Path(self.executable).exists():
            raise FileNotFoundError(f"Brave not found at {self.executable}")

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                executable_path=self.executable,
                headless=self.headless,
                args=[
                    f"--profile-directory={self.profile_directory}",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport={"width": 1440, "height": 900},
                ignore_default_args=["--enable-automation"],
            )
            self._owned = True
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            logger.info(
                "brave_profile_launched",
                profile=self.profile_directory,
                headless=self.headless,
            )
        except Exception as e:
            if "already in use" in str(e).lower() or "existing browser session" in str(e).lower():
                raise RuntimeError(
                    "Brave 'Switch' profile is already open. To use logged-in sessions:\n"
                    "  1. Close all Brave windows using the Switch profile, OR\n"
                    "  2. Launch Brave with remote debugging:\n"
                    '     /Applications/Brave\\ Browser.app/Contents/MacOS/Brave\\ Browser \\\n'
                    '       --profile-directory="Profile 2" --remote-debugging-port=9222\n'
                    "  3. Set BRAVE_CDP_URL=http://127.0.0.1:9222 in .env"
                ) from e
            raise

    @property
    def page(self):
        if not self._page:
            raise RuntimeError("Browser session not connected")
        return self._page

    async def navigate(self, url: str, wait_ms: int = 3000):
        page = self.page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(wait_ms / 1000)
        except Exception as e:
            logger.warning("navigation_failed", url=url, error=str(e))
            raise

    async def detect_blocker(self, *, wait_for_human: bool = True) -> Optional[str]:
        """Detect a real on-screen CAPTCHA / challenge. Optionally wait for you to solve it."""
        reason = await self._detect_blocker_once()
        if not reason:
            return None
        if not wait_for_human or self.captcha_wait_seconds <= 0:
            return reason
        if not self._is_human_solvable(reason):
            return reason
        return await self._wait_for_human_solve(reason)

    async def _detect_blocker_once(self) -> Optional[str]:
        """Visible/URL blockers only — ignore 'captcha' strings buried in JS bundles."""
        page = self.page
        url = (page.url or "").lower()

        # Strong URL-only signals (real interstitial pages)
        url_blockers = [
            ("/authwall", "Authentication wall"),
            ("cdn-cgi/challenge", "Security challenge detected"),
            ("/challenge-platform/", "Security challenge detected"),
            ("accounts.google.com/v3/signin", "Login required"),
        ]
        for signal, reason in url_blockers:
            if signal in url:
                return reason

        try:
            reason = await page.evaluate(
                """() => {
                  const title = (document.title || '').toLowerCase();
                  // Visible text only — NOT innerHTML (avoids JS false positives)
                  const text = ((document.body && document.body.innerText) || '')
                    .slice(0, 4000).toLowerCase();

                  const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 24 && r.height > 24
                      && s.visibility !== 'hidden'
                      && s.display !== 'none'
                      && s.opacity !== '0';
                  };

                  const sels = [
                    'iframe[src*="recaptcha"]',
                    'iframe[src*="hcaptcha"]',
                    'iframe[src*="challenges.cloudflare"]',
                    'iframe[src*="turnstile"]',
                    '.g-recaptcha',
                    '#px-captcha',
                    '#challenge-form',
                    '.cf-challenge-running',
                    '[data-callback*="captcha"]',
                  ];
                  for (const sel of sels) {
                    const el = document.querySelector(sel);
                    if (isVisible(el)) return 'CAPTCHA detected';
                  }

                  // Visible challenge copy (must be human-readable on screen)
                  const phrases = [
                    ['verify you are human', 'Human verification required'],
                    ['checking your browser before accessing', 'Security challenge detected'],
                    ['just a moment...', 'Security challenge detected'],
                    ['attention required', 'Security challenge detected'],
                    ['enable javascript and cookies to continue', 'Security challenge detected'],
                    ['sorry, we just need to make sure you', 'Security challenge detected'],
                    ['unusual traffic from your computer', 'Security challenge detected'],
                    ['please complete the security check', 'CAPTCHA detected'],
                    ['press and hold', 'CAPTCHA detected'],
                  ];
                  for (const [phrase, reason] of phrases) {
                    if (title.includes(phrase) || text.includes(phrase)) return reason;
                  }

                  // Title-only captcha (rare real interstitial)
                  if (title.includes('captcha') || title.includes('security check')) {
                    return 'CAPTCHA detected';
                  }
                  return null;
                }"""
            )
        except Exception as e:
            logger.warning("blocker_detect_failed", error=str(e) or type(e).__name__)
            return None

        return reason

    @staticmethod
    def _is_human_solvable(reason: str) -> bool:
        r = (reason or "").lower()
        return any(k in r for k in ("captcha", "challenge", "human", "verification"))

    async def _wait_for_human_solve(self, reason: str) -> Optional[str]:
        """Pause while you solve the challenge in the Brave window."""
        wait_s = self.captcha_wait_seconds
        url = ""
        try:
            url = self.page.url
        except Exception:
            pass

        print("\n" + "=" * 60)
        print(f"ACTION NEEDED: {reason}")
        print("Solve it in the Brave window (do not close the browser).")
        if url:
            print(f"Page: {url}")
        print(f"Waiting up to {wait_s}s for you to finish…")
        print("=" * 60 + "\n")
        logger.warning(
            "captcha_waiting_for_human",
            reason=reason,
            wait_s=wait_s,
            url=url[:120] if url else "",
        )

        deadline = asyncio.get_event_loop().time() + wait_s
        poll = 3.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll)
            still = await self._detect_blocker_once()
            if not still:
                print("✓ Challenge cleared — continuing scrape.\n")
                logger.info("captcha_cleared_by_human")
                return None
            reason = still

        print(f"⏱ Timed out after {wait_s}s — skipping this portal.\n")
        logger.warning("captcha_wait_timeout", reason=reason, wait_s=wait_s)
        return reason

    async def close(self):
        if self._owned and self._context:
            await self._context.close()
        elif self._browser and not self._owned:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
