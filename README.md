# GitHub Radar

GitHub Radar 是一个**通用的** GitHub 开源项目自动发现工具。通过配置搜索关键词、编程语言、领域关键词，定时从 GitHub 挖掘高质量开源项目，用 AI 生成结构化中文总结，然后推送到 PushPlus（微信）/ Bark / WxPusher。

## 核心特性

- **完全可配置**：搜索关键词、语言、star 门槛、领域关键词全部通过环境变量配置，无硬编码假设。
- **5 维评分系统**（满分 100）：领域相关度 30 + 实用性 20 + 展示质量 20 + 学习价值 15 + 健康度 15。
- **防幻觉 AI 总结**：基于小米 Mimo（OpenAI 兼容）生成结构化中文总结，校验不通过时自动 fallback 到本地 heuristic。
- **微信短卡片格式**：AI 将项目改写成聊天窗口可扫读的短卡片；消息过长时按项目边界自动拆分。
- **PushPlus 微信推送（默认）**：固定 `channel=clawbot`、`template=txt`，内容直接出现在微信聊天对话框里。
- **跨运行去重**：`data/seen.json` 记录已推送项目，避免重复推送。
- **支持 `DRY_RUN=true` 本地预览**、GitHub Actions 定时运行（默认每 4 小时）。

## 目录结构

```text
github-radar/
├── .github/workflows/github-radar.yml
├── data/seen.json
├── src/github_radar/
│   ├── config.py              # 配置 + 查询构建
│   ├── ai_message_composer.py # AI 短卡片 + 本地 fallback + 拆分
│   ├── filters.py             # 过滤（排除词 + 软豁免）
│   ├── github_client.py       # GitHub Search + README 抓取
│   ├── main.py                # 编排：搜索→评分→选品→总结→推送
│   ├── mimo_client.py         # 结构化防幻觉总结（Mimo/OpenAI 兼容）
│   ├── models.py              # Repository / RepoSummary 数据模型
│   ├── notifiers.py           # Bark/PushPlus/WxPusher + 推送格式
│   ├── scoring.py             # 5 维评分系统
│   └── storage.py             # seen.json 去重与状态
├── README.md
└── requirements.txt
```

## 快速开始

### 1. 申请 GitHub Token

搜索公开仓库不强制要求 token，但无 token 时限流很严。建议配置。

1. 打开 <https://github.com/settings/personal-access-tokens> → **Generate new token**
2. `Repository access` 选 **Public Repositories**
3. 设置过期时间，生成并复制

### 2. 本地运行

```bash
git clone <your-repo-url>
cd github-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

创建 `.env`（已在 `.gitignore`）：

```bash
# 必填：搜索查询
# 格式：关键词|语言|最低star数（语言和star可省略）
SEARCH_QUERIES=Python AI agent|Python|500,Rust async runtime|Rust|1000,React animation component|TypeScript|800

# 推荐：领域关键词（用于评分时判断相关度）
DOMAIN_KEYWORDS=ai,agent,llm,rust,async,tokio,react,animation

# 可选：软豁免关键词（匹配到这些词的项目不会被 demo/example/sample 排除）
SOFT_EXEMPT_KEYWORDS=library,framework,sdk

# 可选：自定义标题
APP_TITLE=GitHub Radar

# GitHub Token
GH_TOKEN=github_pat_xxx

# AI 总结（推荐）
MIMO_API_KEY=xxx
MIMO_API_URL=https://your-mimo-api.example.com/v1/chat/completions
MIMO_MODEL=mimo-v1

# 推送渠道三选一
PUSHPLUS_TOKEN=xxx
# BARK_URL=https://api.day.app/your-key
# WXPUSHER_APP_TOKEN=xxx ; WXPUSHER_UIDS=UID_xxx
```

### 安全预览（先 dry run）

```bash
DRY_RUN=true ENABLE_AI_MESSAGE_COMPOSER=true PYTHONPATH=src python -m github_radar.main
```

### 正式运行

```bash
PYTHONPATH=src python -m github_radar.main
```

## 配置参考

### 搜索查询 `SEARCH_QUERIES`

**必填。** 逗号分隔的查询列表，每条格式：`关键词|语言|最低star数`

| 部分 | 是否必填 | 说明 |
|---|---|---|
| 关键词 | 是 | GitHub 搜索关键词，建议 1-3 个词 |
| 语言 | 可选 | 限定编程语言（如 Python、Rust、TypeScript） |
| 最低 star | 可选 | 默认 100 |

示例：

```bash
# Python AI 生态
SEARCH_QUERIES=Python AI agent|Python|500,Python LLM framework|Python|1000,Python RAG|Python|300

# Rust 生态
SEARCH_QUERIES=Rust async runtime|Rust|1000,Rust web framework|Rust|500,Rust CLI tool|Rust|300

