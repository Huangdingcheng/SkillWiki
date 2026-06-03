# SkillWiki/SkillOS 面向 EMNLP Demo 2026 的方法与功能说明

本文档以当前代码为准，整理 SkillWiki/SkillOS 的系统方法、功能边界和 demo-paper 叙述口径。旧设计文档只可作为背景材料；本文优先依据 `skillos/skillwiki` 后端、`skillos-frontend` 前端、`skills/` 快照目录和当前 API 实现。

## 1. 系统定位

SkillWiki/SkillOS 是一个以 Skill Wiki 为中心的技能生产与自治管理系统。它面对的不是一次性静态知识库，而是源源不断进入系统的知识原料流：任务描述、执行轨迹、文档、API 文档、脚本、历史 Skill、以及 agent 自身执行后的经验记录。系统的核心不是简单地在这些原料上加一个检索中间层，而是把原料持续加工成可复用、可执行、可验证、可版本化、可治理、可迭代的 Skill 资产。

适合论文中的核心表述：

```text
SkillWiki/SkillOS is an autonomous skill production system that continuously converts heterogeneous knowledge materials into governed, graph-grounded, versioned, executable, and self-maintained Skill assets.
```

更好的 story 是“知识原料进入工厂，Skill 被生产、加工、使用和再生产”：

- 原料进入：trajectory、document、api_doc、script、past_skills 和 runtime `agent_execution` 都可以进入系统，但仍保持为来源证据。
- 生产 Skill：ExperiencePipeline、Ctx2Skill-lite 和 Builder Agent 从原料中抽取 action、结构化经验、候选接口、候选实现、候选验证契约和图关系。
- 加工 Skill：Auditor、Harness、Verifier 和 Lifecycle gate 对候选 Skill 做 schema、安全、postcondition、执行和确定性验证。
- 入库 Skill：Skill Wiki 保存 S1-S4 Skill 资产；Graph 保存依赖、组合、演化、来源、执行、验证和版本证据。
- 使用 Skill：Task Execution Agents 从 Wiki 检索 Skill，规划和执行任务，并把执行结果转为新的 `agent_execution` 原料。
- 维护 Skill：Monitor、Reflection memory 和 Maintainer Agent 把失败、退化和重复错误转成 maintenance proposal。
- 迭代 Skill：Git-backed governance 把 proposal 变成 snapshot、structured diff、review branch、release tag 和 restore commit。

因此，SkillWiki 的中心论点是：Agent 的知识不是被动存储在文档或向量库里，而是被组织成一条持续运转的 Skill 生产线。每个新原料都可能生成新 Skill，每次执行都可能产生新原料，每个失败都可能触发新一轮加工和版本演化。

## 2. 代码中实际存在的系统边界

当前真实 Python 包入口是 `skillwiki`，配置见 `skillos/pyproject.toml`。FastAPI 应用入口是 `skillos/skillwiki/api/main.py`，默认挂载以下模块：

- `/api/v1/skills`
- `/api/v1/lifecycle`
- `/api/v1/graph`
- `/api/v1/execution`
- `/api/v1/harness`
- `/api/v1/evolution`
- `/api/v1/evaluation`
- `/api/v1/ingest`
- `/api/v1/repository`
- `/ws`

启动时 `AppState` 会装配以下组件：

- Wiki/Repository：`MemoryWikiManager` 或 `SkillWikiManager`，默认 `repository_backend="git"`。
- Graph：当前启动代码使用 `MemoryGraphManager`，并支持 Skill-only 图和异构证据图。
- Search：`MemorySearchEngine`，复用 `rank_search_results`。
- Runtime：`SkillRetriever`、`SkillPlanner`、`SkillExecutor`、`CompositionAgent`、`VerifierAgent`、`ReflectionAgent`、`StateTracker`。
- Governance：`VersionController`、`SkillReviewer`、`SkillMerger` 和 Git-backed snapshot/release workflow。
- Self-management：`SkillBuilderAgent`、`SkillAuditorAgent`、`SkillMaintainerAgent`、`SkillLibrarianAgent`、`MetaControllerAgent`。
- Evolution：`SkillMonitor`、`SkillRepair`、`EvolutionEngine`。
- Experience：`ExperiencePipeline`。

需要在论文中谨慎表述的边界：

- 当前图谱运行时主要是内存实现；代码中有 PostgreSQL/Neo4j/Redis 适配边界，但 demo 默认不是生产级数据库。
- `past_skills` 和 `Ctx2Skill-lite` 是 lightweight demo-paper 实现，不是完整复现大规模自博弈训练。
- Runtime 可执行 prompt/code/composite Skill，但真实浏览器、真实开放环境和官方 benchmark sandbox 不应被过度声称。
- 自管理 Agent 生成的是草稿、审计、修复候选和 proposal，不应描述为无人工审查直接修改 canonical Skill。

## 3. 核心数据模型：Skill 作为可治理资产

代码中的核心模型位于 `skillos/skillwiki/models/skill_model.py`。一个 Skill 不是简单 prompt，而是一个完整资产对象：

```text
Skill =
  identity + classification + lifecycle +
  interface + implementation + test/evaluation +
  external references + runtime metrics +
  provenance + graph relations + deprecation metadata
```

### 3.1 三层 Skill 类型

代码定义了三类 Skill：

- `atomic`：原子操作，如点击、输入、调用单个工具或执行单个解析动作。
- `functional`：可复用功能单元，可由子 Skill 组合，也可用 workflow prompt 表示。
- `strategic`：管理、生成、维护、质量保证、知识管理、图谱和生命周期相关的元技能；代码要求 strategic Skill 必须带 `meta_category`。

Strategic Skill 的 `meta_category` 包括：

- `lifecycle`
- `optimization`
- `quality_assurance`
- `knowledge_management`
- `generation`
- `maintenance`
- `graph`

