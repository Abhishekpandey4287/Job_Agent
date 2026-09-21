"""LLM-powered enhancements for matching and resume tailoring.

Providers (failover on quota / auth / unavailable):
  1. Google Gemini
  2. Groq (OpenAI-compatible)
  3. Cerebras (OpenAI-compatible)

When a key is missing, expired, or a model is exhausted, that provider/model
is skipped for the rest of the run and the next one is tried.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.matching.dual_track import classify_role_track
from job_agent.matching.skill_intelligence import (
    RESUME_INTELLIGENCE_RULES,
    SkillAnalyzer,
)
from job_agent.schemas import (
    ApplicationQuestion,
    JobMatch,
    MasterResume,
    TailoredResume,
)

logger = get_logger(__name__)

GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
)
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODELS = (
    "openai/gpt-oss-120b",  # production flagship
    "openai/gpt-oss-20b",  # production fast
    "qwen/qwen3.8-27b",  # preview — strong JSON
)
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_DEFAULT_MODELS = (
    "llama3.1-8b",
    "llama-3.3-70b",
    "gpt-oss-120b",
)
DEFAULT_PROVIDER_ORDER = ("gemini", "groq", "cerebras")


@dataclass
class _Endpoint:
    provider: str
    model: str
    kind: str  # "gemini" | "openai"
    client: Any
    key_id: str = ""  # skip whole provider on auth failure


@dataclass
class _ProviderState:
    skipped_models: set[str] = field(default_factory=set)
    skipped_providers: set[str] = field(default_factory=set)


def _csv_models(raw: str, defaults: Tuple[str, ...]) -> List[str]:
    if (raw or "").strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(defaults)


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        m in text
        for m in (
            "429",
            "resource_exhausted",
            "resource exhausted",
            "exceeded your current quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "tokens per day",
            "tpm",
            "rpm",
            "quota",
        )
    )


def _is_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        m in text
        for m in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "incorrect api key",
            "api key not valid",
            "authentication",
            "permission_denied",
            "expired",
            "revoked",
        )
    )


def _is_not_found_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "404" in text or "not_found" in text or "no longer available" in text or "does not exist" in text


def _is_transient_llm_error(exc: BaseException) -> bool:
    if _is_not_found_error(exc) or _is_auth_error(exc):
        return False
    text = str(exc).lower()
    markers = (
        "503",
        "502",
        "504",
        "unavailable",
        "high demand",
        "try again",
        "overloaded",
        "deadline exceeded",
        "temporarily",
        "timeout",
        "connection",
    )
    return any(m in text for m in markers) or _is_quota_error(exc)


def _parse_json_object(raw: str) -> dict:
    """Parse model JSON; tolerate markdown fences / truncated tails when possible."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("Could not parse JSON object", text, 0)


