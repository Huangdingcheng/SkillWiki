# Module 05: Frontend, REST API & Experience Pipeline

**负责人分支：`frontend-dev`**

---

## 职责概述

本模块负责：
- React 前端（9 个功能页面）
- FastAPI REST API 层（路由、Schema、依赖注入）
- Experience Processing Pipeline（知识导入）
- WebSocket 实时事件推送

---

## 5.1 Frontend（`skillos-frontend/`）

### 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18 | UI 框架 |
| TypeScript | 5 | 类型安全 |
| Vite | 5 | 构建工具 |
| Ant Design | 5 | UI 组件库 |
| AntV G6 | 5 | 知识图谱可视化 |
| Framer Motion | 11 | 动画 |
| Zustand | 4 | 状态管理 |
| Axios | 1 | HTTP 客户端 |

### 页面清单

| 路由 | 文件 | 功能描述 |
|------|------|----------|
| `/` | `Dashboard.tsx` | 系统概览：核心指标、类型分布、健康报告、Self-Evolution Metrics、实时事件 |
| `/demo` | `SelfEvolutionDemo.tsx` | **EMNLP 核心展示**：完整自演化闭环（检索→规划→执行→记录→学习） |
| `/wiki` | `SkillWiki.tsx` | Skill 目录：搜索、过滤、详情抽屉、发布/废弃操作 |
| `/graph` | `SkillGraph.tsx` | 交互式异构知识图谱（AntV G6）：Source / Skill / Tool / API / Test / Version / Feedback 节点展示与关系过滤 |
| `/execution` | `AgentExecution.tsx` | 任务执行：检索到的 Skill 展示、步骤结果、经验记录反馈 |
| `/evolution` | `Evolution.tsx` | 健康监控：degraded/critical Skill 列表、修复、演化周期 |
| `/lifecycle` | `LifecycleDemo.tsx` | 状态机可视化：S0-S7 转换演示 |
| `/ingest` | `KnowledgeImport.tsx` | 知识导入：4 种来源、Pipeline 阶段可视化、Skill 创建 |
| `/versions` | `VersionControl.tsx` | 版本管理：版本历史、diff 视图、新版本创建 |

### 状态管理（`store/appStore.ts`）

```typescript
interface AppStore {
  darkMode: boolean
  toggleDark: () => void
  wsEvents: WsEvent[]       // WebSocket 事件列表
  addWsEvent: (e: WsEvent) => void
  clearWsEvents: () => void
}
```

### WebSocket Hook（`hooks/useWebSocket.ts`）

```typescript
// 连接到 ws://localhost:8000/ws
// 自动重连（3秒间隔）
// 事件写入 appStore.wsEvents
```

### API 客户端（`api/client.ts`）

```typescript
// 所有 API 调用通过 axios 实例，baseURL = '/api/v1'
export const skillsApi = { list, get, getFull, search, versions, delete }
export const lifecycleApi = { release, deprecate, transition, review, reviewAndRelease, newVersion, getDiff }
export const graphApi = { full, subgraph, stats }
export const executionApi = { executeSkill, executePlan, getState, resetState, history }
export const evolutionApi = { systemHealth, skillHealth, repair, runCycle }
export const ingestApi = { parse, parseAndCreate }
export const statsApi = { overview, evolutionStats }
```

---

## 5.2 REST API（`skillos/api/`）

### 依赖注入（`api/deps.py`）

所有路由通过 `AppState` 获取共享资源：

```python
class AppState:
    wiki: SkillWikiManager
    graph: SkillGraphManager
    search: SkillSearchEngine
    executor: SkillExecutor
    planner: ExecutionPlanner
    state_tracker: StateTracker
    # Self-Management Agents
    builder: SkillBuilderAgent
    auditor: SkillAuditorAgent
    maintainer: SkillMaintainerAgent
    librarian: SkillLibrarianAgent
    meta_controller: MetaControllerAgent
    # Experience Pipeline
    pipeline: ExperiencePipeline
    # LLM
    llm: LLMClient
```

### 完整端点表

**Skills（`routes/skills.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/skills` | 列出（state/type/tags/limit/offset 过滤） |
| POST | `/skills` | 创建 |
| GET | `/skills/evolution-stats` | 演化统计 |
| GET | `/skills/{id}` | 摘要 |
| GET | `/skills/{id}/full` | 完整信息 |
| PATCH | `/skills/{id}` | 更新 |
| DELETE | `/skills/{id}` | 删除 |
| POST | `/skills/search` | 语义搜索 |
| GET | `/skills/{id}/versions` | 版本历史 |

**Lifecycle（`routes/lifecycle.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/lifecycle/{id}/transition` | 手动状态转换 |
| POST | `/lifecycle/{id}/release` | 发布 |
| POST | `/lifecycle/{id}/deprecate` | 废弃 |
| POST | `/lifecycle/{id}/new-version` | 创建新版本 |
| POST | `/lifecycle/{id}/review` | LLM 审核 |
| POST | `/lifecycle/{id}/review-and-release` | 审核并发布 |
| POST | `/lifecycle/{id}/record-execution` | 记录执行 |
| GET | `/lifecycle/{id}/diff` | 变更历史 |
| GET | `/lifecycle/{id}/diff/versions` | 版本比较 |