### 3.2 八阶段生命周期

代码中的 `SkillState` 是 S0-S7：

- `S0 Raw Experience`：原始经验，未结构化。
- `S1 Skill Candidate`：候选 Skill，已识别但未验证。
- `S2 Draft`：结构化草稿。
- `S3 Verified`：已验证。
- `S4 Released`：已发布，可被 agent 使用。
- `S5 Degraded`：降级，成功率下降或健康异常。
- `S6 Deprecated`：废弃，不再推荐使用。
- `S7 Archived`：归档，保留历史。

代码中的合法状态转换为：

```text
S0 -> S1
S1 -> S2
S2 -> S3 or S1
S3 -> S4 or S2
S4 -> S5 or S6
S5 -> S4 or S6
S6 -> S7
S7 -> terminal
```

论文中可强调：SkillWiki 把 transient experience 和 released capability 分离，避免把一次执行轨迹直接当作可信技能。

### 3.3 Skill 接口、实现和评估契约

`SkillInterface` 包含：

- `input_schema`
- `output_schema`
- `preconditions`
- `postconditions`
- `side_effects`

`SkillImplementation` 至少需要以下之一：

- `code`
- `prompt_template`
- `tool_calls`
- `sub_skill_ids`

这使 Skill 既可以是 prompt-based，也可以是 code-based，也可以是组合式 workflow。

`SkillEvaluation` 包含：

- `verifier_specs`
- `test_case_refs`
- `benchmark_task_ids`
- `validation_summary`
- `harness_validation`

其中 `verifier_specs` 是运行时确定性验证的核心，它支持把 “是否成功” 从主观 LLM 判断下沉到可重复规则。

### 3.4 Provenance 与图谱关系

`SkillProvenance` 保存：

- `source_type`
- `source_ids`
- `parent_skill_ids`
- `created_by_agent`
- `creation_context`

Skill 还保存便捷图关系字段：

- `dependency_ids`
- `component_ids`
- `implementation.sub_skill_ids`
- `provenance.parent_skill_ids`

Graph 层会据此生成 `depends_on`、`composes_with`、`evolved_from` 等边。

## 4. 方法总览

系统方法应当从“持续生产”而不是“静态中间层”来叙述。SkillWiki/SkillOS 的方法是一条自治 Skill 生产线，加上两套 agent 框架。

### 4.1 主链：从原料流到 Skill 资产再到再生产

```text
Knowledge materials
  -> parse / extract / normalize / summarize / index
  -> S1 Candidate Skill
  -> audit / harness / deterministic verifier
  -> S2 Draft / S3 Verified / S4 Released
  -> retrieval / planning / execution
  -> execution experience / metrics / reflection memory
  -> maintenance proposal
  -> Git review bundle / snapshot / tag / restore commit
  -> new or repaired Skill version
```

这条链条的创新点在于：知识原料不是一次性消费品。被执行过的 Skill 会产生新经验，失败经验会进入 reflection memory，重复失败会生成 maintenance proposal，proposal 会进入 Git-governed review，最终形成新的 Skill 版本。这使系统具备持续吸收、生产、加工、使用和再生产能力。

可以把系统理解为六个生产阶段：

- 采集：把外部原料和 agent 内部执行经验统一接入。
- 提炼：抽取动作、约束、接口、验证信号和候选关系。
- 成型：生成 S1 Candidate Skill，并保留 provenance。
- 质检：通过 Auditor、Verifier 和 Harness gate 审查。
- 投产：Released Skill 被 Runtime 检索和执行。
- 返修：失败、退化、重复反思进入 proposal 和 Git review。

### 4.2 双框架一：Task Execution Agents

执行框架服务于“把已生产 Skill 投入任务使用”：

- Retrieval：从 Skill Wiki 搜索候选 Skill。
- Planner：把自然语言目标转成执行计划。
- Executor：执行 prompt/code/composite Skill。
- State Tracker：记录执行状态和状态变更。
- Verifier：用 deterministic specs 或 LLM fallback 验证结果。
- Reflection：把失败和验证结果整理为维护建议。

### 4.3 双框架二：Self-Management Agents

自管理框架服务于“让 Skill 生产线自主运行”：

- Builder：从任务或轨迹生成 Skill 草稿。
- Auditor：检查 schema、安全、postcondition、prompt 变量和实现完整性。
- Maintainer：提出 repair、split、merge、deprecate 等维护动作。
- Librarian：同步 Wiki、Graph 和版本控制语义。
- Meta-controller：路由自管理事件。
- Monitor/Evolution Engine：评估健康状态并产生演化任务和维护 proposal。

关键边界：自管理 Agent 不直接绕过治理层修改 live Skill。D 侧生成 proposal，B 侧治理层创建 snapshot/diff/review bundle，E 侧前端让人审查。

这个边界不削弱“自主”叙事。更准确的说法是：系统自主发现、生成、审计、诊断、提出修改和准备治理材料；P0 demo 中最终合入仍是 human-in-the-loop，以保证 demo-paper 能清楚展示安全治理和可审计演化。

## 5. 知识原料集：不改变原始材料，只生成可审查候选

知识导入路由位于 `skillos/skillwiki/api/routes/ingest.py`。当前支持五类 parse source：

- `trajectory`
- `document`
- `api_doc`
- `script`
- `past_skills`

候选创建额外接受：

- `agent_execution`

### 5.1 ExperiencePipeline

`ExperiencePipeline` 位于 `layers/input_knowledge/pipeline.py`，包含：

- `ExtractorAgent`：抽取可复用 action。
- `NormalizerAgent`：把 action 归一化为结构化操作。
- `SummarizerAgent`：生成候选 Skill 描述、类型和标签。
- `IndexerAgent`：生成检索关键词和 embedding hint。
- `Ctx2SkillLiteExtractor`：生成 context pack、challenge、rubric、judge/replay-like evidence 和候选 Skill metadata。

