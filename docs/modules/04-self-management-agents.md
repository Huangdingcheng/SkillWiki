# Module 04: Self-Management Agents

**负责人分支：`agents-dev`**

---

## 职责概述

Self-Management Agents 是 SkillOS 自演化能力的核心，负责：
- 从任务/轨迹自动生成新 Skill
- 对 Skill 进行安全审计和质量评估
- 修复、拆分、合并、废弃 Skill
- 维护 Wiki 页面和图谱关系
- 协调所有自管理流程（Meta-Controller）
- 系统健康监控与演化周期调度

---

## 5 个 Self-Management Agents

### 4.1 Skill Builder Agent（`layers/skill_management/builder.py`）

从任务描述或执行轨迹自动生成 Skill 草稿。

```python
class SkillBuilderAgent:
    async def build_from_task(
        task_description: str,
        context: Dict = {},
    ) -> SkillDraft

    async def build_from_trajectory(
        trajectory: str,
    ) -> SkillDraft

@dataclass
class SkillDraft:
    name: str
    description: str
    skill_type: str
    tags: List[str]
    input_schema: Dict
    output_schema: Dict
    prompt_template: Optional[str]
    confidence: float           # 生成置信度 [0, 1]
    source: str                 # "task" / "trajectory"
```

**使用的 Meta-Skill：**
- `generate_skill_from_task()`
- `generate_skill_from_trajectory()`
- `formalize_skill_schema()`

### 4.2 Skill Auditor Agent（`layers/skill_management/auditor.py`）

对 Skill 进行安全审计和质量评估。

```python
class SkillAuditorAgent:
    async def audit(skill: Skill) -> AuditResult

@dataclass
class AuditResult:
    skill_id: str
    is_safe: bool
    audit_score: float          # [0, 1]，越高越好
    risks: List[str]            # 风险列表
    quality_issues: List[str]   # 质量问题
    recommendations: List[str]  # 改进建议
    passed: bool                # audit_score >= 0.6 且 is_safe
```

**审计维度：**
1. **本地规则检查**（无需 LLM）：
   - 名称/描述是否为空
   - 接口 Schema 是否完整
   - 代码是否包含危险操作（`import os`, `subprocess`, `open(` 等）
2. **LLM 深度审计**（使用 `audit_skill_safety()` Meta-Skill）：
   - 代码注入风险
   - 权限越界
   - 资源滥用
   - 数据泄露

### 4.3 Skill Maintainer Agent（`layers/skill_management/maintainer.py`）

修复、拆分、废弃 Skill。

```python
class SkillMaintainerAgent:
    async def repair(skill: Skill, failure_info: str) -> MaintenanceResult
    async def split(skill: Skill, reason: str) -> MaintenanceResult
    async def deprecate(skill: Skill, reason: str) -> MaintenanceResult

@dataclass
class MaintenanceResult:
    action: str             # "repair" / "split" / "deprecate"
    success: bool
    root_cause: str
    new_skills: List[Dict]  # split 时生成的子 Skill 草稿
    notes: str
```

**使用的 Meta-Skill：**
- `repair_failed_skill()`
- `split_oversized_skill()`
- `merge_redundant_skills()`
- `deprecate_low_utility_skill()`

### 4.4 Skill Librarian Agent（`layers/skill_management/librarian.py`）

维护 Wiki 页面内容、图谱关系和版本记录。

```python
class SkillLibrarianAgent:
    async def update(skill_id: str, update_reason: str, **kwargs)
    async def register_new(draft: SkillDraft, wiki: SkillWikiManager) -> Skill
    async def add_relation(
        source_id: str,
        target_id: str,
        relation_type: str,
        graph: SkillGraphManager,
    )
```

**使用的 Meta-Skill：**
- `update_skill_wiki_page()`
- `update_skill_graph_relation()`

### 4.5 Meta-Controller Agent（`layers/skill_management/meta_controller.py`）

协调所有自管理流程，接收事件并路由到对应 Agent。

```python
class MetaControllerAgent:
    def enqueue(event_type: str, payload: Dict)
    async def process_queue(wiki: SkillWikiManager)

# 事件类型
EVENT_TYPES = {
    "skill_failed": → Maintainer.repair()
    "skill_degraded": → Maintainer.repair() 或 deprecate()
    "new_experience": → Builder.build_from_trajectory()
    "skill_oversized": → Maintainer.split()
    "skills_redundant": → Maintainer（merge）
    "skill_updated": → Librarian.update()
}
```

---

## 12 个 Meta-Skills（Strategic L3）

Meta-Skills 是 Self-Management Agents 使用的工具，以 Strategic Skill 形式存储在 SkillWiki 中。

