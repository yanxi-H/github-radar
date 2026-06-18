from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Repository:
    full_name: str
    name: str
    owner: str
    html_url: str
    description: str
    topics: tuple[str, ...]
    stargazers_count: int
    pushed_at: datetime
    language: str | None = None
    archived: bool = False
    readme: str = ""
    summary: "RepoSummary | None" = None
    final_score: float = 0.0
    domain_relevance_score: float = 0.0
    practical_value_score: float = 0.0
    presentation_score: float = 0.0
    learning_value_score: float = 0.0
    health_score: float = 0.0
    star_growth: int = 0
    tags: tuple[str, ...] = ()
    project_type: str = "其他"

    @classmethod
    def from_github_item(cls, item: dict[str, Any]) -> "Repository":
        pushed_at = parse_github_datetime(item.get("pushed_at"))
        owner = item.get("owner") or {}
        return cls(
            full_name=str(item.get("full_name") or ""),
            name=str(item.get("name") or ""),
            owner=str(owner.get("login") or ""),
            html_url=str(item.get("html_url") or ""),
            description=str(item.get("description") or ""),
            topics=tuple(item.get("topics") or ()),
            stargazers_count=int(item.get("stargazers_count") or 0),
            pushed_at=pushed_at,
            language=item.get("language"),
            archived=bool(item.get("archived")),
        )

    def with_readme(self, readme: str) -> "Repository":
        return replace_kwarg(self, readme=readme)

    def with_summary(self, summary: "RepoSummary | None") -> "Repository":
        return replace_kwarg(self, summary=summary)

    def with_scores(
        self,
        *,
        final_score: float,
        domain_relevance_score: float,
        practical_value_score: float,
        presentation_score: float,
        learning_value_score: float,
        health_score: float,
        star_growth: int,
        tags: tuple[str, ...],
        project_type: str,
    ) -> "Repository":
        return replace_kwarg(
            self,
            final_score=final_score,
            domain_relevance_score=domain_relevance_score,
            practical_value_score=practical_value_score,
            presentation_score=presentation_score,
            learning_value_score=learning_value_score,
            health_score=health_score,
            star_growth=star_growth,
            tags=tags,
            project_type=project_type,
        )

    @property
    def score_breakdown(self) -> dict[str, float]:
        """Backwards-compatible view used by logging and seen.json."""
        return {
            "domain_relevance": round(self.domain_relevance_score, 1),
            "practical_value": round(self.practical_value_score, 1),
            "presentation": round(self.presentation_score, 1),
            "learning_value": round(self.learning_value_score, 1),
            "health": round(self.health_score, 1),
            "final_score": round(self.final_score, 1),
        }

    @property
    def pushed_at_iso(self) -> str:
        return self.pushed_at.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RepoSummary:
    """Structured, anti-hallucination summary of a repo."""

    positioning: str
    highlights: tuple[str, ...]
    usage: str
    source_points: tuple[str, ...]
    integration: str


@dataclass(frozen=True)
class NotificationResult:
    channel: str
    ok: bool
    detail: str = ""


@dataclass
class RunStats:
    queried: int = 0
    candidates: int = 0
    selected: int = 0
    notified_channels: list[NotificationResult] = field(default_factory=list)


def parse_github_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def replace_kwarg(repo: Repository, **changes: Any) -> Repository:
    """Frozen-dataclass copy helper that preserves all unspecified fields."""
    return Repository(**{**repo.__dict__, **changes})
