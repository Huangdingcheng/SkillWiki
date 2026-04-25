# Module 02: Skill Governance Layer

**负责人分支：`governance-dev`**

---

## 职责概述

Skill Governance Layer 负责 Skill 的全生命周期治理，包括：
- Git 式版本控制（branch/commit/PR/review/merge）
- 状态机驱动的生命周期管理（S0-S7）
- Skill 构建与验证（从候选到草稿）
- 变更历史与 diff 视图

---

## 子模块

### 2.1 Version Control（`layers/skill_governance/version_control.py`）

实现 Git 式版本管理，每次 `new-version` 操作创建新的 Skill 实例（不可变版本）。

**版本号规则（Semantic Versioning）：**
- `patch`：1.0.0 → 1.0.1（bug 修复、小调整）
- `minor`：1.0.0 → 1.1.0（新功能、向后兼容）
- `major`：1.0.0 → 2.0.0（破坏性变更）

**变更记录（ChangeRecord）：**
```python
@dataclass
class ChangeRecord:
    record_id: str
    skill_id: str
    from_version: str
    to_version: str
    change_type: str        # "patch" / "minor" / "major"
    summary: str
    author: str
    created_at: datetime
    diff: Dict[str, Any]    # {field: {old: ..., new: ...}}
    is_breaking: bool
```

### 2.2 Lifecycle Management（`api/routes/lifecycle.py`）

状态转换的 API 层，封装了所有合法的状态迁移操作。

**合法转换路径：**
```
S0 → S1 (ingest)
S1 → S2 (review)
S2 → S3 (verify/audit)
S3 → S4 (release)
S4 → S5 (degrade, 自动触发)
S5 → S4 (repair)
S4/S5 → S6 (deprecate)
S6 → S7 (archive)
```

**Skill 模型上的状态方法：**
```python
class Skill:
    def transition_to(self, new_state: SkillState, reason: str = "")
    def record_execution(self, success: bool, latency_ms: float)
    # 自动降级：success_rate < 0.6 且 total_executions >= 10 → S5
```

### 2.3 Reviewer（`layers/skill_governance/reviewer.py`）

LLM 驱动的 Skill 审核，检查描述完整性、接口合理性、实现安全性。

### 2.4 Merger（`layers/skill_governance/merger.py`）

将功能重复的多个 Skill 合并为一个统一版本。

### 2.5 Skill Construction（`layers/skill_construction/`）

从候选经验构建正式 Skill：

| 文件 | 职责 |
|------|------|
| `candidate_miner.py` | 从经验单元中挖掘 Skill 候选 |
| `formalizer.py` | 将非正式描述规范化为标准 JSON Schema |
| `validator.py` | 验证 Skill 定义的完整性和合法性 |

---

## 工作流详解

### 新版本创建流程

```
POST /lifecycle/{id}/new-version  { bump: "patch" }
    │
    ▼
1. 获取当前 Skill（必须是 S4 Released）
2. 计算新版本号（semver bump）
3. 克隆 Skill，更新 version、state=S2 Draft
4. 记录 ChangeRecord（diff = 空，等待后续修改）
5. 写入 SkillWiki（新 skill_id）
6. 返回新版本 Skill
```

### Diff 计算流程

```
GET /lifecycle/{id}/diff
    │
    ▼
1. 获取该 Skill 的所有版本历史
2. 对相邻版本计算字段级 diff
3. _format_diff() 将 {field: {old, new}} 转换为
   [{field, type, old_lines, new_lines}] 格式
4. 返回完整变更历史列表
```

### 自动降级流程

```
每次 record_execution() 调用后：
    │
    ▼
计算 success_rate = successful / total
    │
    ├── success_rate < 0.6 AND total >= 10 AND state == S4
    │       → 自动 transition_to(S5, "自动降级：成功率过低")
    │
    └── 否则保持当前状态
```

---

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/v1/lifecycle/{id}/transition` | 手动状态转换 |
| `POST` | `/api/v1/lifecycle/{id}/release` | 发布（S2/S3 → S4） |
| `POST` | `/api/v1/lifecycle/{id}/deprecate` | 废弃（→ S6） |
| `POST` | `/api/v1/lifecycle/{id}/new-version` | 创建新版本 |
| `POST` | `/api/v1/lifecycle/{id}/review` | LLM 审核 |
| `POST` | `/api/v1/lifecycle/{id}/review-and-release` | 审核并发布 |
| `POST` | `/api/v1/lifecycle/{id}/record-execution` | 记录执行结果 |
| `GET` | `/api/v1/lifecycle/{id}/diff` | 获取变更历史与 diff |
| `GET` | `/api/v1/lifecycle/{id}/diff/versions` | 比较两个特定版本 |

---

## 关键文件

```
skillos/skillos/
├── layers/
│   ├── skill_governance/
│   │   ├── version_control.py  # 版本控制核心逻辑
│   │   ├── reviewer.py         # LLM 审核
│   │   └── merger.py           # Skill 合并
│   └── skill_construction/
│       ├── candidate_miner.py  # 候选挖掘
│       ├── formalizer.py       # Schema 规范化
│       └── validator.py        # 合法性验证
├── models/skill_model.py       # SkillState 枚举、transition_to()
└── api/routes/lifecycle.py     # 生命周期 API 路由
```

---

## 优化方向（Member B 任务）

1. **Diff 精细化**：当前 diff 只比较顶层字段，可深入比较 `interface.input_schema` 的具体字段变化
2. **Breaking Change 检测**：自动检测 major bump 是否真的有破坏性变更（如删除必填输入字段）
3. **审核流程完善**：`reviewer.py` 当前是 LLM 调用，可增加规则引擎（如检查 prompt_template 是否包含所有 input_schema 字段）
4. **版本回滚**：增加 `POST /lifecycle/{id}/rollback/{version}` 端点
5. **变更通知**：当 Skill 状态变更时，通过 WebSocket 广播事件（当前已有 ws.py 基础设施）
6. **Skill Construction 完善**：`candidate_miner.py` 和 `formalizer.py` 的 LLM prompt 可进一步优化

---

*更新此文档时请同步更新 `architecture.md` 中的 Governance Flow 部分（联系负责人）*