| Meta-Skill | 分类 | 功能 |
|------------|------|------|
| `generate_skill_from_task` | generation | 从任务描述生成 Skill 草稿 |
| `generate_skill_from_trajectory` | generation | 从执行轨迹提取 Skill |
| `formalize_skill_schema` | knowledge_management | 规范化 Skill Schema |
| `generate_skill_tests` | quality_assurance | 自动生成测试用例 |
| `audit_skill_safety` | quality_assurance | 安全审计 |
| `verify_skill_postcondition` | quality_assurance | 验证后置条件 |
| `repair_failed_skill` | maintenance | 修复失败 Skill |
| `split_oversized_skill` | maintenance | 拆分过大 Skill |
| `merge_redundant_skills` | maintenance | 合并重复 Skill |
| `deprecate_low_utility_skill` | lifecycle | 废弃低质量 Skill |
| `update_skill_wiki_page` | knowledge_management | 更新 Wiki 页面 |
| `update_skill_graph_relation` | graph | 更新图谱关系 |

---

## Feedback & Evolution（`layers/feedback_evolution/`）

### Monitor（`monitor.py`）

持续监控所有 Skill 的健康状态。

```python
class SkillHealthMonitor:
    async def check_skill(skill: Skill) -> HealthReport
    async def check_all(skills: List[Skill]) -> SystemHealth

@dataclass
class HealthReport:
    skill_id: str
    skill_name: str
    status: str         # "healthy" / "degraded" / "critical" / "stale"
    success_rate: float
    usage_count: int
    avg_latency_ms: float
    issues: List[str]
    recommendations: List[str]
```

**健康状态判断规则：**
- `healthy`：success_rate >= 0.8 且 total_executions >= 5
- `degraded`：success_rate < 0.8 且 >= 0.5
- `critical`：success_rate < 0.5
- `stale`：total_executions < 5（数据不足）

### Repair（`repair.py`）

调用 Maintainer Agent 修复 degraded/critical Skill。

### Evolution Engine（`evolution_engine.py`）

定期运行演化周期，批量处理需要维护的 Skill。

```python
class EvolutionEngine:
    async def run_cycle(wiki, graph) -> EvolutionCycleResult
    # 1. 检查所有 Skill 健康状态
    # 2. 修复 critical Skill
    # 3. 废弃长期 stale 且低质量的 Skill
    # 4. 识别并合并重复 Skill
    # 5. 拆分过大 Skill
```

---

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/v1/evolution/health` | 系统健康报告 |
| `GET` | `/api/v1/evolution/health/{id}` | 单个 Skill 健康报告 |
| `POST` | `/api/v1/evolution/repair/{id}` | 修复指定 Skill |
| `POST` | `/api/v1/evolution/cycle` | 运行完整演化周期 |

---

## 关键文件

```
skillos/skillos/layers/
├── skill_management/
│   ├── builder.py          # SkillBuilderAgent
│   ├── auditor.py          # SkillAuditorAgent
│   ├── maintainer.py       # SkillMaintainerAgent
│   ├── librarian.py        # SkillLibrarianAgent
│   ├── meta_controller.py  # MetaControllerAgent
│   └── __init__.py         # 统一导出
└── feedback_evolution/
    ├── monitor.py          # SkillHealthMonitor
    ├── repair.py           # 修复逻辑
    └── evolution_engine.py # 演化周期调度