输出的 `StructuredExperience` 包含：

- `unit_id`
- `source_type`
- `raw_content`
- `extracted_actions`
- `normalized_actions`
- `summary`
- `proposed_skill_name`
- `proposed_description`
- `proposed_type`
- `confidence`
- `index_keywords`
- `index_embedding_hint`
- `metadata`

### 5.2 Ctx2Skill-lite

对 `trajectory/document/api_doc/script`，pipeline 会先执行通用抽取、归一化、摘要和索引，然后调用 Ctx2Skill-lite 产生候选 metadata。该 metadata 可包含：

- `ctx2skill_evidence`
- `layering_reason`
- `graph_relation_preview`
- `candidate_interface`
- `candidate_implementation`
- `candidate_relations`
- `candidate_evaluation`

论文中建议表述为：

```text
We implement a Ctx2Skill-inspired lightweight evidence generator, not the full self-play training pipeline.
```

### 5.3 Past Skills 导入

对 `past_skills`，系统会解析 legacy Skill JSON/YAML/JSONL/Markdown/free text，将其规范化为 SkillOS schema 候选，并生成：

- SkillX-style `atomic/functional/strategic` 分层推断。
- 输入/输出 schema。
- 安全 prompt template。
- dependencies/components/parents/tool_calls 候选关系。
- verifier specs 和 test refs。

这使系统可以把既有 prompt、脚本或外部技能说明转成 Skill Wiki 中可审查的 S1 Candidate。

### 5.4 候选创建

`/ingest/parse-and-create` 和 `/ingest/create-candidate` 只创建 `S1 Skill Candidate`。创建时会：

- 构造 `Skill` 对象。
- 设置 `provenance.source_type` 和 `source_ids`。
- 标记 `human_review_required=True`。
- 调用 Auditor 生成审计结果。
- 写入 Wiki。
- 同步 Skill-only 图。
- 同步异构证据图：Source -> Skill -> Execution -> Validation -> Version。

因此，知识导入不是“自动发布技能”，而是“生成带证据、带审计、带图谱关系的候选 Skill”。

## 6. Skill Wiki 与检索层

Skill Wiki 的核心职责是保存 Skill 资产、支持检索、维护图谱和统计。

当前 demo 内存实现位于 `api/memory_store.py`：

- `MemoryWikiManager`
- `MemorySearchEngine`
- `MemoryGraphManager`

持久化实现边界位于：

- `layers/skill_repository/repository.py`
- `layers/skill_repository/graph_manager.py`
- `storage/postgres_db.py`
- `storage/neo4j_db.py`
- `storage/redis_cache.py`

### 6.1 Wiki 基础能力

`MemoryWikiManager` 支持：

- get / get_by_name / get_many
- list / count
- create / update / delete
- create_new_version
- get_version_history
- transition_state / release / deprecate
- record_execution
- get_overview_stats

`create_new_version` 会复制源 Skill，生成新 `skill_id`，递增 semver，并把新版本置为 `S2 Draft`。

### 6.2 搜索方法

搜索实现位于 `layers/skill_repository/indexing.py`。`SearchQuery` 支持：

- text
- tags
- skill_type
- domain
- state
- min_success_rate
- max_results
- include_deprecated
- mode: `lexical` 或 `hybrid`

默认 lexical 模式使用规则评分：

- name/display_name 精确匹配。
- name token 匹配。
- description 匹配。
- tag 匹配。
- domain 匹配。
- success rate/usage 质量加成。
- lifecycle state 加成。

Hybrid 模式使用：

```text
score = lexical_score * 0.5 + semantic_score * 0.4 + health_score * 0.1
```

其中 semantic score 由本地 deterministic `LocalHashEmbeddingProvider` 生成，不依赖外部 embedding 服务，适合作为 demo 中可复跑的轻量语义检索。

### 6.3 Graph 层

同质 Skill 图的边类型包括：

- `depends_on`
- `composes_with`
- `similar_to`
- `evolved_from`
- `conflicts_with`
- `replaces`
- `specializes`
- `generalizes`

`MemoryGraphManager.sync_auto_edges()` 会根据：

- `implementation.sub_skill_ids`
- `provenance.parent_skill_ids`
- `dependency_ids`

自动维护 `composes_with`、`evolved_from` 和 `depends_on` 边。自动边带 metadata：

```json
{"auto_generated": true, "source": "skill_repository"}
```

### 6.4 异构证据图

异构图节点类型：

- `source`
- `skill`
- `execution`
- `validation`
- `version`

异构图边类型：

- `derived_from`
- `executed_as`
- `validated_by`
- `versioned_as`
- `composes_with`

系统提供三种图视图：

- Skill-only：技能依赖、组合、相似、演化关系。
- Provenance：Source/Skill/Execution/Validation/Version typed evidence chain。
- Version impact：从异构图投影回 Skill-only 图，展示 meta-path projection。

这支持论文中“graph-grounded Skill evidence”的 demo 叙述。

## 7. 执行层：检索、规划、执行、验证、经验回写

执行 API 位于 `api/routes/execution.py`，主要接口：

- `POST /api/v1/execution/skill`
- `POST /api/v1/execution/plan`
- `GET /api/v1/execution/history`
- `GET /api/v1/execution/history/{execution_id}/experience`
- `GET /api/v1/execution/state`
- `DELETE /api/v1/execution/state`

### 7.1 Plan 执行流程

`/execution/plan` 的代码流程：

```text
goal + context
  -> state_tracker.update(context)
  -> search(SearchQuery(text=goal))
  -> filter executable runtime skills
  -> planner.plan(goal, available_skills, current_state)
  -> wiki.get_many(plan.skill_ids)
  -> executor.execute_plan(plan, skill_map, state)
  -> wiki.record_execution(each step)
  -> deterministic verifier over each Skill's verifier_specs
  -> build ExecutionExperienceUnit
  -> store recent execution history
```

