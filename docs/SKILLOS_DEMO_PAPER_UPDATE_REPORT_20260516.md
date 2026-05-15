# SkillOS Demo-Paper Prototype 更新报告

版本日期：2026-05-16
对应分支：`demo-paper-ready-20260514`
上一提交基线：`c84b86b feat: integrate demo-paper SkillOS prototype`

## 1. 总体结论

本轮更新后，SkillOS 已经从“有前后端、有基础 Skill Wiki、有演示 benchmark”的 prototype，推进到更接近 demo paper 的系统闭环：

- 输入侧支持五类来源：Trajectory、Document、API Doc、Script、Past Skills。
- Document 不再只是总结文本，而是加入 Ctx2Skill-lite 的 challenge/rubric/judge/proposer 证据。
- Past Skills 能把已有 skill 描述转换成 SkillOS schema，并按 SkillX-style 分成 atomic、functional、strategic。
- Runtime 新增 harness verification loop：S2 Draft 可以被执行、验证、失败修复、重试，并在通过后进入 S3 Verified。
- Graph 从 Skill-only 关系扩展到 Source、Skill、Execution、Validation、Version 的异构证据链，并能投影回 Skill-only 视图。
- 前端全英文化，并新增 Past Skills、拖拽导入、Harness Verification 页面、Ctx2Skill Evidence、SkillX Layering 和 Graph Relation Preview。
- 一键启动和一键恢复 demo 状态已经公开化，组员可以从仓库直接复现。

更准确的论文口径是：**这是一个 paper-inspired / method-grounded P0/P1 demo-paper prototype，不是完整复现某一篇论文的全量训练或 benchmark。**

## 2. 相比上一次提交的显著进步

### 2.1 输入层从四类变成五类

上一次提交主要展示 Trajectory、Document、API Doc、Script 四类输入。现在新增：

- `past_skills`
- 前端 `Past Skills` tab
- 后端 `PARSE_SOURCE_TYPES` 支持 `past_skills`
- Past Skills JSON/YAML/Markdown/自由文本归一化
- 依赖、组件、父 Skill、来源关系预览

这让 SkillOS 不只从“新经验”学习，也能吸收已有 skill 库、外部 agent skill、团队历史任务说明。

### 2.2 Document 从弱项变成有证据链的输入方式

上一次 Document 更像文档摘要。现在 Document 会走 Ctx2Skill-lite：

- Context Pack：把文档拆成 facts、procedures、constraints、examples、tools/APIs。
- Challenger：根据 context 生成 challenge task。
- Rubric：为 challenge 生成可检查标准。
- Reasoner/Judge：用 rubric 判断候选 Skill 是否足够。
- Proposer：从失败或缺口生成 Skill draft。
- Cross-Time Replay lite：同一 context 下比较候选，选分数更高的 draft。

实现程度：保留 Ctx2Skill 的核心系统机制，但没有复现论文中的大规模长期 self-play、模型训练或 benchmark。

### 2.3 Runtime 从“能执行”升级为“能验证并推进生命周期”

上一次 runtime 主要证明 Skill 可以被 planner 选中并执行 demo plan。现在新增：

- `LocalSkillOSHarness`
- `CodexCliHarness`
- `VerificationLoop`
- harness evidence workspace/store
- `/api/v1/harness/{skill_id}/verify-loop`
- `/api/v1/harness`
- `/api/v1/harness/{loop_id}`
- 前端 `/harness` 页面

最关键变化：Skill 现在可以从 S2 Draft 经过执行和 postcondition verifier 检查进入 S3 Verified。失败时会尝试 repair，再重试，而不是直接人工标记。

### 2.4 Graph 从关系展示升级为证据图

上一次 Graph 主要是 Skill-only 的依赖/组合/相似关系。现在新增：

- Source -> Skill
- Skill -> Execution
- Execution -> Validation
- Validation -> Version
- Version -> Skill
- HIN meta-path projection 到 Skill-only graph
- Graph relation strength 元数据

页面现在明确说明：

- 强关系：`depends_on`、`composes_with`、`evolved_from`、`replaces`
- 弱关系：`similar_to`

这避免把共享来源投影出来的 `similar_to` 误讲成执行依赖。

### 2.5 演示复现能力明显增强

上一次 PR 依赖本机准备好的状态。现在仓库内新增：

- `START_SKILLOS_DEMO.bat`
- `STOP_SKILLOS_DEMO.bat`
- `RESTORE_SKILLOS_DEMO_STATE.bat`
- `skillos-one-click-launcher`
- `scripts/restore_demo_state.py`
- `docs/demo-fixtures`
- `docs/SKILLOS_DEMO_OPERATOR_GUIDE_20260516.md`