```

---

## 优化方向（Member D 任务）

1. **Builder 质量提升**：`build_from_task()` 的 LLM prompt 可增加 few-shot 示例，提升生成 Skill 的质量和格式一致性
2. **Auditor 规则扩展**：增加更多本地规则检查（如 prompt_template 变量与 input_schema 的一致性验证）
3. **Meta-Controller 事件持久化**：当前 action queue 是内存列表，可接入消息队列（如 Redis）
4. **演化周期自动触发**：当前需要手动调用 `/evolution/cycle`，可增加定时任务（APScheduler）
5. **Skill 合并流程**：`merge_redundant_skills` Meta-Skill 已定义，但 Maintainer 的 merge 逻辑尚未完整实现
6. **健康监控告警**：当 critical Skill 数量超过阈值时，通过 WebSocket 推送告警事件

---

## 第一阶段完成内容（agents-dev）

第一阶段目标是先稳定 D 模块底座，不扩张跨组接口，不改变飞书锁定结构。当前已完成：

### Prompt 与 fallback 清理

- `builder.py`、`auditor.py`、`maintainer.py`、`repair.py` 的 LLM-facing prompt 已改为稳定英文 ASCII prompt。
- 所有 prompt 都明确要求只返回 JSON，减少 LLM 额外解释导致的解析失败。
- Builder 和 Repair 在 LLM 调用失败或返回无效内容时，会返回可读、结构清楚的 fallback 结果，不再让乱码或异常直接污染接口输出。

### Builder 输出归一化

`SkillBuilderAgent` 当前会对 LLM 输出做轻量归一化：

- `name` 为空或非法时生成安全 snake_case 名称。
- 非法 `skill_type` 回退为 `atomic`。
- `confidence` clamp 到 `[0, 1]`。
- 缺失或非法 schema 补为 `{ "type": "object", "properties": {} }`。
- 缺失 `prompt_template` 时补可读的默认 prompt。
- Strategic Skill 会自动补 `MetaSkillCategory.GENERATION`，避免模型校验失败。

### Auditor 第一批规则增强

`SkillAuditorAgent` 保留原有本地规则，并新增：

- `input_schema.required` 必须是 list。
- `required` 中的字段必须存在于 `input_schema.properties`。
- `prompt_template` 中出现的 `{xxx}` 或 `{{xxx}}` 变量必须存在于 `input_schema.properties`。
- Skill 名称必须符合 snake_case 并以小写字母开头。
- 审计分数保持 `[0, 1]`，失败项写入 `issues`，修复建议写入 `recommendations`。

### Maintainer / Repair 稳定性

- `SkillMaintainerAgent.repair()` 不再创建空 `SkillImplementation`，避免 Pydantic 校验失败。
- repair 输出如果没有可用 prompt/code，会返回清楚的失败原因。
- `SkillMaintainerAgent.split()` 会归一化子 Skill 名称，并补默认 description / prompt。
- `SkillRepair.repair()` 捕获 LLM 调用失败，返回稳定 `RepairResult`，不让 `/evolution/repair/{id}` 因 LLM 不可用直接崩溃。
- `RepairResult`、`MaintenanceResult` 字段未变。

### Evolution API 契约保持不变

本阶段不修改这些对外响应字段：

| API | 响应结构 |
| --- | --- |
| `GET /api/v1/evolution/health` | `SystemHealthResponse` |
| `GET /api/v1/evolution/health/{id}` | `HealthReportResponse` |
| `POST /api/v1/evolution/repair/{id}` | 现有 repair dict |
| `POST /api/v1/evolution/cycle` | `EvolutionCycleResponse` |

新增测试覆盖了 Evolution API response model 字段，防止后续误改字段名。

### 第一阶段新增测试

新增 `tests/test_skill_management_phase1.py`，覆盖：

- Builder：LLM 返回非法 `skill_type`、空描述、非法 name、confidence 越界时能归一化。
- Builder：LLM 失败时 fallback Skill 可读且结构完整。
- Auditor：`required` 字段不存在于 `properties` 时审计失败。
- Auditor：`prompt_template` 使用 schema 中不存在的变量时审计失败。
- Repair：LLM 调用失败时返回清楚错误，不抛出到 API 层。
- Evolution：健康报告、系统健康、演化周期响应字段保持稳定。

## 第二阶段完成内容（agents-dev）

第二阶段目标是增强 Builder 和 Auditor 的质量判断，不新增跨组接口，不进入 merge、自动演化或 WebSocket 告警。

### Builder 质量增强

- `build_from_task()` 和 `build_from_trajectory()` 的 LLM prompt 增加 few-shot 风格示例，覆盖 atomic、functional、strategic 三类 Skill。
- prompt 明确要求 `skill_type` 只能是 `atomic` / `functional` / `strategic`，并要求 `prompt_template` 变量与 `input_schema.properties` 对齐。
- Builder 会自动补齐 prompt 中出现但 schema 未声明的变量，补为 string 类型参数。
- Builder 会清理 `required` 中不存在于 `properties` 的字段，避免生成不一致 schema。
- description 为空或过短时会补成可读说明，减少候选 Skill 只有空描述或任务名的情况。

### Auditor 规则增强

- 缺少 implementation 或空 `prompt_template` 会进入 `issues`。
- `output_schema.properties` 为空时给出 `recommendations` 并轻微扣分，但不直接阻断合法 Skill。
- `subprocess`、`eval(`、`exec(`、`open(` 等危险代码命中后 `safety_ok = false`，审计不通过。
- functional / strategic Skill 缺少组合意图或 `sub_skill_ids` 时给出质量建议，不强制改变接口。
- 审计分数从简单按 issue 数扣分改为轻量权重，最终仍保持 `[0, 1]`。

### 新增测试覆盖

`tests/test_skill_management_phase1.py` 在第一阶段测试基础上新增：

- Builder 自动补齐 prompt 变量到 `input_schema.properties`。
- Builder 自动清理无效 `required` 字段。
- Builder 对 strategic Skill 自动补 `MetaSkillCategory.GENERATION`。
- Auditor 对危险代码失败，并给出更低审计分数。
- Auditor 对合法 prompt Skill 通过，且审计分数保持较高。

### 前两阶段仍未完成项

以下内容不属于第一、第二阶段，放到后续阶段：

- `merge_redundant_skills` 的完整合并流程。
- 演化周期自动定时触发。
- WebSocket 健康告警：`health_degraded` / `health_critical` / `evolution_cycle_done`。
- D 与 C 的 Reflection → Maintainer 自动修复闭环真实联调。
- D 与 A 的图谱关系写入、合并/拆分边同步真实联调。

---

*更新此文档时请同步更新 `architecture.md` 中的 Self-Management Flow 部分（联系负责人）*