`_is_runtime_planning_skill()` 会过滤：

- `test_graph_` demo node
- `test` tag
- `meta` tag
- strategic Skill
- 无可执行 implementation 的 Skill

因此，实际任务执行主要使用 atomic/functional 可执行 Skill，而不是把管理型元技能误选为任务步骤。

### 7.2 Planner

`SkillPlanner` 支持：

- LLM planning：要求返回严格 JSON，且只能使用候选 Skill 中真实存在的 `skill_id`。
- fallback planning：当 demo key 或 LLM 失败时，按检索顺序最多选择 5 个 Skill 顺序执行。
- input mapping repair：根据 required schema、context、task input 和前序步骤填补缺失参数。

PlanStep 字段包括：

- `step_id`
- `step_index`
- `skill_id`
- `skill_name`
- `description`
- `input_mapping`
- `depends_on`
- `status`
- `result`
- `error`
- timestamps/latency

### 7.3 Executor

`SkillExecutor` 支持三类执行路径：

- prompt Skill：格式化 `prompt_template` 后调用 LLM，尝试解析 JSON。
- code Skill：在受限 Python namespace 内执行 `implementation.code`。
- composite Skill：按 `sub_skill_ids` 递归调用子 Skill。

Code Skill 的 sandbox 只暴露有限 safe builtins，例如 `len/range/list/dict/str/int/float/bool/min/max/sum/any/all` 等。它没有暴露 `open`、`import`、`eval`、`exec` 等危险能力。需要注意：当前仍是 demo 级受限命名空间，不应声称为强安全沙箱。

执行器还实现：

- 无依赖 ready steps 并行执行。
- `max_retries=2`。
- `step_timeout_s=30.0`。
- 执行前后状态快照。
- 失败时 rollback checkpoint。
- 依赖失败时把后续步骤标记为 `skipped`。
- WebSocket 事件：`plan_started`、`step_started`、`step_completed`、`step_failed`、`step_skipped`、`plan_completed`。

### 7.4 Verifier

`VerifierAgent` 优先执行 deterministic verifier specs。支持的 spec 类型包括：

- `boolean_success`
- `json_exists`
- `json_nonempty`
- `json_equals`
- `json_array`
- `json_array_nonempty`
- `json_object`
- `json_object_nonempty`
- `contains`

Verifier 使用 dotted path 解析，例如：

```text
output.success
output.steps[0]
final_state.submitted
```

若没有 verifier specs，则可走 LLM verifier；LLM 失败时 fallback 会检查空输出、`success=false`、`ok=false`、`error`、`exception`、`failed`、`timeout`、`skipped` 等失败证据。

### 7.5 ExecutionExperienceUnit

每次执行都会构造 `ExecutionExperienceUnit`：

- `source_type="agent_execution"`
- `source_execution_id`
- raw execution JSON
- extracted action text
- normalized actions
- proposed skill name/description/type
- confidence
- keywords
- metadata，包括 `paper_method="XSkill action-level experience stream"`

这使 runtime execution history 成为新的知识原料，但仍需通过 `/ingest/audit-candidate` 或 `/ingest/create-candidate` 进入 S1 候选，而不是自动发布。

## 8. Harness 验证闭环

Harness API 位于 `api/routes/harness.py`：

- `POST /api/v1/harness/{skill_id}/verify-loop`
- `GET /api/v1/harness`
- `GET /api/v1/harness/{loop_id}`

`VerificationLoop` 的目标是：

```text
S2 Draft -> harness run -> verifier -> repair/retry -> S3 gate
```

核心规则：

- 只接受 `S2 Draft` Skill。
- 支持 `local_skillos` 和 `codex_cli` harness。
- 根据 `test_cases` 或 schema 自动构造测试用例。
- 若 verifier 全通过，状态为 `verified`。
- 若失败且允许 repair，先尝试 deterministic repair。
- deterministic repair 会根据 verifier 中缺失的 `output.*` 字段生成代码补丁。
- 若 deterministic repair 不足，可调用 `SkillRepair`。
- 修复后的版本会创建新的 Draft Skill，并写入 `evolved_from` 图边。
- promotion gate 要求 `overall >= 0.75`，通过后可把 Skill 推进到 S3 Verified。
- harness evidence 会写入 workspace，并更新 `evaluation.harness_validation`。

这为论文 demo 提供了“候选 Skill 不能直接发布，必须经过可复跑 harness/verifier gate”的证据链。

## 9. 生命周期与 Git 式治理

Lifecycle API 位于 `api/routes/lifecycle.py`。系统同时支持普通生命周期操作和 Git-backed governance。

### 9.1 普通生命周期接口

- `POST /lifecycle/{id}/transition`
- `POST /lifecycle/{id}/release`
- `POST /lifecycle/{id}/deprecate`
- `POST /lifecycle/{id}/new-version`
- `POST /lifecycle/{id}/review`
- `POST /lifecycle/{id}/review-and-release`
- `POST /lifecycle/{id}/record-execution`
- `GET /lifecycle/{id}/diff`
- `GET /lifecycle/{id}/diff/versions`

发布或推进到 audited target states 时，会调用 `SkillAuditorAgent`。如果审计失败，API 返回问题、警告和建议，不允许继续发布。

### 9.2 Snapshot

Git-backed snapshot 代码位于 `layers/skill_governance/skill_snapshot.py`。

Snapshot 路径固定为：

```text
skills/<skill_id>/<version>.json
```

Snapshot 保存稳定字段：

- identity/classification/lifecycle
- interface
- implementation
- test_cases
- evaluation
- provenance
- dependency_ids
- component_ids

它不保存运行噪声字段，例如 metrics、created_at、updated_at 等。

