# SkillOS 相比上次提交的最终更新报告

最后更新：2026-05-28
工作分支：`C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526`
对比基线：上次 demo-paper 原型提交和后续 PR #10 的 demo restore/harness evidence flow

## 1. 总体结论

这次收口把 SkillOS 从“功能比较丰富的演示原型”推进到“组长可以按证据逐项检查的 demo-paper prototype”。最关键的变化有四个：

1. 先吸收学长 PR #11 中有价值的 Git/version 设计，不整包 merge，避免回退现有 harness/evaluation/governance。
2. 把五类输入扩到 P0 规模：每类 25 条，共 125 条，全部带来源和 license note。
3. 跑完整 SkillOS 工作流：parse -> audit -> create S1 -> graph -> business diff -> snapshot -> harness 代表验证。
4. 把前端图谱从普通图升级为更适合演示大图的 Nebula/Readable/Debug 可调星云图。

最终可以支持的 demo-paper 主张是：SkillOS 是一个以 Skill 为中心的 agent operating system prototype，能够把多来源经验转换为可治理、可验证、可成图、可版本化的 Skill，并通过执行端 harness 把部分 S2 Draft 推进到 S3 Verified。

## 2. PR #11 整合情况

学长 PR #11 的可取点主要在 Git/version 功能：

- business-readable diff；
- editable version fields；
- Version Lab 页面；
- 从用户视角查看 Skill 版本差异。

我们没有直接 merge PR #11，原因：

- PR #11 基于较早版本，会删除或回退现有 harness、evaluation、governance、graph、demo restore 等工作；
- 其 `repository_store.py` 弱于当前 `GitSkillStore` / `GitVersionStore`；
- 前端 lint 有问题；
- 临时 worktree 中出现过 hardcoded key-looking token，不能直接复制。

本次吸收方式：

- 后端 lifecycle diff 增加 `business_diff`、`business_summary`、`breaking`、`suggested_bump`；
- `NewVersionRequest` 增加 `tags`、`interface`、`implementation`、`evaluation`、`test_cases`、`metadata`；
- 修改 interface 或 implementation 后，新版本进入 S2 Draft，需要 harness 重新验证；
- 前端 Version Control 新增 Version Lab，而不是直接搬 PR #11 页面；
- 保留现有 Git snapshot、release tag、rollback、review bundle、harness evidence。

## 3. 五类输入 P0 语料

新语料根目录：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527
```

Manifest：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527\manifests\input_skill_eval_manifest_p0_20260527.json
```

P0 数量：

| 输入类型 | 数量 | 覆盖域 |
| --- | ---: | --- |
| `trajectory` | 25 | data、finance、office、security、software、web |
| `document` | 25 | data、finance、office、science、security、software |
| `api_doc` | 25 | api、data、security、software |
| `script` | 25 | data、office、science、software |
| `past_skills` | 25 | api、data、finance、office、science、security、software、web |

所有行都有 `source_url` 和 `license_note`，没有缺失文件或空文件。

语料来源：

- SkillsBench：任务说明、oracle/setup 相关材料、跨域任务样例；
- Ctx2Skill：context-to-skill 方法结构和样例风格；
- WebArena / browser-use / RCI-agent：trajectory 样例；
- Anthropic Skills：past_skills 输入；
- Kubernetes、Docker、GitHub Actions、OpenAPI：document/api_doc/script 公开样例。

## 4. 输入转 Skill 完整工作流评测

报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_INPUT_TO_SKILL_P0_FULL_WORKFLOW_REPORT.md
```

结果：

- fixture count：125；
- created candidates：125；
- overall score：0.91；
- 五类输入 parse success、create success、audit pass、graph present、business diff、snapshot、schema、Ctx2Skill evidence、SkillX layer 全部为 1.00。

这说明现在不是只测 `/ingest/parse`，而是测试了：

```text
fixture -> parse -> audit -> create S1 -> graph check -> lifecycle diff/history -> snapshot -> report
```

边界：这个报告不把 SkillsBench 或 harness 分数混进去，避免把不同证据混成一个“虚高总分”。

## 5. SkillsBench / BenchFlow 状态

SkillsBench sparse subset：

```text
C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\artifacts\skillsbench-runs\skillsbench-sparse-p0
```

报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_SKILLSBENCH_P0_REPORT.md
```

已经完成：

- `uv sync --locked` 成功；
- 5 个 SkillsBench task metadata check 通过；
- 40 个 fixture-task 映射；
- 映射覆盖 citation-check、sales-pivot-analysis、software-dependency-audit、court-form-filling、dialogue-parser。

未完成：

- 官方 oracle/no_skill/generated_skill sandbox 得分没有跑出；
- 原因是当前机器没有 Docker/Compose，BenchFlow oracle 尝试报 `[WinError 2] 系统找不到指定的文件`。

所以报告口径是：官方任务元数据有效，本地 SkillOS workflow 与 SkillsBench task 映射已完成；官方 benchmark 分数仍需 Docker/Compose 或其他 BenchFlow sandbox。

## 6. Harness 执行验证

