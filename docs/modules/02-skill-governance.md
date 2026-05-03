# Module 02: Skill Governance Layer

**负责分支：`governance-dev`**

---

## 职责概览

Skill Governance Layer 负责 Skill 的生命周期治理与版本治理，包括：

- Skill 生命周期状态流转：S0-S7。
- Skill 审核、发布、废弃、修复后的治理记录。
- Skill 版本历史、diff、breaking change 判断。
- 基于 Git 的 branch / commit / history / diff / rollback / PR 工作流封装。

当前方向不是重新实现一个 Git-like 系统，而是把 Git 作为底层版本事实来源。SkillOS 只在 Git 之上增加 Skill 语义，例如“某次 commit 对应哪个 Skill 版本”“某个 diff 是否是 breaking change”“某次修复是否需要 review”。

---

## 当前子模块

### 2.1 Version Control

`layers/skill_governance/version_control.py`

保留现有语义化版本控制能力：

- 记录 `ChangeRecord`。
- 计算两个 Skill 对象之间的字段级 diff。
- 根据 diff 建议 `major` / `minor` / `patch`。
- 创建新版本时把 Skill 回到 Draft 状态，等待后续审核。

### 2.2 Git Version Store

`layers/skill_governance/git_version_store.py`

Git 包装层，职责是安全调用 Git，而不是实现 Git：

- 检查目标目录是否是 Git 仓库。
- 获取当前分支和 HEAD commit。
- 创建、检查、切换本地分支。
- 获取某个 Skill 快照文件的 commit history。
- 获取两个 commit 之间的 Git diff。
- 在测试用临时仓库中创建 commit。

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
- 删除 output schema property。
- 修改已有 schema property 的 `type`。
- 删除或清空已有 `implementation.prompt_template` / `implementation.code`。

### 2.4 Skill Change Workflow

`layers/skill_governance/skill_change_workflow.py`

第三阶段新增的可审查变更工作流：

- 根据新 Skill 版本生成固定分支名：`skill/<skill_name>/<skill_id_prefix>-v<version>`。
- 在临时或指定 Git 仓库中创建变更分支。
- 写入新版本 Skill snapshot。
- 生成固定提交信息：`skill(<skill_name>): propose v<version>`。
- 返回 review bundle：分支名、base/head commit、snapshot 路径、commit message、diff、breaking 标记、建议 review 状态。

当前建议 review 状态：

- `no_changes`：新旧快照没有变化，不创建分支和 commit。
- `review_required`：存在非 breaking diff。
- `breaking_review_required`：存在 breaking change。

这一层暂时不调用 LLM Reviewer，也不创建 GitHub PR。后续阶段再把 review bundle 接入 API、GitHub PR 或 E 前端展示。

### 2.5 Lifecycle Management

`api/routes/lifecycle.py`

封装现有生命周期接口：

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

前三阶段均不修改这些接口的请求和响应字段。

### 2.6 Reviewer / Merger

`layers/skill_governance/reviewer.py`

`layers/skill_governance/merger.py`

现有 Reviewer 负责 Skill 质量审核，Merger 负责相似 Skill 合并和大 Skill 拆分。B 后续阶段会让这些治理动作也产生 Git-backed 版本记录。

---

## B 任务阶段规划

### 阶段一：Git-backed Version Adapter 底座

目标：先把 Git 调用能力放进 governance 层，保证后续不用自研 Git-like 版本系统。

已完成：

- 新增 `GitVersionStore`。
- 新增临时 Git 仓库测试。
- 保持 REST 接口不变。
- 更新本模块文档，明确“Git 外壳”路线。

### 阶段二：Skill 快照与领域级 Diff

目标：把 Skill 序列化为稳定 JSON 快照，并用 Git diff + Skill 字段级解释生成更可读的版本差异。

已完成：

- 新增 Skill snapshot 稳定序列化。
- 约定 snapshot 路径规则。
- 排除运行时噪声字段。
- 新增领域级 diff。
- 新增 breaking change 检测。
- 新增临时 Git 仓库快照提交测试。

### 阶段三：Branch / Review 工作流封装

目标：把 Skill 修改映射为 Git branch、snapshot commit 和 review bundle。

已完成：

- 新增本地分支检查、创建、切换封装。
- 新增 `SkillChangeReviewBundle`。
- 新增 `propose_skill_change()`。
- 支持 `no_changes` / `review_required` / `breaking_review_required` 三种规则型预审状态。
- 新增临时 Git 仓库分支和 review bundle 测试。

### 阶段四：Rollback / Tag / Release

目标：支持按 commit 或版本 tag 回滚 Skill。

重点：

- version tag 规则。
- rollback API 设计。
- rollback 后的 Skill 状态与审计记录。

### 阶段五：联调与交付

目标：与 E 前端和 D 自管理 Agent 联调。

重点：

- E 展示 Skill 版本历史和 diff。
- D 的 repair / merge / split 产生可追踪版本记录。
- 中文 PR 说明、交付文档、组长 review。

---

## 当前边界

- 不修改 `docs/interfaces.md` 和 `docs/architecture.md`。
- 不新增依赖。
- 不改现有 lifecycle API 字段。
- 不碰 E 的 `frontend-dev` PR。
- 不碰 D 的 `agents-dev` PR。
- 不自动创建 GitHub PR。
