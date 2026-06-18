from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from urllib.parse import quote

import requests

from .config import PushConfig
from .models import NotificationResult, RepoSummary, Repository

logger = logging.getLogger(__name__)


class Notifier(ABC):
    channel: str

    @abstractmethod
    def send(self, title: str, content: str) -> NotificationResult:
        raise NotImplementedError


class PushPlusNotifier(Notifier):
    """PushPlus via the WeChat ClawBot channel, plain-text template.

    Fixed payload: {token, title, content, template:"txt", channel:"clawbot"}.
    ClawBot drops the content straight into the WeChat chat (no "view details"
    card), so the body must be plain text — no markdown.
    """

    channel = "PushPlus"
    endpoint = "https://www.pushplus.plus/send"

    def __init__(self, token: str) -> None:
        self.token = token

    def send(self, title: str, content: str) -> NotificationResult:
        payload = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": "txt",
            "channel": "clawbot",
        }
        try:
            response = requests.post(self.endpoint, json=payload, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            full = getattr(exc.response, "text", None) or str(exc)
            logger.error("PushPlus push failed (HTTP). Full response: %s", full)
            print(full)
            return NotificationResult(self.channel, False, full)

        try:
            body = response.json()
        except ValueError:
            body = None

        full = json.dumps(body, ensure_ascii=False) if body is not None else response.text
        code = (body or {}).get("code")
        if code != 200:
            logger.error("PushPlus push failed (code=%s). Full response: %s", code, full)
            print(full)
            return NotificationResult(self.channel, False, full)

        logger.info("PushPlus push ok: %s", full)
        return NotificationResult(self.channel, True, full)


class WxPusherNotifier(Notifier):
    channel = "WxPusher"

    def __init__(self, app_token: str, uids: tuple[str, ...], topic_ids: tuple[int, ...]) -> None:
        self.app_token = app_token
        self.uids = uids
        self.topic_ids = topic_ids

    def send(self, title: str, content: str) -> NotificationResult:
        payload = {
            "appToken": self.app_token,
            "content": content,
            "summary": title[:99],
            "contentType": 1,  # plain text
        }
        if self.uids:
            payload["uids"] = list(self.uids)
        if self.topic_ids:
            payload["topicIds"] = list(self.topic_ids)
        response = requests.post("https://wxpusher.zjiecode.com/api/send/message", json=payload, timeout=20)
        response.raise_for_status()
        return NotificationResult(self.channel, True, response.text[:200])


class BarkNotifier(Notifier):
    channel = "Bark"

    def __init__(self, *, url: str | None, key: str | None, server: str) -> None:
        self.url = url
        self.key = key
        self.server = server

    def send(self, title: str, content: str) -> NotificationResult:
        if self.url:
            endpoint = self.url
        else:
            endpoint = f"{self.server}/{self.key}/{quote(title)}"
        response = requests.post(
            endpoint,
            json={"title": title, "body": content, "group": "GitHub Radar", "isArchive": 1},
            timeout=20,
        )
        response.raise_for_status()
        return NotificationResult(self.channel, True, response.text[:200])


def build_notifiers(config: PushConfig) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if config.pushplus_token:
        notifiers.append(PushPlusNotifier(config.pushplus_token))
    if config.wxpusher_app_token and (config.wxpusher_uids or config.wxpusher_topic_ids):
        notifiers.append(
            WxPusherNotifier(
                config.wxpusher_app_token,
                config.wxpusher_uids,
                config.wxpusher_topic_ids,
            )
        )
    if config.bark_url or config.bark_key:
        notifiers.append(BarkNotifier(url=config.bark_url, key=config.bark_key, server=config.bark_server))
    return notifiers


def send_all(notifiers: list[Notifier], title: str, content: str) -> list[NotificationResult]:
    results: list[NotificationResult] = []
    for notifier in notifiers:
        try:
            result = notifier.send(title, content)
            if result.ok:
                logger.info("Sent notification through %s", notifier.channel)
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - one channel must not block others.
            logger.exception("Failed to send notification through %s", notifier.channel)
            results.append(NotificationResult(notifier.channel, False, str(exc)))
    return results


def format_text(repos: list[Repository], app_title: str = "GitHub Radar") -> str:
    """Plain-text push body for PushPlus ClawBot / Bark / WxPusher text."""
    lines: list[str] = [
        app_title,
        "",
        f"发现 {len(repos)} 个值得看的项目",
    ]
    for repo in repos:
        lines.append("")
        lines.extend(_format_repo_text(repo))
    return "\n".join(lines).rstrip() + "\n"


def _format_repo_text(repo: Repository) -> list[str]:
    summary = repo.summary or RepoSummary(
        positioning="信息不足，建议人工打开确认。",
        highlights=("信息不足，建议人工打开确认。",),
        usage="信息不足，建议人工打开确认。",
        source_points=(),
        integration="信息不足，建议人工打开确认。",
    )
    tags = " / ".join(repo.tags) or "未标注"
    lines = [
        "",
        repo.full_name,
        f"评分：{repo.final_score:.0f}/100",
        f"方向：{tags}",
        f"一句话：{summary.positioning}",
        "为什么值得看：",
    ]
    lines.extend(f"- {item}" for item in summary.highlights)
    lines.append("我能怎么用：")
    lines.append(summary.usage)
    if summary.source_points:
        lines.append("源码值得看：")
        lines.extend(f"- {item}" for item in summary.source_points)
    lines.append("集成与风险：")
    lines.append(summary.integration)
    lines.append(f"链接：{repo.html_url}")
    return lines
