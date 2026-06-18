from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from .config import MimoConfig, get_int_env
from .mimo_client import extract_summary
from .models import Repository, RepoSummary

logger = logging.getLogger(__name__)

FORBIDDEN_OUTPUT_TERMS = (
    "Score breakdown",
    "score breakdown",
    "Pushed",
    "Star Growth",
    "Star growth",
    "Topics",
    "topics",
    "其余略过",
)
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


SYSTEM_PROMPT = """你是一个开源项目筛选助手。你的任务是把候选开源项目改写成适合聊天窗口阅读的短摘要，不是写完整报告。

硬性要求：
1. 不减少项目数量，输入多少个项目就输出多少个项目。
2. 不要使用 Markdown 标题、表格、markdown 链接。
3. 不要输出 Topics 全量、Pushed、Star Growth、Score breakdown。
4. 不要重复标题。
5. 每个项目最多 5 行。
6. 每个项目必须包含 GitHub 链接。
7. 只基于输入数据写，不要编造。
8. 信息不足时写"信息不足，建议打开确认"。"""


def compose_wechat_digest_with_ai(
    repos: list[Repository],
    app_title: str = "GitHub Radar",
    max_chars: int = 3500,
    mimo_config: MimoConfig | None = None,
) -> str:
    """Return a plain-text digest that contains every repo."""
    header = app_title
    if not repos:
        return f"{header}\n本轮发现 0 个项目\n"

    config = mimo_config or _load_mimo_config_from_env()
    if config.enabled:
        content = _request_ai_digest(repos, config, app_title=app_title, max_chars=max_chars)
        if content and _is_valid_digest(content, repos):
            return compact_wechat_digest(_normalize_digest(content, repos, app_title), repos, app_title, max_chars)
        logger.warning("AI digest was invalid; falling back to local compact formatter")
    else:
        logger.warning("Mimo is not configured; using local compact formatter")

    return compact_wechat_digest(format_compact_wechat_digest(repos, app_title), repos, app_title, max_chars)


def format_compact_wechat_digest(repos: list[Repository], app_title: str = "GitHub Radar") -> str:
    header = app_title
    lines = [
        header,
        f"本轮发现 {len(repos)} 个项目",
    ]
    for index, repo in enumerate(repos, start=1):
        lines.append("")
        lines.extend(_format_repo_card(index, repo, include_risk=True))
    return "\n".join(lines).rstrip() + "\n"


def compact_wechat_digest(content: str, repos: list[Repository], app_title: str = "GitHub Radar", max_chars: int = 3500) -> str:
    content = _normalize_digest(content, repos, app_title)
    if len(content) <= max_chars:
        return content

    without_risk = "\n".join(
        line for line in content.splitlines() if not line.startswith("风险：")
    ).rstrip() + "\n"
    if _contains_all_links(without_risk, repos) and len(without_risk) <= max_chars:
        return without_risk

    compact = _format_with_card_mode(repos, app_title=app_title, mode="compact")
    if len(compact) <= max_chars:
        return compact

    minimal = _format_with_card_mode(repos, app_title=app_title, mode="minimal")
    if len(minimal) <= max_chars:
        return minimal

    logger.warning(
        "Digest still exceeds WECHAT_DIGEST_MAX_CHARS after minimal compression: chars=%s max=%s repos=%s",
        len(minimal),
        max_chars,
        len(repos),
    )
    return minimal


def split_pushplus_message(content: str, app_title: str = "GitHub Radar", max_chars: int = 1800) -> list[str]:
    """Split a digest by project card boundaries, never by cutting a card in half."""
    if len(content) <= max_chars:
        return [content.rstrip() + "\n"]

    intro, cards = _split_digest_cards(content)
    if not cards:
        return [content.rstrip() + "\n"]

    chunks: list[list[str]] = []
    current: list[str] = []
    for card in cards:
        trial = current + [card]
        if current and _estimated_chunk_len(trial, header=app_title, intro=intro, total=99, index=99) > max_chars:
            chunks.append(current)
            current = [card]
        else:
            current = trial
    if current:
        chunks.append(current)

    total = len(chunks)
    output: list[str] = []
    for index, chunk_cards in enumerate(chunks, start=1):
        header = f"{app_title}（{index}/{total}）"
        body = "\n\n".join(card.strip() for card in chunk_cards)
        output.append(f"{header}\n{intro}\n\n{body}\n".rstrip() + "\n")
    return output


