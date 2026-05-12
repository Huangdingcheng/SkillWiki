# Module 04: Self-Management Agents

负责分支：`agents-dev`

Self-Management Agents 是 SkillOS 的自我维护层。它不直接负责前端展示，而是让系统能从经验中生成 Skill、审计 Skill、修复或拆分坏掉的 Skill，并把健康状态和演化结果通知出去。

## 模块职责

- **Builder**：从任务描述或执行轨迹生成 Skill 草稿。
- **Auditor**：检查 Skill 的安全性、schema 一致性、prompt 变量和实现完整性。
- **Maintainer**：执行 repair / split / merge / deprecate 四类维护动作。
- **Librarian**：把 Skill 变化同步到 Wiki、Graph 和版本记录。
- **Meta-Controller**：根据事件把工作路由到对应 Agent。
- **Monitor / Evolution Engine**：评估 Skill 健康状态，并运行演化周期。

## 当前 Agent 能力

### Skill Builder Agent

路径：`skillos/layers/skill_management/builder.py`

当前能力：
- 生成 atomic / functional / strategic 三类 Skill 草稿。
- 使用稳定英文 ASCII prompt，要求 LLM 只返回 JSON。
- 自动归一化非法名称、非法 `skill_type`、空描述、空 prompt 和越界 confidence。
- 自动补齐 prompt 中出现但 schema 未声明的变量。
- 清理 `required` 中不存在于 `properties` 的字段。

输出保持现有 `SkillDraft` 结构，不新增跨组字段。

### Skill Auditor Agent

路径：`skillos/layers/skill_management/auditor.py`

当前能力：
- 检查 Skill 名称、描述、input/output schema、implementation 是否完整。
- 检查 `input_schema.required` 与 `properties` 是否一致。
- 检查 `prompt_template` 中 `{xxx}` / `{{xxx}}` 变量是否存在于 `input_schema.properties`。
- 检查危险代码模式，例如 `subprocess`、`eval(`、`exec(`、`open(`。
- 使用轻量加权评分，`audit_score` 保持在 `[0, 1]`。

### Skill Maintainer Agent

路径：`skillos/layers/skill_management/maintainer.py`

当前能力：
- `repair()`：修复 prompt/code，LLM 返回空实现时给出清晰失败原因。
- `split()`：最多生成 5 个子 Skill，跳过空子项，归一化非法名称、描述和 prompt。
- `merge()`：合并两个相似 Skill，通过 `MaintenanceResult.updated_skill` 返回合并后的 Skill，并在 `details` 中记录 source ids、merge rationale、confidence。
- `deprecate()`：返回废弃决策，记录 reason 和可选 replacement skill id，不直接修改 Wiki 状态。

### Feedback & Evolution

路径：`skillos/layers/feedback_evolution/`

当前能力：
- `monitor.py`：根据成功率、执行次数、延迟、长期未使用情况评估健康状态。
- `repair.py`：根据健康报告生成修复结果，LLM 不可用时返回稳定 `RepairResult`。
- `evolution_engine.py`：运行一次演化周期，生成 repair / deprecate / split / merge 任务并汇总结果。

## Evolution API

当前对外接口保持不变：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/api/v1/evolution/health` | 系统健康报告 |
| `GET` | `/api/v1/evolution/health/{id}` | 单个 Skill 健康报告 |
| `POST` | `/api/v1/evolution/repair/{id}` | 修复指定 Skill |
| `POST` | `/api/v1/evolution/cycle` | 运行一次完整演化周期 |

响应字段继续使用现有 `HealthReportResponse`、`SystemHealthResponse`、`EvolutionCycleResponse`，本模块没有修改飞书锁定接口。

## 第四阶段：健康事件与演化事件

第四阶段新增的是“事件可见性”，不是后台自动调度系统。

### 健康事件

单个 Skill 或系统健康报告中发现异常状态时，API 层会通过现有 WebSocket 广播：

| 事件名 | 触发条件 |
| --- | --- |
| `health_degraded` | Skill 或系统报告中存在 degraded 状态 |
| `health_critical` | Skill 或系统报告中存在 critical 状态 |

payload 摘要：

```json
{
  "skill_id": "skill-id-or-system",
  "skill_name": "Skill name",
  "status": "degraded",
  "success_rate": 0.72,
  "issues": ["low success rate"],
  "timestamp": "2026-05-03T12:00:00Z"
}
```

系统级事件会额外带上 `total_skills`、`healthy_count`、`degraded_count`、`critical_count` 和最多 10 个 `affected_skills`。

### 演化周期完成事件

`POST /api/v1/evolution/cycle` 完成后广播：

```text
evolution_cycle_done
```

payload 摘要：

```json
{
  "cycle_id": "cycle-id",
  "tasks_total": 3,
  "tasks_completed": 2,
  "tasks_failed": 1,
  "repaired": 1,
  "deprecated": 0,
  "merged": 0,
  "split": 1,
  "errors": [],
  "timestamp": "2026-05-03T12:00:00Z"
}
```

事件广播沿用当前 WebSocket 旧格式：

```json
{ "event": "...", "data": {} }
```

E 前端已经兼容旧格式和飞书格式，所以本阶段不重写 WebSocket 契约。广播失败会被记录为非阻塞 warning，不影响 REST API 正常返回。

健康事件带有 30 秒服务端冷却窗口，同一事件和同一对象短时间内不会重复广播。这样可以避免 Dashboard 自动刷新触发健康检查、健康检查又触发前端刷新，从而形成重复告警循环。

## Meta-Skills

Self-Management Agents 设计上会使用一组 Strategic L3 Meta-Skills。当前代码已经围绕这些能力建立 Builder / Auditor / Maintainer / Librarian 的入口，真实 Meta-Skill 内容后续可继续沉淀到 Wiki。

| Meta-Skill | 分类 | 功能 |
| --- | --- | --- |
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

## 关键文件

```text
skillos/skillos/layers/
├── skill_management/
│   ├── builder.py
│   ├── auditor.py
│   ├── maintainer.py
│   ├── librarian.py
│   ├── meta_controller.py
│   └── __init__.py
└── feedback_evolution/
    ├── monitor.py
    ├── repair.py
    └── evolution_engine.py
```

## 已完成阶段

- **第一阶段：底座稳定**
  清理 Builder / Auditor / Maintainer / Repair 的 LLM-facing prompt 和 fallback，增加基础归一化与回归测试。

- **第二阶段：Builder + Auditor 质量增强**
  增强 few-shot prompt、schema/prompt 变量对齐、审计规则和 audit score 稳定性。

- **第三阶段：Maintainer 维护动作收口**
  补齐 repair / split / merge / deprecate 四类维护动作，并补充对应测试。

- **第四阶段：事件可见性**
  增加 `health_degraded`、`health_critical`、`evolution_cycle_done` WebSocket 事件，让 E 前端 Dashboard / Evolution 能跟随 D 的真实健康和演化结果刷新。

## 仍未完成

- 后台自动定时演化周期。
- WebSocket 事件在真实多客户端场景下的端到端联调。
- C 组真实执行历史与 D 组健康退化判断的闭环联调。
- A 组真实 Skill 图谱边与 D 组 merge/split 结果的写入联调。
- D 任务最终 PR 交付说明与组长 review。

## 验证命令

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos\skillos
python -m compileall -q skillos\api skillos\layers\feedback_evolution skillos\layers\skill_management
python -m pytest tests\test_skill_management_phase1.py -q
python -m pytest skillos\tests\test_governance_runtime_evolution.py -q
python -m pytest skillos\tests\test_layers.py -q
git diff --check
```
