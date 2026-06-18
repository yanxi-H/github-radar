from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from .config import MimoConfig
from .models import Repository, RepoSummary

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是一名资深技术观察员，熟悉各种编程语言和开源生态。"
    "你的任务：严格依据仓库 description、topics、README 中真实存在的信息，"
    "为一名开发者生成结构化中文总结。"
    "绝对禁止凭空猜测项目类型或编造不存在的功能；信息不足时必须明确指出。"
)


def build_prompt(repo: Repository) -> str:
    topics = ", ".join(repo.topics) or "无"
    tags = "、".join(repo.tags) or "无"
    readme = (repo.readme or "README 不可用").strip()
    readme = readme[:7000]

    return f"""请基于下方仓库信息，输出严格的 JSON（只输出 JSON，不要任何额外文字或 markdown 代码块）。

仓库：{repo.full_name}
语言：{repo.language or "未知"}
Stars：{repo.stargazers_count}
分类标签：{tags}
Topics：{topics}
Description：{repo.description or "无"}

README（前 7000 字符）：
{readme}

JSON 结构与字段要求：
{{
  "positioning": "一句话定位。必须准确说明这个项目到底是什么；不确定就写'从 README 看，它主要是……'。禁止猜测项目类型。",
  "highlights": ["它牛在哪里：2 条，每条必须能在 README/topics/description 中找到依据关键词"],
  "usage": "我能怎么用：能否直接集成？适合什么场景？2-4 句。信息不足就写'信息不足，建议人工打开确认'。",
  "source_points": ["值得看源码的方向 1-3 条，例如架构设计、API 抽象、性能优化、可扩展性、测试策略"],
  "integration": "集成与风险：支持哪些包管理器？最近是否仍维护？是否只是 demo、是否生产可用、有无维护风险？信息不足就写'信息不足，建议人工打开确认'。"
}}

防幻觉硬规则（违反即判定总结失败）：
1. 如果 description/topics/README 中没有出现某个功能的关键词，绝不能编造该功能。
2. 信息不足时写'信息不足，建议人工打开确认'，禁止编造功能或亮点。
3. 每条结论都应能在上面提供的关键词中找到依据。
只输出 JSON。"""