def _request_ai_digest(repos: list[Repository], config: MimoConfig, *, app_title: str, max_chars: int) -> str | None:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(repos, app_title=app_title, max_chars=max_chars)},
        ],
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            str(config.api_url),
            headers=headers,
            json=payload,
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        text = extract_summary(response.json()).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - AI formatting must never block push.
        logger.warning("AI digest request failed: %s", exc)
        return None


def _build_user_prompt(repos: list[Repository], *, app_title: str, max_chars: int) -> str:
    blocks: list[str] = []
    for index, repo in enumerate(repos, start=1):
        summary = repo.summary or _empty_summary()
        topics = ", ".join(repo.topics[:12]) or "无"
        readme_excerpt = _readme_excerpt(repo.readme)
        blocks.append(
            f"""项目 {index}
name: {repo.name}
full_name: {repo.full_name}
url: {repo.html_url}
language: {repo.language or "未知"}
score: {repo.final_score:.0f}
project_type: {repo.project_type}
description: {repo.description or "无"}
topics: {topics}
structured_positioning: {summary.positioning}
structured_usage: {summary.usage}
structured_highlights: {"；".join(summary.highlights) or "无"}
structured_source_points: {"；".join(summary.source_points) or "无"}
structured_integration: {summary.integration}
readme_excerpt: {readme_excerpt}
"""
        )

    separator = "\n---\n"
    return f"""请把下面 {len(repos)} 个项目全部改写成聊天窗口短卡片，目标总长度尽量不超过 {max_chars} 字。

固定格式：
{app_title}
本轮发现 {len(repos)} 个项目

① 项目名｜分数｜类型
用途：一句话说明它到底是什么。
能用：一句话说明怎么用。
看点：2-3 个关键词短语。
风险：一句话风险；信息不足就写"信息不足，建议打开确认"。
链接：https://github.com/owner/repo

项目输入：
{separator.join(blocks)}
"""


def _is_valid_digest(content: str, repos: list[Repository]) -> bool:
    if not _contains_all_links(content, repos):
        return False
    if re.search(r"\[[^\]]+\]\(https?://", content):
        return False
    if "#" in content:
        return False
    if any(term in content for term in FORBIDDEN_OUTPUT_TERMS):
        return False
    return True


def _normalize_digest(content: str, repos: list[Repository], app_title: str = "GitHub Radar") -> str:
    lines = [line.rstrip() for line in content.strip().splitlines()]
    lines = [line for line in lines if not line.lstrip().startswith("#")]
    lines = [line for line in lines if not any(term in line for term in FORBIDDEN_OUTPUT_TERMS)]
    if not lines or lines[0] != app_title:
        lines.insert(0, app_title)
    lines = _dedupe_header(lines, app_title)
    if len(lines) == 1 or not any("本轮发现" in line for line in lines[:3]):
        lines.insert(1, f"本轮发现 {len(repos)} 个项目")
    return "\n".join(lines).rstrip() + "\n"


def _dedupe_header(lines: list[str], header: str) -> list[str]:
    seen = False
    output: list[str] = []
    for line in lines:
        if line == header:
            if seen:
                continue
            seen = True
        output.append(line)
    return output


def _contains_all_links(content: str, repos: list[Repository]) -> bool:
    return all(repo.html_url in content for repo in repos)


def _format_repo_card(index: int, repo: Repository, *, include_risk: bool) -> list[str]:
    lines = [
        f"{_marker(index)} {repo.name}｜{repo.final_score:.0f}分｜{repo.project_type}",
        f"用途：{_purpose(repo)}",
        f"能用：{_usage(repo)}",
        f"看点：{_highlights(repo)}",
    ]
    if include_risk:
        lines.append(f"风险：{_risk(repo)}")
    lines.append(f"链接：{repo.html_url}")
    return lines


