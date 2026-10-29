"""Base class and registry for job source adapters."""

import asyncio
from abc import ABC, abstractmethod
from typing import List

from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)


class JobSourceAdapter(ABC):
    """One job website.

    Register the subclass in ``job_agent.discovery.catalog``
    (``API_WEBSITES`` or ``ATS_WEBSITES``). Browser sites are extractors
    added to ``browser/portals.py`` instead of this class.
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        ...

    async def health_check(self) -> bool:
        return True


class JobSourceRegistry:
    def __init__(self):
        self._adapters: dict[str, JobSourceAdapter] = {}

    def register(self, adapter: JobSourceAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get_all(self) -> List[JobSourceAdapter]:
        return list(self._adapters.values())

    async def search_all(
        self,
        queries: List[str],
        locations: List[str],
        max_per_source: int = 50,
    ) -> tuple[List[RawJob], List[str]]:
        async def _search_adapter(adapter: JobSourceAdapter) -> tuple[List[RawJob], List[str]]:
            jobs_out: List[RawJob] = []
            errs_out: List[str] = []
            for query in queries:
                for location in locations:
                    try:
                        jobs = await adapter.search(query, location, max_per_source)
                        jobs_out.extend(jobs)
                        logger.info(
                            "source_search_complete",
                            source=adapter.name,
                            query=query,
                            location=location,
                            count=len(jobs),
                        )
                    except Exception as e:
                        msg = f"{adapter.name}/{query}/{location}: {e}"
                        errs_out.append(msg)
                        logger.error("source_search_failed", source=adapter.name, error=str(e))
            return jobs_out, errs_out

        batches = await asyncio.gather(
            *[_search_adapter(adapter) for adapter in self._adapters.values()]
        )
        all_jobs: List[RawJob] = []
        errors: List[str] = []
        for jobs_out, errs_out in batches:
            all_jobs.extend(jobs_out)
            errors.extend(errs_out)

        return all_jobs, errors
