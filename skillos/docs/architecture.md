# SkillOS System Architecture & Workflows

> **⚠️ 此文档由项目负责人统一维护。Team 成员请勿直接修改本文档。**
> 各模块细化内容请在 `modules/` 目录下对应文档中更新。

---

## 1. 系统总览

SkillOS 由五大组件构成，通过四条数据流协同工作：

```
┌─────────────────────────────────────────────────────────────────┐
│              A. Task Execution Agents                           │
│  Planner → Retrieval → Composition → Execution → Verifier → Reflection │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Use & Feedback
┌──────────────────────────▼──────────────────────────────────────┐
│                      SkillOS Core                               │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐  │
│  │ 1. Skill Repository     │  │ 2. Skill Governance          │  │
│  │    SkillWiki + Graph    │  │    Git-style Version Control │  │
│  └─────────────────────────┘  └──────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 3. Skill-Centric Runtime                                    │ │
│  │    Resolution → Executor → StateTracker → Verifier → Trace │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  C. Knowledge Sources    D. Experience Pipeline   E. Lifecycle  │
│  Tasks/Trajectories/Docs → Extractor→Normalizer→Summarizer→Indexer │
└─────────────────────────────────────────────────────────────────┘
                           │ Self-Management Flow
┌──────────────────────────▼──────────────────────────────────────┐
│              B. Self-Management Agents                          │
│  Builder / Auditor / Maintainer / Librarian / Meta-Controller  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 四条核心工作流

### 2.1 Task Execution Flow（任务执行流）

```
用户输入 Goal
    │
    ▼
[Planner Agent]
  理解目标，分解为高层计划
    │
    ▼
[Retrieval Agent]
  在 SkillWiki 中检索相关 Skill（BM25 + 语义相似度）
    │
    ▼
[Composition Agent]
  将检索到的 Skill 组合为可执行工作流（DAG）
    │
    ▼
[Execution Agent / SkillExecutor]
  按计划执行 Skill（prompt→LLM / code→sandbox / composite→递归）
  StateTracker 记录前后状态快照
    │
    ▼
[Verifier Agent]
  验证执行结果是否满足 Goal 的后置条件
    │
    ▼
[Reflection Agent]
  分析成功/失败原因，生成改进建议
    │
    ▼
经验写入 Experience Store → 触发 Self-Management Flow
```

### 2.2 Self-Management Flow（自管理流）

```
触发条件：执行完成 / 定时演化周期 / 健康监控告警
    │
    ▼
[Meta-Controller Agent]
  接收事件，路由到对应 Agent
    │
    ├──→ [Skill Builder Agent]
    │      从任务描述/轨迹生成新 Skill 草稿 → S1 Candidate
    │
    ├──→ [Skill Auditor Agent]
    │      安全审计 + 质量评估 → 生成 AuditResult
    │
    ├──→ [Skill Maintainer Agent]
    │      repair（修复失败 Skill）
    │      split（拆分过大 Skill）
    │      deprecate（废弃低质量 Skill）
    │
    └──→ [Skill Librarian Agent]
           更新 Wiki 页面、图谱关系、版本记录
```

### 2.3 Experience / Feedback Flow（经验反馈流）

```
原始输入（轨迹/文档/API文档/代码脚本）
    │
    ▼
[Extractor Agent]
  收集并解析原始数据，提取动作序列
    │
    ▼
[Normalizer Agent]
  将动作序列结构化为标准 ExperienceUnit 格式
    │
    ▼
[Summarizer Agent]
  提取关键步骤和状态变更，生成摘要
    │
    ▼
[Indexer Agent]
  写入 Experience Store，建立检索索引
    │
    ▼
触发 Skill Builder → 生成 Skill 候选
```

### 2.4 Governance Flow（治理流）

```
Skill 创建（S0 Raw）
    │
    ▼
S1 Candidate（Builder 生成草稿）
    │
    ▼
S2 Draft（人工/自动审核）
    │
    ▼
S3 Verified（Auditor 通过安全审计）
    │
    ▼
S4 Released（发布，可被 Agent 使用）
    │
    ├──→ S5 Degraded（成功率下降，触发 Maintainer 修复）
    │         │
    │         └──→ S4 Released（修复成功）
    │
    └──→ S6 Deprecated（废弃，有替代 Skill）
              │
              └──→ S7 Archived（归档）

