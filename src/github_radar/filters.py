from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Repository
from .scoring import is_quality_project


def pushed_after(hours: int, now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    return base - timedelta(hours=hours)


def is_recent(repo: Repository, hours: int, now: datetime | None = None) -> bool:
    return repo.pushed_at >= pushed_after(hours, now)


def _word_boundary_match(term: str, haystack: str) -> bool:
    """Match ``term`` as a whole word inside ``haystack``.

    Whole-word matching prevents ``api`` from nuking every library README or
    ``server`` from catching "server-side rendering". Hyphenated terms such as
    ``demo-only`` are handled by treating the hyphen as a word char.
    """
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def has_excluded_term(
    repo: Repository,
    excluded_terms: tuple[str, ...],
    soft_terms: tuple[str, ...] = (),
    soft_exempt_keywords: tuple[str, ...] = (),
) -> bool:
    haystack = " ".join(
        [repo.full_name, repo.name, repo.description, " ".join(repo.topics)]
    ).lower()
    protected = is_quality_project(repo, soft_exempt_keywords)
    for term in excluded_terms:
        term = term.strip().lower()
        if not term:
            continue
        if not _word_boundary_match(term, haystack):
            continue
        # Soft terms (demo/example/sample) are only fatal for non-quality repos.
        if term in soft_terms and protected:
            continue
        return True
    return False


def is_eligible(
    repo: Repository,
    *,
    min_stars: int,
    pushed_within_hours: int,
    excluded_terms: tuple[str, ...],
    soft_terms: tuple[str, ...],
    soft_exempt_keywords: tuple[str, ...] = (),
    now: datetime | None = None,
) -> bool:
    if repo.archived:
        return False
    if repo.stargazers_count < min_stars:
        return False
    if not is_recent(repo, pushed_within_hours, now):
        return False
    if has_excluded_term(repo, excluded_terms, soft_terms, soft_exempt_keywords):
        return False
    return True


def sort_key(repo: Repository) -> tuple[int, float]:
    return (repo.stargazers_count, repo.pushed_at.timestamp())
