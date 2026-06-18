from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import requests

from .ai_message_composer import (
    compose_wechat_digest_with_ai,
    format_compact_wechat_digest,
    split_pushplus_message,
)
from .config import AppConfig, load_config
from .filters import is_eligible, pushed_after, sort_key
from .github_client import GitHubClient
from .mimo_client import MimoClient
from .models import Repository, RunStats
from .notifiers import build_notifiers, send_all
from .scoring import score_repository
from .storage import SeenStore

logger = logging.getLogger(__name__)


def main() -> int:
    config = load_config()
    setup_logging(config.log_level)
    logger.info(
        "Starting GitHub Radar (title=%s, queries=%s, domain_keywords=%s)",
        config.app_title,
        len(config.queries),
        len(config.domain_keywords),
    )

    if not config.queries:
        logger.error(
            "No search queries configured. Set the SEARCH_QUERIES environment variable. "
            "Format: keyword1|Language|minStars,keyword2|Language|minStars,..."
        )
        return 1

    try:
        stats = run(config)
    except requests.HTTPError as exc:
        logger.exception("GitHub Radar failed because an HTTP request failed: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level guard for scheduled jobs.
        logger.exception("GitHub Radar failed unexpectedly: %s", exc)
        return 1

    logger.info(
        "Finished GitHub Radar: queries=%s candidates=%s selected=%s channels=%s",
        stats.queried,
        stats.candidates,
        stats.selected,
        len(stats.notified_channels),
    )
    failures = [result for result in stats.notified_channels if not result.ok]
    if failures:
        logger.error(
            "Push failed for channels: %s — exiting non-zero",
            [result.channel for result in failures],
        )
        return 1
    return 0


def run(config: AppConfig) -> RunStats:
    now = datetime.now(timezone.utc)
    cutoff = pushed_after(config.pushed_within_hours, now)
    github = GitHubClient(config.github)
    store = SeenStore(config.seen_path)
    stats = RunStats()

    candidates_by_name: dict[str, Repository] = {}

    for query in config.queries:
        stats.queried += 1
        try:
            repos = github.search_repositories(query, cutoff)
        except requests.HTTPError as exc:
            logger.warning("Search query failed and will be skipped: query=%r error=%s", query.text, exc)
            continue
        logger.info("Query %r returned %s repositories", query.text, len(repos))
        for repo in repos:
            if not repo.full_name:
                continue
            if not is_eligible(
                repo,
                min_stars=query.min_stars,
                pushed_within_hours=config.pushed_within_hours,
                excluded_terms=config.excluded_terms,
                soft_terms=config.soft_excluded_terms,
                soft_exempt_keywords=config.soft_exempt_keywords,
                now=now,
            ):
                logger.debug("Skipping ineligible repo: %s", repo.full_name)
                continue
            candidates_by_name.setdefault(repo.full_name.lower(), repo)

    all_candidates = sorted(candidates_by_name.values(), key=sort_key, reverse=True)
    stats.candidates = len(all_candidates)
    if not stats.candidates:
        logger.info("No eligible repositories found")
        _record_empty_run(config, store)
        return stats

    pool = all_candidates[: max(config.max_push_count * 3, config.max_push_count)]
    unseen_pool = [repo for repo in pool if not store.contains(repo.full_name)]
    if not unseen_pool:
        logger.info("Eligible repositories were found, but all have already been pushed")
        _record_empty_run(config, store)
        return stats

    effective_min_score = _effective_min_score(config, store)
    logger.info(
        "Scoring %s candidates; effective min score=%.1f (empty_runs=%s)",
        len(unseen_pool),
        effective_min_score,
        store.get_consecutive_empty_runs(),
    )

    scored: list[Repository] = []
    for repo in unseen_pool:
        readme = github.fetch_readme(repo.full_name, config.readme_max_chars)
        repo_with_readme = repo.with_readme(readme)
        scored_repo = score_repository(
            repo_with_readme,
            domain_keywords=config.domain_keywords,
            previous_stars=store.get_previous_stars(repo.full_name),
            now=now,
            pushed_within_hours=config.pushed_within_hours,
        )
        logger.info(
            "Scored %s: final=%.1f type=%s domain=%s practical=%s presentation=%s learning=%s health=%s tags=%s",
            scored_repo.full_name,
            scored_repo.final_score,
            scored_repo.project_type,
            scored_repo.domain_relevance_score,
            scored_repo.practical_value_score,
            scored_repo.presentation_score,
            scored_repo.learning_value_score,
            scored_repo.health_score,
            ",".join(scored_repo.tags) or "none",
        )
        scored.append(scored_repo)

    if not config.dry_run and scored:
        store.mark_observed(scored)

    selected = _select(config, scored, effective_min_score)
    stats.selected = len(selected)
    if not selected:
        logger.info(
            "No high-quality repositories found with final_score >= %.1f; skipping push",
            effective_min_score,
        )
        _record_empty_run(config, store)
        return stats

    logger.info("Selected %s repositories", len(selected))

    mimo = MimoClient(config.mimo)
    enriched = []
    for repo in selected:
        summary = mimo.summarize(repo)
        enriched.append(repo.with_summary(summary))

    title = f"{config.app_title}：发现 {len(enriched)} 个项目"
    if config.enable_ai_message_composer:
        content = compose_wechat_digest_with_ai(
            enriched,
            app_title=config.app_title,
            max_chars=config.wechat_digest_max_chars,
            mimo_config=config.mimo,
        )
    else:
        content = format_compact_wechat_digest(enriched, config.app_title)

    chunks = [content]
    if config.push.split_long_message:
        chunks = split_pushplus_message(content, app_title=config.app_title, max_chars=config.push.chunk_max_chars)

    if config.dry_run:
        logger.warning("DRY_RUN=true; printing message without sending or updating seen state")
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = _chunk_title(title, index, len(chunks))
            logger.info("\n%s\n%s", chunk_title, chunk)
        return stats

    notifiers = build_notifiers(config.push)
    if not notifiers:
        logger.warning("No push channel configured; printing message instead")
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = _chunk_title(title, index, len(chunks))
            logger.info("\n%s\n%s", chunk_title, chunk)
    else:
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = _chunk_title(title, index, len(chunks))
            stats.notified_channels.extend(send_all(notifiers, chunk_title, chunk))

    store.mark_seen(enriched)
    store.reset_empty_runs()
    store.save()
    return stats


def _effective_min_score(config: AppConfig, store: SeenStore) -> float:
    if store.get_consecutive_empty_runs() >= config.empty_runs_before_fallback:
        logger.info(
            "Lowering min score to floor %.1f after %s consecutive empty runs",
            config.min_score_floor,
            store.get_consecutive_empty_runs(),
        )
        return config.min_score_floor
    return config.min_score


def _select(config: AppConfig, scored: list[Repository], min_score: float) -> list[Repository]:
    """Apply quality + domain-relevance gates, then take top-N."""
    order = sorted(
        scored,
        key=lambda repo: (repo.final_score, repo.domain_relevance_score, repo.stargazers_count),
        reverse=True,
    )

    qualified: list[Repository] = []
    for repo in order:
        if repo.final_score < min_score:
            continue
        if repo.domain_relevance_score < config.min_domain_relevance:
            continue
        # Skip pure CLI tools — users want plugins/skills/extensions, not CLI apps.
        if repo.project_type == "CLI 工具":
            continue
        qualified.append(repo)

    return qualified[: config.max_push_count]


def _record_empty_run(config: AppConfig, store: SeenStore) -> None:
    if config.dry_run:
        return
    store.increment_empty_runs()
    store.save()


def _chunk_title(title: str, index: int, total: int) -> str:
    if total <= 1:
        return title
    return f"{title}（{index}/{total}）"


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