### 9.3 Structured Diff

`diff_skill_snapshots()` 会比较领域字段，包括：

- name/version/description/type/domain/state/tags
- interface input/output/pre/post/side_effects
- implementation prompt/code/tool_calls/sub_skill_ids/execution_order
- test_cases/evaluation/provenance
- dependency_ids/component_ids

Diff 分类包括：

- `schema_change`
- `postcondition_change`
- `implementation_change`
- `dependency_change`
- `provenance_change`
- `metadata_change`

Breaking change 规则包括：

- input/output schema property 删除。
- 新增 required 字段。
- output schema 属性新增、删除或修改。
- schema type 修改。
- 移除已有 prompt/code。
- 移除已有 sub_skill dependency。
- execution_order reorder。

Review recommendation 为：

- `no_changes`
- `review_required`
- `breaking_review_required`

### 9.4 Branch / Review Bundle

`propose_skill_change()` 会：

- 获取当前分支和 HEAD。
- 计算 old/new Skill snapshot structured diff。
- 若无变更，返回 `no_changes`。
- 创建分支：`skill/<skill_name>/<skill_id_prefix>-v<version>`。
- checkout 到分支。
- 写入 snapshot。
- 提交 commit：`skill(<name>): propose v<version>`。
- finally checkout 回原分支。
- 返回 review bundle。

这对应论文中的 Git-style reviewable Skill evolution。

### 9.5 Release Tag 与 Restore Commit

Release tag 格式：

```text
skill/<skill_name>/<skill_id_prefix>/v<version>
```

Rollback 不是 destructive reset，而是：

```text
read snapshot at source_ref
write it to current worktree
commit: skill(<name>): restore from <ref>
```

这一点很适合 demo 展示：系统保留错误修改和恢复操作的完整历史，而不是覆盖历史。

### 9.6 Governance Repository Status

`GET /api/v1/lifecycle/repository/status` 返回本地 Git 状态，包括分支、HEAD、dirty、staged/unstaged/untracked、upstream、ahead/behind 等。当前实现不 push、不创建远程 PR。

## 10. 自主管理与生命周期闭环

自管理层代码位于：

- `layers/skill_management/`
- `layers/feedback_evolution/`
- `api/routes/evolution.py`
- `models/maintenance_model.py`

### 10.1 Builder

`SkillBuilderAgent` 可从：

- task description
- trajectory

生成 `SkillDraft`。它会：

- 生成 snake_case name。
- 选择 atomic/functional/strategic。
- 生成 input/output schema。
- 生成 prompt_template。
- 对 prompt 变量和 input schema 做对齐。
- 失败时生成 fallback draft。

### 10.2 Auditor

`SkillAuditorAgent` 做本地规则审计，并可选 LLM 深度审计。规则包括：

- input/output schema 必须是 object schema。
- required 字段必须存在于 properties。
- name 必须是 snake_case。
- implementation 不能为空。
- prompt_template 不能为空。
- verified/released/degraded Skill 必须有 provenance。
- atomic Skill 必须有 code/prompt/tool_calls。
- functional Skill 必须有 sub_skill_ids 或 workflow prompt。
- strategic Skill 必须有 meta_category。
- S3/S4/S5 必须有 postconditions 或 evaluation verifier/test evidence。
- code 中检查 `os.system`、`subprocess`、`eval(`、`exec(`、`__import__`、`open(` 等危险模式。
- prompt_template 变量必须出现在 input_schema.properties 中。

审计输出：

- passed
- schema_ok
- safety_ok
- postcondition_ok
- issues
- warnings
- recommendations
- audit_score

### 10.3 Maintainer

`SkillMaintainerAgent` 支持四类维护动作：

- repair：生成修复 prompt/code 候选。
- split：把过大的 Skill 拆成 2-5 个 atomic child Skills。
- merge：把两个相似 Skill 合并为一个新 Draft Skill。
- deprecate：返回废弃决策和 replacement 信息。

重要边界：`repair()` 返回 `candidate_updated_skill` 和 `requires_human_review=True`，不是直接修改 live Wiki。

### 10.4 Monitor 与 Evolution

Evolution API 支持：

- `GET /evolution/health`
- `GET /evolution/health/{skill_id}`
- `GET /evolution/proposals`
- `POST /evolution/proposals/{proposal_id}/accept`
- `POST /evolution/proposals/{proposal_id}/reject`
- `POST /evolution/reflection-memory`
- `POST /evolution/repair/{skill_id}`
- `POST /evolution/cycle`

健康检查会生成 `MaintenanceProposal.from_health_report()`，并可持久化到：

```text
SkillStorage/metadata/maintenance/proposal_queue.json
SkillStorage/metadata/maintenance/reflection_memory.json
```

Reflection memory 的阈值为 3。也就是说，同一个 `(skill_id, failure_signature)` 的失败反思出现 3 次后，系统才会生成维护 proposal。这符合“先聚合证据，再进入治理”的策略。

Proposal 被 accept 后，返回的 next action 是：

```text
POST /api/v1/lifecycle/{skill_id}/propose-maintenance-change
```

因此，自管理层和治理层之间的职责划分是：

- D 自管理层：收集证据、形成 proposal。
- B 治理层：创建 patched_skill 的 snapshot、diff 和 review bundle。
- E 前端：让人审查和操作。
- A Wiki/Graph：保存被接受后的 Skill 和证据关系。

## 11. 前端 demo 功能

前端路由位于 `skillos-frontend/src/App.tsx`，API 客户端位于 `src/api/client.ts`。

当前页面包括：

