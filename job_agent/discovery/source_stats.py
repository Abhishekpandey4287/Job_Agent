"""Structured per-source discovery statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SourceStats:
    name: str
    status: str = "PENDING"  # SUCCESS | FAILED | BLOCKED | SKIPPED | DISABLED
    raw_discovered: int = 0
    stage: str = ""
    error: str = ""
    blocked_reason: str = ""
    queries_run: int = 0

    def mark_success(self, count: int) -> None:
        self.status = "SUCCESS"
        self.raw_discovered = count

    def mark_blocked(self, reason: str) -> None:
        self.status = "BLOCKED"
        self.blocked_reason = reason
        self.stage = "security_challenge"

    def mark_failed(self, stage: str, error: str) -> None:
        self.status = "FAILED"
        self.stage = stage
        self.error = error


class DiscoveryStatsCollector:
    """Collect and print source-level discovery metrics."""

    def __init__(self) -> None:
        self._sources: Dict[str, SourceStats] = {}

    def get(self, name: str) -> SourceStats:
        if name not in self._sources:
            self._sources[name] = SourceStats(name=name)
        return self._sources[name]

    def record_success(self, name: str, count: int, queries_run: int = 1) -> None:
        stat = self.get(name)
        stat.mark_success(count)
        stat.queries_run += queries_run

    def record_blocked(self, name: str, reason: str) -> None:
        self.get(name).mark_blocked(reason)

    def record_failed(self, name: str, stage: str, error: str) -> None:
        self.get(name).mark_failed(stage, error)

    def record_disabled(self, name: str, reason: str = "not configured") -> None:
        stat = self.get(name)
        stat.status = "DISABLED"
        stat.error = reason

    def merge_portal_counts(self, portal_counts: Dict[str, int], blocked: Dict[str, str]) -> None:
        for portal, count in portal_counts.items():
            if portal in blocked:
                reason = blocked[portal]
                # Early-abort empty/login walls are not security challenges
                if "0 jobs" in reason or "login" in reason.lower() or "selector" in reason.lower():
                    self.record_disabled(portal, reason)
                else:
                    self.record_blocked(portal, reason)
            elif count == 0:
                self.record_disabled(portal, "0 jobs (login wall / selector / empty)")
            else:
                self.record_success(portal, count)

    def format_health(self) -> str:
        lines = ["SOURCE HEALTH"]
        for name in sorted(self._sources.keys()):
            s = self._sources[name]
            if s.status == "SUCCESS":
                mark = "✅" if s.raw_discovered > 0 else "⚠️"
                lines.append(f"  {name:<22} {mark} {s.raw_discovered}")
            elif s.status == "BLOCKED":
                lines.append(f"  {name:<22} ❌ BLOCKED {s.blocked_reason[:40]}")
            elif s.status == "DISABLED":
                lines.append(f"  {name:<22} ⏭  {s.error[:40]}")
            elif s.status == "FAILED":
                lines.append(f"  {name:<22} ❌ {s.error[:40]}")
            else:
                lines.append(f"  {name:<22} {s.status} {s.raw_discovered}")
        return "\n".join(lines)

    def format_summary(
        self,
        total_raw: int,
        after_dedup: int,
        relevant_android: int,
        relevant_backend: int,
        relevant_hybrid: int,
        within_24h: int,
        top_count: int,
    ) -> str:
        lines = [
            "=" * 44,
            "JOB SOURCE DISCOVERY",
            "=" * 44,
        ]
        for name in sorted(self._sources.keys()):
            s = self._sources[name]
            if s.status == "SUCCESS":
                lines.append(f"{name:<22} SUCCESS   {s.raw_discovered:>4} jobs")
            elif s.status == "BLOCKED":
                lines.append(f"{name:<22} BLOCKED   {s.blocked_reason[:30]}")
            elif s.status == "DISABLED":
                lines.append(f"{name:<22} DISABLED  {s.error[:30]}")
            elif s.status == "FAILED":
                lines.append(f"{name:<22} FAILED    {s.stage}: {s.error[:24]}")
            else:
                lines.append(f"{name:<22} {s.status:<8}  {s.raw_discovered:>4} jobs")

        lines.extend([
            "-" * 44,
            f"Total raw jobs:        {total_raw}",
            f"After deduplication:   {after_dedup}",
            f"Relevant Android:      {relevant_android}",
            f"Relevant Backend:      {relevant_backend}",
            f"Relevant Hybrid:       {relevant_hybrid}",
            f"Jobs within 24h:       {within_24h}",
            f"Top jobs in report:    {top_count}",
            "=" * 44,
        ])
        return "\n".join(lines)
