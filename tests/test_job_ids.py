"""Job ID column must only ever hold the employer's own reference id."""

import pytest

from job_agent.discovery.job_ids import extract_company_reference_id


@pytest.mark.parametrize(
    "source,source_job_id,job_url",
    [
        # Aggregators: the number identifies the board's copy, not the requisition.
        ("shine", "19393891", "https://www.shine.com/jobs/java-fullstack-developer/harjai/19393891"),
        ("indeed", "c58512e68583b3f0", "https://in.indeed.com/rc/clk?jk=c58512e68583b3f0&bb=2vdk"),
        ("naukri", "19393891", "https://www.naukri.com/job-listings-android-dev-19393891"),
        ("weworkremotely", "12345", "https://weworkremotely.com/remote-jobs/acme-android-12345"),
        ("remoteok", "99887", "https://remoteok.com/remote-jobs/99887"),
        ("glassdoor", "abc123", "https://www.glassdoor.co.in/job-listing/abc123"),
        # Tracking URLs must never leak into the column.
        ("indeed", "https://in.indeed.com/pagead/clk?mo=r&ad=-6NYlbfk", "https://in.indeed.com/pagead/clk?mo=r&ad=-6NYlbfk"),
        # Nothing to go on at all.
        ("", "", ""),
    ],
)
def test_portal_listings_have_no_reference_id(source, source_job_id, job_url):
    assert extract_company_reference_id(
        source=source, source_job_id=source_job_id, job_url=job_url
    ) == ""


@pytest.mark.parametrize(
    "source,source_job_id,job_url,expected",
    [
        (
            "lever_careers",
            "0dd11234-883c-44f5-9f88-4bc7790abcde",
            "https://jobs.lever.co/xsolla/0dd11234-883c-44f5-9f88-4bc7790abcde",
            "0dd11234-883c-44f5-9f88-4bc7790abcde",
        ),
        (
            "greenhouse:postman",
            "4567890",
            "https://boards.greenhouse.io/postman/jobs/4567890",
            "4567890",
        ),
        (
            "ashby:openai",
            "",
            "https://jobs.ashbyhq.com/openai/1a2b3c4d-5e6f-7a8b-9c0d",
            "1a2b3c4d-5e6f-7a8b-9c0d",
        ),
        (
            "smartrecruiters:visa",
            "",
            "https://jobs.smartrecruiters.com/Visa/743999912345",
            "743999912345",
        ),
        # Enterprise careers portal exposing an explicit requisition parameter.
        (
            "company_careers",
            "",
            "https://careers.example.com/search/job?requisitionId=REQ-20481",
            "REQ-20481",
        ),
    ],
)
def test_company_ats_reference_id_is_kept(source, source_job_id, job_url, expected):
    assert extract_company_reference_id(
        source=source, source_job_id=source_job_id, job_url=job_url
    ) == expected


def test_company_ats_source_without_usable_id_stays_blank():
    assert extract_company_reference_id(
        source="lever:meesho", source_job_id="https://jobs.lever.co/meesho", job_url=""
    ) == ""