- `/` Dashboard：系统概览、Skill 数量、发布/退化状态、执行次数、健康摘要。
- `/evaluation` Evaluation Dashboard：读取本地 benchmark artifacts，展示 no-skill/raw-prompt/with-skill、search eval、LLM planner eval。
- `/wiki` Skill Wiki：Skill 列表、筛选、详情抽屉、interface/implementation/evidence/timeline/metrics。
- `/graph` Skill Graph：Skill-only、Provenance、Version impact 三种视图。
- `/versions` Version Control：new version、snapshot、history、diff、release tag、rollback、maintenance review。
- `/lifecycle` Lifecycle Demo：S0-S7 状态演示。
- `/demo` Self-Evolution Demo：串起 retrieval、plan generation、execution、experience recording、evolution learning。
- `/execution` Agent Execution：执行目标、展示检索 Skill、步骤、历史和 latency。
- `/harness` Harness Verification：运行 verify-loop、查看 score/attempt/evidence。
- `/evolution` Evolution：系统健康、proposal queue、accept/reject、repair/cycle。
- `/ingest` Knowledge Import：trajectory/document/api_doc/script/past_skills 五类输入，展示 Ctx2Skill Evidence、SkillX Layering、Graph Relation Preview、Resource Contract Preview，并支持候选审计和创建。

前端不是完全静态 mock。它通过 `axios` 调用 `/api/v1` 后端接口，且页面对后端错误有兜底处理。

## 12. 典型 demo 流程

建议 EMNLP Demo 展示顺序：

### 12.1 导入知识并生成 Candidate

1. 打开 `/ingest`。
2. 选择 document、script、api_doc、trajectory 或 past_skills。
3. 运行 parse。
4. 展示 extracted actions、normalized actions、Ctx2Skill evidence、SkillX layering、candidate interface/implementation/evaluation。
5. 运行 audit candidate。
6. 创建 S1 Candidate。
7. 跳转 `/wiki?skill_id=...` 查看 Skill 资产。
8. 跳转 `/graph` 查看 Source -> Skill -> Execution -> Validation -> Version 证据链。

### 12.2 验证 Draft 并推进到 Verified

1. 在 `/wiki` 或 `/versions` 选择 S2 Draft Skill。
2. 打开 `/harness`。
3. 运行 local SkillOS verify-loop。
4. 展示 attempts、verifier specs、repair record、score 和 evidence path。
5. 若 promotion gate 通过，Skill 进入 S3 Verified。

### 12.3 发布和版本治理

1. 在 `/versions` 创建 new version 或 snapshot。
2. 查看 structured diff 和 raw Git diff。
3. 若有 breaking change，展示 impacted skills。
4. 创建 release tag。
5. 演示 rollback：从历史 ref 恢复，并生成 restore commit。

### 12.4 执行 Skill 并回写经验

1. 打开 `/execution` 或 `/demo`。
2. 输入 goal。
3. 展示 Skill retrieval、plan generation、execution steps、verifier summary。
4. 查看 execution history。
5. 打开 execution experience，说明 runtime trace 如何变成 `agent_execution` 原料。

### 12.5 自管理 proposal 到治理闭环

1. 打开 `/evolution`。
2. 查看 health degraded/critical/stale。
3. 运行 evolution cycle 或提交 reflection memory。
4. 展示 proposal queue。
5. Accept proposal。
6. 跳转 `/versions?skill_id=...&proposal_id=...`。
7. 提交 patched_skill 到 lifecycle proposal endpoint。
8. 生成 Git review bundle 和 structured diff。

## 13. 论文方法贡献点

### 13.1 Skill supply chain: from knowledge materials to reusable capabilities

最核心贡献不应写成“我们在 agent 和知识之间加了一个 Skill 中间层”，而应写成“我们实现了一条 Skill supply chain”。在这条链中，原料不是静态 corpus，而是持续进入系统的 `trajectory/document/api_doc/script/past_skills/agent_execution`；产物也不是一次性文本摘要，而是带接口、实现、验证契约、来源证据、图关系和生命周期状态的 Skill 资产。

对应代码路径是 `/ingest`、`ExperiencePipeline`、`Ctx2SkillLiteExtractor`、`SkillBuilderAgent`、`SkillAuditorAgent`、Wiki/Graph 写入和 lifecycle gate。论文中可以把它概括为：

```text
SkillWiki turns a continuous stream of heterogeneous knowledge materials into an auditable production line of executable Skills.
```

这比“memory/RAG 增强 agent”更有区分度：RAG 通常把知识作为检索上下文消费，SkillWiki 把知识加工成可复用生产资料，并让这些生产资料在后续任务中继续产生新原料。

### 13.2 Skill Wiki as governed skill memory

系统把短暂轨迹、文档片段、脚本和 agent execution 转成 Skill Wiki 中的资产对象。每个 Skill 都有 lifecycle、接口、实现、测试、evaluation、provenance 和 graph relations。

区别于普通 memory/RAG：SkillWiki 保存的是可执行、可验证、可版本化的能力单元，而不只是文本片段。

### 13.3 Evidence-preserving input-to-skill conversion

系统不直接把原始材料写成 released Skill，而是生成 S1 Candidate，并保留：

- source evidence
- Ctx2Skill-lite challenge/rubric/judge/replay evidence
- interface/implementation/evaluation preview
- graph relation preview
- human review flag

这个设计让系统能讲清楚“生产”和“发布”的边界：原料进入后先变成带证据的候选，而不是被 LLM 直接写入线上能力库。

### 13.4 Dual-agent autonomous management

Task Execution Agents 用 Skill 解决任务；Self-Management Agents 维护 Skill 系统。两者通过 execution history、reflection memory、maintenance proposal 和 governance review bundle 衔接。

这形成了两个闭环：

- 使用闭环：检索 Skill、规划任务、执行 Skill、验证结果、记录 execution history。
- 生产闭环：从 history/原料中生成候选、审计候选、提出维护 proposal、进入 Git-governed review、产生新版本。