def _build_endpoints() -> List[_Endpoint]:
    """Ordered Gemini → Groq → Cerebras endpoints from settings + keys."""
    settings = get_settings()
    order_raw = (getattr(settings, "llm_provider_order", "") or "").strip()
    order = [
        p.strip().lower()
        for p in (order_raw.split(",") if order_raw else DEFAULT_PROVIDER_ORDER)
        if p.strip()
    ]
    if not order:
        order = list(DEFAULT_PROVIDER_ORDER)

    endpoints: List[_Endpoint] = []

    for provider in order:
        if provider == "gemini":
            api_key = (settings.gemini_api_key or "").strip()
            if not api_key:
                continue
            try:
                from google import genai
            except ImportError:
                logger.warning("llm_provider_skip", provider="gemini", reason="google-genai not installed")
                continue
            client = genai.Client(api_key=api_key)
            primary = (settings.llm_model or "").strip()
            if primary and not primary.startswith("gemini"):
                primary = ""
            models = _csv_models(
                getattr(settings, "gemini_fallback_models", "") or "",
                GEMINI_FALLBACK_MODELS,
            )
            if primary:
                models = [primary] + [m for m in models if m != primary]
            for model in models:
                endpoints.append(
                    _Endpoint(
                        provider="gemini",
                        model=model,
                        kind="gemini",
                        client=client,
                        key_id="gemini",
                    )
                )

        elif provider == "groq":
            api_key = (getattr(settings, "groq_api_key", "") or "").strip()
            if not api_key:
                continue
            try:
                from openai import AsyncOpenAI
            except ImportError:
                logger.warning("llm_provider_skip", provider="groq", reason="openai package not installed")
                continue
            client = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
            models = _csv_models(
                getattr(settings, "groq_models", "") or "",
                GROQ_DEFAULT_MODELS,
            )
            for model in models:
                endpoints.append(
                    _Endpoint(
                        provider="groq",
                        model=model,
                        kind="openai",
                        client=client,
                        key_id="groq",
                    )
                )

        elif provider == "cerebras":
            api_key = (getattr(settings, "cerebras_api_key", "") or "").strip()
            if not api_key:
                continue
            try:
                from openai import AsyncOpenAI
            except ImportError:
                logger.warning(
                    "llm_provider_skip", provider="cerebras", reason="openai package not installed"
                )
                continue
            client = AsyncOpenAI(api_key=api_key, base_url=CEREBRAS_BASE_URL)
            models = _csv_models(
                getattr(settings, "cerebras_models", "") or "",
                CEREBRAS_DEFAULT_MODELS,
            )
            for model in models:
                endpoints.append(
                    _Endpoint(
                        provider="cerebras",
                        model=model,
                        kind="openai",
                        client=client,
                        key_id="cerebras",
                    )
                )

    return endpoints


def resolve_llm_client() -> Tuple[Optional[Any], str, str]:
    """Return (primary_client, model, provider_name) for status display."""
    endpoints = _build_endpoints()
    if not endpoints:
        return None, GEMINI_DEFAULT_MODEL, "none"
    ep = endpoints[0]
    return ep.client, ep.model, ep.provider


