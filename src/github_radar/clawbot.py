"""PushPlus ClawBot API client for receiving user messages."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

ACCESS_KEY_URL = "https://www.pushplus.plus/api/common/openApi/getAccessKey"
GET_MSG_URL = "https://www.pushplus.plus/api/open/clawBot/getMsg"

# Stopwords to strip from user messages when extracting search keywords.
STOPWORDS = frozenset(
    "搜索 搜一下 帮我搜 帮我找 查一下 查找 找一下 有什么 好用的 推荐 发我 发给我 "
    "的 了 吗 呢 吧 啊 一下 一些 有没有 能不能 想要 我想 我要 请 请问 告诉我".split()
)


def get_access_key(token: str, secret_key: str) -> str | None:
    """Get a temporary AccessKey from PushPlus Open API.

    Returns the access key string, or None on failure.
    """
    try:
        response = requests.post(
            ACCESS_KEY_URL,
            json={"token": token, "secretKey": secret_key},
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 200:
            logger.error("Failed to get AccessKey: %s", body)
            return None
        key = (body.get("data") or {}).get("accessKey")
        if not key:
            logger.error("AccessKey response missing data.accessKey: %s", body)
            return None
        return key
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to get AccessKey: %s", exc)
        return None


def get_messages(access_key: str) -> list[dict[str, Any]]:
    """Fetch unread messages from ClawBot.

    Returns a list of message dicts with 'type' (1=text, 3=voice) and 'text'.
    """
    try:
        response = requests.get(
            GET_MSG_URL,
            headers={"access-key": access_key},
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 200:
            logger.error("Failed to get ClawBot messages: %s", body)
            return []
        data = body.get("data")
        if not isinstance(data, list):
            return []
        return data
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to get ClawBot messages: %s", exc)
        return []


def parse_search_query(text: str) -> str | None:
    """Extract search keywords from a user message.

    Returns cleaned keyword string, or None if the message isn't a search request.

    Examples:
        "搜索 React UI skill" → "React UI skill"
        "帮我找 MCP server" → "MCP server"
        "rust async 有什么好用的" → "rust async"
        "Claude Code skill" → "Claude Code skill"
        "你好" → None (too short, likely not a search)
    """
    if not text:
        return None

    text = text.strip()

    # Skip very short messages (likely greetings or irrelevant).
    if len(text) < 3:
        return None

    # Strip common prefixes.
    cleaned = text
    for prefix in ("搜索", "搜一下", "帮我搜", "帮我找", "查一下", "查找", "找一下", "推荐"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break

    # Remove stopwords.
    words = []
    for word in re.split(r"[\s,，。.!?！？]+", cleaned):
        word = word.strip()
        if not word:
            continue
        if word.lower() in STOPWORDS:
            continue
        words.append(word)

    result = " ".join(words).strip()
    if len(result) < 2:
        return None

    return result