因此“agent 自主管理”不是一句抽象口号，而是落在 Builder/Auditor/Maintainer/Monitor/Evolution Engine 与 lifecycle API 的分工上。更安全的论文口径是：agent 自主发现、生成、审计、诊断和提出变更，最终合入仍由治理层显式 gate。

### 13.5 Deterministic verifier contract

`verifier_specs` 把验证逻辑写入 SkillEvaluation，使 Skill 质量不完全依赖 LLM judge。Harness loop 可用这些 specs 执行 repair/retry/promotion gate。

### 13.6 Git-backed lifecycle governance

Skill 变更不是覆盖数据库字段，而是通过：

- stable JSON snapshot
- branch
- commit
- structured diff
- breaking-change detection
- release tag
- restore commit

形成可审计证据链。

这个贡献的 paper 价值在于把软件工程中的可审计演化机制引入 agent capability memory：Skill 的变更可以被 diff、review、tag、rollback，而不是在向量库或 prompt 集合里不可追踪地漂移。

### 13.7 Graph-grounded provenance and impact

系统同时提供 Skill-only graph 和 heterogeneous provenance graph，并可从异构图投影出 version impact 视图。这使 demo 可以展示“这个 Skill 来自哪里、怎么验证、发布成哪个版本、影响哪些 Skill”。

### 13.8 Lifecycle-aware skill factory rather than static library

SkillWiki 的最终定位应是 lifecycle-aware skill factory，而不是 static skill library。S0-S7 状态机把“原始经验、候选、草稿、验证、发布、退化、废弃、归档”放在同一个对象生命周期内；runtime metrics 和 Monitor 把运行表现重新接回生产线；Maintainer 把退化和失败转成 repair/split/merge/deprecate proposal。

这给论文一个更强的 story：系统不是只展示“已有 Skill 如何被调用”，而是展示“Skill 如何从原料中被生产出来，如何被质检，如何被使用，如何因使用结果而再生产”。

## 14. 评测证据与 LaTeX 表格

当前项目中可直接复查的评测 artifacts 包括 `skillos/benchmarks/results/` 下的 demo benchmark、search eval 和 LLM planner eval。项目报告中还记录了 P0 input-to-skill full workflow、harness、SkillsBench sparse subset、readiness 和测试验收结果。二者应在论文中分开表述：前者是当前仓库内 artifact，后者是报告记录的外部评测路径或历史验收结果。

代码和 git 历史还显示，当前 `skills/` 目录下有 205 个 Skill JSON snapshot，本地 git 历史中也有 205 条面向 `skills/**/*.json` 的 snapshot 相关记录。这支持“SkillWiki 已经不是空壳，而是持续积累 Skill 资产库”的叙事。

### 14.1 结果摘要

可用于论文或 appendix 的核心结果：

- Demo benchmark：12 个跨 web/API/document/script/runtime/governance/graph 的任务，`with_skill` 为 12/12，`no_skill` 和 `raw_prompt` 均为 0/12。
- Search eval：20 个查询、22 个 catalog Skills，lexical 和 hybrid 的 Top-1/Top-3 hit rate 均为 20/20。
- LLM planner eval：12 个任务，DeepSeek `deepseek-v4-flash`，LLM planner 成功 12/12，API failure 为 0。
- Skill asset repository：当前 `skills/` 目录包含 205 个 `1.0.0.json` snapshots，本地 git history 中也可复查到 205 条 Skill snapshot 记录。
- Input-to-Skill P0：五类输入各 25 条，共 125 fixtures；创建 125 个 S1 candidates；overall score 0.91。
- Harness P0：15 个代表 generated Skills，每类输入 3 个；positive pass rate 1.0，negative rejection rate 1.0。
- SkillsBench 5-task subset：Oracle 5/5，No-skill 2/5，SkillOS generated skills 3/5；`dialogue-parser` 从 0.667 提升到 1.0。
- Readiness：15/15；后端代表测试 135 passed；前端 lint/build passed。

如果只能在正文放一个表，建议把表标题从“性能结果”改成“production-line evidence”。原因是 demo paper 的主张不是单点 SOTA，而是系统实现了从原料、生产、加工、使用到再生产的闭环。

### 14.2 LaTeX 表格

论文中可直接使用下表。需要在 LaTeX preamble 中加入：

```latex
\usepackage{booktabs}
\usepackage{tabularx}
```

```latex
\begin{table*}[t]
\centering
\small
\begin{tabularx}{\textwidth}{l l l l X}
\toprule
\textbf{Evaluation} & \textbf{Scope} & \textbf{Baseline} & \textbf{SkillWiki/SkillOS} & \textbf{Evidence and caveat} \\
\midrule
Skill asset repository & 205 Skill snapshots & -- & 205 versioned JSON assets & Current repository state under \texttt{skills/}; git history also contains 205 snapshot records over \texttt{skills/**/*.json}. \\
Demo benchmark & 12 tasks & no-skill: 0/12; raw-prompt: 0/12 & with-skill: 12/12 & Current artifact: \texttt{skillos/benchmarks/results/latest\_summary.json}; deterministic demo benchmark, not an official external benchmark. \\
Skill retrieval & 20 queries, 22 catalog Skills & lexical Top-1/Top-3: 20/20 & hybrid Top-1/Top-3: 20/20 & Current artifact: \texttt{search\_eval\_latest.json}; hybrid uses local hash embedding and is offline/reproducible. \\
LLM planner & 12 tasks & -- & 12/12 success, 0 API failures & Current artifact: \texttt{llm\_eval\_latest.json}; model \texttt{deepseek-v4-flash}, success excludes API failures. \\
Input-to-Skill workflow & 125 fixtures across 5 input types & -- & 125 S1 candidates, overall score 0.91 & Reported in \texttt{SKILLOS\_UPDATE\_REPORT\_SINCE\_LAST\_PR\_20260527.md}; covers parse, audit, create, graph, diff, snapshot, schema, Ctx2Skill evidence, and SkillX layer checks. \\
Harness verification & 15 generated Skills & negative rejection: 1.0 & positive pass: 1.0 & Reported in final update/audit docs; deterministic local contract verification, not open-world semantic correctness. \\
SkillsBench subset & 5 external tasks & no-skill: 2/5; oracle: 5/5 & generated Skills: 3/5 & Reported in \texttt{SKILLOS\_SKILLSBENCH\_FIVE\_TASK\_DEEP\_ANALYSIS\_20260529.md}; clean combined evidence on a small P0 subset. \\
System readiness & 15 service checks & -- & 15/15 passed & Reported in final acceptance docs; backend representative tests: 135 passed; frontend lint/build passed. \\
\bottomrule
\end{tabularx}
\caption{Production-line evidence for SkillWiki/SkillOS. Results are separated by evidence source to distinguish current repository artifacts from report-grounded historical evaluations and to avoid overclaiming official benchmark coverage.}
\label{tab:skillos_eval_summary}
\end{table*}
```

