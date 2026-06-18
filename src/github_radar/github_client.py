from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .config import GitHubConfig, SearchQuery
from .models import Repository

logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, config: GitHubConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-radar/0.2.0",
            }
        )
        if config.token:
            self.session.headers["Authorization"] = f"Bearer {config.token}"

    def search_repositories(self, query: SearchQuery, pushed_after: datetime) -> list[Repository]:
        full_query = self._build_query(query, pushed_after)
        repos: list[Repository] = []
        for page in range(1, self.config.max_pages_per_query + 1):
            logger.info(
                "Searching GitHub: query=%r lang=%s page=%s",
                query.text,
                query.language,
                page,
            )
            if page > 1 or self.config.throttle_seconds > 0:
                time.sleep(max(0.0, self.config.throttle_seconds))
            payload = self._request_json(
                "GET",
                f"{self.config.api_url}/search/repositories",
                params={
                    "q": full_query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": self.config.per_page,
                    "page": page,
                },
            )
            items = payload.get("items") or []
            repos.extend(Repository.from_github_item(item) for item in items)
            if len(items) < self.config.per_page:
                break
        return repos

    def fetch_readme(self, full_name: str, max_chars: int) -> str:
        try:
            payload = self._request_json(
                "GET",
                f"{self.config.api_url}/repos/{full_name}/readme",
                params={"mediaType": "raw"},
                allow_404=True,
            )
        except requests.RequestException as exc:
            logger.warning("Failed to fetch README for %s: %s", full_name, exc)
            return ""

        if not payload:
            return ""
        encoded = payload.get("content") or ""
        if not encoded:
            return ""
        try:
            decoded = base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - keep one repo failure from breaking the run.
            logger.warning("Failed to decode README for %s: %s", full_name, exc)
            return ""
        return decoded[:max_chars]

    def _build_query(self, query: SearchQuery, pushed_after: datetime) -> str:
        pushed = pushed_after.astimezone(timezone.utc).strftime("%Y-%m-%d")
        parts = [query.text, f"stars:>={query.min_stars}", f"pushed:>={pushed}", "archived:false"]
        if query.language:
            parts.append(f"language:{query.language}")
        return " ".join(parts)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any]:
        for attempt in range(2):
            response = self.session.request(method, url, params=params, json=json, timeout=30)
            if allow_404 and response.status_code == 404:
                return {}
            if response.status_code in {403, 429} and attempt == 0:
                if self._handle_rate_limit(response):
                    continue
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        return {}

    def _handle_rate_limit(self, response: requests.Response) -> bool:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        retry_after = response.headers.get("Retry-After")

        sleep_seconds = 0
        if retry_after and retry_after.isdigit():
            sleep_seconds = int(retry_after)
        elif remaining == "0" and reset and reset.isdigit():
            sleep_seconds = max(0, int(reset) - int(time.time()))

        if 0 < sleep_seconds <= self.config.rate_limit_max_sleep_seconds:
            logger.warning("GitHub rate limited; sleeping %s seconds before retry", sleep_seconds)
            time.sleep(sleep_seconds)
            return True

        logger.error(
            "GitHub API rate limit or abuse protection hit: status=%s remaining=%s reset=%s body=%s",
            response.status_code,
            remaining,
            reset,
            response.text[:500],
        )
        return False