# 前端生态
SEARCH_QUERIES=React animation component|TypeScript|800,Vue UI library|TypeScript|500,design system component|TypeScript|1000

# 混合
SEARCH_QUERIES=Python AI agent|Python|500,Rust async|Rust|1000,React animation|TypeScript|800
```

### 领域关键词 `DOMAIN_KEYWORDS`

**推荐配置。** 逗号分隔的关键词列表，用于评分时判断项目与目标领域的相关度。每个匹配的关键词都会为 `domain_relevance_score` 贡献分数（满分 30）。

```bash
DOMAIN_KEYWORDS=ai,agent,llm,rust,async,tokio,react,animation
```

不配置时，所有项目的领域相关度会基于 description/topics/README 质量给出中等分数。

### 环境变量一览

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEARCH_QUERIES` | **必填** | 搜索查询列表 |
| `DOMAIN_KEYWORDS` | 空 | 领域关键词（评分用） |
| `SOFT_EXEMPT_KEYWORDS` | 空 | 软豁免关键词 |
| `APP_TITLE` | `GitHub Radar` | 推送标题前缀 |
| `MAX_PUSH_COUNT` | `5` | 每轮最多推送项目数 |
| `MIN_SCORE` | `65` | 最低 final_score |
| `MIN_DOMAIN_RELEVANCE` | `12` | 最低领域相关度 |
| `PUSHED_WITHIN_HOURS` | `720` | 只看最近多少小时内有 push 的项目（默认 30 天） |
| `DRY_RUN` | `false` | `true` 时只打印、不推送、不更新 seen |
| `ENABLE_AI_MESSAGE_COMPOSER` | `true` | 开启 AI 微信正文优化 |
| `WECHAT_DIGEST_MAX_CHARS` | `3500` | 单轮摘要目标总长度 |
| `PUSHPLUS_SPLIT_LONG_MESSAGE` | `true` | 过长时按项目边界拆分 |
| `PUSHPLUS_CHUNK_MAX_CHARS` | `1800` | 单条 PushPlus 消息目标长度 |
| `GITHUB_SEARCH_PER_PAGE` | `30` | 每页结果数 |
| `GITHUB_SEARCH_MAX_PAGES` | `1` | 每个查询最大页数 |
| `GITHUB_SEARCH_THROTTLE_SECONDS` | `1.0` | 查询间隔（秒） |
| `EXCLUDED_TERMS` | 见源码 | 排除词（逗号分隔） |
| `SOFT_EXCLUDED_TERMS` | `demo,example,...` | 软排除词 |

### 推送渠道

#### PushPlus（微信，默认）

<https://www.pushplus.plus/> → 登录 → 一对一推送 → 复制 token。

```bash
PUSHPLUS_TOKEN=your-token
```

#### Bark（iPhone / iPad）

```bash
BARK_URL=https://api.day.app/your-key
# 或分别配置
BARK_KEY=your-key
BARK_SERVER=https://api.day.app
```

#### WxPusher（微信公众号）

```bash
WXPUSHER_APP_TOKEN=xxx
WXPUSHER_UIDS=UID_xxx
# 或
WXPUSHER_TOPIC_IDS=123
```

## 评分系统（满分 100）

| 维度 | 满分 | 说明 |
|---|---|---|
| `domain_relevance_score` | 30 | 由 `DOMAIN_KEYWORDS` 驱动，关键词命中越多分越高 |
| `practical_value_score` | 20 | 包管理器支持、安装文档、API 使用说明、demo app |
| `presentation_score` | 20 | 截图/GIF/视频、文档结构、代码示例、设计质量 |
| `learning_value_score` | 15 | 架构设计、API 抽象、文档深度、代码示例 |
| `health_score` | 15 | 最近 push、stars、star 增长、topics、非 archived |

推送门禁：`final_score >= MIN_SCORE(65)` 且 `domain_relevance_score >= MIN_DOMAIN_RELEVANCE(12)`。

连续 `EMPTY_RUNS_BEFORE_FALLBACK(2)` 轮无结果时，阈值自动降到 `MIN_SCORE_FLOOR(65)`。

## 过滤规则

默认排除词（整词匹配 name / description / topics）：

```text
awesome interview leetcode tutorial course starter boilerplate template
example-only demo-only docs website blog admin dashboard backend server
api bot crawler dataset benchmark
```

**软豁免**：`demo` / `example` / `sample` 默认也会排除，但当项目匹配 `SOFT_EXEMPT_KEYWORDS` 时保留。

## 配置示例

### Python AI 生态

```bash
SEARCH_QUERIES=Python AI agent|Python|500,Python LLM framework|Python|1000,Python RAG|Python|300
DOMAIN_KEYWORDS=ai,agent,llm,rag,langchain,openai,anthropic,transformer
SOFT_EXEMPT_KEYWORDS=library,framework,sdk
APP_TITLE=AI Radar
```