版本演化：
  Released Skill → new-version（patch/minor/major）→ 新版本进入 S2 Draft
```

---

## 3. Skill 类型体系

| 层级 | 类型 | 描述 | 示例 |
|------|------|------|------|
| L1 | **Atomic** | 单一、不可分割的操作 | `click_element`, `type_text` |
| L2 | **Functional** | 组合多个 Atomic Skill 的工作流 | `fill_form`, `login_flow` |
| L3 | **Strategic** | 元认知策略，操作其他 Skill | `generate_skill_from_task`, `audit_skill_safety` |

---

## 4. Skill 生命周期状态机

```
S0 (Raw Experience)
    │ ingest / extract
    ▼
S1 (Candidate) ←──────────────────────────────────────┐
    │ review / formalize                               │
    ▼                                                  │
S2 (Draft)                                             │
    │ audit / verify                                   │
    ▼                                                  │
S3 (Verified)                                          │
    │ release                                          │
    ▼                                                  │
S4 (Released) ──→ S5 (Degraded) ──→ repair ───────────┘
    │                    │
    │                    └──→ S6 (Deprecated)
    │                              │
    └──→ S6 (Deprecated)           └──→ S7 (Archived)
```

状态转换规则：
- `S0 → S1`：经验提取后自动创建候选
- `S1 → S2`：人工或 LLM 审核通过
- `S2 → S3`：Auditor 安全审计通过
- `S3 → S4`：发布（release）
- `S4 → S5`：成功率低于阈值（默认 60%）
- `S5 → S4`：Maintainer 修复成功
- `S4/S5 → S6`：废弃（有更好替代）
- `S6 → S7`：归档

---

## 5. 模块间接口关系

| 调用方 | 被调用方 | 接口 |
|--------|----------|------|
| Planner Agent | SkillWiki | `wiki.search(query)` |
| Retrieval Agent | Indexing | `search.search(SearchQuery)` |
| Composition Agent | SkillGraph | `graph.get_execution_order()` |
| SkillExecutor | SkillWiki | `wiki.get(skill_id)` |
| SkillExecutor | LLMClient | `llm.chat(messages)` |
| Verifier Agent | LLMClient | `llm.chat(messages)` |
| Reflection Agent | LLMClient | `llm.chat(messages)` |
| Meta-Controller | Builder/Auditor/Maintainer/Librarian | 事件路由 |
| Skill Builder | LLMClient + SkillWiki | 生成草稿 + 写入 |
| Experience Pipeline | LLMClient | 各阶段 LLM 调用 |
| API Routes | AppState (deps.py) | 依赖注入 |

---

## 6. 关键数据模型

### Skill
```python
class Skill(BaseModel):
    skill_id: str           # UUID
    name: str
    description: str
    skill_type: SkillType   # atomic / functional / strategic
    state: SkillState       # S0-S7
    version: str            # semver (1.0.0)
    tags: List[str]
    interface: SkillInterface       # input/output schema + pre/postconditions
    implementation: SkillImplementation  # code / prompt_template / sub_skill_ids
    metrics: SkillMetrics           # success_rate, total_executions, avg_latency
    provenance: SkillProvenance     # source_type, author
    meta_category: Optional[MetaSkillCategory]  # L3 only
```

### ExecutionPlan
```python
class ExecutionPlan:
    plan_id: str
    task_description: str
    steps: List[PlanStep]   # 有序/并行步骤
    is_complete: bool
    has_failures: bool
```

---

## 7. 部署架构

```
                    ┌─────────────────┐
                    │   Browser       │
                    │  (React/Vite)   │
                    └────────┬────────┘
                             │ HTTP + WebSocket
                    ┌────────▼────────┐
                    │  FastAPI        │
                    │  (port 8000)    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼──────┐ ┌─────▼──────┐ ┌───▼────────────┐
    │  Memory Store  │ │ LLM Client │ │  SkillGraph    │
    │  (in-memory)   │ │ (Anthropic)│ │  (in-memory)   │
    └────────────────┘ └────────────┘ └────────────────┘

当前：全内存存储（demo 模式）
生产：PostgreSQL + Neo4j + Redis（storage/ 目录已有适配器）
```

---

*最后更新：2026-04-25 | 维护人：项目负责人*
