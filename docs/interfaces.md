# SkillOS 跨模块接口契约

> **状态：** 锁定（由项目负责人维护）
> **用途：** 定义所有跨模块共享的数据结构，任何成员修改这些结构前必须在群里通知所有相关成员，并由负责人统一更新此文档。

---

## 接口总览

| 接口 | 定义位置 | 生产方 | 消费方 |
|------|----------|--------|--------|
| `SearchResult` / `SearchQuery` | `layers/skill_repository/indexing.py` | A | C, D |
| `RetrievedSkill` | `api/schemas.py` | C | E |
| `ExecutionResult` | `api/schemas.py` | C | E |
| `ExecutionHistoryItem` | `api/schemas.py` | C | E |
| `SkillDraft` | `layers/skill_management/builder.py` | D | A (写入), C (触发) |
| `AuditResult` | `layers/skill_management/auditor.py` | D | B (治理流程) |
| `MaintenanceResult` | `layers/skill_management/maintainer.py` | D | C (Reflection→修复) |
| `SkillHealthReport` / `SystemHealthReport` | `layers/feedback_evolution/monitor.py` | D | E (展示) |
| `StructuredExperience` / `PipelineResult` | `layers/input_knowledge/pipeline.py` | E | A (写入索引) |
| WebSocket 事件格式 | `api/routes/ws.py` | C, D | E |

---

## 1. 检索层接口（Member A 负责）

### `SearchQuery`
```python
# layers/skill_repository/indexing.py
@dataclass
class SearchQuery:
    text: str = ""                          # 自然语言查询
    tags: List[str] = field(default_factory=list)
    skill_type: Optional[SkillType] = None
    domain: Optional[str] = None
    state: Optional[SkillState] = None      # 默认只返回 RELEASED
    min_success_rate: float = 0.0
    max_results: int = 20
    include_deprecated: bool = False
```

### `SearchResult`
```python
# layers/skill_repository/indexing.py
@dataclass
class SearchResult:
    skill: Skill
    score: float                            # 综合相关性分数 [0, 1]，越高越相关
    match_reasons: List[str] = field(default_factory=list)
```

**约定：**
- `score` 取值范围 `[0, 1]`，语义固定为"越高越相关"，无论底层是 BM25 还是 embedding，A 负责归一化
- `match_reasons` 是人类可读的匹配原因列表，E 前端直接展示
- A 引入 embedding 检索后，字段名和类型不得变更，只能改内部计算逻辑

---

## 2. 执行层接口（Member C 负责）

### `RetrievedSkill`（API 响应用）
```python
# api/schemas.py
class RetrievedSkill(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: str         # "atomic" / "functional" / "strategic"
    score: float            # 来自 SearchResult.score，[0, 1]
    match_reason: str       # 来自 SearchResult.match_reasons 拼接
```

### `ExecutionStepResult`
```python
# api/schemas.py
class ExecutionStepResult(BaseModel):
    step_id: str
    step_index: int
    skill_id: str
    skill_name: str
    status: str             # "pending" / "running" / "success" / "failed" / "skipped"
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    latency_ms: Optional[float]
```

### `ExecutionResult`
```python
# api/schemas.py
class ExecutionResult(BaseModel):
    plan_id: str
    goal: str
    status: str             # "success" / "partial" / "failed"
    steps: List[ExecutionStepResult]
    total_latency_ms: float
    final_state: Dict[str, Any]
    retrieved_skills: List[RetrievedSkill] = []   # 检索到的候选 Skill
    experience_recorded: bool = False
    suggested_skill: Optional[Dict[str, Any]] = None
```

### `ExecutionHistoryItem`
```python
# api/schemas.py
class ExecutionHistoryItem(BaseModel):
    execution_id: str
    goal: str
    status: str
    step_count: int
    success_count: int
    total_latency_ms: float
    retrieved_skill_count: int
    created_at: datetime
```

**约定：**
- C 和 E 不得单方面修改 `RetrievedSkill` 和 `ExecutionResult` 的字段，需双方协商
- `status` 枚举值固定为 `"success"` / `"partial"` / `"failed"`，前端按此渲染颜色

---

## 3. 执行计划内部接口（Member C 内部，B/D 可读）

### `StepStatus`
```python
# layers/skill_runtime/planner.py
class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
```

### `PlanStep`
```python
@dataclass
class PlanStep:
    step_id: str
    step_index: int
    skill_id: str
    skill_name: str
    description: str
    input_mapping: Dict[str, Any]
    depends_on: List[str]           # 依赖的 step_id 列表
    status: StepStatus
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    @property
    def latency_ms(self) -> Optional[float]: ...
```

### `VerificationResult`（Verifier → Reflection）
```python
# layers/skill_runtime/verifier.py
@dataclass
class VerificationResult:
    passed: bool
    score: float                    # [0, 1]
    goal: str
    issues: List[str]
    suggestions: List[str]
    details: Dict[str, Any]
```

### `Feedback`（Reflection → MetaController/Maintainer）
```python
# layers/skill_runtime/reflection.py
@dataclass
class Feedback:
    task_id: str
    goal: str
    success: bool
    root_cause: str
    failed_skill_ids: List[str]
    improvement_suggestions: List[str]
    skill_update_proposals: List[Dict[str, Any]]
    experience_summary: str
```

**约定：**
- `Feedback.skill_update_proposals` 是 D（Maintainer）的输入，格式为 `[{"skill_id": ..., "field": ..., "suggested_value": ...}]`
- C 的 Reflection → 自动修复功能需调用 D 的 `SkillMaintainerAgent.repair()`，接口见第 4 节

