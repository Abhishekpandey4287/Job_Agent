"""Company list supplied by the user. This file is the source of truth."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("companies.tsv")


@dataclass(frozen=True)
class Company:
    name: str
    target_profiles: str
    portal_url: str


def load_companies(path: Path | None = None) -> list[Company]:
    catalog = path or CATALOG_PATH
    companies: list[Company] = []
    seen: set[str] = set()
    with catalog.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected = {"Company Name", "Target Job Profiles", "Live Portal Access Links"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"Unexpected company catalog headers: {reader.fieldnames}")
        for line_no, row in enumerate(reader, start=2):
            name = (row.get("Company Name") or "").strip()
            profiles = (row.get("Target Job Profiles") or "").strip()
            url = (row.get("Live Portal Access Links") or "").strip()
            if not name or not profiles or not url:
                raise ValueError(f"Blank company catalog field on line {line_no}")
            if not url.startswith("https://"):
                raise ValueError(f"Portal link for {name} must be an https URL")
            key = name.casefold()
            if key in seen:
                raise ValueError(f"Duplicate company name: {name}")
            seen.add(key)
            companies.append(Company(name=name, target_profiles=profiles, portal_url=url))
    if not companies:
        raise ValueError("Company catalog is empty")
    return companies