### Rust 生态

```bash
SEARCH_QUERIES=Rust async runtime|Rust|1000,Rust web framework|Rust|500,Rust CLI tool|Rust|300,Rust database ORM|Rust|500
DOMAIN_KEYWORDS=rust,async,tokio,axum,actix,serde,cli,wasm
SOFT_EXEMPT_KEYWORDS=library,framework,crate
APP_TITLE=Rust Radar
```

### 前端生态

```bash
SEARCH_QUERIES=React animation component|TypeScript|800,Vue UI library|TypeScript|500,design system component|TypeScript|1000,React state management|TypeScript|1000
DOMAIN_KEYWORDS=react,vue,nextjs,svelte,animation,component,design-system,tailwind
SOFT_EXEMPT_KEYWORDS=library,component,framework,ui
APP_TITLE=Frontend Radar
```

### iOS 开发（原 iOS 组件雷达模式）

```bash
SEARCH_QUERIES=SwiftUI animation|Swift|100,SwiftUI component|Swift|100,SwiftUI transition|Swift|100,SwiftUI chart|Swift|100,iOS UIKit|Swift|100,iOS animation|Swift|100,SwiftUI gesture|Swift|100,SwiftUI sheet|Swift|100
DOMAIN_KEYWORDS=swift,swiftui,uikit,ios,animation,transition,gesture,haptics,chart,calendar,onboarding,bottom sheet,liquid glass
SOFT_EXEMPT_KEYWORDS=swift,swiftui,uikit,ios,component,animation,library,gallery
APP_TITLE=iOS 组件雷达
```

## GitHub Actions

工作流 `.github/workflows/github-radar.yml` 默认每 4 小时运行一次，支持手动触发。

### Secrets

| Secret | 说明 |
|---|---|
| `GH_TOKEN` | GitHub token（可选，默认用 `github.token`） |
| `MIMO_API_KEY` | Mimo API Key（推荐） |
| `MIMO_API_URL` | Mimo Chat Completions 接口地址（推荐） |
| `MIMO_MODEL` | 模型名（可选，默认 `mimo-v1`） |
| `PUSHPLUS_TOKEN` | PushPlus token（推荐） |
| `WXPUSHER_APP_TOKEN` | WxPusher 应用 token |
| `WXPUSHER_UIDS` | WxPusher UID 列表 |
| `WXPUSHER_TOPIC_IDS` | WxPusher topic ID 列表 |
| `BARK_URL` | Bark 完整推送 URL |
| `BARK_KEY` | Bark key |
| `BARK_SERVER` | Bark 服务器地址 |

### Variables

| Variable | 说明 |
|---|---|
| `SEARCH_QUERIES` | 搜索查询列表（**必填**） |
| `DOMAIN_KEYWORDS` | 领域关键词 |
| `SOFT_EXEMPT_KEYWORDS` | 软豁免关键词 |
| `APP_TITLE` | 推送标题前缀 |
| `MAX_PUSH_COUNT` | 每轮最多推送数 |
| `MIN_SCORE` | 最低总分 |
| `MIN_DOMAIN_RELEVANCE` | 最低领域相关度 |
| `PUSHED_WITHIN_HOURS` | 最近活跃时间窗口 |
| `DRY_RUN` | 预览模式 |
| `ENABLE_AI_MESSAGE_COMPOSER` | AI 正文优化 |
| `WECHAT_DIGEST_MAX_CHARS` | 摘要目标长度 |

## 常见问题

### Q1：为什么没有推送？

1. 没配置 `SEARCH_QUERIES`（必填）
2. 没配置任何推送渠道（配 `PUSHPLUS_TOKEN` / `BARK_URL` / `WXPUSHER_APP_TOKEN`）
3. `DRY_RUN=true`
4. 没有项目达到 `MIN_SCORE=65`
5. 项目的 `domain_relevance_score < MIN_DOMAIN_RELEVANCE`
6. 项目都已推送过（看 `data/seen.json`）

### Q2：为什么重复推送？

1. `data/seen.json` 没提交回仓库（检查 workflow 的 `Persist seen state` 步骤）
2. `DRY_RUN=true` 不更新 seen

### Q3：GitHub API 限流？

1. 配置 `GH_TOKEN`
2. 降低搜索量：`GITHUB_SEARCH_MAX_PAGES=1`、增大 `GITHUB_SEARCH_THROTTLE_SECONDS`

### Q4：AI 总结失败？

1. 确认 `MIMO_API_KEY` / `MIMO_API_URL` 配置正确
2. 程序会自动 retry 并在失败时使用本地兜底
3. 用 `DRY_RUN=true` 查看日志

## 开发

```bash
python3 -m compileall src                 # 语法检查
DRY_RUN=true PYTHONPATH=src python -m github_radar.main   # 预览
PYTHONPATH=src python -m github_radar.main                # 正式运行
```
