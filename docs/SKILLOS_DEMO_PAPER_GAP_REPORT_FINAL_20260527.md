# SkillOS Demo Paper Gap Report

最后更新：2026-05-28
结论等级：可以做组内展示和 demo-paper 占坑位；距离更强论文投稿仍差官方 benchmark、真实执行环境和长期学习实证。

## 1. 当前完成度判断

当前 SkillOS 已经达到“可展示、可复跑、可被组长按证据检查”的 demo-paper prototype 水平。

证据链包括：

- 一键启动；
- 前后端连通；
- 五类输入 P0 125 条语料；
- 完整 input-to-skill workflow 评测；
- SkillsBench subset task check 和映射；
- harness 正负例；
- graph nebula 截图；
- version business diff / snapshot；
- readiness 15/15；
- 后端测试、前端 lint/build、secret scan。

最适合的论文口径是：

```text
SkillOS is a skill-centric operating system prototype for self-evolving agents.
It converts multi-source experiences into governed, graph-grounded, versioned,
and execution-verified Skill candidates.
```

## 2. Demo paper 可以讲的内容

可以明确讲：

1. SkillOS 有五类输入：
   - trajectory；
   - document；
   - api_doc；
   - script；
   - past_skills。

2. 所有输入默认只产生 S1 Candidate，不绕过治理流程。

3. Document 使用 Ctx2Skill-inspired 方法：
   - context pack；
   - challenge；
   - rubric；
   - judge；
   - proposer；
   - replay-lite evidence。

4. Past Skills 使用 SkillX-style 分层：
   - atomic；
   - functional；
   - strategic。

5. Graph 有同构和异构两条线：
   - Skill-only graph；
   - heterogeneous provenance graph；
   - meta-path projection；
   - relation strength。

6. Runtime 有执行验证闭环：
   - S2 Draft；
   - local harness execution；
   - deterministic postcondition verifier；
   - repair/retry；
   - S3 Verified gate。

7. Version/Git 有治理证据：
   - business diff；
   - raw diff；
   - snapshot；
   - rollback；
   - Version Lab。

8. P0 实证规模：
   - 125 fixtures；
   - 125 candidates；
   - full workflow overall score 0.91；
   - representative harness positive 1.0、negative rejection 1.0；
   - SkillsBench 5 个 task metadata checks 通过，40 个映射。

## 3. 不能过度讲的内容

不能讲：

- 完整复现 Ctx2Skill；
- 完整复现 SkillX；
- 完整 WebXSkill benchmark；
- 官方 SkillsBench generated-skill 分数；
- 长期 self-evolution 已经证明提升性能；
- memory backend 数据是永久数据库；
- local deterministic harness 等价于真实开放环境 execution。

推荐表达：

```text
We implement a Ctx2Skill-inspired lite extraction layer rather than full large-scale self-play training.
```

```text
We validate generated Skills with local deterministic harness contracts and keep official SkillsBench sandbox evaluation as a next step once Docker/Compose is available.
```

## 4. 各模块 gap

### A. Skill Repository

已完成：

- Skill schema 包含 interface、implementation、evaluation、provenance；
- Past Skills 可转 SkillOS schema；
- SkillX-style atomic/functional/strategic；
- depends_on、composes_with、evolved_from、similar_to；
- heterogeneous evidence chain。

主要 gap：

- 尚未做真正强 semantic dedup / merge；
- `similar_to` 仍需要阈值、聚合或人工确认；
- 大规模检索质量仍以本地 demo eval 为主。

是否阻塞 demo paper：不阻塞。可以作为 limitation。

### B. Governance

已完成：

- S1/S2/S3/S4 lifecycle；
- audit；
- business diff；
- snapshot；
- rollback；
- Version Lab；
- implementation/interface 改动后走 S2 re-verification。

主要 gap：

- S3 -> S4 仍需更强 reviewer；
- Git backend 的大规模并发/冲突处理未系统验证；
- PR #11 的完整 merge workflow 没有吸收，只吸收了可取 version UX。

是否阻塞 demo paper：不阻塞。当前足够展示治理链。

### C. Runtime

已完成：

- execution plan；
- single skill execution；
- deterministic verifier specs；
- local harness；
- Codex CLI harness adapter；
- S2 -> S3 verify-loop；
- 正负例验证。

主要 gap：