class MimoClient:
    def __init__(self, config: MimoConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def summarize(self, repo: Repository) -> RepoSummary:
        if not self.config.enabled:
            logger.warning(
                "Mimo is not configured; using local fallback summary for %s",
                repo.full_name,
            )
            return fallback_summary(repo)

        summary = self._request_summary(repo, temperature=0.2)
        if summary and is_valid_summary(summary):
            return summary

        logger.warning(
            "Mimo summary invalid/generic for %s; retrying with lower temperature",
            repo.full_name,
        )
        summary = self._request_summary(repo, temperature=0.0)
        if summary and is_valid_summary(summary):
            return summary

        logger.warning("Using local fallback summary for %s", repo.full_name)
        return fallback_summary(repo)

    def _request_summary(self, repo: Repository, *, temperature: float) -> RepoSummary | None:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(repo)},
            ],
            "temperature": temperature,
        }
        # Prefer structured JSON when the backend advertises support.
        payload["response_format"] = {"type": "json_object"}

        try:
            response = self.session.post(
                str(self.config.api_url),
                headers=headers,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            if response.status_code >= 400 and "response_format" in response.text.lower():
                # Some backends reject response_format; retry without it.
                payload.pop("response_format", None)
                response = self.session.post(
                    str(self.config.api_url),
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
            response.raise_for_status()
            body = response.json()
            text = extract_summary(body)
            if not text:
                logger.warning(
                    "Mimo response had no extractable text for %s; top-level keys=%s",
                    repo.full_name,
                    sorted(body.keys()),
                )
                return None
            return parse_structured_summary(text)
        except Exception as exc:  # noqa: BLE001 - summary failures must not stop notifications.
            logger.warning("Mimo summary failed for %s: %s", repo.full_name, exc)
            return None


def parse_structured_summary(text: str) -> RepoSummary | None:
    obj = extract_json_object(text)
    if obj is None:
        return None
    try:
        positioning = str(obj.get("positioning") or "").strip()
        highlights = _as_str_list(obj.get("highlights"))
        usage = str(obj.get("usage") or "").strip()
        source_points = _as_str_list(obj.get("source_points"))
        integration = str(obj.get("integration") or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not positioning and not usage:
        return None
    return RepoSummary(
        positioning=positioning or "信息不足，建议人工打开确认。",
        highlights=tuple(highlights[:3]) if highlights else ("信息不足，建议人工打开确认。",),
        usage=usage or "信息不足，建议人工打开确认。",
        source_points=tuple(source_points[:3]) if source_points else (),
        integration=integration or "信息不足，建议人工打开确认。",
    )


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a model response."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def extract_summary(payload: dict[str, Any]) -> str:
    """Return raw text content from a chat-completions-style payload."""
    choices = payload.get("choices")
    summary = extract_from_choices(choices)
    if summary:
        return summary
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()
        summary = extract_from_choices(data.get("choices"))
        if summary:
            return summary
        for key in ("summary", "text", "content", "output"):
            value = data.get(key)
            if isinstance(value, str):
                return value.strip()
    for key in ("summary", "text", "content", "output"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def extract_from_choices(choices: Any) -> str:
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message") or {}
    if isinstance(message, dict):
        content = extract_content_text(message.get("content"))
        if content:
            return content
    delta = first.get("delta") or {}
    if isinstance(delta, dict):
        content = extract_content_text(delta.get("content"))
        if content:
            return content
    text = first.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


# ── Validation ───────────────────────────────────────────────────────────────

LOW_INFO_PATTERNS = (
    "亮点集中在",
    "一个近期活跃的开源项目",
)


def is_valid_summary(summary: RepoSummary) -> bool:
    text = _join_summary(summary)
    if len(re.sub(r"\s+", "", text)) < 24:
        return False
    if any(pattern in text for pattern in LOW_INFO_PATTERNS):
        return False
    return True


def _join_summary(summary: RepoSummary) -> str:
    return " ".join(
        [
            summary.positioning,
            summary.usage,
            summary.integration,
            *summary.highlights,
            *summary.source_points,
        ]
    )


# ── Local heuristic fallback ─────────────────────────────────────────────────

def fallback_summary(repo: Repository) -> RepoSummary:
    text = combined_text(repo)
    positioning = detect_positioning(repo, text)
    highlights = detect_highlights(repo, text)
    usage = detect_usage(repo, text)
    source_points = detect_source_points(repo, text)
    integration = detect_integration(repo, text)
    return RepoSummary(
        positioning=positioning,
        highlights=tuple(highlights) or ("信息不足，建议人工打开确认。",),
        usage=usage,
        source_points=tuple(source_points),
        integration=integration,
    )


def combined_text(repo: Repository) -> str:
    return "\n".join(
        [repo.description, " ".join(repo.topics), (repo.readme or "")[:5000]]
    ).lower()


def _evidence_terms(text: str, candidates: tuple[str, ...], limit: int = 2) -> list[str]:
    return [term for term in candidates if term in text][:limit]


def detect_positioning(repo: Repository, text: str) -> str:
    language = (repo.language or "").strip()
    capability, keywords = detect_capability_with_evidence(text)
    kw = "、".join(keywords) if keywords else ""
    lang_label = f"{language} " if language else ""
    base = f"{repo.name} 是一个 {lang_label}{capability}"
    if kw:
        base += f"（依据关键词：{kw}）"
    if not repo.description and not repo.readme:
        base = f"信息不足，建议人工打开确认。{repo.name} 从现有信息看疑似 {lang_label}{capability}。"
    return base + "。"


def detect_capability_with_evidence(text: str) -> tuple[str, list[str]]:
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Web 框架", ("web framework", "http server", "rest api", "graphql", "router")),
        ("CLI 工具", ("cli", "command-line", "terminal", "console")),
        ("数据库/ORM", ("database", "sql", "orm", "migration", "query builder")),
        ("AI/ML 项目", ("machine learning", "deep learning", "neural", "inference", "training", "model")),
        ("测试框架", ("testing", "test framework", "mock", "assertion", "benchmark")),
        ("异步/并发框架", ("async", "concurrency", "parallel", "runtime", "tokio")),
        ("解析器/编译器", ("parser", "lexer", "tokenizer", "ast", "grammar")),
        ("DevOps 工具", ("devops", "ci-cd", "docker", "kubernetes", "deploy", "terraform")),
        ("认证/授权库", ("authentication", "authorization", "oauth", "jwt", "session")),
        ("SDK/API 客户端", ("sdk", "client library", "api client", "wrapper")),
        ("UI 组件库", ("component", "components", "ui library", "design system", "widget")),
        ("动效/动画库", ("animation", "transition", "motion", "keyframe", "spring")),
        ("日志/可观测性", ("logging", "tracing", "observability", "telemetry")),
        ("缓存库", ("cache", "caching", "redis")),
        ("序列化库", ("serialization", "serde", "json", "protobuf", "yaml")),
        ("加密库", ("crypto", "encryption", "hashing", "certificate")),
        ("插件/扩展框架", ("plugin", "extension", "addon", "middleware")),
        ("通用框架/库", ("framework", "library", "sdk")),
        ("开发工具", ("tool", "utility", "helper")),
    )
    for label, needles in rules:
        matched = _evidence_terms(text, needles, limit=2)
        if matched:
            return label, matched
    return "开源项目", []


def detect_highlights(repo: Repository, text: str) -> list[str]:
    highlights: list[str] = []
    rules = (
        ("提供截图/GIF/视频等可视化演示", ("![", ".gif", ".mp4", "youtube", "screenshot", "preview")),
        ("有丰富的代码示例和使用指南", ("example", "tutorial", "getting started", "quickstart", "guide")),
        ("支持多种包管理器安装", ("npm", "pip", "cargo", "brew", "swift package", "maven", "gradle")),
        ("提供完整的 API 文档", ("api reference", "documentation", "docs")),
        ("支持插件/中间件扩展", ("plugin", "middleware", "extension", "addon")),
        ("提供主题/配置/自定义选项", ("theme", "config", "customizable", "configurable", "options")),
        ("有活跃的社区和贡献指南", ("contributing", "code of conduct", "community", "discord")),
    )
    for label, needles in rules:
        if any(needle in text for needle in needles):
            highlights.append(label)
        if len(highlights) == 2:
            break
    return highlights


def detect_usage(repo: Repository, text: str) -> str:
    language = (repo.language or "").strip()
    parts: list[str] = []
    # Installation method.
    install = detect_install_method(text)
    if install:
        parts.append(f"可{install}集成到项目中")
    # Scenario.
    scenario = detect_scenario(text)
    parts.append(scenario)
    if language:
        parts.append(f"适合 {language} 项目参考")
    return "；".join(parts) + "。"


def detect_install_method(text: str) -> str:
    readme = text
    if "pip install" in readme or "pip3 install" in readme or "pypi" in readme:
        return "通过 pip"
    if "npm install" in readme or "npm i " in readme or "yarn add" in readme or "pnpm add" in readme:
        return "通过 npm/yarn"
    if "cargo" in readme or "crates.io" in readme:
        return "通过 Cargo"
    if "go get" in readme or "go.mod" in readme:
        return "通过 go get"
    if "swift package" in readme or "package.swift" in readme or "spm" in readme:
        return "通过 Swift Package Manager"
    if "brew install" in readme or "homebrew" in readme:
        return "通过 Homebrew"
    if "maven" in readme or "gradle" in readme:
        return "通过 Maven/Gradle"
    return ""


def detect_scenario(text: str) -> str:
    mapping = (
        ("Web 后端/API 服务开发", ("web framework", "http server", "rest api", "graphql", "router")),
        ("命令行工具与自动化脚本", ("cli", "command-line", "terminal")),
        ("数据处理与分析", ("data", "pandas", "dataframe", "etl", "pipeline")),
        ("机器学习与 AI 推理", ("machine learning", "deep learning", "neural", "inference")),
        ("测试与质量保障", ("testing", "test framework", "mock", "assertion", "benchmark")),
        ("异步与高并发服务", ("async", "concurrency", "parallel", "runtime")),
        ("数据库操作与 ORM", ("database", "sql", "orm", "migration")),
        ("CI/CD 与部署自动化", ("ci-cd", "docker", "kubernetes", "deploy", "terraform")),
        ("UI 组件与界面开发", ("component", "ui library", "design system", "widget")),
        ("日志收集与可观测性", ("logging", "tracing", "observability", "telemetry")),
    )
    for label, needles in mapping:
        if any(n in text for n in needles):
            return f"适合用在{label}"
    return "适合作为参考库用在日常开发中"


def detect_source_points(repo: Repository, text: str) -> list[str]:
    points: list[str] = []
    rules = (
        ("模块化架构与依赖注入设计", ("modular", "dependency injection", "inversion of control", "plugin", "middleware")),
        ("API 抽象与接口设计", ("abstract", "interface", "trait", "protocol", "generic", "builder pattern")),
        ("性能优化与并发模型", ("async", "concurrency", "parallel", "performance", "benchmark", "cache")),
        ("错误处理与容错策略", ("error handling", "retry", "fallback", "circuit breaker", "resilient")),
        ("配置管理与可扩展性", ("config", "customizable", "configurable", "extensible", "options")),
        ("测试策略与示例工程", ("test", "example", "demo", "playground", "sample")),
        ("文档结构与使用指南", ("documentation", "docs", "guide", "tutorial", "getting started")),
    )
    for label, needles in rules:
        if any(n in text for n in needles):
            points.append(label)
        if len(points) == 3:
            break
    return points


def detect_integration(repo: Repository, text: str) -> str:
    parts: list[str] = []
    install = detect_install_method(text)
    parts.append(install + "集成" if install else "集成方式信息不足，建议人工确认")
    readme = (repo.readme or "").lower()
    if any(s in readme for s in ("demo only", "just a demo", "not production", "experiment")):
        parts.append("更像 demo/实验项目，生产可用性需自行评估")
    elif "license" in readme:
        parts.append("有 License，较适合参考/集成")
    if repo.archived:
        parts.append("⚠️ 已 archived，存在维护风险")
    if not parts:
        parts.append("信息不足，建议人工打开确认")
    return "；".join(parts) + "。"
