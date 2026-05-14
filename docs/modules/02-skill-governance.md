# Module 02: Skill Governance Layer

**负责分支：`governance-dev`**

---

## 职责概览

Skill Governance Layer 负责 Skill 的生命周期治理和版本治理。当前路线不是重新实现一套 Git-like 系统，而是把真实 Git 作为底层版本事实来源，再由 SkillOS 在上面增加 Skill 语义。

换句话说：

- Git 负责 branch、commit、diff、history、tag 等成熟版本能力。
- SkillOS 负责解释“某个 commit 对应哪个 Skill 版本”“某个 diff 是否是 breaking change”“某次修改是否需要 review”“某次恢复来自哪个历史版本”。

P0 demo-paper 口径下，具体治理规则以 `docs/modules/00-skill-governance-policy.md` 为准：D/C 可以生成维护证据和 proposal，B 负责 snapshot / structured diff / review bundle / release / restore commit，E 负责把 review 决策交给人确认，A 负责保存被接受后的 Skill 和图谱证据。

当前 B 任务已经形成五层能力：

- Git-backed Version Adapter
- Skill Snapshot / Domain Diff / Breaking Change 检测
- Branch / Review 工作流封装
- Release Tag / Restore Commit 回滚
- 最小 REST API 联调入口

---

## 当前子模块

### 2.1 Version Control

`layers/skill_governance/version_control.py`

保留原有语义化版本控制能力：

- 记录 `ChangeRecord`。
- 计算两个 Skill 对象之间的字段级 diff。
- 根据 diff 建议 `major` / `minor` / `patch`。
- 创建新版本时让 Skill 回到 Draft 状态，等待后续审核。

### 2.2 Git Version Store

`layers/skill_governance/git_version_store.py`

Git 包装层只负责安全调用 Git，不重新实现 Git：

- 检查目标目录是否是 Git 仓库。
- 获取当前分支和 HEAD commit。
- 创建、检查、切换本地分支。
- 获取指定文件的 commit history。
- 获取两个 commit 之间的 Git diff。
- 在测试用临时仓库中创建 commit。
- 检查和创建本地 lightweight tag。
- 从 commit、branch 或 tag 读取指定文件内容。

所有 Git 命令都通过 `subprocess` 调用，并带有超时、错误捕获和清晰异常信息。

### 2.3 Skill Snapshot / Domain Diff

`layers/skill_governance/skill_snapshot.py`

Skill 语义层快照和 diff 能力：

- 将 `Skill` 稳定序列化为 JSON 快照。
- 快照路径固定为 `skills/<skill_id>/<version>.json`。
- 快照排除 `metrics` 和时间戳字段，避免运行时噪声污染版本 diff。
- 支持写入临时或指定 Git 仓库，并复用 `GitVersionStore` 生成 commit。
- 支持字段级 diff 和 breaking change 检测。

当前 breaking change 规则：

- 删除 input schema property。
- 给 input schema 新增 required 字段。
- 新增、删除或修改 output schema property。
- 修改已有 schema property 的 `type`。
- 删除或清空已有 `implementation.prompt_template` / `implementation.code`。
- 删除 executable composition 中已有 `implementation.sub_skill_ids`。

当前 structured diff 会为前端和 review workflow 返回语义分类：

- `schema_change`
- `postcondition_change`
- `implementation_change`
- `dependency_change`
- `provenance_change`
- `metadata_change`

### 2.4 Skill Change Workflow

`layers/skill_governance/skill_change_workflow.py`

可审查变更工作流：

- 根据新 Skill 版本生成固定分支名：`skill/<skill_name>/<skill_id_prefix>-v<version>`。
- 在临时或指定 Git 仓库中创建变更分支。
- 写入新版本 Skill snapshot。
- 生成固定提交信息：`skill(<skill_name>): propose v<version>`。
- 返回 review bundle，包括分支名、base/head commit、snapshot 路径、commit message、diff、breaking 标记和建议 review 状态。