---

## 4. 自管理 Agent 接口（Member D 负责）

### `SkillDraft`（Builder 输出 → A 写入）
```python
# layers/skill_management/builder.py
@dataclass
class SkillDraft:
    skill: Skill                    # 完整 Skill 对象（state=S0 Raw）
    confidence: float               # 生成置信度 [0, 1]
    source_type: str                # "task" / "trajectory"
    raw_input: str
    build_notes: str
```

### `AuditResult`（Auditor 输出 → B 治理流程）
```python
# layers/skill_management/auditor.py
@dataclass
class AuditResult:
    skill_id: str
    skill_name: str
    passed: bool                    # schema_ok AND safety_ok AND audit_score >= 0.6
    schema_ok: bool
    safety_ok: bool
    postcondition_ok: bool
    issues: List[str]
    recommendations: List[str]
    audit_score: float              # [0, 1]
```

### `MaintenanceResult`（Maintainer 输出 → C Reflection 消费）
```python
# layers/skill_management/maintainer.py
class MaintenanceAction(str, Enum):
    REPAIR     = "repair"
    SPLIT      = "split"
    MERGE      = "merge"
    DEPRECATE  = "deprecate"
    NO_ACTION  = "no_action"

@dataclass
class MaintenanceResult:
    action: MaintenanceAction
    skill_id: str
    success: bool
    updated_skill: Optional[Skill]
    new_skills: List[Skill]         # split 时生成的子 Skill
    reason: str
    details: Dict[str, Any]
```

### `SkillHealthReport` / `SystemHealthReport`（Monitor 输出 → E 展示）
```python
# layers/feedback_evolution/monitor.py
class HealthStatus(str, Enum):
    HEALTHY  = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    STALE    = "stale"
    UNKNOWN  = "unknown"

@dataclass
class SkillHealthReport:
    skill_id: str
    skill_name: str
    status: HealthStatus
    success_rate: float             # [0, 1]
    usage_count: int
    avg_latency_ms: float
    issues: List[str]
    recommendations: List[str]
    generated_at: datetime

@dataclass
class SystemHealthReport:
    total_skills: int
    healthy_count: int
    degraded_count: int
    critical_count: int
    stale_count: int
    skill_reports: List[SkillHealthReport]
    generated_at: datetime
```

**约定：**
- D 新增 WebSocket 健康告警时，事件 payload 结构见第 6 节
- `HealthStatus` 枚举值固定，E 前端按此渲染颜色（green/yellow/red/gray）

---

## 5. Experience Pipeline 接口（Member E 负责，A 消费索引）

### `StructuredExperience` / `PipelineResult`
```python
# layers/input_knowledge/pipeline.py
@dataclass
class StructuredExperience:
    unit_id: str
    source_type: str                # "trajectory" / "document" / "api_doc" / "script"
    raw_content: str
    extracted_actions: List[str]
    normalized_actions: List[Dict[str, Any]]
    summary: str
    proposed_skill_name: Optional[str]
    proposed_description: Optional[str]
    proposed_type: Optional[str]    # "atomic" / "functional" / "strategic"
    confidence: float               # [0, 1]
    index_keywords: List[str]
    index_embedding_hint: str

@dataclass
class PipelineResult:
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str]
    units: List[StructuredExperience]
```

**约定：**
- `proposed_type` 必须是 `"atomic"` / `"functional"` / `"strategic"` 之一，或 `None`
- `confidence >= 0.7` 的 unit 才会被自动提交给 Builder 生成 Skill

---

## 6. WebSocket 事件格式（全员共用）

所有 WebSocket 消息统一格式：

```json
{
  "type": "event_type_snake_case",
  "payload": { ... },
  "timestamp": "2026-04-27T12:00:00Z"
}
```

### 现有事件（C 负责发送）

| `type` | 触发时机 | `payload` 关键字段 |
|--------|----------|-------------------|
| `plan_started` | 开始执行计划 | `plan_id`, `goal`, `step_count` |
| `step_completed` | 单步执行成功 | `plan_id`, `step_id`, `skill_name`, `latency_ms` |
| `step_failed` | 单步执行失败 | `plan_id`, `step_id`, `skill_name`, `error` |
| `plan_completed` | 计划执行完毕 | `plan_id`, `status`, `total_latency_ms` |

### 新增事件（D 负责发送，需提前告知 E）

| `type` | 触发时机 | `payload` 关键字段 |
|--------|----------|-------------------|
| `health_degraded` | Skill 进入 degraded 状态 | `skill_id`, `skill_name`, `success_rate` |
| `health_critical` | Skill 进入 critical 状态 | `skill_id`, `skill_name`, `success_rate` |
| `evolution_cycle_done` | 演化周期完成 | `repaired`, `deprecated`, `split`, `merged` |

**约定：**
- `type` 统一使用 `snake_case`
- `timestamp` 统一使用 ISO 8601 UTC 格式
- D 新增事件类型前，必须在此文档中登记，并通知 E 更新前端消费逻辑

---

## 修改流程

```
发现需要修改某接口
    │
    ▼
在群里通知所有相关成员（见接口总览表）
    │
    ▼
协商确认新字段/类型
    │
    ▼
各成员同步修改自己负责的代码
    │
    ▼
PR 合并后，负责人更新此文档
```

---

*此文档由项目负责人维护，team 成员不直接修改*