组员可以：

1. 配置自己的 API key。
2. 一键启动前后端和网页。
3. 一键导入公开 demo fixtures。
4. 重复恢复 memory backend 的演示状态。

## 3. 参考项目和论文方法

### 3.1 SkillX

采用内容：

- atomic / functional / strategic 三层 Skill 表示。
- Skill 之间存在依赖、组合、层级关系。
- Skill repository 应支持按粒度组织和检索。

实现程度：

- 已实现 schema 字段、前端展示、Past Skills 分层、图关系预览。
- 未完整复现 SkillX 自动构建 SkillKB、pseudo-plan feedback correction、active expansion。

对应模块：

- A：Skill Repository
- E：Knowledge Import / Candidate Review
- Graph：Skill-only dependency/composition view

### 3.2 Ctx2Skill

采用内容：

- Challenger 生成任务挑战。
- Reasoner 尝试解决任务。
- Judge 根据 rubric 判断成功或失败。
- Proposer/Generator 根据缺口生成 Skill。
- Cross-Time Replay lite 用挑战回放比较候选。

实现程度：

- 已实现 demo-paper 级 Ctx2Skill-lite extraction evidence。
- Document、Past Skills、Script/API Doc/Trajectory 都可以携带 ctx2skill evidence 或 fallback warning。
- 未复现完整 long-horizon self-play、大规模并行采样、训练流程。

对应模块：

- E：Knowledge Import
- A：Candidate Skill schema
- C：harness 后续验证入口

### 3.3 XSkill / Trace2Skill

采用内容：

- 区分 action-level experience 和 task-level Skill。
- Trajectory 不是直接发布成正式 Skill，而是先进入 ExperienceUnit 和 S1 Candidate。
- Execution history 可以作为后续学习材料。

实现程度：

- 已实现 `/ingest/parse`、ExperienceUnit、Candidate Review。
- Agent execution history 可转换为 experience。
- 多轨迹归纳成一个泛化 Skill 仍是 P1/P2 后续。

对应模块：

- C：Runtime execution history
- E：Trajectory input
- A：Skill repository

### 3.4 SKILLFOUNDRY

采用内容：

- Skill 应有 provenance。
- Skill 应有 tests / validation evidence。
- Skill lifecycle 不应跳过验证。
- 异构资源可以汇聚为 structured skill cards。

实现程度：

- 已实现 provenance、candidate audit、harness validation、Source/Skill/Execution/Validation/Version 异构链。
- 未复现 SKILLFOUNDRY 的完整资源生态和大规模实验。

对应模块：

- A：repository + graph
- B：governance / lifecycle
- C：harness validation
- E：frontend evidence panels

### 3.5 WebXSkill / SkillWeaver

采用内容：

- Web Skill 应有参数化动作程序。
- 自然语言步骤和可执行动作应能互相对应。
- Web task 需要 verifier，而不是只看文本输出。

实现程度：

- 当前实现是 fake-web/action-program 和 deterministic verifier。
- 尚未接入完整真实浏览器环境 benchmark。
- Harness loop 为未来 Playwright/Browser harness 留出了接口。

对应模块：

- C：runtime execution
- C：harness
- E：execution / evaluation 页面

### 3.6 Reflexion / ExpeL / SkillClaw

采用内容：

- 失败经验应被记录成可读反思或修复建议。
- 不应静默修改正式 Skill。
- 失败 -> 修复 -> 重试 -> 验证 适合放入生命周期 gate。

实现程度：

- Harness loop 已实现失败记录、deterministic repair、retry、S3 promotion。
- Evolution / maintenance proposal 保留 human-in-the-loop。
- 尚未完成长期跨任务记忆带来的性能提升实验证明。

对应模块：

- C：verification loop
- D：self-management / evolution
- B：governance gate

### 3.7 HIN Survey / GraphRAG

采用内容：

- 多类型节点和多类型边表达异构系统。
- meta-path projection 可把异构图投影成同构分析图。
- 图关系应带解释和证据，而不是只有边。

实现程度：

- 已实现 typed heterogeneous graph。
- 已实现 Skill-only projection。
- 已实现 relation strength 元数据，说明 `similar_to` 是弱关系。
- 尚未实现复杂 GNN、社区发现或大规模 GraphRAG retrieval。

对应模块：

- A：graph manager
- E：Knowledge Graph UI

### 3.8 Anthropic Skills / Ctx2Skill GitHub 测试材料

采用内容：

- Anthropic-style Skills 用作 Past Skills 输入测试风格。
- Ctx2Skill GitHub/数据样例用于设计 Document/Script 输入的测试语料和自博弈证据结构。

实现程度：

