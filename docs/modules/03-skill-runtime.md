# Module 03: Skill Runtime & Task Execution Agents

**负责人分支：`runtime-dev`**

---

## 职责概述

Skill Runtime 是 SkillOS 的执行引擎，负责：
- 6 个 Task Execution Agents 的协同调度
- Skill 的实际执行（LLM / 代码沙箱 / 组合递归）
- 执行状态追踪与快照管理
- 执行结果验证与反思

---

## 子模块

### 3.1 Planner Agent（`layers/skill_runtime/planner.py`）

将自然语言目标分解为结构化执行计划。

```python
class ExecutionPlanner:
    async def plan(
        task_description: str,
        available_skills: List[Skill],
        current_state: Dict,
    ) -> ExecutionPlan
```

**ExecutionPlan：**
```python
@dataclass
class ExecutionPlan:
    plan_id: str
    task_description: str
    steps: List[PlanStep]
    is_complete: bool       # 所有步骤成功
    has_failures: bool      # 存在失败步骤
    total_steps: int

    def get_ready_steps(self) -> List[PlanStep]  # 无依赖且未执行的步骤
    def to_summary(self) -> Dict
```

**PlanStep：**
```python
@dataclass
class PlanStep:
    step_id: str
    step_index: int
    skill_id: str
    input_mapping: Dict[str, Any]
    dependencies: List[str]     # 依赖的 step_id 列表
    status: StepStatus          # PENDING / RUNNING / SUCCESS / FAILED / SKIPPED
    result: Optional[Dict]
    error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    latency_ms: Optional[float]
```

**规划策略：**
- 有 LLM：调用 LLM 分析任务，从 available_skills 中选择并排序
- 无 LLM（fallback）：按相关度分数直接排序，顺序执行

### 3.2 Retrieval Agent（`layers/skill_repository/indexing.py`）

在 SkillWiki 中检索与任务相关的 Skill。

```python
class SkillSearchEngine:
    async def search(query: SearchQuery) -> List[SearchResult]
```

检索策略（混合打分）：
- 名称/描述关键词匹配（BM25 风格）
- 标签精确匹配（加权）
- 类型过滤
- 状态过滤（默认只返回 S4 Released）

### 3.3 Composition Agent（`layers/skill_runtime/composition.py`）

将检索到的 Skill 组合为可执行的 DAG 工作流。

```python
class CompositionAgent:
    async def compose(
        task: str,
        skills: List[Skill],
        current_state: Dict,
    ) -> SkillGraph

@dataclass
class SkillGraph:
    nodes: Dict[str, Skill]
    edges: List[SkillEdge]

    @property
    def execution_order(self) -> List[str]  # 拓扑排序
```

### 3.4 Execution Agent / SkillExecutor（`layers/skill_runtime/executor.py`）

按执行计划运行 Skill，支持并行执行无依赖步骤。

**三种执行路径：**

```python
async def _run_skill(skill, input_data, current_state):
    if impl.prompt_template:
        return await _run_prompt_skill(...)   # LLM 调用
    if impl.code:
        return await _run_code_skill(...)     # 受限沙箱执行
    if impl.sub_skill_ids:
        return await _run_composite_skill(...)  # 递归执行子 Skill
```

**代码沙箱安全限制：**
```python
safe_builtins = {
    "len", "range", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "list", "dict", "set", "tuple",
    "str", "int", "float", "bool", "type", "isinstance",
    "print", "repr", "abs", "min", "max", "sum", "round",
    "any", "all", "next", "iter", "hasattr", "getattr"
}
# 禁止：import, open, exec, eval, __import__ 等
```

**并行执行：**
```python
# 无依赖的步骤并行执行
ready_steps = plan.get_ready_steps()
if len(ready_steps) > 1:
    results = await asyncio.gather(*[
        _execute_step(step, skill_map, tracker)
        for step in ready_steps
    ], return_exceptions=True)
```

**重试机制：**
- 默认最多重试 2 次（`max_retries=2`）
- 超时默认 30 秒（`step_timeout_s=30.0`）
- 超时/异常后自动 rollback 状态

### 3.5 State Tracker（`layers/skill_runtime/state_tracker.py`）

