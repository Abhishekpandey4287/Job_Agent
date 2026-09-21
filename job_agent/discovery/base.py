"""Base class and registry for job source adapters."""

from abc import ABC, abstractmethod
from typing import List

from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)


class JobSourceAdapter(ABC):
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
        all_jobs: List[RawJob] = []
        errors: List[str] = []

        for adapter in self._adapters.values():
            for query in queries:
                for location in locations:
                    try:
                        jobs = await adapter.search(query, location, max_per_source)
                        all_jobs.extend(jobs)
                        logger.info(
                            "source_search_complete",
                            source=adapter.name,
                            query=query,
                            location=location,
                            count=len(jobs),
                        )
                    except Exception as e:
                        msg = f"{adapter.name}/{query}/{location}: {e}"
                        errors.append(msg)
                        logger.error("source_search_failed", source=adapter.name, error=str(e))

        return all_jobs, errors