- 主仓库中只保留了小型 synthetic public fixtures，避免把外部大仓库和本机 artifacts 提交进 PR。
- 大规模 external corpus 仍保留在本机 Codex-skilos 工作记录中，不作为主仓库交付物。

## 4. 按 ABCDE 的完成度说明

### A. Skill Repository

完成：

- Skill schema v0.2 支持分层、provenance、evaluation、harness_validation。
- Past Skills 能导入已有 skill 描述。
- 依赖、组件、继承关系进入 Skill-only graph。
- 异构图记录 Source/Skill/Execution/Validation/Version。

显著进步：

- 从“列表式 Wiki”提升到“带证据和关系的 Skill repository”。

仍有限制：

- 还没有完整 semantic merge 和自动去重。
- `similar_to` 目前是弱投影关系，后续需要阈值或人工确认。

### B. Governance

完成：

- Candidate audit 仍作为 S1 创建前 gate。
- Lifecycle 仍保持 S1/S2/S3/S4 等状态。
- Harness 不会直接发布 S4，只推进到 S3。
- Graph relation strength 帮助审阅者理解哪些关系能作为强依赖声明。

显著进步：

- Governance 不再只看静态字段，而是开始接入执行验证结果。

仍有限制：

- S3 到 S4 仍需要更完整的人工审查或更强自动 reviewer。

### C. Runtime

完成：

- `/execution/skill` 和 `/execution/plan` 保留。
- 新增 harness verify-loop。
- 支持 local SkillOS harness 和 Codex CLI harness adapter。
- Verifier specs 支持 input/output JSON contract。
- 支持失败 repair 和重复版本避免冲突。

显著进步：

- 从“能跑 demo”升级到“能用执行结果给 Skill 打分并推进生命周期”。

仍有限制：

- 真实浏览器和真实外部工具执行仍未完整接入。
- Codex CLI harness 有 mocked/unit test，现场主要用 local SkillOS harness。

### D. Self-Management

完成：

- 失败验证可以产生 repair。
- repair 后创建新版本并形成 `evolved_from` 关系。
- Evolution 页面仍保留 maintenance proposal 思路。

显著进步：

- 自我修复不再只是文字计划，已经进入可运行 harness loop。

仍有限制：

- 长期反思记忆、跨任务学习收益仍缺实验。
- 仍保持 human-in-the-loop，不做静默自改正式 Skill。

### E. Frontend

完成：

- 全面英语 UI。
- Knowledge Import 支持五类输入。
- 支持粘贴和拖拽文件。
- Candidate Review 显示 Ctx2Skill Evidence、SkillX Layering、Graph Relation Preview。
- 新增 Harness Verification 页面。
- Graph 页面新增 relation strength 说明。

显著进步：

- 前端从“展示系统”变成“可操作的 demo-paper workflow”。

仍有限制：

- 大图可读性还需要继续优化，尤其是 dense `similar_to`。

## 5. 当前可以演示到什么程度

可以稳定演示：

- 一键启动前后端和网页。
- 配置真实 OpenAI-compatible LLM。
- 五类输入生成 Candidate Skill。
- Document 的 Ctx2Skill-lite evidence。
- Past Skills 的三层分层和图关系。
- Harness loop 把 S2 推到 S3。
- Skill-only graph、heterogeneous graph、projection graph。
- 公开 fixtures 的一键恢复。

不建议夸大：

- 不是完整 Ctx2Skill。
- 不是完整 SkillX。
- 不是完整 WebXSkill。
- 不是 SkillsBench 规模 benchmark。
- memory backend 导入数据不是持久数据库。

## 6. 验证状态

最近一次本机验证：

- 后端重点测试：`45 passed`
- `npm run lint`：通过
- `npm run build`：通过
- demo restore：通过
- related graph restore：score `1.0`
- harness restore：expectation pass rate `1.0`

已有 warning：

- Python dependency deprecation warnings。
- Git CRLF warnings。

这些 warning 不影响当前 demo 功能。

## 7. PR 中建议重点说明

建议 PR 描述强调：

- 本 PR 是 demo-paper prototype update。
- 目标是系统闭环，不是单篇论文全量复现。
- 新增 public demo fixtures 和 restore script，降低组员复现成本。
- API key 不进入仓库。
- `similar_to` 是弱关系，强依赖看 `depends_on` / `composes_with` / `evolved_from`。

## 8. 后续建议

P1 可继续做：

- 给 `similar_to` 加阈值、分组或折叠展示。
- 接入真实 browser harness。
- 增强 Codex CLI harness 的现场可用性。
- 扩大 benchmark，但保持公开可复现。
- 增加 Git backend 的 demo-state persistence。
- 为剩余 Anthropic-style artifact/API Skills 生成更强负例 verifier。