### 14.3 推荐论文口径

建议在正文中这样讲评测：

```text
We evaluate the system at three levels: (i) repository-level production capacity, where 125 heterogeneous fixtures are converted into 125 governed S1 Skill candidates; (ii) runtime-level use, where Skill-augmented execution solves all 12 deterministic demo tasks while no-skill and raw-prompt baselines solve none; and (iii) external benchmark probing, where a 5-task SkillsBench subset improves from 2/5 without Skills to 3/5 with SkillOS-generated Skills, revealing both a structural gain and two concrete repair targets.
```

中文解释：

```text
我们不是只证明“检索更准”，而是证明整条生产线在工作：原料能进入，能变成候选 Skill，候选能审计和入图，部分 Draft 能被 harness 推进到 Verified，Released Skill 能被执行层使用，失败又能变成下一轮维护目标。
```

## 15. 可安全声称与不可过度声称

### 15.1 可以安全声称

- 系统实现了五类输入到 S1 Candidate Skill 的转换。
- Skill 数据模型包含 interface、implementation、evaluation、provenance 和 lifecycle。
- Runtime 实现了检索、规划、执行、状态追踪、确定性 verifier 和 execution history。
- Execution history 会转成 `agent_execution` 经验单元，可被导入为候选 Skill。
- Harness 实现了 S2 Draft 到 S3 Verified 的验证/修复/重试/promotion gate。
- Governance 使用真实 Git 保存 snapshot、diff、branch、tag 和 restore commit。
- Self-management 生成维护 proposal，并保留 human-in-the-loop 边界。
- 前端提供完整 demo surfaces。
- 当前项目包含 205 个 Skill JSON snapshot；本地 git 历史也保留了 205 条面向 `skills/**/*.json` 的 snapshot 记录。
- 当前 artifacts 和报告显示系统已覆盖 input-to-skill、retrieval、runtime benchmark、LLM planner、harness、SkillsBench subset、readiness 和测试验收多层证据。

### 15.2 不应过度声称

- 不应说完整复现 Ctx2Skill、SkillX、WebXSkill 或 SkillsBench。
- 不应说当前内存图谱是生产级长期数据库。
- 不应说 local deterministic harness 等价于真实浏览器或开放环境 benchmark。
- 不应说系统已经证明长期自演化显著提升任务性能。
- 不应说维护 Agent 会安全地全自动发布修改；当前是 proposal-first 和 review-gated。
- 不应说 code Skill sandbox 是强安全隔离；当前是受限 namespace demo 实现。

## 16. 面向论文的系统摘要

可用于中文论文草稿的方法摘要：

```text
我们提出 SkillWiki/SkillOS，一个面向自演化智能体的自治 Skill 生产与治理系统。系统把持续进入的知识原料流，包括任务、轨迹、文档、API 文档、脚本、历史 Skill 和 agent 执行经验，转化为可复用、可执行、可验证、可版本化的 Skill 资产。每个 Skill 被建模为带有接口、实现、测试、确定性评估契约、运行指标、来源溯源和图谱关系的生命周期对象。系统包含两套 agent 框架：任务执行框架负责从 Skill Wiki 检索、规划、组合、执行和验证 Skill；自管理框架负责从新经验中生成候选 Skill、审计质量、诊断退化、提出修复/拆分/合并/废弃 proposal，并把 proposal 送入 Git-backed lifecycle governance。治理层通过 Skill snapshot、structured diff、breaking-change detection、review branch、release tag 和 restore commit 保留可审计演化历史。前端 demo 展示从原料进入、Skill 生产、加工质检、入库成图、任务使用、失败反馈到版本迭代的完整闭环。
```

## 17. 建议论文标题

可选标题：

```text
SkillWiki: A Governed Skill-Centric Wiki for Self-Evolving Agents
```

或：

```text
SkillOS: A Skill-Centric Operating System for Governed and Self-Evolving Agents
```

如果论文中心强调 Wiki，建议使用第一个；如果强调完整 demo 系统和执行/治理闭环，建议使用第二个。

## 18. 建议 Demo Track 展示重点

Demo paper 最有说服力的展示顺序不是“模型效果”，而是“持续 Skill 生产线”：

1. 多源输入如何变成带证据的 S1 Candidate。
2. Candidate 如何通过审计和 harness 才能进入 Verified。
3. Released Skill 如何被 agent 检索、规划和执行。
4. 执行记录如何回写为 action-level experience。
5. 失败如何变成 reflection memory 和 maintenance proposal。
6. proposal 如何进入 Git review bundle，而不是直接覆盖 Skill。
7. 版本 diff、release tag 和 restore commit 如何构成可审计生命周期。

这一叙述与当前代码实现最一致，也能避免过度承诺尚未完成的大规模 benchmark 和真实开放环境执行能力。
