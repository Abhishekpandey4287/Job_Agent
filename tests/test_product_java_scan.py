"""Tests for the standalone product-company Java scanner."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from product_java_scan.ats import board_from_url, detect_boards
from product_java_scan.catalog import load_companies
from product_java_scan.decide import build_result
from product_java_scan.locations import location_check
from product_java_scan.models import BoardFetch, CompanyResult, FoundJob, RawPosting
from product_java_scan.scanner import scan_company
from product_java_scan.scoring import classify_role, experience_acceptable
from product_java_scan.workbook import update_workbook

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_is_the_supplied_company_list():
    companies = load_companies()
    assert len(companies) == 200
    assert len({company.name.casefold() for company in companies}) == 200
    assert companies[0].name == "Google India"
    assert companies[-1].name == "Cyan Robotics"
    assert companies[0].portal_url.startswith("https://careers.google.com/jobs/results/")
    paper = next(company for company in companies if company.name == "Paper Boat")
    assert paper.portal_url == "https://hector.darwinbox.in/ms/candidatev2/main/careers/allJobs"
    trip = next(company for company in companies if company.name == "MakeMyTrip")
    assert trip.portal_url == "https://www.maketrip.com/careers/"


def test_package_does_not_import_the_job_agent():
    for path in (ROOT / "product_java_scan").rglob("*.py"):
        assert "job_agent" not in path.read_text(encoding="utf-8")


def test_role_priority_scores():
    spring = classify_role("Spring Boot Developer", "Java microservices")
    assert spring is not None
    assert spring.role == "Spring Boot Developer"
    assert spring.score == 97

    backend = classify_role("Senior Java Backend Developer", "3-5 years")
    assert backend is not None
    assert backend.role == "Java Backend Developer"
    assert backend.score == 95

    fullstack = classify_role("Java React Full Stack Developer", "Spring Boot")
    assert fullstack is not None
    assert fullstack.role == "Java + React Full Stack"

    sde = classify_role("Backend SDE", "Build Java services with Spring Boot")
    assert sde is not None
    assert sde.role == "Spring Boot Developer"

    generic = classify_role("Software Engineer", "Java, Spring Boot, Kafka, 4+ years")
    assert generic is not None
    assert generic.score >= 70

    assert classify_role("Account Executive", "Java") is None
    assert classify_role("Android Developer", "Java, Kotlin") is None
    assert classify_role("Java Backend Intern", "Spring Boot") is None


def test_three_to_five_years_is_kept_and_senior_ranges_are_not():
    assert experience_acceptable("3-5 years") is True
    assert experience_acceptable("3 to 5 years") is True
    assert experience_acceptable("2-4 years") is True
    assert experience_acceptable("1-3 years") is True
    assert experience_acceptable("2+ years") is True
    assert experience_acceptable("up to 5 years") is True
    assert experience_acceptable("4+ years") is False
    assert experience_acceptable("4-6 years") is False
    assert experience_acceptable("5-8 years") is False
    assert experience_acceptable("6+ years") is False
    assert experience_acceptable("10+ years") is False
    assert experience_acceptable("") is None


def test_recent_role_under_four_years_is_kept_and_older_or_senior_roles_are_not():
    company = next(item for item in load_companies() if item.name == "Paytm")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    recent = today.isoformat()
    result = build_result(
        company,
        final_url=company.portal_url,
        board_fetches=[
            BoardFetch(
                label="greenhouse:paytm",
                complete=True,
                postings=[
                    RawPosting(
                        title="Java Backend Developer",
                        location="Bengaluru, India",
                        url="https://example.com/jobs/recent",
                        description="Java Spring Boot. 3-5 years.",
                        posting_date=recent,
                    ),
                    RawPosting(
                        title="Java Backend Developer",
                        location="Noida, India",
                        url="https://example.com/jobs/senior",
                        description="Java Spring Boot. 5-8 years.",
                        posting_date=recent,
                    ),
                    RawPosting(
                        title="Spring Boot Developer",
                        location="Noida, India",
                        url="https://example.com/jobs/old",
                        description="Java Spring Boot. 2-3 years.",
                        posting_date="2020-01-01",
                    ),
                ],
            )
        ],
        html_postings=[],
        js_shell=False,
        explicit_empty=False,
        today=today,
    )
    assert result.hiring_status == "Actively hiring Java/Spring"
    assert [job.url for job in result.jobs] == ["https://example.com/jobs/recent"]
    assert result.jobs[0].experience == "3-5 years"


def test_location_does_not_treat_the_word_in_as_india():
    assert location_check("Engineer in New York") == "outside_india"
    assert location_check("Bengaluru, India") == "india"
    assert location_check("Bengaluru / San Francisco") == "india"
    assert location_check("") == "unspecified"
    assert location_check("Remote - United States") == "outside_india"
    assert location_check("Prague, Czech Republic") == "outside_india"
    assert location_check("Malmö") == "outside_india"
    assert location_check("Remote: EMEA") == "outside_india"
    assert classify_role("Manager, Software Engineering", "Java and Spring Boot") is None


def test_workday_portal_is_detected_from_the_catalog_link():
    adobe = next(company for company in load_companies() if company.name == "Adobe India")
    board = board_from_url(adobe.portal_url)
    assert board is not None
    assert board.kind == "workday"
    assert board.tenant == "adobe"
    assert board.site == "external_experienced"
    html = '<a href="https://boards.greenhouse.io/phonepe">Open roles</a>'
    found = detect_boards("https://www.phonepe.com/careers/", "https://www.phonepe.com/careers/", html)
    assert any(item.kind == "greenhouse" and item.slug == "phonepe" for item in found)


def test_failed_portal_is_not_recorded_as_no_vacancies():
    company = load_companies()[0]
    result = build_result(
        company,
        final_url=company.portal_url,
        board_fetches=[BoardFetch(label="greenhouse:google", error="HTTP 503")],
        html_postings=[],
        js_shell=True,
        explicit_empty=True,
    )
    assert result.hiring_status == "Needs another scan"
    assert result.needs_rescan is True
    assert result.listing_complete is False
    assert result.jobs == []
    assert "No priority" not in result.hiring_status


def test_found_java_role_does_not_require_rescan_when_the_board_was_read():
    company = next(item for item in load_companies() if item.name == "Razorpay")
    postings = [
        RawPosting(
            title="Java Backend Developer",
            location="Bengaluru, India",
            url="https://boards.greenhouse.io/razorpay/jobs/1",
            description="Java and Spring Boot. 3-5 years.",
            posting_date=datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
            verified_via="ats:greenhouse",
        ),
        RawPosting(
            title="Software Engineer",
            location="Bengaluru, India",
            url="https://boards.greenhouse.io/razorpay/jobs/2",
            description="",
            verified_via="ats:greenhouse",
        ),
    ]
    result = build_result(
        company,
        final_url="https://boards.greenhouse.io/razorpay",
        board_fetches=[BoardFetch(label="greenhouse:razorpay", postings=postings, complete=True)],
        html_postings=[],
        js_shell=False,
        explicit_empty=False,
    )
    assert result.hiring_status == "Actively hiring Java/Spring"
    assert result.needs_rescan is False
    assert result.listing_complete is False
    assert len(result.jobs) == 1


def test_unread_generic_titles_are_not_treated_as_no_vacancies():
    company = next(item for item in load_companies() if item.name == "Razorpay")
    result = build_result(
        company,
        final_url="https://boards.greenhouse.io/razorpay",
        board_fetches=[
            BoardFetch(
                label="greenhouse:razorpay",
                postings=[
                    RawPosting(
                        title="Software Engineer",
                        location="Bengaluru, India",
                        url="https://boards.greenhouse.io/razorpay/jobs/2",
                        verified_via="ats:greenhouse",
                    )
                ],
                complete=True,
            )
        ],
        html_postings=[],
        js_shell=False,
        explicit_empty=False,
    )
    assert result.hiring_status == "Needs another scan"
    assert result.needs_rescan is True
    assert result.jobs == []


def test_verified_empty_board_can_be_confirmed():
    company = next(item for item in load_companies() if item.name == "Postman")
    result = build_result(
        company,
        final_url="https://boards.greenhouse.io/postman",
        board_fetches=[BoardFetch(label="greenhouse:postman", postings=[], complete=True)],
        html_postings=[],
        js_shell=False,
        explicit_empty=False,
    )
    assert result.hiring_status == "No priority roles on verified listing"
    assert result.needs_rescan is False


def _job(company: str, url: str, title: str = "Java Backend Developer", score: int = 95) -> FoundJob:
    return FoundJob(
        company=company,
        title=title,
        location="Bengaluru, India",
        experience="3-5 years",
        skills="Java, Spring Boot",
        url=url,
        posting_date="2026-10-12",
        match_score=score,
        role_match="Java Backend Developer",
        verified_via="ats:greenhouse",
        location_check="india",
    )


def _result(company_name: str, jobs: list[FoundJob], *, loaded: bool = True, complete: bool = True) -> CompanyResult:
    catalog = {company.name: company for company in load_companies()}
    company = catalog[company_name]
    active = any(job.match_score >= 85 and job.role_match == "Java Backend Developer" for job in jobs)
    if not loaded:
        status = "Scan failed"
    elif jobs and active:
        status = "Actively hiring Java/Spring"
    elif jobs:
        status = "Priority roles found"
    elif complete:
        status = "No priority roles on verified listing"
    else:
        status = "Needs another scan"
    return CompanyResult(
        company=company.name,
        target_profiles=company.target_profiles,
        portal_url=company.portal_url,
        final_url=company.portal_url,
        hiring_status=status,
        scanning_failure="" if loaded else "HTTP 503",
        needs_rescan=not loaded or not complete,
        notes="" if loaded else "No vacancy decision was made because the careers portal did not load.",
        ats_checked="greenhouse:example" if loaded else "",
        jobs=jobs,
        portal_loaded=loaded,
        listing_complete=complete and loaded,
        actively_hiring=active and loaded,
    )


def test_workbook_updates_the_same_job_and_keeps_unscanned_companies(tmp_path: Path):
    output = tmp_path / "java_backend_vacancies.xlsx"
    catalog = [company for company in load_companies() if company.name in {"Postman", "Razorpay"}]
    stamp = datetime(2026, 10, 14, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    url = "https://boards.greenhouse.io/postman/jobs/123"
    first = update_workbook(
        output,
        catalog,
        [_result("Postman", [_job("Postman", url)])],
        stamp,
    )
    assert len(first.new_jobs) == 1
    assert first.actively_hiring == ["Postman"]
    assert any(name == "Razorpay" for name, _url, _reason in first.needs_rescan)

    revised = _job("Postman", url + "?utm_source=board")
    revised = FoundJob(
        company=revised.company,
        title=revised.title,
        location=revised.location,
        experience="1-3 years",
        skills=revised.skills,
        url=revised.url,
        posting_date=revised.posting_date,
        match_score=revised.match_score,
        role_match=revised.role_match,
        verified_via=revised.verified_via,
        location_check=revised.location_check,
    )
    second = update_workbook(
        output,
        catalog,
        [_result("Postman", [revised]), _result("Razorpay", [], loaded=False, complete=False)],
        stamp.replace(hour=10),
    )
    assert second.new_jobs == []
    assert second.updated_jobs == 1

    from openpyxl import load_workbook

    workbook = load_workbook(output)
    vacancies = list(workbook["Vacancies"].iter_rows(min_row=2, values_only=True))
    assert len(vacancies) == 1
    headers = [cell.value for cell in workbook["Vacancies"][1]]
    row = dict(zip(headers, vacancies[0]))
    assert row["Experience"] == "1-3 years"
    assert row["Record Status"] == "Updated"
    assert row["First Seen"] == "2026-10-14 09:00 IST"
    assert row["Last Seen"] == "2026-10-14 10:00 IST"
    statuses = {
        row[0]: row[3]
        for row in workbook["Company Status"].iter_rows(min_row=2, values_only=True)
    }
    assert statuses["Postman"] == "Actively hiring Java/Spring"
    assert statuses["Razorpay"] == "Scan failed"


def test_failed_rescan_keeps_existing_vacancy(tmp_path: Path):
    output = tmp_path / "java_backend_vacancies.xlsx"
    catalog = [company for company in load_companies() if company.name == "Zepto"]
    stamp = datetime(2026, 10, 14, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    update_workbook(
        output,
        catalog,
        [_result("Zepto", [_job("Zepto", "https://example.com/jobs/9")])],
        stamp,
    )
    update_workbook(
        output,
        catalog,
        [_result("Zepto", [], loaded=False, complete=False)],
        stamp.replace(hour=11),
    )
    from openpyxl import load_workbook

    workbook = load_workbook(output)
    headers = [cell.value for cell in workbook["Vacancies"][1]]
    rows = list(workbook["Vacancies"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 1
    row = dict(zip(headers, rows[0]))
    assert row["Job Title"] == "Java Backend Developer"
    assert row["Record Status"] == "Carried forward"
    status = next(workbook["Company Status"].iter_rows(min_row=2, values_only=True))
    assert status[3] == "Scan failed"
    assert status[6] == "Yes"


@pytest.mark.asyncio
async def test_scan_reads_linked_greenhouse_and_skips_non_india_roles():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/jobs/123"):
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "content": "<p>Java Spring Boot microservices. 3-5 years. Kafka.</p>",
                    "updated_at": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat() + "T00:00:00Z",
                },
            )
        if "/jobs/" in url:
            return httpx.Response(404)
        if url.rstrip("/").endswith("/jobs"):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 123,
                            "title": "Java Backend Developer",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                            "location": {"name": "Bengaluru, India"},
                        },
                        {
                            "id": 456,
                            "title": "Java Backend Developer",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/456",
                            "location": {"name": "Seattle, United States"},
                        },
                    ]
                },
            )
        if "example.test" in url:
            html = "<html><body><a href='https://boards.greenhouse.io/acme'>Jobs</a><p>Open roles</p></body></html>"
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    company = next(item for item in load_companies() if item.name == "PhonePe")
    company = type(company)(
        name=company.name,
        target_profiles=company.target_profiles,
        portal_url="https://jobs.example.test/careers",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        result = await scan_company(client, company)

    assert result.portal_loaded is True
    assert result.hiring_status == "Actively hiring Java/Spring"
    assert result.needs_rescan is False
    assert len(result.jobs) == 1
    assert result.jobs[0].location == "Bengaluru, India"
    assert "Spring Boot" in result.jobs[0].skills
    assert result.jobs[0].experience == "3-5 years"
    assert result.jobs[0].posting_date == datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert result.jobs[0].verified_via == "ats:greenhouse"


@pytest.mark.asyncio
async def test_http_failure_does_not_claim_the_company_has_no_roles():
    company = next(item for item in load_companies() if item.name == "Cred")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scan_company(client, company)

    assert result.hiring_status == "Scan failed"
    assert result.needs_rescan is True
    assert result.portal_loaded is False
    assert result.jobs == []
    assert "no vacancy" in result.notes.lower() or "did not load" in result.notes.lower()