def _format_with_card_mode(repos: list[Repository], *, app_title: str = "GitHub Radar", mode: str) -> str:
    lines = [
        app_title,
        f"本轮发现 {len(repos)} 个项目",
    ]
    for index, repo in enumerate(repos, start=1):
        lines.append("")
        if mode == "minimal":
            lines.extend(
                [
                    f"{_marker(index)} {repo.name}｜{repo.final_score:.0f}分｜{repo.project_type}",
                    f"用途：{_shorten(_purpose(repo), 58)}",
                    f"链接：{repo.html_url}",
                ]
            )
        else:
            lines.extend(
                [
                    f"{_marker(index)} {repo.name}｜{repo.final_score:.0f}分｜{repo.project_type}",
                    f"用途：{_shorten(_purpose(repo), 58)}",
                    f"能用：{_shorten(_usage(repo), 62)}",
                    f"看点：{_shorten(_highlights(repo), 56)}",
                    f"链接：{repo.html_url}",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _purpose(repo: Repository) -> str:
    summary = repo.summary
    if summary and summary.positioning:
        return _clean_sentence(summary.positioning)
    return repo.description or "信息不足，建议打开确认。"


def _usage(repo: Repository) -> str:
    summary = repo.summary
    if summary and summary.usage:
        return _clean_sentence(summary.usage)
    return "信息不足，建议打开确认。"


def _highlights(repo: Repository) -> str:
    summary = repo.summary
    points: list[str] = []
    if summary:
        points.extend(summary.source_points[:2])
        points.extend(summary.highlights[:2])
    if points:
        return "、".join(_clean_fragment(p) for p in points[:3])
    return "信息不足，建议打开确认。"


def _risk(repo: Repository) -> str:
    summary = repo.summary
    if summary and summary.integration:
        return _clean_sentence(summary.integration)
    return "信息不足，建议打开确认。"


def _split_digest_cards(content: str) -> tuple[str, list[str]]:
    lines = content.strip().splitlines()
    intro_lines: list[str] = []
    cards: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _is_card_start(line):
            if current:
                cards.append(current)
            current = [line]
        elif current is None:
            pass  # skip header lines
        else:
            current.append(line)
    if current:
        cards.append(current)
    intro = "\n".join(line for line in intro_lines if line.strip()).strip()
    return intro, ["\n".join(card).strip() for card in cards]


def _is_card_start(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[①-⑳]|\d+[.、])\s+", line))


def _estimated_chunk_len(cards: list[str], *, header: str, intro: str, total: int, index: int) -> int:
    h = f"{header}（{index}/{total}）"
    return len(h) + 2 + len(intro) + 2 + len("\n\n".join(cards))


def _marker(index: int) -> str:
    if 1 <= index <= len(CIRCLED):
        return CIRCLED[index - 1]
    return f"{index}."


def _clean_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.replace("⚠️", "").replace("**", "")
    return cleaned or "信息不足，建议打开确认。"


def _clean_fragment(text: str) -> str:
    cleaned = _clean_sentence(text)
    return cleaned.rstrip("。；;")


def _shorten(text: str, limit: int) -> str:
    text = _clean_sentence(text)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，。；; ") + "。"


def _readme_excerpt(readme: str) -> str:
    if not readme:
        return "README 不可用"
    cleaned = re.sub(r"\s+", " ", readme).strip()
    return cleaned[:1400]


def _empty_summary() -> RepoSummary:
    return RepoSummary(
        positioning="信息不足，建议打开确认。",
        highlights=("信息不足，建议打开确认。",),
        usage="信息不足，建议打开确认。",
        source_points=(),
        integration="信息不足，建议打开确认。",
    )


def _load_mimo_config_from_env() -> MimoConfig:
    return MimoConfig(
        api_key=os.getenv("MIMO_API_KEY"),
        api_url=os.getenv("MIMO_API_URL"),
        model=os.getenv("MIMO_MODEL", "mimo-v1"),
        timeout_seconds=get_int_env("MIMO_TIMEOUT_SECONDS", 60),
    )
