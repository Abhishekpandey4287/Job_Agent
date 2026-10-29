"""Drop jobs that already appear in earlier Excel files under output/.

Edit ``RULES`` in this file to change what counts as a duplicate or as
already applied. The job search calls ``exclude_known_jobs`` and does not
contain the matching rules itself.

A job is removed when any previous workbook has the same application link,
the same job id, or the same company and title. A row whose Status is
applied, interviewing, offered, or rejected is always removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse

from openpyxl import load_workbook

from job_agent.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class HistoryRules:
    """Knobs for the history comparison. Change these, not the search pipeline."""

    # Remove a job when any older workbook already lists it.
    drop_previously_seen: bool = True
    # Same company and title counts as the same job even if the city text differs.
    match_company_and_title: bool = True
    # Also treat these Status / Application Status values as already applied.
    # Matching is case-insensitive. The word "applied" is always included.
    applied_statuses: tuple[str, ...] = (
        "applied",
        "application_started",
        "interview",
        "offer",
        "rejected",
    )


RULES = HistoryRules()


@dataclass
class HistoryReport:
    kept: list
    removed_seen: int = 0
    removed_applied: int = 0
    workbooks: list[Path] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return self.removed_seen + self.removed_applied


@dataclass
class _PastJob:
    keys: set[str]
    applied: bool
    label: str


def exclude_known_jobs(
    rows: Sequence,
    output_dir: Path,
    *,
    skip_file: Path | None = None,
    rules: HistoryRules | None = None,
) -> HistoryReport:
    """Return ``rows`` with jobs from earlier workbooks taken out.

    ``rows`` items are ``(match, role_track, resume_used)`` tuples from the
    job search. ``skip_file`` is the workbook about to be written, so a
    re-run the same day does not erase that file's own fresh jobs.
    """
    active = rules or RULES
    past = _load_history(output_dir, skip_file=skip_file, rules=active)
    if not past:
        return HistoryReport(kept=list(rows), workbooks=_workbook_paths(output_dir, skip_file))

    kept = []
    removed_seen = 0
    removed_applied = 0
    samples: list[str] = []
    for row in rows:
        match = row[0]
        keys = keys_for_job(match.job, rules=active)
        hit = _find_hit(keys, past, rules=active)
        if hit is None:
            kept.append(row)
            continue
        if hit.applied:
            removed_applied += 1
            reason = "already applied"
        else:
            removed_seen += 1
            reason = "already in an earlier Excel"
        if len(samples) < 8:
            job = match.job
            samples.append(f"{job.company} — {job.title} ({reason})")
        logger.info(
            "history_job_skipped",
            company=getattr(match.job, "company", ""),
            title=getattr(match.job, "title", ""),
            reason=reason,
        )

    report = HistoryReport(
        kept=kept,
        removed_seen=removed_seen,
        removed_applied=removed_applied,
        workbooks=_workbook_paths(output_dir, skip_file),
        samples=samples,
    )
    logger.info(
        "history_filter_complete",
        kept=len(kept),
        removed_seen=removed_seen,
        removed_applied=removed_applied,
        workbooks=len(report.workbooks),
    )
    return report


def keys_for_job(job, *, rules: HistoryRules | None = None) -> set[str]:
    """Identity keys for a newly discovered job. Edit this to match differently."""
    active = rules or RULES
    urls = [
        getattr(job, "application_url", "") or "",
        getattr(job, "job_url", "") or "",
        getattr(job, "career_portal_url", "") or "",
    ]
    ids = [
        getattr(job, "source_job_id", "") or "",
        getattr(job, "career_portal_job_id", "") or "",
    ]
    return _keys(
        title=getattr(job, "title", "") or "",
        company=getattr(job, "company", "") or "",
        urls=urls,
        job_ids=ids,
        rules=active,
    )


def _workbook_paths(output_dir: Path, skip_file: Path | None) -> list[Path]:
    root = Path(output_dir)
    if not root.exists():
        return []
    skip = skip_file.resolve() if skip_file else None
    found: list[Path] = []
    for path in sorted(root.rglob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        if skip and path.resolve() == skip:
            continue
        found.append(path)
    return found


def _load_history(
    output_dir: Path,
    *,
    skip_file: Path | None,
    rules: HistoryRules,
) -> list[_PastJob]:
    past: list[_PastJob] = []
    for path in _workbook_paths(output_dir, skip_file):
        try:
            past.extend(_read_workbook(path, rules))
        except Exception as e:
            logger.warning("history_workbook_skipped", path=str(path), error=str(e)[:160])
    return past


def _read_workbook(path: Path, rules: HistoryRules) -> list[_PastJob]:
    wb = load_workbook(str(path), read_only=False, data_only=False)
    jobs: list[_PastJob] = []
    try:
        for ws in wb.worksheets:
            jobs.extend(_read_sheet(ws, rules))
    finally:
        wb.close()
    return jobs


def _read_sheet(ws, rules: HistoryRules) -> list[_PastJob]:
    rows = ws.iter_rows(min_row=1, max_row=1)
    header_row = next(rows, None)
    if not header_row:
        return []
    headers: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        name = str(cell.value or "").strip().lower()
        if name:
            headers[name] = index
    if "job title" not in headers or "company" not in headers:
        return []

    jobs: list[_PastJob] = []
    for row in ws.iter_rows(min_row=2):
        title = _text(row, headers, "job title")
        company = _text(row, headers, "company")
        if not title or not company:
            continue
        urls = [
            _link(row, headers, "direct application link"),
            _link(row, headers, "career portal url"),
        ]
        job_ids = [
            _text(row, headers, "job id"),
            _text(row, headers, "career portal job id"),
        ]
        status = _text(row, headers, "status") or _text(row, headers, "application status")
        keys = _keys(title=title, company=company, urls=urls, job_ids=job_ids, rules=rules)
        if not keys:
            continue
        jobs.append(_PastJob(keys=keys, applied=_is_applied(status, rules), label=f"{company} — {title}"))
    return jobs


def _text(row, headers: dict[str, int], name: str) -> str:
    index = headers.get(name)
    if index is None or index >= len(row):
        return ""
    return str(row[index].value or "").strip()


def _link(row, headers: dict[str, int], name: str) -> str:
    index = headers.get(name)
    if index is None or index >= len(row):
        return ""
    cell = row[index]
    target = ""
    if cell.hyperlink is not None and cell.hyperlink.target:
        target = str(cell.hyperlink.target).strip()
    if target.startswith("http"):
        return target
    value = str(cell.value or "").strip()
    if value.lower() in ("", "apply here", "not generated", "—", "-"):
        return ""
    return value


def _keys(
    *,
    title: str,
    company: str,
    urls: Iterable[str],
    job_ids: Iterable[str],
    rules: HistoryRules,
) -> set[str]:
    keys: set[str] = set()
    for url in urls:
        canon = _canon_url(url)
        if canon:
            keys.add(f"url:{canon}")
    for job_id in job_ids:
        token = _norm_id(job_id)
        if token:
            keys.add(f"id:{token}")
    if rules.match_company_and_title:
        company_key = _norm_text(company)
        title_key = _norm_text(title)
        if company_key and title_key:
            keys.add(f"role:{company_key}|{title_key}")
    return keys


def _find_hit(keys: set[str], past: Sequence[_PastJob], *, rules: HistoryRules) -> _PastJob | None:
    if not keys:
        return None
    seen = None
    for item in past:
        if not (keys & item.keys):
            continue
        if item.applied:
            return item
        if rules.drop_previously_seen and seen is None:
            seen = item
    return seen


def _is_applied(status: str, rules: HistoryRules) -> bool:
    raw = (status or "").strip().lower()
    if not raw or "not applied" in raw:
        return False
    if re.search(r"\bapplied\b", raw):
        return True
    token = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return token in {item.strip().lower() for item in rules.applied_statuses}


def _canon_url(url: str) -> str:
    text = (url or "").strip()
    if not text.lower().startswith("http"):
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/").lower()
    if not host:
        return ""
    return f"{host}{path}"


def _norm_text(value: str) -> str:
    text = re.sub(r"[^a-z0-9+#]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _norm_id(value: str) -> str:
    text = (value or "").strip().lower()
    if not text or text.startswith("http") or text in ("n/a", "na", "—", "-"):
        return ""
    return re.sub(r"\s+", "", text)
