# SkillOS Team Task Assignment

> **项目：** SkillOS — A Skill-Centric Operating System for Self-Evolving Agents
> **目标：** EMNLP 2025 Demo Track 提交
> **负责人：** 项目 Owner（负责 architecture.md 维护和最终 merge）

---

## 分支规范

每位成员在开始工作前：

```bash
# 1. 克隆仓库
git clone https://github.com/[owner]/skillos.git
cd skillos

# 2. 创建自己的开发分支（分支名见下表）
git checkout -b [your-branch-name]

# 3. 开发完成后推送
git push origin [your-branch-name]

# 4. 在 GitHub 上创建 Pull Request，目标分支为 main
```

**分支命名规则：** `[模块缩写]-dev`，例如 `repo-dev`、`frontend-dev`

---

## 成员分工

### Member A — Skill Repository Layer
**分支：** `repo-dev`
**文档：** [`docs/modules/01-skill-repository.md`](./modules/01-skill-repository.md)

**负责代码：**
```
skillos/skillos/
├── models/skill_model.py
├── models/graph_model.py
├── layers/skill_repository/
│   ├── repository.py
│   ├── graph_manager.py
│   └── indexing.py
├── api/memory_store.py
└── api/routes/
    ├── skills.py
    └── graph.py
```

**优化任务：**
- [ ] 提升检索质量（引入 embedding 向量检索）
- [ ] 图谱自动构建（Skill 创建时自动建立 `composes_with` 边）
- [ ] 版本历史完善（增加版本间 diff 自动计算）
- [ ] 图谱可视化数据优化（节点大小/颜色与统计数据联动）

**文档要求：** 修改代码后同步更新 `01-skill-repository.md` 中的接口说明和优化方向

---

### Member B — Skill Governance Layer
**分支：** `governance-dev`
**文档：** [`docs/modules/02-skill-governance.md`](./modules/02-skill-governance.md)

**负责代码：**
```
skillos/skillos/
├── layers/skill_governance/
│   ├── version_control.py
│   ├── reviewer.py
│   └── merger.py
├── layers/skill_construction/
│   ├── candidate_miner.py
│   ├── formalizer.py
│   └── validator.py
└── api/routes/lifecycle.py
```

**优化任务：**
- [ ] Diff 精细化（深入比较 interface.input_schema 字段变化）
- [ ] Breaking Change 自动检测
- [ ] 版本回滚端点（`POST /lifecycle/{id}/rollback/{version}`）
- [ ] 审核流程完善（规则引擎 + LLM 双重验证）
- [ ] Skill Construction LLM prompt 优化

**文档要求：** 修改代码后同步更新 `02-skill-governance.md` 中的工作流和 API 端点

---

### Member C — Skill Runtime & Task Execution Agents
**分支：** `runtime-dev`
**文档：** [`docs/modules/03-skill-runtime.md`](./modules/03-skill-runtime.md)

**负责代码：**
```
skillos/skillos/
├── layers/skill_runtime/
│   ├── planner.py
│   ├── composition.py
│   ├── executor.py
│   ├── state_tracker.py
│   ├── verifier.py
│   └── reflection.py
└── api/routes/execution.py
```

**优化任务：**
- [ ] Planner 质量提升（few-shot 示例 + 更好的 prompt）
- [ ] Reflection → 自动修复（接入 Maintainer Agent）
- [ ] 执行历史持久化（接入存储层）
- [ ] Verifier 增强（基于 postconditions 的规则验证）
- [ ] 并行执行优化（全局超时协调）

**文档要求：** 修改代码后同步更新 `03-skill-runtime.md` 中的执行流程和 Agent 接口

---

### Member D — Self-Management Agents
**分支：** `agents-dev`
**文档：** [`docs/modules/04-self-management-agents.md`](./modules/04-self-management-agents.md)

**负责代码：**
```
skillos/skillos/
├── layers/skill_management/
│   ├── builder.py
│   ├── auditor.py
│   ├── maintainer.py
│   ├── librarian.py
│   └── meta_controller.py
└── layers/feedback_evolution/
    ├── monitor.py
    ├── repair.py
    └── evolution_engine.py
```

**优化任务：**
- [ ] Builder LLM prompt 优化（few-shot 示例）
- [ ] Auditor 规则扩展（prompt_template 变量一致性检查）
- [ ] Skill 合并流程完整实现（`merge_redundant_skills`）
- [ ] 演化周期自动触发（定时任务）
- [ ] 健康监控 WebSocket 告警

**文档要求：** 修改代码后同步更新 `04-self-management-agents.md` 中的 Agent 接口和 Meta-Skill 列表

---

### Member E — Frontend, API & Experience Pipeline
**分支：** `frontend-dev`
**文档：** [`docs/modules/05-frontend-api-pipeline.md`](./modules/05-frontend-api-pipeline.md)

**负责代码：**
```
skillos-frontend/src/
├── pages/              # 所有前端页面
├── api/                # API 客户端
├── components/         # 公共组件
├── store/              # 状态管理
└── hooks/              # WebSocket 等

skillos/skillos/
├── api/
│   ├── main.py
│   ├── deps.py
│   ├── schemas.py
│   └── routes/ingest.py
└── layers/input_knowledge/
    └── pipeline.py
```

**优化任务：**
- [ ] SelfEvolutionDemo 增强（历史执行记录面板）
- [ ] SkillGraph 交互优化（点击展开子图、跳转 Wiki）
- [ ] KnowledgeImport 创建后显示 Skill 链接
- [ ] Dashboard 自动刷新
- [ ] Experience Pipeline LLM prompt 优化
- [ ] 前端错误处理统一化

**文档要求：** 修改代码后同步更新 `05-frontend-api-pipeline.md` 中的页面清单和 API 端点表

---

## 工作规范

### 提交规范

```
feat(module): 简短描述
fix(module): 简短描述
docs(module): 更新文档
refactor(module): 重构

示例：
feat(runtime): add few-shot examples to planner prompt
fix(governance): fix semver comparison in version history
docs(repository): update indexing API documentation
```

### PR 规范

PR 标题格式：`[模块] 功能描述`

PR 描述模板：
```markdown
## 改动内容
- 改了什么

## 测试
- 如何验证

## 文档更新
- 更新了 docs/modules/xx.md 的哪些部分
```

### 禁止事项

- ❌ **不要修改 `docs/architecture.md`**（由负责人统一维护）
- ❌ **不要直接 push 到 main 分支**
- ❌ **不要修改其他成员负责的核心文件**（如有交叉，先沟通）
- ❌ **不要提交 `.env` 文件或 API Key**

### 合并流程

```
Member 开发完成
    │
    ▼
创建 PR → 负责人 Review
    │
    ▼
Review 通过 → 负责人 Merge 到 main
    │
    ▼
负责人更新 architecture.md（如有必要）
```

---

## 环境配置

```bash
# 后端
cd skillos
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 启动（需要 API Key）
python -m skillos.api.main --api-key YOUR_KEY --port 8000

# 前端
cd skillos-frontend
npm install
npm run dev
```

**API Key 获取：** 联系项目负责人获取测试用 API Key（不要使用自己的 Key 提交代码）

---

## 时间节点

| 里程碑 | 目标日期 | 说明 |
|--------|----------|------|
| 各成员完成优化任务 | TBD | 各自分支开发完成 |
| PR 提交截止 | TBD | 所有 PR 提交，等待 Review |
| 负责人 Merge + 文档整合 | TBD | 统一 merge，更新 architecture.md |
| Demo 最终测试 | TBD | 端到端测试，准备 EMNLP 演示 |

---

*如有问题请联系项目负责人*
