"""CLI entry point for the Job Search Agent."""

import asyncio
import sys

import uvicorn

from job_agent.config import get_settings
from job_agent.logging_config import setup_logging
from job_agent.pipeline.daily_pipeline import DailyPipeline


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        settings = get_settings()
        setup_logging(settings.log_level)
        pipeline = DailyPipeline()
        summary = asyncio.run(pipeline.run())
        print(f"\nPipeline complete!")
        print(f"  Jobs discovered: {summary.jobs_discovered}")
        print(f"  Relevant Android jobs: {summary.relevant_android_jobs}")
        print(f"  Within recency: {summary.jobs_within_recency}")
        print(f"  Strong matches: {summary.strong_matches}")
        print(f"  Resumes generated: {summary.resumes_generated}")
        print(f"  Output: {summary.output_path}")
        if summary.errors:
            print(f"  Errors: {len(summary.errors)}")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        settings = get_settings()
        uvicorn.run(
            "job_agent.api.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=False,
        )
        return

    print("Job Search Agent")
    print("  python -m job_agent search   — Run daily job search pipeline")
    print("  python -m job_agent serve    — Start API server + scheduler")


if __name__ == "__main__":
    main()