追踪执行过程中的环境状态变化。

```python
class StateTracker:
    def update(changes: Dict)           # 更新当前状态
    def snapshot_before(skill_id, name) # 执行前快照
    def snapshot_after(skill_id, name)  # 执行后快照
    def push_checkpoint()               # 保存检查点
    def rollback()                      # 回滚到上一检查点
    @property
    def current(self) -> Dict           # 当前状态
```

### 3.6 Verifier Agent（`layers/skill_runtime/verifier.py`）

验证执行结果是否满足目标的后置条件。

```python
class VerifierAgent:
    async def verify(
        goal: str,
        final_output: Dict,
        trace_summary: str,
    ) -> VerificationResult

@dataclass
class VerificationResult:
    passed: bool
    confidence: float
    reasoning: str
    violations: List[str]
```

**Fallback 策略：** LLM 不可用时，`output 非空 = 通过`

### 3.7 Reflection Agent（`layers/skill_runtime/reflection.py`）

分析执行结果，生成改进建议和 Skill 更新提案。

```python
class ReflectionAgent:
    async def reflect(
        task_id: str,
        goal: str,
        trace: List[Dict],
        verification_result: VerificationResult,
    ) -> Feedback

@dataclass
class Feedback:
    root_cause: str
    failed_skill_ids: List[str]
    improvement_suggestions: List[str]
    skill_update_proposals: List[Dict]  # 建议更新的 Skill 字段
```

---

## 完整执行流程

```
POST /execution/plan  { goal: "填写登录表单" }
    │
    ▼
1. [Retrieval] search(goal, max_results=10)
   → [click_element(0.85), type_text(0.82), fill_form(0.91)]
    │
    ▼
2. [Planner] plan(goal, available_skills, state)
   → ExecutionPlan:
     Step 1: fill_form (depends_on: [])
     Step 2: click_element (depends_on: [fill_form])
    │
    ▼
3. [Executor] execute_plan(plan, skill_map, initial_state)
   → Step 1: fill_form → _run_composite_skill
       → sub: click_element → _run_code_skill → output["success"]=True
       → sub: type_text → _run_code_skill → output["success"]=True
   → Step 2: click_element → _run_code_skill
    │
    ▼
4. StateTracker 记录每步前后状态快照
    │
    ▼
5. 返回 ExecutionResult {
     retrieved_skills: [...],
     steps: [...],
     experience_recorded: true,
     final_state: {...}
   }
```

