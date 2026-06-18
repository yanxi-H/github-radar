from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Repository

logger = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict[str, Any]] = {}
        self.meta: dict[str, Any] = {"consecutive_empty_runs": 0}
        self._load()

    def contains(self, full_name: str) -> bool:
        record = self.records.get(full_name.lower())
        return bool(record and record.get("last_notified_at"))

    def get_previous_stars(self, full_name: str) -> int | None:
        record = self.records.get(full_name.lower())
        if not record:
            return None
        try:
            return int(record.get("stars"))
        except (TypeError, ValueError):
            return None

    def get_consecutive_empty_runs(self) -> int:
        try:
            return int(self.meta.get("consecutive_empty_runs", 0))
        except (TypeError, ValueError):
            return 0

    def reset_empty_runs(self) -> None:
        self.meta["consecutive_empty_runs"] = 0

    def increment_empty_runs(self) -> None:
        self.meta["consecutive_empty_runs"] = self.get_consecutive_empty_runs() + 1

    def mark_observed(self, repos: list[Repository]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for repo in repos:
            previous = self.records.get(repo.full_name.lower(), {})
            self.records[repo.full_name.lower()] = {
                **previous,
                "full_name": repo.full_name,
                "url": repo.html_url,
                "first_seen_at": previous.get("first_seen_at", now),
                "last_seen_at": now,
                "pushed_at": repo.pushed_at_iso,
                "stars": repo.stargazers_count,
                "final_score": repo.final_score,
                "domain_relevance_score": round(repo.domain_relevance_score, 1),
                "practical_value_score": round(repo.practical_value_score, 1),
                "presentation_score": round(repo.presentation_score, 1),
                "learning_value_score": round(repo.learning_value_score, 1),
                "health_score": round(repo.health_score, 1),
                "score_breakdown": repo.score_breakdown,
                "star_growth": repo.star_growth,
                "tags": list(repo.tags),
                "project_type": repo.project_type,
            }

    def mark_seen(self, repos: list[Repository]) -> None:
        self.mark_observed(repos)
        now = datetime.now(timezone.utc).isoformat()
        for repo in repos:
            previous = self.records.get(repo.full_name.lower(), {})
            self.records[repo.full_name.lower()] = {
                **previous,
                "last_notified_at": now,
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "meta": self.meta,
            "repositories": sorted(self.records.values(), key=lambda item: item["full_name"].lower()),
        }
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            temp_path = Path(tmp.name)
        temp_path.replace(self.path)
        logger.info("Saved seen state to %s", self.path)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load seen state %s: %s", self.path, exc)
            return

        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            self.meta.update(meta)
            self.meta.setdefault("consecutive_empty_runs", 0)

        records: dict[str, dict[str, Any]] = {}
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, str):
                    records[item.lower()] = {"full_name": item, "url": ""}
                elif isinstance(item, dict) and item.get("full_name"):
                    records[str(item["full_name"]).lower()] = item
        elif isinstance(payload, dict):
            repositories = payload.get("repositories")
            if isinstance(repositories, list):
                for item in repositories:
                    if isinstance(item, dict) and item.get("full_name"):
                        records[str(item["full_name"]).lower()] = item
        self.records = records
