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
4. **执行历史持久化**：当前 `_execution_history` 是内存列表，重启丢失，可接入存储层
5. **WebSocket 实时推送**：executor 已有 `_emit()` 机制，可完善前端 WebSocket 消费逻辑
6. **Verifier 增强**：当前 fallback 过于宽松，可增加基于 postconditions 的规则验证

---

*更新此文档时请同步更新 `architecture.md` 中的 Task Execution Flow 部分（联系负责人）*
