"""Run the product-company Java backend scan."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from product_java_scan.catalog import load_companies
from product_java_scan.models import CompanyResult
from product_java_scan.scanner import scan_companies
from product_java_scan.workbook import update_workbook

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "product_java_scan" / "java_backend_vacancies.xlsx"


def _print_progress(result: CompanyResult, completed: int, total: int) -> None:
    print(
        f"[{completed}/{total}] {result.company}: {result.hiring_status} "
        f"({len(result.jobs)} matching roles)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan the product-company career list for Java backend and Spring Boot roles. "
            "This command does not run the job search agent."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--company",
        action="append",
        default=[],
        help="Scan one catalog company. Repeat the flag to scan several. Default: all companies.",
    )
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    catalog = load_companies()
    selected = catalog
    if args.company:
        wanted = {name.casefold() for name in args.company}
        selected = [company for company in catalog if company.name.casefold() in wanted]
        missing = wanted - {company.name.casefold() for company in selected}
        if missing:
            print("Unknown company: " + ", ".join(sorted(missing)))
            return 2
    if args.concurrency < 1:
        print("Concurrency must be at least 1")
        return 2

    results = asyncio.run(
        scan_companies(
            selected,
            concurrency=args.concurrency,
            timeout=args.timeout,
            on_result=_print_progress,
        )
    )
    summary = update_workbook(
        args.output,
        catalog,
        results,
        datetime.now(ZoneInfo("Asia/Kolkata")),
    )
    print()
    print(f"Catalog companies: {summary.catalog_size}")
    print(f"Scanned this run: {summary.scanned}")
    print(f"Newly discovered matching jobs: {len(summary.new_jobs)}")
    print(f"Updated jobs: {summary.updated_jobs}")
    print(f"Actively hiring Java/Spring: {len(summary.actively_hiring)}")
    for name in summary.actively_hiring:
        print(f"  - {name}")
    print(f"Companies requiring another scan: {len(summary.needs_rescan)}")
    for name, _url, reason in summary.needs_rescan[:20]:
        print(f"  - {name}: {reason}")
    if len(summary.needs_rescan) > 20:
        print(f"  - … {len(summary.needs_rescan) - 20} more in the Scan Report sheet")
    print(f"Workbook: {summary.output_path}")
    return 0
