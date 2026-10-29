"""Gemini and Groq should accept work at the same time."""

import asyncio
import time

from job_agent.ai.llm_service import LLMService, _Endpoint, _ProviderState


def _service() -> LLMService:
    service = LLMService()
    service._endpoints = [
        _Endpoint("gemini", "gemini-test", "gemini", client=object(), key_id="gemini"),
        _Endpoint("groq", "groq-test", "openai", client=object(), key_id="groq"),
    ]
    service._state = _ProviderState()
    service._parallel_providers = True
    service._min_interval = 0
    service._lane_last = {"gemini": 0.0, "groq": 0.0}
    service._lane_locks = {
        "gemini": asyncio.Lock(),
        "groq": asyncio.Lock(),
    }
    service._rr = 0
    return service


async def test_gemini_and_groq_run_at_the_same_time(monkeypatch):
    service = _service()
    seen: list[str] = []

    async def _slow(name: str) -> str:
        seen.append(name)
        await asyncio.sleep(0.25)
        return name

    async def _gemini(ep, prompt, **kwargs):
        return await _slow("gemini")

    async def _groq(ep, prompt, **kwargs):
        return await _slow("groq")

    monkeypatch.setattr(service, "_call_gemini", _gemini)
    monkeypatch.setattr(service, "_call_openai", _groq)

    started = time.monotonic()
    results = await asyncio.gather(service._generate("one"), service._generate("two"))
    elapsed = time.monotonic() - started

    assert sorted(results) == ["gemini", "groq"]
    assert sorted(seen) == ["gemini", "groq"]
    assert elapsed < 0.4


async def test_resume_and_cover_letter_stay_on_the_same_model(monkeypatch):
    service = _service()
    seen: list[str] = []

    async def _gemini(ep, prompt, **kwargs):
        seen.append(f"gemini:{prompt}")
        await asyncio.sleep(0.2)
        return "gemini"

    async def _groq(ep, prompt, **kwargs):
        seen.append(f"groq:{prompt}")
        await asyncio.sleep(0.2)
        return "groq"

    monkeypatch.setattr(service, "_call_gemini", _gemini)
    monkeypatch.setattr(service, "_call_openai", _groq)

    async def _job(provider: str, label: str) -> None:
        token = service.pin(provider)
        try:
            await service._generate(f"{label}-resume")
            await service._generate(f"{label}-cover")
        finally:
            service.unpin(token)

    started = time.monotonic()
    await asyncio.gather(_job("gemini", "job1"), _job("groq", "job2"))
    elapsed = time.monotonic() - started

    assert [item for item in seen if item.startswith("gemini:")] == [
        "gemini:job1-resume",
        "gemini:job1-cover",
    ]
    assert [item for item in seen if item.startswith("groq:")] == [
        "groq:job2-resume",
        "groq:job2-cover",
    ]
    assert elapsed < 0.55


async def test_exhausted_model_hands_the_document_to_the_other(monkeypatch):
    service = _service()

    async def _gemini(ep, prompt, **kwargs):
        raise RuntimeError("429 quota exceeded")

    async def _groq(ep, prompt, **kwargs):
        return "written-by-groq"

    monkeypatch.setattr(service, "_call_gemini", _gemini)
    monkeypatch.setattr(service, "_call_openai", _groq)

    token = service.pin("gemini")
    try:
        text = await service._generate("resume")
    finally:
        service.unpin(token)

    assert text == "written-by-groq"