当前建议 review 状态：

- `no_changes`：新旧快照没有变化，不创建分支和 commit。
- `review_required`：存在非 breaking diff。
- `breaking_review_required`：存在 breaking change。

这一层暂时不调用 LLM Reviewer，也不创建 GitHub PR。后续可以把 review bundle 接入 API、GitHub PR 或 E 前端展示。

### 2.5 Skill Release / Rollback

`layers/skill_governance/skill_release.py`

Release tag 和 restore commit 回滚能力：

- Skill release tag 规则：`skill/<skill_name>/<skill_id_prefix>/v<version>`。
- `release_skill_snapshot()` 会确认目标 ref 下存在对应 Skill snapshot，然后创建本地 lightweight tag。
- `read_skill_snapshot_at_ref()` 可以从 commit、branch 或 tag 读取历史 snapshot JSON。
- `restore_skill_snapshot()` 会从历史 ref 读取 snapshot，把内容写回当前工作树，并创建一个新的 restore commit。

回滚策略非常关键：这里的回滚不是 `git reset`，也不是破坏性覆盖历史，而是新增恢复提交。这样 Git 历史会完整保留，review 时也能看见“从哪个 tag/commit 恢复到了当前内容”。

当前 restore commit 信息固定为：

```text
skill(<name>): restore from <ref>
```

### 2.6 Lifecycle API Integration

`api/routes/lifecycle.py`

第五阶段新增最小 Git-backed 联调入口，全部放在现有 `/api/v1/lifecycle` 下：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `POST` | `/api/v1/lifecycle/{id}/snapshot` | 将当前 Skill 写成 snapshot 并创建 commit |
| `GET` | `/api/v1/lifecycle/{id}/snapshot/history` | 读取当前 Skill snapshot 的 Git commit history |
| `GET` | `/api/v1/lifecycle/{id}/snapshot/diff` | 返回 raw Git diff、结构化 diff 和 breaking 标记 |
| `POST` | `/api/v1/lifecycle/{id}/release-tag` | 为指定 ref 下的 Skill snapshot 创建本地 lightweight tag |
| `POST` | `/api/v1/lifecycle/{id}/rollback` | 从历史 ref 读取 snapshot 并创建 restore commit |

Git 仓库路径策略：

- 优先读取环境变量 `SKILLOS_GOVERNANCE_REPO`。
- 未设置时默认使用当前项目 Git 仓库根目录。
- 测试使用临时 Git 仓库，不污染真实项目仓库。

重要边界：

- `rollback` 只恢复 Git snapshot 文件，不直接改 Wiki 中的 live Skill 对象。
- 本阶段不 push tag，不创建 GitHub Release，不自动创建 GitHub PR。
- 现有 lifecycle API 字段不变。

### 2.7 Existing Lifecycle Management

`api/routes/lifecycle.py`

保留现有生命周期接口：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `POST` | `/api/v1/lifecycle/{id}/transition` | 手动状态流转 |
| `POST` | `/api/v1/lifecycle/{id}/release` | 发布 Skill |
| `POST` | `/api/v1/lifecycle/{id}/deprecate` | 废弃 Skill |
| `POST` | `/api/v1/lifecycle/{id}/new-version` | 创建新版本 |
| `POST` | `/api/v1/lifecycle/{id}/review` | LLM 审核 |
| `POST` | `/api/v1/lifecycle/{id}/review-and-release` | 审核并发布 |
| `POST` | `/api/v1/lifecycle/{id}/record-execution` | 记录执行结果 |
| `GET` | `/api/v1/lifecycle/{id}/diff` | 获取变更历史 diff |
| `GET` | `/api/v1/lifecycle/{id}/diff/versions` | 比较两个版本 |

### 2.8 Reviewer / Merger

`layers/skill_governance/reviewer.py`

`layers/skill_governance/merger.py`

现有 Reviewer 负责 Skill 质量审核，Merger 负责相似 Skill 合并和大 Skill 拆分。后续可以让这些治理动作也写入 Git-backed snapshot 和 review workflow，形成可追踪版本记录。