**Execution（`routes/execution.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/execution/plan` | 执行计划 |
| POST | `/execution/skill` | 执行单个 Skill |
| GET | `/execution/state` | 当前状态 |
| DELETE | `/execution/state` | 重置状态 |
| GET | `/execution/history` | 执行历史 |

**Evolution（`routes/evolution.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/evolution/health` | 系统健康 |
| GET | `/evolution/health/{id}` | 单 Skill 健康 |
| POST | `/evolution/repair/{id}` | 修复 |
| POST | `/evolution/cycle` | 演化周期 |

**Graph（`routes/graph.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/graph` | 完整异构图谱；内存 demo 优先返回 source / skill / tool / api_doc / test / version / feedback 等节点 |
| POST | `/graph/subgraph` | 指定 Skill 周边的异构子图 |
| POST | `/graph/edges` | 添加边 |
| GET | `/graph/{id}/dependencies` | 依赖链 |
| GET | `/graph/{id}/execution-order` | 执行顺序 |
| GET | `/graph/stats/overview` | 统计 |

**Ingest（`routes/ingest.py`）**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/ingest/parse` | 解析预览 |
| POST | `/ingest/parse-and-create` | 解析并创建 Skill |

**WebSocket（`routes/ws.py`）**

| 路径 | 功能 |
|------|------|
| `WS /ws` | 实时事件推送（plan_started/step_completed/step_failed/plan_completed 等） |

---

## 5.3 Experience Processing Pipeline（`layers/input_knowledge/`）

### Pipeline 架构

```
原始输入（轨迹/文档/API文档/代码脚本）
    │
    ▼
[Extractor Agent]
  收集并解析原始数据
  输出：List[RawAction]
    │
    ▼
[Normalizer Agent]
  结构化为标准 ExperienceUnit
  输出：List[ExperienceUnit]
    │
    ▼
[Summarizer Agent]
  提取关键步骤和状态变更
  输出：StructuredExperience（含摘要）
    │
    ▼
[Indexer Agent]
  写入 Experience Store，建立检索索引
  输出：PipelineResult（含 unit_count, token_usage）
```

### 支持的输入类型

| source_type | 描述 | 示例 |
|-------------|------|------|
| `trajectory` | 操作轨迹（步骤列表） | "1. 点击登录按钮 2. 输入用户名..." |
| `document` | 技术文档/操作说明 | Markdown 格式的操作规范 |
| `api_doc` | API 文档/OpenAPI 规范 | REST API 描述 |
| `script` | 代码脚本 | Python/JavaScript 函数 |

当前 demo 优先支持固定研究输入：前端 Knowledge Import 页面提供静态 JSON fixture，后端 pipeline 会跳过真实采集，直接解析 `skills[]` 数组并生成结构化经验单元。`parse-and-create` 现在通过 `MetaControllerAgent` 调度 `SkillBuilderAgent`、`SkillAuditorAgent` 和 `SkillLibrarianAgent`，创建或复用 Skill，并同步写入异构图节点与关系，包括 source、tool、api_doc、test、version 和 Skill 节点。响应中的 `agent_trace` 用于前端展示内部 Agent 管理过程。

### 关键数据结构

```python
@dataclass
class ExperienceUnit:
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: List[str]
    proposed_skill_name: Optional[str]
    proposed_description: Optional[str]
    proposed_type: Optional[str]        # atomic/functional/strategic
    confidence: float

@dataclass
class PipelineResult:
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str]
    units: List[ExperienceUnit]
```

---

## 关键文件

```
skillos-frontend/src/
├── pages/              # 9 个页面组件
├── api/
│   ├── client.ts       # API 客户端（所有 API 方法）
│   └── types.ts        # TypeScript 类型定义
├── components/
│   └── AppLayout.tsx   # 主布局（侧边栏导航）
├── store/
│   └── appStore.ts     # Zustand 全局状态
└── hooks/
    └── useWebSocket.ts # WebSocket 连接管理

skillos/skillos/
├── api/
│   ├── main.py         # FastAPI 应用入口 + seed data
│   ├── deps.py         # 依赖注入（AppState）
│   ├── schemas.py      # 请求/响应 Pydantic 模型
│   └── routes/         # 6 个路由模块
└── layers/input_knowledge/
    ├── pipeline.py     # ExperiencePipeline（4阶段）
    ├── base_parser.py  # 解析器基类
    ├── trajectory_parser.py
    ├── doc_parser.py
    └── script_analyzer.py
```

---

## 优化方向（Member E 任务）

1. **SelfEvolutionDemo 增强**：添加"历史执行记录"面板，展示多次执行后 Skill 质量的提升趋势
2. **SkillGraph 交互**：点击节点展开子图，双击节点跳转到 Skill Wiki 详情页
3. **KnowledgeImport 结果跳转**：创建 Skill 后显示可点击链接，直接跳转到 Wiki 页面
4. **Dashboard 实时刷新**：添加自动刷新按钮或定时刷新（当前需要手动刷新页面）
5. **Experience Pipeline 优化**：各阶段的 LLM prompt 可进一步优化，提升 Skill 候选质量
6. **API Schema 完善**：`schemas.py` 中部分响应模型使用了 `dict`，可替换为具体 Pydantic 模型
7. **错误处理统一**：前端各页面的错误处理逻辑不一致，可抽取统一的 error handler hook

---

*更新此文档时请同步更新 `architecture.md` 中的 API 端点表（联系负责人）*
