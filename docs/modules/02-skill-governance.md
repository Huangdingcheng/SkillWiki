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

第一阶段新增的 Git 包装层，职责是安全调用 Git，而不是实现 Git：

- 检查目标目录是否是 Git 仓库。
- 获取当前分支。
- 获取当前 HEAD commit。
- 获取某个 Skill 快照文件的 commit history。
- 获取两个 commit 之间的 Git diff。
- 在测试用临时仓库中创建 commit。

这一层暂时不直接改 lifecycle API，也不自动写入真实 Skill 快照。后续阶段会把 Skill JSON 快照接到这一层。

### 2.3 Lifecycle Management

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

第一阶段不修改这些接口的请求和响应字段。

### 2.4 Reviewer / Merger

`layers/skill_governance/reviewer.py`

`layers/skill_governance/merger.py`

现有 Reviewer 负责 Skill 质量审核，Merger 负责相似 Skill 合并和大 Skill 拆分。B 后续阶段会让这些治理动作也产生 Git-backed 版本记录。

---

## B 任务阶段规划

### 阶段一：Git-backed Version Adapter 底座

目标：先把 Git 调用能力放进 governance 层，保证后续不用自研 Git-like 版本系统。

完成内容：

- 新增 `GitVersionStore`。
- 新增临时 Git 仓库测试。
- 保持 REST 接口不变。
- 更新本模块文档，明确“Git 外壳”路线。

### 阶段二：Skill 快照与领域级 Diff

目标：把 Skill 序列化为稳定 JSON 快照，并用 Git diff + Skill 字段级解释生成更可读的版本差异。

重点：

- Skill snapshot 路径规则。
- JSON 稳定排序。
- interface / implementation / prompt_template 的字段级 diff。
- breaking change 检测。

### 阶段三：Branch / Review 工作流封装

目标：把 Skill 修改映射为 Git branch 和 review 流程。

重点：

- Skill 修改分支。
- commit message 规范。
- review 状态与 lifecycle 状态对应。
- 后续可接 GitHub PR，但不自造 PR 系统。

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
