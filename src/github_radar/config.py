from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_EXCLUDED_TERMS = (
    "awesome",
    "interview",
    "leetcode",
    "tutorial",
    "course",
    "starter",
    "boilerplate",
    "template",
    "example-only",
    "demo-only",
    "docs",
    "website",
    "blog",
    "admin",
    "dashboard",
    "backend",
    "server",
    "api",
    "bot",
    "crawler",
    "dataset",
    "benchmark",
)

# Soft terms that are normally excluded, but kept when the repo is clearly a
# high-quality library or framework (matched against SOFT_EXEMPT_KEYWORDS).
SOFT_EXCLUDED_TERMS = (
    "demo",
    "example",
    "examples",
    "sample",
    "samples",
)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    language: str | None = None
    min_stars: int = 100


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None = None
    api_url: str = "https://api.github.com"
    per_page: int = 30
    max_pages_per_query: int = 1
    rate_limit_max_sleep_seconds: int = 120
    throttle_seconds: float = 1.0


@dataclass(frozen=True)
class MimoConfig:
    api_key: str | None = None
    api_url: str | None = None
    model: str = "mimo-v1"
    timeout_seconds: int = 60

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_url)


@dataclass(frozen=True)
class PushConfig:
    pushplus_token: str | None = None
    wxpusher_app_token: str | None = None
    wxpusher_uids: tuple[str, ...] = field(default_factory=tuple)
    wxpusher_topic_ids: tuple[int, ...] = field(default_factory=tuple)
    bark_url: str | None = None
    bark_key: str | None = None
    bark_server: str = "https://api.day.app"
    split_long_message: bool = True
    chunk_max_chars: int = 1800


@dataclass(frozen=True)
class AppConfig:
    github: GitHubConfig
    mimo: MimoConfig
    push: PushConfig
    seen_path: Path
    app_title: str = "GitHub Radar"
    max_push_count: int = 5
    min_score: float = 65.0
    min_score_floor: float = 65.0
    empty_runs_before_fallback: int = 2
    min_domain_relevance: float = 12.0
    pushed_within_hours: int = 720
    readme_max_chars: int = 8000
    enable_ai_message_composer: bool = True
    wechat_digest_max_chars: int = 3500
    excluded_terms: tuple[str, ...] = DEFAULT_EXCLUDED_TERMS
    soft_excluded_terms: tuple[str, ...] = SOFT_EXCLUDED_TERMS
    domain_keywords: tuple[str, ...] = ()
    soft_exempt_keywords: tuple[str, ...] = ()
    queries: tuple[SearchQuery, ...] = ()
    dry_run: bool = False
    log_level: str = "INFO"


def load_config() -> AppConfig:
    load_dotenv_if_available()
    root = Path(os.getenv("GITHUB_RADAR_ROOT", ".")).resolve()
    seen_path = Path(os.getenv("SEEN_PATH", root / "data" / "seen.json")).resolve()

    github_cfg = GitHubConfig(
        token=os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN"),
        api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        per_page=get_int_env("GITHUB_SEARCH_PER_PAGE", 30),
        max_pages_per_query=get_int_env("GITHUB_SEARCH_MAX_PAGES", 1),
        rate_limit_max_sleep_seconds=get_int_env("GITHUB_RATE_LIMIT_MAX_SLEEP", 120),
        throttle_seconds=get_float_env("GITHUB_SEARCH_THROTTLE_SECONDS", 1.0),
    )

    queries = build_queries()
    domain_keywords = parse_csv(os.getenv("DOMAIN_KEYWORDS"))
    soft_exempt_keywords = parse_csv(os.getenv("SOFT_EXEMPT_KEYWORDS"))

    return AppConfig(
        github=github_cfg,
        mimo=MimoConfig(
            api_key=os.getenv("MIMO_API_KEY"),
            api_url=os.getenv("MIMO_API_URL"),
            model=os.getenv("MIMO_MODEL", "mimo-v1"),
            timeout_seconds=get_int_env("MIMO_TIMEOUT_SECONDS", 60),
        ),
        push=PushConfig(
            pushplus_token=os.getenv("PUSHPLUS_TOKEN"),
            wxpusher_app_token=os.getenv("WXPUSHER_APP_TOKEN"),
            wxpusher_uids=parse_csv(os.getenv("WXPUSHER_UIDS")),
            wxpusher_topic_ids=parse_int_csv(os.getenv("WXPUSHER_TOPIC_IDS")),
            bark_url=os.getenv("BARK_URL"),
            bark_key=os.getenv("BARK_KEY"),
            bark_server=os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/"),
            split_long_message=get_bool_env("PUSHPLUS_SPLIT_LONG_MESSAGE", True),
            chunk_max_chars=get_int_env("PUSHPLUS_CHUNK_MAX_CHARS", 1800),
        ),
        seen_path=seen_path,
        app_title=os.getenv("APP_TITLE", "GitHub Radar"),
        max_push_count=get_int_env("MAX_PUSH_COUNT", 5),
        min_score=get_float_env("MIN_SCORE", 65.0),
        min_score_floor=get_float_env("MIN_SCORE_FLOOR", 65.0),
        empty_runs_before_fallback=get_int_env("EMPTY_RUNS_BEFORE_FALLBACK", 2),
        min_domain_relevance=get_float_env("MIN_DOMAIN_RELEVANCE", 12.0),
        pushed_within_hours=get_int_env("PUSHED_WITHIN_HOURS", 720),
        readme_max_chars=get_int_env("README_MAX_CHARS", 8000),
        enable_ai_message_composer=get_bool_env("ENABLE_AI_MESSAGE_COMPOSER", True),
        wechat_digest_max_chars=get_int_env("WECHAT_DIGEST_MAX_CHARS", 3500),
        excluded_terms=tuple(parse_csv(os.getenv("EXCLUDED_TERMS"))) or DEFAULT_EXCLUDED_TERMS,
        soft_excluded_terms=tuple(parse_csv(os.getenv("SOFT_EXCLUDED_TERMS"))) or SOFT_EXCLUDED_TERMS,
        domain_keywords=domain_keywords,
        soft_exempt_keywords=soft_exempt_keywords,
        queries=queries,
        dry_run=get_bool_env("DRY_RUN", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def build_queries() -> tuple[SearchQuery, ...]:
    """Build search queries from the SEARCH_QUERIES environment variable.

    Format: ``keyword1|Language|minStars,keyword2|Language|minStars,...``

    Language and minStars are optional; defaults are None and 100 respectively.
    A bare keyword (no pipes) is also accepted.
    """
    raw = os.getenv("SEARCH_QUERIES", "").strip()
    if not raw:
        return ()

    queries: list[SearchQuery] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        segments = part.split("|")
        text = segments[0].strip()
        if not text:
            continue
        language = segments[1].strip() if len(segments) > 1 and segments[1].strip() else None
        min_stars = 100
        if len(segments) > 2:
            try:
                min_stars = int(segments[2].strip())
            except ValueError:
                pass
        queries.append(SearchQuery(text=text, language=language, min_stars=min_stars))
    return tuple(queries)


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_int_csv(value: str | None) -> tuple[int, ...]:
    ids: list[int] = []
    for item in parse_csv(value):
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return tuple(ids)


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
