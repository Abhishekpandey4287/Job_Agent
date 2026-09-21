"""Load ATS board slugs from data file + environment overrides."""

import json
from pathlib import Path
from typing import List

from job_agent.config import get_settings

BOARDS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "discovery" / "company_boards.json"


def _load_json_boards() -> dict:
    if not BOARDS_FILE.exists():
        return {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": []}
    return json.loads(BOARDS_FILE.read_text(encoding="utf-8"))


def greenhouse_boards() -> List[str]:
    settings = get_settings()
    env = settings.greenhouse_board_list
    if env:
        return env
    return _load_json_boards().get("greenhouse", [])


def lever_companies() -> List[str]:
    settings = get_settings()
    env = settings.lever_company_list
    if env:
        return env
    return _load_json_boards().get("lever", [])


def ashby_orgs() -> List[str]:
    settings = get_settings()
    if settings.ashby_orgs_list:
        return settings.ashby_orgs_list
    return _load_json_boards().get("ashby", [])


def smartrecruiters_companies() -> List[str]:
    settings = get_settings()
    if settings.smartrecruiters_companies_list:
        return settings.smartrecruiters_companies_list
    return _load_json_boards().get("smartrecruiters", [])