- 真实浏览器 Playwright/WebXSkill harness 未完成；
- Codex CLI harness 主要是 adapter/unit test，现场验证仍以 local harness 为主；
- 真实工具调用的安全沙箱还需要加强。

是否阻塞 demo paper：不阻塞 demo，占坑位足够；阻塞更强系统论文。

### D. Self-Management

已完成：

- failure -> repair -> retry；
- repaired version；
- evolved_from graph edge；
- human-in-loop proposal 思路保留。

主要 gap：

- 没有长期跨任务 memory 提升曲线；
- 没有大规模 ablation；
- 自动 repair 的语义深度仍有限。

是否阻塞 demo paper：不阻塞，但要明确只是 P0/P1 self-evolution prototype。

### E. Frontend

已完成：

- English UI；
- five input tabs；
- drag-and-drop import；
- Ctx2Skill Evidence；
- SkillX Layering；
- Graph Relation Preview；
- Harness Verification；
- Version Lab；
- Graph Nebula settings。

主要 gap：

- 390px mobile 下大图仍需要横向空间；
- dense `similar_to` 可读性还可优化；
- 超大图性能没有系统 benchmark。

是否阻塞 demo paper：不阻塞。当前已满足组长“点小一点、星云图”的要求，并进一步做了可调设置。

## 5. SkillsBench gap

已完成：

- sparse subset；
- `uv sync --locked`；
- 5 个 task metadata check；
- 40 个 fixture-task 映射；
- official oracle attempted。

阻塞：

- Docker/Compose 缺失，official oracle/no_skill/generated_skill sandbox 不能完整跑。

当前可讲：

- 官方任务元数据有效；
- SkillOS 生成 Skill 已映射到任务；
- 本地完整 workflow 证据成立。

当前不能讲：

- 官方 SkillsBench pass rate；
- generated_skill 相比 no_skill 的官方 delta。

下一步：

1. 安装 Docker Desktop；
2. 重新跑 oracle；
3. 跑 no_skill；
4. 跑 generated_skill adapter；
5. 报告 delta。

## 6. Demo paper 距离估计

按“能投 demo paper / short demo 占坑位”标准：

- 系统完整度：约 85%-90%；
- 演示可复现度：约 85%；
- 评测严谨度：约 70%-75%；
- 论文 claim 安全度：约 80%；
- 强 benchmark 证据：约 55%-60%。

如果目标是组内展示和 demo-paper 占坑位：基本够了。

如果目标是更强 conference demo 或 workshop paper：还需要 1-2 轮：

- Docker/SkillsBench official scores；
- 真实 browser/tool harness；
- P1 250 fixtures；
- semantic verifier；
- 消融对比；
- 更精炼的系统架构图和 demo video。

## 7. 最推荐的下一轮工作

优先级最高：

1. 安装 Docker 并跑 official SkillsBench subset。
2. 做最小 generated_skill adapter，与 no_skill 对比。
3. 加 Playwright/browser harness，把 web trajectory 的验证从 fake-web 推到真实浏览器。
4. 对 `similar_to` 做 threshold/grouping。
5. 把 P0 125 扩到 P1 250，但只在 P0 全绿后做。

优先级中等：

- 增强 Document semantic verifier；
- 增强 API Doc 参数完整性 verifier；
- 增强 Script side-effect sandbox；
- 让 Codex CLI harness 在本机真实跑一次；
- 增加 Dashboard 中的 P0 eval summary。

## 8. 最终可交付证据索引

P0 corpus：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527
```

Input workflow：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_INPUT_TO_SKILL_P0_FULL_WORKFLOW_REPORT.md
```

SkillsBench：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_SKILLSBENCH_P0_REPORT.md
```

Harness：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_HARNESS_P0_REPORT.md
```

Graph screenshots：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\screenshots
```

Readiness：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_DEMO_READINESS_FINAL_20260528.md
```

Final operation manual：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\demo-paper-roadmap-20260509\SKILLOS_GROUP_OPERATION_MANUAL_FINAL_20260527.md
```

Final update report：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\demo-paper-roadmap-20260509\SKILLOS_UPDATE_REPORT_SINCE_LAST_PR_20260527.md
```

Final gap report：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\demo-paper-roadmap-20260509\SKILLOS_DEMO_PAPER_GAP_REPORT_FINAL_20260527.md
```