报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_HARNESS_P0_REPORT.md
```

结果：

- 15 个代表 generated Skills；
- 每类输入 3 个；
- positive pass rate：1.0；
- negative rejection rate：1.0；
- positive 全部进 S3；
- evidence path 指向 `artifacts\harness-runs\...`。

这部分回应了“Skill 要能执行，执行后才能打分”的要求。现在至少在 deterministic local contract 范围内，Skill 能从 S2 Draft 经过 harness verify-loop 进入 S3 Verified。

边界：这不是开放世界语义正确性，也不是官方 SkillsBench sandbox 结果。

## 7. Graph Nebula UI

截图报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\screenshots\SKILLOS_GRAPH_UI_SCREENSHOT_REPORT.md
```

新增能力：

- Nebula preset：小节点、弱标签、适合大图；
- Readable preset：讲解时显示更多标签；
- Debug preset：显示更明显边和节点；
- node size、edge width、edge opacity；
- label mode；
- edge label mode；
- charge strength、link distance、dense mode；
- 设置写入 localStorage。

验证数据规模：

- skill-only：193 nodes / 7 edges；
- provenance：630 nodes / 505 edges；
- version-impact：126 nodes / 0 edges。

截图覆盖 Nebula、Readable、Debug、selected subgraph、mobile layout。移动端的说明是：主标题不再和侧边栏重叠，但图谱本质仍是 admin workbench，大图在 390px 宽屏上仍然需要横向空间。

## 8. Readiness 和最终验证

最终 readiness 报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_DEMO_READINESS_FINAL_20260528.md
```

结果：15/15。

覆盖：

- backend health；
- frontend home；
- frontend proxy；
- skills list；
- graph view；
- evaluation dashboard；
- version repo status；
- LLM 配置状态；
- ingest parse；
- create candidate；
- version diff；
- version snapshot；
- new harness Draft；
- harness verify-loop；
- execution plan。

最终命令验证：

- 后端代表测试：135 passed；
- compileall：通过；
- 前端 lint：通过；
- 前端 build：通过，只有 Vite chunk warning；
- `git diff --check`：只有 LF/CRLF warnings；
- secret scan：无 key 命中。

## 9. 论文/项目支撑与实现程度

| 模块 | 参考 | 采用内容 | 实现程度 |
| --- | --- | --- | --- |
| A Repository | SkillX、SKILLFOUNDRY、HIN/GraphRAG | Skill 分层、Skill card、provenance、异构图/投影 | P0/P1 原型级实现 |
| B Governance | SKILLFOUNDRY、Git/version PR #11、软件版本治理 | lifecycle gate、business diff、snapshot、rollback | 当前 demo 可展示 |
| C Runtime | WebXSkill、SkillWeaver、Reflexion/ExpeL | execution、verifier、repair/retry、harness gate | deterministic local harness 完成，真实浏览器未完成 |
| D Self-management | Reflexion、ExpeL、SkillClaw | failure memory、repair proposal、human-in-loop | P0/P1，长期学习效果未实证 |
| E Frontend | Obsidian-style graph、Ctx2Skill evidence UI、agent skill UI | 五类导入、证据面板、Version Lab、Nebula Graph | 演示可用，移动端和超大图仍可优化 |

## 10. 相比旧 demo 多出的实际功能

旧 demo 主要能展示 Wiki、Graph、Execution、Evaluation 的基础流程。现在多出：

- 第五类输入 `past_skills`；
- Document 的 Ctx2Skill-lite evidence；
- Candidate Review 中的 SkillX layer 和 graph relation preview；
- 文件拖拽导入；
- harness verify-loop 页面和 API；
- S2 -> S3 的执行验证闭环；
- P0 125 条输入语料和评测报告；
- SkillsBench subset、任务检查和映射报告；
- business diff / editable version / Version Lab；
- graph nebula presets 和可调参数；
- final readiness 自动检查；
- 更明确的 limitation 报告。

## 11. 当前限制

最重要的限制：

- 官方 SkillsBench 分数缺 Docker/Compose；
- 真实浏览器/真实工具 harness 还没完整接入；
- Ctx2Skill 是 lite 复现，不是大规模 self-play；
- SkillX 是 schema/layering/graph-level 吸收，不是完整 SkillKB 自动构建；
- memory backend 重启会清空演示导入，需要 restore；
- dense `similar_to` 仍然需要阈值或聚合优化；
- 部分 API/doc/script 的语义 verifier 仍是 P0 合约级，不是人类专家级语义判断。

这些限制不阻塞组内 demo 或 demo-paper 占坑位，但如果要投稿更强 paper，需要补 SkillsBench official sandbox、真实执行环境、长期学习对比实验。

## 12. PR 建议描述

建议 PR body 写：

```text
Summary
- Git/version: business diff, editable Version Lab, S2 re-verification when implementation changes
- Evaluation: P0 125-fixture five-input corpus, isolated full workflow runner, readiness check
- Benchmark: SkillsBench sparse subset checks and mapping report; official sandbox blocked by missing Docker
- Runtime: representative harness positive/negative report across five input types
- Graph UI: Nebula/Readable/Debug presets, node/edge/label controls, screenshots
- Docs: final operation manual, update report, gap report

Verification
- Backend representative tests: 135 passed
- compileall: passed
- Frontend lint/build: passed
- Readiness: 15/15
- Input workflow: 125 created, overall 0.91
- Harness: positive 1.0, negative rejection 1.0
- Secret scan: no matches

Limitations
- No official SkillsBench generated-skill score until Docker/Compose is available
- Local harness is deterministic contract-level verification
- Memory backend demo data needs restore after restart
```
