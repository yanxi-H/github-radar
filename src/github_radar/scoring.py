"""Generic domain-agnostic repository scoring.

The final score answers: *how useful is this repo to someone working in the
user's target domain?*  Five dimensions, each capped, sum to at most 100:

    domain_relevance  /30  Hit-rate of user-supplied DOMAIN_KEYWORDS.
    practical_value   /20  Package manager, install docs, API surface, demo app.
    presentation      /20  Screenshots/GIF/video, real-world demos, visual polish.
    learning_value    /15  Code examples, architecture patterns, documentation depth.
    health            /15  Recency, stars, star growth, topics, not archived.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from .models import Repository

# ── Generic tag rules ────────────────────────────────────────────────────────
# Each entry: (display_tag, (needles …))
# Users can extend these via DOMAIN_KEYWORDS; these are the platform-agnostic
# baseline tags that apply to any technology stack.

GENERIC_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Animation", ("animation", "transition", "motion", "keyframe", "spring")),
    ("CLI", ("cli", "command-line", "terminal", "console")),
    ("Database", ("database", "sql", "orm", "migration", "query builder")),
    ("DevOps", ("devops", "ci-cd", "docker", "kubernetes", "deploy", "terraform")),
    ("Testing", ("testing", "test framework", "mock", "assertion", "benchmark")),
    ("Serialization", ("serialization", "serde", "json", "protobuf", "yaml", "toml")),
    ("Async", ("async", "await", "concurrency", "parallel", "runtime", "tokio")),
    ("Web Framework", ("web framework", "http server", "rest api", "graphql", "router")),
    ("Auth", ("authentication", "authorization", "oauth", "jwt", "session", "sso")),
    ("Caching", ("cache", "caching", "redis", "memcached")),
    ("Logging", ("logging", "logger", "tracing", "observability", "telemetry")),
    ("Parser", ("parser", "lexer", "tokenizer", "ast", "grammar")),
    ("ML/AI", ("machine learning", "deep learning", "neural", "model", "inference", "training")),
    ("Crypto", ("crypto", "encryption", "hashing", "certificate", "tls")),
    ("Image", ("image processing", "image", "photo", "camera", "vision")),
    ("UI Library", ("component", "components", "ui library", "design system", "widget")),
    ("SDK", ("sdk", "client library", "api client", "wrapper")),
    ("Plugin", ("plugin", "extension", "addon", "middleware")),
)

# Concrete signals used across several scoring dimensions.
CODE_EXAMPLE_SIGNALS = (
    "example",
    "demo",
    "tutorial",
    "getting started",
    "quickstart",
    "quick start",
    "sample",
    "playground",
    "showcase",
)

DOC_QUALITY_SIGNALS = (
    "documentation",
    "docs",
    "guide",
    "reference",
    "api reference",
    "changelog",
    "migration guide",
    "contributing",
    "architecture",
)

ARCHITECTURE_SIGNALS = (
    "design pattern",
    "architecture",
    "modular",
    "plugin",
    "middleware",
    "dependency injection",
    "inversion of control",
    "abstract",
    "interface",
    "trait",
    "protocol",
    "generic",
    "builder pattern",
    "factory pattern",
    "observer",
    "event-driven",
)

PACKAGE_MANAGERS = (
    # Python
    "pip",
    "pypi",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "poetry",
    # Node / JS / TS
    "npm",
    "yarn",
    "pnpm",
    "package.json",
    # Rust
    "cargo",
    "crates.io",
    # Go
    "go get",
    "go.mod",
    # Swift
    "swift package manager",
    "swift package",
    "spm",
    "cocoapods",
    "carthage",
    # Ruby
    "gem",
    "bundler",
    # Java / Kotlin
    "maven",
    "gradle",
    # .NET
    "nuget",
    # C/C++
    "conan",
    "vcpkg",
    "cmake",
)


def score_repository(
    repo: Repository,
    *,
    domain_keywords: tuple[str, ...],
    previous_stars: int | None,
    now: datetime,
    pushed_within_hours: int,
) -> Repository:
    tags = generate_tags(repo, domain_keywords)
    project_type = classify_project_type(repo, tags)
    star_growth = max(0, repo.stargazers_count - previous_stars) if previous_stars is not None else 0

    domain_rel = score_domain_relevance(repo, domain_keywords)
    practical = score_practical_value(repo)
    presentation = score_presentation(repo)
    learning = score_learning_value(repo)
    health = score_health(repo, now, pushed_within_hours, star_growth)
    final = round(
        min(100.0, domain_rel + practical + presentation + learning + health), 1
    )

    return repo.with_scores(
        final_score=final,
        domain_relevance_score=domain_rel,
        practical_value_score=practical,
        presentation_score=presentation,
        learning_value_score=learning,
        health_score=health,
        star_growth=star_growth,
        tags=tags,
        project_type=project_type,
    )


# ── Tag generation ───────────────────────────────────────────────────────────

def generate_tags(repo: Repository, domain_keywords: tuple[str, ...]) -> tuple[str, ...]:
    haystack = searchable_text(repo)
    tags: list[str] = []

    # Domain keywords → tags (each keyword that matches becomes a tag).
    for kw in domain_keywords:
        if matches_keyword(haystack, kw):
            tags.append(kw.strip().title())

    # Generic tag rules.
    for tag, needles in GENERIC_TAG_RULES:
        if any(matches_keyword(haystack, n) for n in needles):
            tags.append(tag)

    # Language as a tag.
    lang = (repo.language or "").strip()
    if lang:
        tags.append(lang)

    return tuple(dict.fromkeys(tags))


# ── Project type classification ──────────────────────────────────────────────

def classify_project_type(repo: Repository, tags: tuple[str, ...]) -> str:
    """Heuristic project type from metadata + README evidence."""
    text = searchable_text(repo)
    name = repo.name.lower()

    # Rule-based detection from tags and text signals.
    if any(t in tags for t in ("CLI",)):
        return "CLI 工具"
    if any(t in tags for t in ("ML/AI",)):
        return "AI/ML 项目"
    if any(t in tags for t in ("Web Framework",)):
        return "Web 框架"
    if any(t in tags for t in ("Database",)):
        return "数据库/ORM"
    if any(t in tags for t in ("DevOps",)):
        return "DevOps 工具"
    if any(t in tags for t in ("Testing",)):
        return "测试框架"
    if any(t in tags for t in ("SDK",)):
        return "SDK/API 客户端"
    if any(t in tags for t in ("UI Library",)):
        return "UI 组件库"
    if any(t in tags for t in ("Animation",)):
        return "动效/动画库"
    if any(t in tags for t in ("Parser",)):
        return "解析器/编译器"
    if any(t in tags for t in ("Auth",)):
        return "认证/授权"
    if any(t in tags for t in ("Plugin",)):
        return "插件/扩展"
    if any(t in tags for t in ("Async",)):
        return "异步/并发框架"

    # Text-based fallback.
    if any(s in text for s in ("framework", "library", "sdk")):
        return "通用框架/库"
    if any(s in text for s in ("tool", "utility", "helper", "cli")):
        return "开发工具"
    return "其他"


# ── Quality project detection (for soft-term exemption) ──────────────────────

def is_quality_project(repo: Repository, exempt_keywords: tuple[str, ...]) -> bool:
    """True when a repo looks like a genuine library/framework/tool.

    Used to *protect* high-quality repos from soft exclusion terms (demo,
    example, sample) — e.g. "Animation Examples" gallery should survive.
    """
    if not exempt_keywords:
        return False
    haystack = searchable_text(repo)
    return any(matches_keyword(haystack, kw) for kw in exempt_keywords)


# ── Dimension 1: Domain relevance (max 30) ──────────────────────────────────

def score_domain_relevance(repo: Repository, domain_keywords: tuple[str, ...]) -> float:
    """Score how relevant the repo is to the user's target domain.

    Each matching keyword contributes points, capped at 30.  When no domain
    keywords are configured, falls back to a description/topcs-based heuristic.
    """
    text = searchable_text(repo)

    if not domain_keywords:
        # No domain keywords — give a moderate base score so that repos are
        # not unfairly penalised.  The score is based on having a substantive
        # description and topics.
        score = 5.0
        if repo.description.strip():
            score += 5.0
        if repo.topics:
            score += 5.0
        if len(repo.readme or "") > 1000:
            score += 5.0
        return min(30.0, score)

    hits = sum(1 for kw in domain_keywords if matches_keyword(text, kw))
    if not hits:
        return 0.0

    # Diminishing returns: first hits are worth more.
    score = 0.0
    for i in range(hits):
        score += 8.0 / (i + 1)
    return min(30.0, round(score, 1))


# ── Dimension 2: Practical value (max 20) ───────────────────────────────────

def score_practical_value(repo: Repository) -> float:
    text = searchable_text(repo)
    readme = (repo.readme or "").lower()
    score = 0.0

    # Package manager support — generic detection.
    if any(s in text or s in readme for s in PACKAGE_MANAGERS):
        score += 6.0
    # Installation section in README.
    if "## installation" in readme or "### installation" in readme or "## install" in readme:
        score += 3.0
    elif "install" in readme:
        score += 1.0
    # Usage / API surface.
    if "## usage" in readme or "### usage" in readme or "## api" in readme or "### api" in readme:
        score += 4.0
    elif "```" in readme:
        score += 2.0
    # Demo / example app.
    if any(s in text for s in CODE_EXAMPLE_SIGNALS):
        score += 3.0
    # Quick-start / getting started.
    if any(s in readme for s in ("getting started", "quickstart", "quick start", "## quick")):
        score += 2.0
    return min(20.0, score)


# ── Dimension 3: Presentation (max 20) ──────────────────────────────────────

def score_presentation(repo: Repository) -> float:
    text = searchable_text(repo)
    readme = (repo.readme or "").lower()
    score = 0.0

    # Visual evidence in the README.
    if any(s in readme for s in ("![", ".gif", ".mp4", ".webm", "youtube", "youtu.be", "<video", "video")):
        score += 6.0
    elif any(s in text for s in ("screenshot", "preview", "demo gif", "screen recording")):
        score += 3.0
    # Badges / shields (common in well-presented repos).
    if "![" in readme and ("badge" in readme or "shield" in readme):
        score += 2.0
    # Multiple sections suggesting thorough docs.
    section_count = sum(1 for s in ("## ", "### ") if s in readme)
    score += min(4.0, section_count * 0.8)
    # Visual polish / design signals.
    if any(s in text for s in ("design system", "design-system", "theme", "dark mode", "responsive", "animation", "transition")):
        score += 3.0
    # Real-world usage / showcase.
    if any(s in text for s in ("production", "used by", "powered by", "built with", "showcase", "gallery")):
        score += 3.0
    # Code blocks (showing actual usage).
    code_blocks = readme.count("```")
    score += min(4.0, code_blocks * 0.8)
    return min(20.0, score)


# ── Dimension 4: Learning value (max 15) ─────────────────────────────────────

def score_learning_value(repo: Repository) -> float:
    text = searchable_text(repo)
    readme = (repo.readme or "").lower()
    score = 0.0

    # Architecture / design patterns.
    arch_hits = sum(1 for s in ARCHITECTURE_SIGNALS if s in text)
    score += min(5.0, arch_hits * 1.2)

    # Documentation quality.
    doc_hits = sum(1 for s in DOC_QUALITY_SIGNALS if s in text)
    score += min(4.0, doc_hits * 1.0)

    # Code examples / tutorials.
    example_hits = sum(1 for s in CODE_EXAMPLE_SIGNALS if s in text)
    score += min(3.0, example_hits * 1.0)

    # README substance.
    readme_len = len(readme)
    if readme_len > 3000:
        score += 3.0
    elif readme_len > 1500:
        score += 2.0
    elif readme_len > 500:
        score += 1.0

    return min(15.0, score)


# ── Dimension 5: Health (max 15) ─────────────────────────────────────────────

def score_health(
    repo: Repository,
    now: datetime,
    pushed_within_hours: int,
    star_growth: int,
) -> float:
    score = 0.0
    # Recency (0–3.5).
    pushed_at = repo.pushed_at.astimezone(timezone.utc)
    age_hours = max(0.0, (now.astimezone(timezone.utc) - pushed_at).total_seconds() / 3600)
    window = max(1.0, float(pushed_within_hours))
    if age_hours < window:
        score += 3.5 * (1.0 - age_hours / window)
    # Stars (0–6), log-scaled so popularity earns meaningful credit.
    if repo.stargazers_count > 0:
        score += min(6.0, math.log10(repo.stargazers_count + 1) / math.log10(50_000) * 6.0)
    # Star growth (0–2).
    if star_growth > 0:
        score += min(2.0, math.log2(star_growth + 1) / math.log2(200) * 2.0)
    # Curation signals (0–1.5).
    if repo.topics:
        score += 1.5
    elif repo.description.strip():
        score += 1.0
    # Alive (0–1).
    if not repo.archived:
        score += 1.0
    # README substance / license (0–1).
    readme = repo.readme or ""
    if "license" in readme.lower() or len(readme) > 1500:
        score += 1.0
    return min(15.0, score)


# ── Utilities ────────────────────────────────────────────────────────────────

def searchable_text(repo: Repository) -> str:
    return " ".join(
        [
            repo.full_name,
            repo.name,
            repo.description,
            repo.language or "",
            " ".join(repo.topics),
            repo.readme[:6000],
        ]
    ).lower()


def matches_keyword(haystack: str, needle: str) -> bool:
    needle = needle.lower()
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9+#./-]+", needle):
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    return needle in haystack