class LLMService:
    def __init__(self):
        self._endpoints = _build_endpoints()
        self._state = _ProviderState()
        self._last_call_at = 0.0
        settings = get_settings()
        self._min_interval = float(
            getattr(settings, "llm_min_interval_seconds", 45.0) or 0
        )
        self._max_retries = int(getattr(settings, "llm_max_retries", 3) or 3)

        if self._endpoints:
            self.client = self._endpoints[0].client
            self.model = self._endpoints[0].model
            self.provider = self._endpoints[0].provider
            logger.info(
                "llm_ready",
                provider=self.provider,
                model=self.model,
                chain=[
                    f"{e.provider}:{e.model}" for e in self._endpoints
                ],
                min_interval_s=self._min_interval,
            )
        else:
            self.client = None
            self.model = GEMINI_DEFAULT_MODEL
            self.provider = "none"
            logger.warning(
                "llm_no_providers",
                hint="Set GEMINI_API_KEY and/or GROQ_API_KEY and/or CEREBRAS_API_KEY in .env",
            )

    @property
    def available(self) -> bool:
        return bool(self._endpoints)

    def providers_summary(self) -> str:
        seen: List[str] = []
        for e in self._endpoints:
            if e.provider not in seen:
                seen.append(e.provider)
        return " → ".join(seen) if seen else "none"

    async def _pace(self, *, full: bool = True) -> None:
        # Full gap after successful calls; short gap when failing over models
        interval = self._min_interval if full else min(5.0, max(2.0, self._min_interval * 0.1))
        if interval > 0 and self._last_call_at > 0:
            elapsed = time.monotonic() - self._last_call_at
            wait = interval - elapsed
            if wait > 0:
                logger.info("llm_pacing_wait", seconds=round(wait, 1), full=full)
                await asyncio.sleep(wait)

    async def _call_gemini(
        self,
        ep: _Endpoint,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> str:
        from google.genai import types

        thinking_opts = [False] if "lite" in ep.model else [True, False]
        last_err: BaseException | None = None
        for with_thinking in thinking_opts:
            config_kwargs: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            }
            if with_thinking:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                )
            if json_mode:
                config_kwargs["response_mime_type"] = "application/json"
            try:
                response = await ep.client.aio.models.generate_content(
                    model=ep.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                return (response.text or "").strip()
            except Exception as e:
                last_err = e
                if with_thinking and (
                    "thinking" in str(e).lower() or "invalid" in str(e).lower()
                ):
                    continue
                raise
        if last_err:
            raise last_err
        return ""

    async def _call_openai(
        self,
        ep: _Endpoint,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": ep.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        use_json = json_mode
        # gpt-oss often fails strict JSON mode — try once with, then without
        attempts_json = [True, False] if json_mode else [False]
        last_err: BaseException | None = None
        for use_json in attempts_json:
            call_kwargs = dict(kwargs)
            if use_json:
                call_kwargs["response_format"] = {"type": "json_object"}
            try:
                response = await ep.client.chat.completions.create(**call_kwargs)
                content = response.choices[0].message.content if response.choices else ""
                text = (content or "").strip()
                if not text:
                    raise RuntimeError(f"Empty response from {ep.provider}:{ep.model}")
                return text
            except Exception as e:
                last_err = e
                err = str(e).lower()
                if use_json and (
                    "json_validate_failed" in err
                    or "failed to validate json" in err
                    or "response_format" in err
                ):
                    logger.warning(
                        "llm_json_mode_fallback",
                        provider=ep.provider,
                        model=ep.model,
                    )
                    continue
                raise
        if last_err:
            raise last_err
        return ""

    async def _generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 600,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """Try each provider/model; skip on auth/quota/404; retry transient errors."""
        if not self._endpoints:
            raise RuntimeError("No LLM providers configured")

        last_error: BaseException | None = None
        first_try = True
        for ep in self._endpoints:
            if ep.key_id in self._state.skipped_providers:
                continue
            skip_key = f"{ep.provider}:{ep.model}"
            if skip_key in self._state.skipped_models:
                continue

            attempts = max(1, min(self._max_retries, 3))
            for attempt in range(1, attempts + 1):
                await self._pace(full=first_try)
                first_try = False
                try:
                    if ep.kind == "gemini":
                        text = await self._call_gemini(
                            ep,
                            prompt,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            json_mode=json_mode,
                        )
                    else:
                        text = await self._call_openai(
                            ep,
                            prompt,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            json_mode=json_mode,
                        )
                    if not text.strip():
                        raise RuntimeError(f"Empty response from {ep.provider}:{ep.model}")
                    self._last_call_at = time.monotonic()
                    if ep.provider != self.provider or ep.model != self.model:
                        logger.info(
                            "llm_fallback_used",
                            provider=ep.provider,
                            model=ep.model,
                        )
                    self.client = ep.client
                    self.model = ep.model
                    self.provider = ep.provider
                    return text
                except Exception as e:
                    self._last_call_at = time.monotonic()
                    last_error = e

                    if _is_auth_error(e):
                        self._state.skipped_providers.add(ep.key_id)
                        logger.warning(
                            "llm_provider_auth_failed",
                            provider=ep.provider,
                            error=str(e)[:160],
                            hint="Key missing, expired, or revoked — skipping provider",
                        )
                        break

                    if _is_not_found_error(e):
                        self._state.skipped_models.add(skip_key)
                        logger.warning(
                            "llm_model_unavailable",
                            provider=ep.provider,
                            model=ep.model,
                            error=str(e)[:160],
                        )
                        break

                    if _is_quota_error(e):
                        # Skip this model only — other Gemini/Groq models may still work
                        self._state.skipped_models.add(skip_key)
                        logger.warning(
                            "llm_quota_skip",
                            provider=ep.provider,
                            model=ep.model,
                            error=str(e)[:160],
                        )
                        break

                    if not _is_transient_llm_error(e) or attempt >= attempts:
                        logger.warning(
                            "llm_model_exhausted",
                            provider=ep.provider,
                            model=ep.model,
                            error=str(e)[:160],
                        )
                        self._state.skipped_models.add(skip_key)
                        break

                    backoff = min(16.0, 2.0 ** attempt)
                    logger.warning(
                        "llm_retry",
                        provider=ep.provider,
                        model=ep.model,
                        attempt=attempt,
                        backoff_s=backoff,
                        error=str(e)[:160],
                    )
                    await asyncio.sleep(backoff)

        raise RuntimeError(str(last_error) if last_error else "All LLM providers failed")

    def _role_track(self, match: JobMatch) -> str:
        return classify_role_track(match.job.title, match.job.description).value

    def _analyze_skills(self, resume: MasterResume, match: JobMatch):
        track = self._role_track(match)
        jd_text = f"{match.job.title} {match.job.description}"
        analyzer = SkillAnalyzer(resume, role_track=track)
        return analyzer, analyzer.analyze(jd_text.lower())

    async def enhance_match_explanation(self, match: JobMatch, resume: MasterResume) -> str:
        if not self.available:
            return match.match_explanation

        _, analysis = self._analyze_skills(resume, match)
        prompt = f"""You are an ATS/recruitment expert. Given this job match analysis, write a concise 2-3 sentence explanation.

Job: {match.job.title} at {match.job.company}
Match Score: {match.match_score}/100
Explicit + Core Matching Skills: {', '.join(match.matching_skills[:10])}
Implied Domain Skills (reasonable for background, not all on master resume): {', '.join(analysis.implied)}
Adjacent/Transferable Skills: {', '.join(analysis.adjacent)}
Unsupported Gaps (do not claim these): {', '.join(analysis.unsupported)}
Candidate Experience: ~{resume.total_experience_years} years professional

Rules:
- Master resume is NOT an exhaustive skill list — implied domain skills are valid strengths
- Be factual. Never invent experience, years, projects, or companies
- Distinguish strong fits from genuine gaps (unsupported skills)
- Keep it professional and concise"""

        try:
            return await self._generate(prompt, max_tokens=200, temperature=0.3)
        except Exception as e:
            logger.error("llm_explanation_failed", error=str(e))
            return match.match_explanation

    async def tailor_resume(
        self, resume: MasterResume, match: JobMatch
    ) -> TailoredResume:
        analyzer, analysis = self._analyze_skills(resume, match)
        tailored_skills = analyzer.tailor_technical_skills(analysis)
        jd_len = len((match.job.description or "").strip())

        if not self.available:
            logger.warning(
                "resume_tailor_no_llm",
                company=match.job.company,
                jd_chars=jd_len,
                hint="Set GEMINI_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY",
            )
            return self._deterministic_tailor(resume, match, analysis, tailored_skills)

        if jd_len < 80:
            logger.warning(
                "resume_tailor_thin_jd",
                company=match.job.company,
                title=match.job.title,
                jd_chars=jd_len,
            )

        implied_note = ", ".join(analysis.implied) if analysis.implied else "None"
        unsupported_note = ", ".join(analysis.unsupported) if analysis.unsupported else "None"

        # The Profile paragraph is deliberately NOT requested. It is a fixed,
        # human-reviewed block per resume profile, and asking for a company-specific
        # one produced openings like "As a Software Engineer III at JPMorganChase",
        # which reads as a false claim of already working there.
        prompt = f"""You are an expert resume writer selecting which Technical Skills to surface on Abhishek Pandey's resume for ONE specific job.

{RESUME_INTELLIGENCE_RULES}

Job Title: {match.job.title}
Company: {match.job.company}
Job Description (excerpt): {match.job.description[:3000] if jd_len else '(no JD body — judge from title and known role requirements only)'}

Skill analysis for this JD:
- Documented matches: {', '.join(analysis.explicit[:12])}
- Implied domain skills you MAY surface: {implied_note}
- Do NOT claim: {unsupported_note}
- Matching skills overall: {', '.join(match.matching_skills[:12])}

True professional experience: ~{resume.total_experience_years:.0f} years (Mid-level).

Task:
List additional Technical Skills worth including for THIS job, drawn only from the
implied domain skills above and only where relevant to this JD.

CRITICAL:
- NEVER invent skills, experience, projects, companies, achievements, or metrics
- Never include anything from the "Do NOT claim" list

Return JSON only:
{{
  "skills_to_add": ["View Binding", "..."]
}}"""

        try:
            raw = await self._generate(
                prompt, max_tokens=400, temperature=0.2, json_mode=True
            )
            data = _parse_json_object(raw)
            skills_to_add = data.get("skills_to_add", [])

            final_skills = self._merge_llm_skills(tailored_skills, skills_to_add, analysis)
            keywords = list(dict.fromkeys(match.matching_skills + analysis.implied))

            return TailoredResume(
                job_id=match.job.job_id,
                company=match.job.company,
                job_title=match.job.title,
                profile_summary=resume.profile_summary,
                experience=resume.experience,
                projects=resume.projects,
                technical_skills=final_skills,
                education=resume.education,
                certifications=resume.certifications,
                keywords_used=keywords,
            )
        except Exception as e:
            logger.error("llm_tailor_failed", error=str(e))
            return self._deterministic_tailor(resume, match, analysis, tailored_skills)

    def _merge_llm_skills(self, tailored_skills: dict, skills_to_add: List[str], analysis) -> dict:
        result = {cat: list(items) for cat, items in tailored_skills.items()}
        allowed = {s.lower() for s in analysis.explicit + analysis.implied}
        blocked = {s.lower() for s in analysis.unsupported}

        for skill in skills_to_add:
            if skill.lower() in blocked:
                continue
            if skill.lower() not in allowed and skill.lower() not in {s.lower() for s in analysis.implied}:
                continue
            category = next(
                (cat for cat in result if any(skill.lower() in item.lower() for item in result[cat])),
                "Android" if "Android" in result else next(iter(result.keys())),
            )
            if "Android" in result and any(
                h in skill.lower() for h in ("view", "binding", "fragment", "compose", "android")
            ):
                category = "Android"
            elif "Cloud & DevOps" in result and any(
                h in skill.lower() for h in ("aws", "docker", "jenkins", "ec2", "dynamo", "cloud")
            ):
                category = "Cloud & DevOps"
            if not any(skill.lower() in item.lower() for item in result.get(category, [])):
                result.setdefault(category, []).append(skill)
        return result

    def _deterministic_tailor(
        self,
        resume: MasterResume,
        match: JobMatch,
        analysis=None,
        tailored_skills=None,
    ) -> TailoredResume:
        if analysis is None or tailored_skills is None:
            analyzer, analysis = self._analyze_skills(resume, match)
            tailored_skills = analyzer.tailor_technical_skills(analysis)

        keywords = list(dict.fromkeys(match.matching_skills + analysis.implied))

        return TailoredResume(
            job_id=match.job.job_id,
            company=match.job.company,
            job_title=match.job.title,
            profile_summary=resume.profile_summary,
            experience=resume.experience,
            projects=resume.projects,
            technical_skills=tailored_skills,
            education=resume.education,
            certifications=resume.certifications,
            keywords_used=keywords,
        )

    async def generate_cover_letter(
        self, resume: MasterResume, match: JobMatch
    ) -> str:
        if not self.available:
            return self._template_cover_letter(resume, match)

        _, analysis = self._analyze_skills(resume, match)
        skills = list(
            dict.fromkeys(
                (match.matching_skills or [])
                + (analysis.explicit or [])
                + (analysis.implied or [])
            )
        )[:8]
        exp = resume.experience[0] if resume.experience else None
        bullets = (exp.bullets[:3] if exp and exp.bullets else [])
        bullet_block = "\n".join(f"- {b}" for b in bullets) or "- (see resume)"
        jd_excerpt = (match.job.description or "").strip()[:1800] or "(title only — no JD body)"

        prompt = f"""Write a professional job cover letter for Abhishek Pandey.

FORMAT (plain text only — no markdown, no **asterisks**, no bullet lists):
Subject: Application for {{Job Title}} - Abhishek Pandey

Dear Hiring Team,

{{paragraph 1: 2-3 sentences — name the exact role and company; say why you fit}}

{{paragraph 2: 3-4 sentences — map 2-3 real achievements below to THIS job's needs}}

{{paragraph 3: 2 sentences — close politely; invite a conversation}}

Sincerely,
Abhishek Pandey

JOB
- Title: {match.job.title}
- Company: {match.job.company}
- JD excerpt: {jd_excerpt}

CANDIDATE (truth only — do not invent)
- Current: {(exp.title if exp else 'Engineer')} at {(exp.company if exp else 'Percipere')}
- Years: ~{resume.total_experience_years:.0f}
- Skills to emphasize: {', '.join(skills) if skills else 'Kotlin, Android, REST APIs'}
- Real achievements you MAY reference:
{bullet_block}
- Do NOT claim: {', '.join(analysis.unsupported[:6]) if analysis.unsupported else 'skills not in background'}

QUALITY RULES
- 180–260 words total
- Specific to {match.job.company} and "{match.job.title}" — not a generic Android letter
- Never fabricate metrics, titles, companies, or years
- Never write "experience in ," or empty skill lists
- Output the letter only (start with Subject:)"""

        try:
            raw = await self._generate(prompt, max_tokens=700, temperature=0.35)
            letter = self._normalize_cover_letter(raw, resume, match)
            if self._cover_letter_is_weak(letter, match):
                logger.warning(
                    "llm_cover_letter_weak_using_template",
                    company=match.job.company,
                    chars=len(letter),
                )
                return self._template_cover_letter(resume, match, analysis=analysis)
            return letter
        except Exception as e:
            logger.error("llm_cover_letter_failed", error=str(e))
            return self._template_cover_letter(resume, match, analysis=analysis)

    def _normalize_cover_letter(
        self, raw: str, resume: MasterResume, match: JobMatch
    ) -> str:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:\w+)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # Strip markdown bold leftover from resume-style prompts
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = text.replace("**", "").strip()
        # Ensure greeting / sign-off
        if "dear" not in text.lower():
            text = f"Dear Hiring Team,\n\n{text}"
        name = (resume.contact.name if resume.contact else "Abhishek Pandey") or "Abhishek Pandey"
        if name.lower() not in text.lower().split("\n")[-3:]:
            if not re.search(r"(sincerely|best regards|regards)\s*,?", text, re.I):
                text = f"{text.rstrip()}\n\nSincerely,\n{name}"
        if not text.lower().lstrip().startswith("subject:"):
            title = match.job.title or "the role"
            text = f"Subject: Application for {title} - {name}\n\n{text}"
        return text.strip() + "\n"

    def _cover_letter_is_weak(self, letter: str, match: JobMatch) -> bool:
        text = (letter or "").strip()
        if len(text) < 350:
            return True
        lowered = text.lower()
        if "experience in ," in lowered or "experience in  ," in lowered:
            return True
        company = (match.job.company or "").strip().lower()
        if company and company not in ("unknown",) and company not in lowered:
            return True
        # Template-ish truncated dump of profile alone
        if "i would welcome the opportunity to discuss how my experience" in lowered and len(text) < 700:
            return True
        return False

    def _template_cover_letter(
        self,
        resume: MasterResume,
        match: JobMatch,
        analysis=None,
    ) -> str:
        """Strong deterministic letter when LLM is unavailable or weak."""
        name = (resume.contact.name if resume.contact else "Abhishek Pandey") or "Abhishek Pandey"
        title = (match.job.title or "the open role").strip()
        company = (match.job.company or "your company").strip()
        if company.lower() in ("unknown", ""):
            company = "your team"

        skills: List[str] = []
        for s in match.matching_skills or []:
            if s and s.strip():
                skills.append(s.strip())
        if analysis is not None:
            for s in (analysis.explicit or []) + (analysis.implied or []):
                if s and s.strip() and s.strip() not in skills:
                    skills.append(s.strip())
        if not skills:
            # Profile-aware defaults
            text_blob = f"{resume.profile_summary} {title}".lower()
            if any(k in text_blob for k in ("backend", "java", "spring")):
                skills = ["Java", "Spring Boot", "REST APIs"]
            else:
                skills = ["Kotlin", "Android", "MVVM"]
        skill_phrase = ", ".join(skills[:4])

        exp = resume.experience[0] if resume.experience else None
        role_line = (
            f"{exp.title} at {exp.company}" if exp else "Android & Full-Stack Engineer at Percipere"
        )
        highlight = ""
        if exp and exp.bullets:
            for b in exp.bullets:
                clean = (b or "").strip()
                if len(clean) > 40:
                    # Make bullet flow as a sentence continuation
                    if clean[0].isupper():
                        clean = clean[0].lower() + clean[1:]
                    highlight = clean.rstrip(".")
                    break

        if highlight:
            para2 = (
                f"In my current work as {role_line}, I {highlight}. "
                f"That background maps well to the {title} role, especially around {skill_phrase}."
            )
        else:
            para2 = (
                f"In my current work as {role_line}, I deliver production Android and "
                f"backend features end-to-end. That background maps well to the {title} role, "
                f"especially around {skill_phrase}."
            )

        return (
            f"Subject: Application for {title} - {name}\n\n"
            f"Dear Hiring Team,\n\n"
            f"I am writing to apply for the {title} position at {company}. "
            f"I bring hands-on experience with {skill_phrase}, and I am excited about "
            f"the chance to contribute to {company}'s engineering team.\n\n"
            f"{para2}\n\n"
            f"I would welcome the opportunity to discuss how my experience can help "
            f"{company} ship reliable product. Thank you for your time and consideration.\n\n"
            f"Sincerely,\n"
            f"{name}\n"
        )

    async def generate_application_answers(
        self, resume: MasterResume, match: JobMatch, questions: List[str]
    ) -> List[ApplicationQuestion]:
        results = []
        for q in questions:
            answer = await self._answer_question(resume, match, q)
            results.append(answer)
        return results

    async def _answer_question(
        self, resume: MasterResume, match: JobMatch, question: str
    ) -> ApplicationQuestion:
        q_lower = question.lower()

        factual_map = {
            "years of experience": f"Approximately {resume.total_experience_years:.0f} years of professional software development experience, with focused Android development experience since 2022.",
            "current location": resume.contact.location or "Mumbai, India",
            "location": resume.contact.location or "Mumbai, India",
            "email": resume.contact.email,
            "phone": resume.contact.phone,
            "name": resume.contact.name,
            "android experience": "Android development experience since August 2022, including a Google Android Development Internship and current role as Android & Full-Stack Engineer at Percipere.",
        }

        for key, answer in factual_map.items():
            if key in q_lower:
                return ApplicationQuestion(question=question, answer=answer)

        user_input_required = any(
            k in q_lower for k in ["salary", "notice period", "work authorization", "relocation", "expected ctc"]
        )
        if user_input_required:
            return ApplicationQuestion(
                question=question, answer="USER_INPUT_REQUIRED", requires_user_input=True
            )

        if not self.available:
            return ApplicationQuestion(question=question, answer="USER_INPUT_REQUIRED", requires_user_input=True)

        prompt = f"""Answer this job application question based ONLY on the candidate's actual resume. Never fabricate.

Question: {question}
Role: {match.job.title} at {match.job.company}
Resume summary: {resume.profile_summary}
Experience: {resume.experience[0].title if resume.experience else 'N/A'} at {resume.experience[0].company if resume.experience else 'N/A'}

If you cannot answer truthfully from the resume, respond with exactly: USER_INPUT_REQUIRED
Keep answer concise (2-4 sentences)."""

        try:
            answer = await self._generate(prompt, max_tokens=250, temperature=0.3)
            requires_input = answer == "USER_INPUT_REQUIRED"
            return ApplicationQuestion(
                question=question, answer=answer, requires_user_input=requires_input
            )
        except Exception as e:
            logger.error("llm_answer_failed", error=str(e))
            return ApplicationQuestion(question=question, answer="USER_INPUT_REQUIRED", requires_user_input=True)
