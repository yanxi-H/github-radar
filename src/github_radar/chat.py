"""Interactive chat bot: receive ClawBot messages, search GitHub, reply via PushPlus."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import AppConfig, SearchQuery
from .filters import is_eligible, sort_key
from .github_client import GitHubClient
from .mimo_client import MimoClient
from .scoring import score_repository
from .storage import SeenStore

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MIN_STARS = 100
DEFAULT_CHAT_MAX_RESULTS = 3
DEFAULT_CHAT_LANGUAGE = None  # Search all languages unless user specifies.


def handle_search_query(
    query_text: str,
    config: AppConfig,
    *,
    language: str | None = DEFAULT_CHAT_LANGUAGE,
    min_stars: int = DEFAULT_CHAT_MIN_STARS,
    max_results: int = DEFAULT_CHAT_MAX_RESULTS,
) -> str:
    """Run a search for the given keywords and return a formatted result string.

    Reuses the existing radar pipeline: search → filter → score → summarize → format.
    """
    now = datetime.now(timezone.utc)
    github = GitHubClient(config.github)
    mimo = MimoClient(config.mimo)

    query = SearchQuery(text=query_text, language=language, min_stars=min_stars)
    logger.info("Chat search: query=%r lang=%s min_stars=%s", query_text, language, min_stars)

    # Search GitHub.
    from .filters import pushed_after

    cutoff = pushed_after(config.pushed_within_hours, now)
    try:
        repos = github.search_repositories(query, cutoff)
    except Exception as exc:  # noqa: BLE001
        logger.error("Chat search failed: %s", exc)
        return f"搜索失败：{exc}"

    if not repos:
        return f"没有搜到「{query_text}」相关的项目，换个关键词试试？"

    # Filter.
    eligible = []
    for repo in repos:
        if not repo.full_name:
            continue
        if is_eligible(
            repo,
            min_stars=min_stars,
            pushed_within_hours=config.pushed_within_hours,
            excluded_terms=config.excluded_terms,
            soft_terms=config.soft_excluded_terms,
            soft_exempt_keywords=config.soft_exempt_keywords,
            now=now,
        ):
            eligible.append(repo)

    if not eligible:
        return f"搜到 {len(repos)} 个项目，但都不符合条件（star ≥ {min_stars}、近期活跃）。"

    # Score.
    scored = []
    for repo in sorted(eligible, key=sort_key, reverse=True)[: max_results * 3]:
        readme = github.fetch_readme(repo.full_name, config.readme_max_chars)
        repo_with_readme = repo.with_readme(readme)
        scored_repo = score_repository(
            repo_with_readme,
            domain_keywords=config.domain_keywords,
            previous_stars=None,
            now=now,
            pushed_within_hours=config.pushed_within_hours,
        )
        scored.append(scored_repo)

    # Select top N.
    selected = sorted(scored, key=lambda r: r.final_score, reverse=True)[:max_results]

    # AI summarize.
    enriched = []
    for repo in selected:
        summary = mimo.summarize(repo)
        enriched.append(repo.with_summary(summary))

    # Format result.
    from .ai_message_composer import format_compact_wechat_digest

    return format_compact_wechat_digest(enriched, f"🔍 搜索「{query_text}」")


def detect_language(text: str) -> str | None:
    """Try to detect a programming language from the user's message."""
    text_lower = text.lower()
    lang_map = {
        "python": "Python",
        "rust": "Rust",
        "go": "Go",
        "golang": "Go",
        "java": "Java",
        "kotlin": "Kotlin",
        "swift": "Swift",
        "typescript": "TypeScript",
        "ts": "TypeScript",
        "javascript": "JavaScript",
        "js": "JavaScript",
        "react": "TypeScript",
        "vue": "TypeScript",
        "svelte": "TypeScript",
        "c++": "C++",
        "cpp": "C++",
        "c#": "C#",
        "csharp": "C#",
        "ruby": "Ruby",
        "php": "PHP",
        "dart": "Dart",
        "flutter": "Dart",
        "elixir": "Elixir",
    }
    for keyword, lang in lang_map.items():
        if keyword in text_lower:
            return lang
    return None
