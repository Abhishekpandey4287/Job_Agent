"""Career-portal (ATS) job id resolution — never invent an id."""

import pytest

from job_agent.discovery.career_portal import (
    JOB_ID,
    POSTING_ID,
    REQUISITION_ID,
    CareerPortalRef,
    board_slug_candidates,
    is_aggregator_url,
    looks_blocked,
    portal_for_url,
    ref_from_page,
    ref_from_url,
)


@pytest.mark.parametrize(
    "url,portal,job_id,id_type",
    [
        ("https://boards.greenhouse.io/acme/jobs/4567890", "Greenhouse", "4567890", POSTING_ID),
        ("https://jobs.lever.co/acme/8f3c1b2a-4d5e-6f70-8192-a3b4c5d6e7f8", "Lever",
         "8f3c1b2a-4d5e-6f70-8192-a3b4c5d6e7f8", POSTING_ID),
        ("https://jobs.ashbyhq.com/acme/1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809", "Ashby",
         "1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809", POSTING_ID),
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/Mumbai/Android-Engineer_R-123456",
            "Workday", "R-123456", REQUISITION_ID,
        ),
        ("https://careers-acme.icims.com/jobs/28471/android-engineer/job", "iCIMS", "28471", JOB_ID),
    ],
)
def test_extracts_id_from_ats_url(url, portal, job_id, id_type):
    ref = ref_from_url(url)
    assert ref.found
    assert ref.portal == portal
    assert ref.job_id == job_id
    assert ref.id_type == id_type


@pytest.mark.parametrize(
    "url",
    [
        "https://in.indeed.com/viewjob?jk=abc123def456",
        "https://www.linkedin.com/jobs/view/4123456789",
        "https://www.naukri.com/job-listings-android-developer-acme-120925001234",
        "https://weworkremotely.com/remote-jobs/acme-android-engineer",
    ],
)
def test_job_boards_never_yield_a_career_portal_id(url):
    assert is_aggregator_url(url)
    assert not ref_from_url(url).found


def test_no_id_in_url_stays_blank():
    assert ref_from_url("https://acme.com/careers") == CareerPortalRef()


def test_jsonld_identifier_wins():
    html = """
    <script type="application/ld+json">
    {"@type":"JobPosting","identifier":{"@type":"PropertyValue","value":"REQ-88123"}}
    </script>
    """
    ref = ref_from_page(html, "https://acme.wd5.myworkdayjobs.com/careers/job/Android")
    assert ref.job_id == "REQ-88123"
    assert ref.id_type == REQUISITION_ID


def test_greenhouse_embedded_job_post_id():
    html = '<script>{"job_board_id":4002543002,"id":8453937002,"jobPostId":8556658002}</script>'
    ref = ref_from_page(html, "https://job-boards.greenhouse.io/acme/jobs/8556658002")
    assert ref.job_id == "8556658002"


def test_placeholder_values_are_rejected():
    for value in ("null", "undefined", "N/A"):
        html = f'<script>{{"requisitionId":"{value}"}}</script>'
        assert not ref_from_page(html, "https://acme.wd1.myworkdayjobs.com/careers").found


def test_board_slug_candidates():
    assert board_slug_candidates("CircleCI") == ["circleci"]
    assert board_slug_candidates("Unknown") == []


def test_portal_detection_by_host():
    assert portal_for_url("https://acme.bamboohr.com/careers/42") == "BambooHR"
    assert portal_for_url("https://in.indeed.com/viewjob?jk=x") == ""


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_rate_limit_responses_are_treated_as_walls(status):
    assert looks_blocked(status, "<html>ok</html>")


class _FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, **_):
        self.calls.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                return _FakeResponse(200, payload)
        return _FakeResponse(404, {})


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_company_board_lookup_on_exact_title_match():
    from job_agent.discovery.career_portal import resolve_via_company_board

    client = _FakeClient(
        {
            "boards/acme/jobs": {
                "jobs": [
                    {"id": 8475304002, "title": "Senior Software Engineer",
                     "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/8475304002"},
                ]
            },
            "v1/boards/acme": {"name": "Acme"},
        }
    )
    ref = await resolve_via_company_board(client, "Acme", "Senior Software Engineer")
    assert ref.portal == "Greenhouse"
    assert ref.job_id == "8475304002"


@pytest.mark.asyncio
async def test_company_board_lookup_rejects_ambiguous_titles():
    from job_agent.discovery.career_portal import resolve_via_company_board

    client = _FakeClient(
        {
            "boards/acme/jobs": {
                "jobs": [
                    {"id": 1, "title": "DevOps Engineer", "absolute_url": "https://a"},
                    {"id": 2, "title": "DevOps Engineer", "absolute_url": "https://b"},
                ]
            },
            "v1/boards/acme": {"name": "Acme"},
        }
    )
    assert not (await resolve_via_company_board(client, "Acme", "DevOps Engineer")).found


@pytest.mark.asyncio
async def test_company_board_lookup_rejects_slug_collision():
    from job_agent.discovery.career_portal import resolve_via_company_board

    client = _FakeClient(
        {
            "boards/sur/jobs": {
                "jobs": [{"id": 999, "title": "Full Stack Developer", "absolute_url": "https://a"}]
            },
            "v1/boards/sur": {"name": "Surveily Sp. z o.o."},
        }
    )
    assert not (await resolve_via_company_board(client, "Sur", "Full Stack Developer")).found