---

## B 任务阶段状态

### 阶段一：Git-backed Version Adapter 底座

已完成：

- 新增 `GitVersionStore`。
- 新增临时 Git 仓库测试。
- 保持 REST 接口不变。
- 明确“Git 外壳”路线。

### 阶段二：Skill 快照与领域级 Diff

已完成：

- 新增 Skill snapshot 稳定序列化。
- 约定 snapshot 路径规则。
- 排除运行时噪声字段。
- 新增领域级 diff。
- 新增 breaking change 检测。
- 新增临时 Git 仓库快照提交测试。

### 阶段三：Branch / Review 工作流封装

已完成：

- 新增本地分支检查、创建、切换封装。
- 新增 `SkillChangeReviewBundle`。
- 新增 `propose_skill_change()`。
- 支持 `no_changes` / `review_required` / `breaking_review_required` 三种规则型预审状态。
- 新增临时 Git 仓库分支和 review bundle 测试。

### 阶段四：Release Tag 与 Restore Commit 回滚

已完成：

- 新增 tag 检查和创建能力。
- 新增 ref 下文件读取能力。
- 新增 release record 和 rollback record。
- 新增 `release_skill_snapshot()`。
- 新增 `read_skill_snapshot_at_ref()`。
- 新增 `restore_skill_snapshot()`。
- 新增 release / rollback 临时 Git 仓库测试。

### 阶段五：最小 API 联调与 PR 交付

已完成：

- 新增 Git-backed snapshot commit API。
- 新增 snapshot history API。
- 新增 snapshot diff API。
- 新增 release-tag API。
- 新增 restore commit rollback API。
- 新增 lifecycle API 测试，使用临时 Git 仓库验证，不污染真实项目仓库。

后续联调重点：

- E 前端可以接入 `/versions` 页面展示 Git-backed history / diff。
- D 的 repair / merge / split 后续可以调用 snapshot API 形成可追踪版本记录。
- 由组长决定是否把这些新 API 同步到飞书锁定接口文档。

---

## 当前边界

- 不修改 `docs/interfaces.md` 和 `docs/architecture.md`。
- 不新增依赖。
- 不改现有 lifecycle API 字段。
- 不碰 E 的 `frontend-dev` PR。
- 不碰 D 的 `agents-dev` PR。
- 不自动合并 PR。
- 不 push release tag。
- 不使用 `git reset --hard`、`git checkout -- <path>`、`git clean` 等破坏性回滚操作。
## 2.9 B-P1 Paper-Grounded Governance Extensions

This B-P1 slice uses one minimal method per target effect:

- Version impact analysis uses the HIN Survey meta-path idea, restricted to a deterministic projection: `changed Skill <- depends_on/composes_with - impacted Skill`. SkillX provides the layered interpretation: an atomic Skill change can impact functional or strategic Skills that depend on or compose it.
- Git performance and safety uses local Git engineering practice: path-scoped commits, batch history reads, a repo-local governance lock, and refusal to commit when unrelated paths are already staged.
- Remote readiness is read-only. SkillOS reports local branch, dirty paths, upstream, ahead, and behind. It never pushes or creates remote PRs in this B-P1 implementation.

The lifecycle snapshot diff response now includes `impacted_skills` only when a structured diff is breaking. Each impact entry records `method="hin_meta_path_projection"` and `paper_basis` so the UI can explain that the result is a deterministic paper-inspired graph traversal, not a causal proof.

The maintenance review-bundle endpoint also returns `impacted_skills` for breaking proposed patches. The endpoint creates the proposal commit on a review branch and then returns to the original branch, so a long-running API process does not accidentally continue work on a proposal branch.

The governance repository status endpoint is:

```text
GET /api/v1/lifecycle/repository/status
```

It returns local Git state only: branch, HEAD commit, dirty flag, staged paths, unstaged paths, untracked paths, upstream, ahead, and behind.