---

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/v1/execution/plan` | 执行完整计划（检索+规划+执行） |
| `POST` | `/api/v1/execution/skill` | 直接执行单个 Skill |
| `GET` | `/api/v1/execution/state` | 获取当前执行状态 |
| `DELETE` | `/api/v1/execution/state` | 重置执行状态 |
| `GET` | `/api/v1/execution/history` | 获取最近 20 次执行历史 |

---

## 关键文件

```
skillos/skillos/layers/skill_runtime/
├── planner.py          # ExecutionPlanner, ExecutionPlan, PlanStep
├── retriever.py        # 检索辅助（已集成到 indexing.py）
├── composition.py      # CompositionAgent, SkillGraph, SkillEdge
├── executor.py         # SkillExecutor（核心执行引擎）
├── state_tracker.py    # StateTracker
├── verifier.py         # VerifierAgent, VerificationResult
└── reflection.py       # ReflectionAgent, Feedback
```

---

## 优化方向（Member C 任务）

1. **Planner 质量提升**：当前 LLM prompt 较简单，可增加 few-shot 示例，提升计划质量
2. **并行执行优化**：当前并行执行无超时协调，可增加全局超时和部分失败处理策略
3. **Reflection → 自动修复**：当前 Reflection 只生成建议，可接入 Maintainer Agent 自动触发修复
4. **执行历史持久化**：接入 PostgreSQL 存储层 , 基本实现日志永久化
5. **WebSocket 实时推送**：executor 已有 `_emit()` 机制，可完善前端 WebSocket 消费逻辑
6. **Verifier 增强**：当前 fallback 过于宽松，可增加基于 postconditions 的规则验证

---

## Phase 1: Runtime contract stabilization

第一阶段先稳定 C 与 E/D/A 的联调契约，不扩展新的 REST endpoint。

- `/api/v1/execution/plan` 会把 A 层 `SearchResult.match_reasons` 拼接成 `RetrievedSkill.match_reason`，避免前端执行页因字段名混用报错。
- `ExecutionResult.status` 统一为 `success` / `partial` / `failed`，与飞书接口和 E 前端颜色语义保持一致。
- `ExecutionStepResult` 保留 E 当前消费的 `outputs`，同时增加飞书契约中的 `step_index` 和 `result`；后端返回时 `outputs` 与 `result` 使用同一份 step 输出。
- `/api/v1/execution/history` 返回 `ExecutionHistoryItem` 列表，继续使用内存历史，保持最近执行倒序展示。
- plan 执行完成后会按 step 调用 `wiki.record_execution()`，让 Skill metrics 能被 D 的健康监控消费。
- `SkillExecutor._emit()` 支持同步和异步 WebSocket callback；异步 callback 会被调度执行，callback 异常不会影响主执行流程。
- 执行事件 payload 补齐 `plan_id`、`goal`、`step_count`、`status`、`total_latency_ms`、`skill_name` 等轻量字段；传输格式暂时保持现有 `{ "event": "...", "data": {} }`，由 E 的兼容层消费。

验证命令：

```powershell
python -m compileall -q skillos\api\routes\execution.py skillos\layers\skill_runtime skillos\api\schemas.py
python -m pytest tests\test_skill_runtime_phase1.py -q
python -m pytest tests\test_models.py tests\test_config.py -q
python -m pytest skillos\tests\test_layers.py -q
git diff --check
```

---

## Phase 2: Planner / Retriever quality baseline

第二阶段继续先稳住 C 的智能入口，不扩 REST endpoint，也不改前端契约。

- Planner 的 LLM-facing prompt 改成稳定英文 ASCII，并补充少量 few-shot 示例，明确要求只返回 JSON，且只能使用候选 Skill 中真实存在的 `skill_id`。
- Planner 会归一化 LLM 返回的 steps：过滤不存在的 Skill、重新编号 `step_index`、补齐空的 `skill_name` / `description`、把非法 `input_mapping` 降级为空对象，并清理无效依赖。
- Planner 的 fallback 限制为最多 5 个候选 Skill，按顺序生成可执行计划，并标记 `metadata.source = "fallback"`，避免 LLM 失效时生成过长或不可解释的计划。
- Retriever 的 LLM-facing prompt 改成稳定英文 ASCII，明确 `reuse` / `compose` / `adapt` / `generate` 四种策略边界。
- Retriever 会归一化 LLM 返回的 strategy、selected ids、execution order、confidence 和 parameter mapping；非法 strategy 回退为 `reuse`，confidence clamp 到 `[0, 1]`，不存在的 Skill ID 会被过滤。
- 如果 LLM 没有给出可用 Skill，但搜索结果存在，Retriever 回退到最高分候选；如果搜索结果为空，返回 `generate`，为后续 Builder / D 任务保留入口。
- `retrieve_by_id()` 改成严格按 `skill_id` 精确匹配，即使搜索引擎先返回模糊命中，也不会误执行错误 Skill。

验证命令：

```powershell
python -m compileall -q skillos\layers\skill_runtime skillos\api\routes\execution.py skillos\api\schemas.py
python -m pytest tests\test_skill_runtime_phase1.py tests\test_skill_runtime_phase2.py -q
python -m pytest tests\test_models.py tests\test_config.py -q
python -m pytest skillos\tests\test_layers.py -q
git diff --check
```

---

## Phase 3: Executor stability and partial success

第三阶段稳定 Executor 的运行语义，不扩 REST endpoint，也不扩大代码 Skill 沙箱权限。

- `SkillExecutor.execute_plan()` 不再因为某个 step 失败就立刻停止整个 plan；无依赖或依赖已满足的其它 step 会继续执行。
- 依赖失败的后续 step 会从 `pending` 明确转为 `skipped`，并记录 `Skipped because dependency failed: <step_id>`，避免执行历史和前端状态一直停在 pending。
- 新增 `step_skipped` 事件，payload 包含 `plan_id`、`step_id`、`step_index`、`skill_id`、`skill_name`、`reason` 和 `failed_dependency`。
- `step_failed` 事件统一补齐 `step_index`、`skill_id`、`skill_name`、`error` 和 `latency_ms`，missing Skill、timeout、普通异常都走同一类事件结构。
- missing Skill 路径会补齐 `started_at` / `completed_at`，让 latency 和历史统计更稳定。
- `plan_completed.status` 继续使用 `success` / `partial` / `failed`：只要有成功 step 且存在失败或跳过，就返回 `partial`；没有成功则返回 `failed`。

验证命令：

```powershell
python -m compileall -q skillos\layers\skill_runtime skillos\api\routes\execution.py skillos\api\schemas.py
python -m pytest tests\test_skill_runtime_phase1.py tests\test_skill_runtime_phase2.py tests\test_skill_runtime_phase3.py -q
python -m pytest tests\test_models.py tests\test_config.py -q
python -m pytest skillos\tests\test_layers.py -q
git diff --check
```

---

## Phase 4: Verifier / Reflection maintenance feedback

第四阶段把 C 的执行结果转成更可靠的验证结果和 D 可消费的维护建议，不扩 REST endpoint，也不在 C 内自动修改 Skill。

- `VerifierAgent` 的 LLM-facing prompt 改成稳定英文 ASCII，并要求只输出 JSON。
- Verifier 会归一化 `passed`、`score`、`issues`、`suggestions` 和 `reasoning`；`score` 会 clamp 到 `[0, 1]`，非法列表会降级为空列表。
- Verifier 的 fallback 不再只看 output 是否非空；会检查 `success=false`、`ok=false`、`error`、`exception`、`failed`、`timeout`、`skipped` 等失败证据。
- `ReflectionAgent` 的 LLM-facing prompt 改成稳定英文 ASCII，并明确只能提出后续动作，不能声称已经修复 Skill。
- Reflection 会归一化 `failed_skill_ids`、`improvement_suggestions` 和 `skill_update_proposals`。
- `skill_update_proposals` 面向 D 的 Maintainer / Repair 消费，使用 `recommended_action = repair | deprecate | review | no_action`；非法动作会回退为 `review`。
- Reflection fallback 在验证失败时会尽量从 trace 中提取失败 Skill ID，并生成 repair proposal；验证成功时不生成修复 proposal。
- 本阶段不自动调用 D 的 `SkillMaintainerAgent`，不改 Wiki，不修改任何 Skill 实体。

验证命令：

```powershell
python -m compileall -q skillos\layers\skill_runtime skillos\api\routes\execution.py skillos\api\schemas.py
python -m pytest tests\test_skill_runtime_phase1.py tests\test_skill_runtime_phase2.py tests\test_skill_runtime_phase3.py tests\test_skill_runtime_phase4.py -q
python -m pytest tests\test_models.py tests\test_config.py -q
python -m pytest skillos\tests\test_layers.py -q
git diff --check
```

---

*更新此文档时请同步更新 `architecture.md` 中的 Task Execution Flow 部分（联系负责人）*

---

## Version 2026-05-12: Paper-Guided Member C Runtime Update

This version applies four paper-guided optimizations while keeping the existing Member C REST contract compatible with A/B/D/E.

### 1. Group-Structured Runtime Retrieval

Inspired by Group of Skills, SkillRetriever now supports a structured SkillGroup in addition to the existing flat RetrievalResult.skills and execution_order fields.

- SkillGroup.start_skill_ids: entry skills that should execute the main task.
- SkillGroup.support_skill_ids: preparation, conversion, or helper skills.
- SkillGroup.check_skill_ids: validation or postcondition skills.
- SkillGroup.avoid_skill_ids: candidates that should not enter the runtime plan.
- Fallback retrieval still returns the highest-scoring skill as before.
- Existing callers can continue to consume skills, execution_order, strategy, confidence, and parameter_mapping.

Changed files:

```text
skillos/skillos/layers/skill_runtime/retriever.py
skillos/skillos/layers/skill_runtime/__init__.py
skillos/tests/test_skill_runtime_phase2.py
```

### 2. DAG Composition From Skill Groups and Schemas

Inspired by AgentSkillOS, CompositionAgent now builds a safer executable Skill DAG.

- It can consume SkillGroup directly: Support -> Start -> Check.
- It filters Avoid skills before graph construction.
- It validates LLM-produced edges by removing unknown nodes, self-loops, duplicate edges, invalid edge types, and cycles.
- If LLM composition fails, it tries schema-based dependency inference using overlapping output and input schema keys.
- If schema inference cannot build a graph, it keeps the previous sequential fallback behavior.
- SkillGraph.parallel_groups exposes dependency layers for executor-side scheduling diagnostics.

Changed files:

```text
skillos/skillos/layers/skill_runtime/composition.py
skillos/tests/test_skill_runtime_phase3.py
```

### 3. Failure-State-Aware Verification and Reflection

Inspired by Skill-RAG, runtime failures are now typed and routed instead of being treated as a generic retry signal.

VerifierAgent adds compatible fields:

- failure_type: none, missing_skill, timeout, runtime_error, dependency_failed, postcondition_failed, bad_output, or unknown.
- recovery_route: none, retrieve_alternative_skill, retry_with_timeout_adjustment, repair_skill, replan_dependencies, add_postcondition_check, inspect_output, or review.

ReflectionAgent propagates the same fields into maintenance feedback while preserving D-compatible skill_update_proposals.

Member C still does not mutate Skills and does not call D Maintainer directly. It only produces structured evidence and recommended routes.

Changed files:

```text
skillos/skillos/layers/skill_runtime/verifier.py
skillos/skillos/layers/skill_runtime/reflection.py
skillos/tests/test_skill_runtime_phase4.py
```

### 4. Task-Local Runtime Memory

Inspired by M*, StateTracker now owns a lightweight RuntimeMemory for a single execution task.

Runtime memory records:

- goal and selected skills
- step inputs and step outputs
- failure events with failure type
- lightweight lifecycle events
- optional verification and reflection summaries

SkillExecutor.last_runtime_memory exposes the memory after execution. The memory is intentionally not injected into ExecutionResult.final_state, so existing API consumers keep the previous response shape.

Changed files:

```text
skillos/skillos/layers/skill_runtime/state_tracker.py
skillos/skillos/layers/skill_runtime/executor.py
skillos/skillos/layers/skill_runtime/__init__.py
skillos/tests/test_skill_runtime_memory.py
```

### Compatibility Notes

- No new REST endpoint was added.
- ExecutionResult, ExecutionStepResult, RetrievedSkill, and ExecutionHistoryItem schemas remain compatible.
- Member C does not take over Member A repository indexing, Member B governance, Member D repair/build operations, or Member E UI rendering.
- The backend directory is now tracked as normal files in the top-level repo instead of being stored as an unresolved gitlink.

### Verification

Commands run during this update:

```powershell
python -m compileall -q skillos\skillos\layers\skill_runtime\retriever.py skillos\skillos\layers\skill_runtime\__init__.py
python -m compileall -q skillos\skillos\layers\skill_runtime\composition.py skillos\tests\test_skill_runtime_phase3.py
python -m compileall -q skillos\skillos\layers\skill_runtime\verifier.py skillos\skillos\layers\skill_runtime\reflection.py skillos\tests\test_skill_runtime_phase4.py
python -m compileall -q skillos\skillos\layers\skill_runtime\state_tracker.py skillos\skillos\layers\skill_runtime\executor.py skillos\skillos\layers\skill_runtime\__init__.py skillos\tests\test_skill_runtime_phase3.py skillos\tests\test_skill_runtime_memory.py
python -m pytest skillos\tests\test_skill_runtime_phase3.py -q
python -m pytest skillos\tests\test_skill_runtime_phase4.py -q
python -m pytest skillos\tests\test_skill_runtime_phase3.py skillos\tests\test_skill_runtime_memory.py -q
```

Known environment issue:

```text
python -m pytest skillos\tests\test_skill_runtime_phase2.py -q
```

The command currently fails before assertions because the active Python environment does not load pytest-asyncio; pytest reports `async def functions are not natively supported` and warns that `pytest.mark.asyncio` is unknown. The dependency is declared in skillos/requirements.txt and skillos/pyproject.toml, but it is not active in the current interpreter.

---

## Version 2026-05-14: Paper-Guided Runtime Path and Execution Graph Update

This version turns the paper-guided Member C runtime mechanisms from isolated layer capabilities into the live Agent Execution path, and adds a frontend execution graph view for demos and debugging.

### 1. Runtime Execution Uses SkillRetriever and SkillGroup

The `/api/v1/execution/plan` route now uses `SkillRetriever.retrieve()` before composition. This connects the Group-of-Skills style runtime contract to the real API path:

- `SkillGroup.support_skill_ids` feed preparation/helper skills.
- `SkillGroup.start_skill_ids` feed the main entry skill.
- `SkillGroup.check_skill_ids` feed validation/postcondition skills.
- `SkillGroup.avoid_skill_ids` stay out of the execution graph.
- If retriever selection fails, the route falls back to direct search filtering so the API remains available.

Commit:

```text
ee8089d feat(runtime): route execution through skill retriever
```

### 2. DAG Data Mapping Drives Runtime Inputs

The executor now resolves DAG mapping references such as:

```text
${step_id.customer_data}
```

before executing a dependent step. This makes schema-derived composition executable rather than only descriptive. A support skill can produce an output, and a later start/check skill can receive it as real `input_data`.

Commit:

```text
0294259 feat(runtime): resolve DAG output mappings during execution
```

### 3. Verification and Reflection Are Attached to Executions

The execution API now runs verifier/reflection after plan execution and exposes compatible optional fields:

- `verification`
- `reflection`
- `failure_type`
- `recovery_route`

This connects the Skill-RAG failure-state idea to live Agent Execution. Member C still does not mutate skills; it produces structured feedback for maintenance and repair agents.

Commit:

```text
4fffe0c feat(runtime): attach verification feedback to executions
```

### 4. Runtime Memory Diagnostics Are Exposed

`RuntimeMemory.to_summary()` now includes verification and reflection summaries when available. `ExecutionResult.runtime_memory` exposes the task-local evidence summary without placing it inside `final_state`.

This keeps the public state output stable while making M*-style task evidence available for debugging, benchmark scoring, and future repair routing.

Commit:

```text
8c83710 feat(runtime): expose runtime memory diagnostics
```

### 5. Benchmark v2 Measures Paper-Guided Runtime Dimensions

The runtime benchmark now evaluates Member C as a full runtime architecture instead of only retrieval/planning/execution success.

New benchmark dimensions:

- `skill_group`
- `planning`
- `composition`
- `execution`
- `verification`
- `recovery`
- `memory`

New benchmark cases include `support_start_check_flow` and `missing_skill_recovery_route`.

Commit:

```text
f20b789 test(runtime): benchmark paper guided runtime dimensions
```

### 6. Agent Execution Graph for Frontend Visualization

`ExecutionResult.execution_graph` now returns a frontend-friendly graph:

```text
goal -> retrieved skills -> execution steps
```

The graph includes:

- nodes for the user goal, retrieved skills, and execution steps
- edges for retrieval, planning, and step dependencies
- composition source and parallel group metadata
- step status, latency, and error information

The frontend Agent Execution page renders this as an `Execution RAG Tree` after execution, giving users a clear visual explanation of which skills were retrieved, how they were composed, and what actually ran.

Changed files:

```text
skillos/skillos/api/routes/execution.py
skillos/skillos/api/schemas.py
skillos/tests/test_skill_runtime_phase1.py
skillos-frontend/src/api/types.ts
skillos-frontend/src/pages/AgentExecution.tsx
```

### Verification

Commands run during this update:

```powershell
python -m compileall -q skillos\skillos\api\routes\execution.py skillos\skillos\api\schemas.py skillos\tests\test_skill_runtime_phase1.py
python -m pytest skillos\tests\test_skill_runtime_phase1.py skillos\tests\test_skill_runtime_phase3.py skillos\tests\test_skill_runtime_memory.py skillos\tests\test_runtime_benchmark.py -q
npm run build
```
