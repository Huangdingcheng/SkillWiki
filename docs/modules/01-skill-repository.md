# Module 01: Skill Repository Layer

**负责人分支：`repo-dev`**

---

## 职责概述

Skill Repository Layer 是 SkillOS 的知识存储核心，负责：
- Skill 的持久化存储（CRUD）
- 语义检索与相似度搜索
- 异构知识图谱的构建与查询
- 版本历史管理

---

## 子模块

### 1.1 SkillWiki（`layers/skill_repository/repository.py`）

SkillWiki 是 Skill 的主存储，提供完整的 CRUD 接口。

**核心接口：**
```python
class SkillWikiManager:
    async def create(skill: Skill) -> Skill
    async def get(skill_id: str) -> Optional[Skill]
    async def get_many(ids: List[str]) -> Dict[str, Skill]
    async def update(skill_id: str, **kwargs) -> Skill
    async def delete(skill_id: str) -> bool
    async def list(state, skill_type, tags, limit, offset) -> List[Skill]
    async def search(query: str, limit: int) -> List[Skill]
    async def get_version_history(name: str) -> List[Skill]
    async def record_execution(skill_id, success, latency_ms)
```

**当前实现：** `api/memory_store.py` 中的 `MemoryWikiManager`（内存存储，demo 模式）

**生产实现：** `storage/postgres_db.py`（PostgreSQL 适配器，待接入）

### 1.2 Skill Graph（`layers/skill_repository/graph_manager.py`）

异构知识图谱，节点为 Skill，边为关系类型。

**边类型（EdgeType）：**
| 类型 | 含义 |
|------|------|
| `depends_on` | A 依赖 B 才能执行 |
| `composes_with` | A 与 B 组合使用 |
| `similar_to` | A 与 B 功能相似 |
| `evolved_from` | A 从 B 演化而来 |
| `conflicts_with` | A 与 B 存在冲突 |
| `replaces` | A 替代了 B |
| `specializes` | A 是 B 的特化版本 |
| `generalizes` | A 是 B 的泛化版本 |

**核心接口：**
```python
class SkillGraphManager:
    async def add_node(skill: Skill)
    async def add_edge(source_id, target_id, edge_type, weight)
    async def get_subgraph(skill_id, depth) -> GraphData
    async def get_dependencies(skill_id) -> List[str]
    async def get_execution_order(skill_ids) -> List[str]  # 拓扑排序
    async def get_stats() -> Dict
```

**当前实现：** `api/memory_store.py` 中的 `MemoryGraphManager`

### 1.3 Indexing & Search（`layers/skill_repository/indexing.py`）

基于 BM25 + 语义相似度的混合检索。

**SearchQuery：**
```python
@dataclass
class SearchQuery:
    text: str
    tags: List[str] = []
    skill_type: Optional[SkillType] = None
    state: Optional[SkillState] = None
    max_results: int = 10
```

**SearchResult：**
```python
@dataclass
class SearchResult:
    skill: Skill
    score: float          # 综合相关度分数 [0, 1]
    match_reasons: List[str]  # 匹配原因列表
    match_reason: str     # 主要匹配原因（逗号拼接）
```

---

## 数据模型

### Skill（`models/skill_model.py`）

```python
class Skill(BaseModel):
    skill_id: str                    # UUID，主键
    name: str                        # 唯一名称
    description: str
    skill_type: SkillType            # atomic / functional / strategic
    state: SkillState                # S0-S7
    version: str                     # semver，如 "1.0.0"
    tags: List[str]
    granularity_level: int           # 1=Atomic, 2=Functional, 3=Strategic
    interface: SkillInterface        # 输入/输出 Schema + 前后置条件
    implementation: SkillImplementation  # 实现方式
    metrics: SkillMetrics            # 执行统计
    provenance: SkillProvenance      # 来源信息
    meta_category: Optional[MetaSkillCategory]  # L3 专用分类
    created_at: datetime
    updated_at: datetime
```

### SkillInterface
```python
class SkillInterface(BaseModel):
    input_schema: Dict               # JSON Schema
    output_schema: Dict              # JSON Schema
    preconditions: List[str]         # 前置条件（自然语言）
    postconditions: List[str]        # 后置条件（自然语言）
```

### SkillImplementation
```python
class SkillImplementation(BaseModel):
    language: str                    # "python" / "javascript" 等
    code: Optional[str]              # 代码实现
    prompt_template: Optional[str]   # LLM prompt 模板
    sub_skill_ids: List[str]         # 子 Skill ID 列表（composite 类型）
    tool_calls: List[str]            # 工具调用列表
```

### SkillGraph（`models/graph_model.py`）
```python
class SkillGraph(BaseModel):
    nodes: Dict[str, SkillNode]
    edges: List[SkillEdge]
```

---

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/v1/skills` | 列出 Skill（支持 state/type/tags 过滤） |
| `POST` | `/api/v1/skills` | 创建 Skill |
| `GET` | `/api/v1/skills/{id}` | 获取 Skill 摘要 |
| `GET` | `/api/v1/skills/{id}/full` | 获取 Skill 完整信息（含 implementation） |
| `PATCH` | `/api/v1/skills/{id}` | 更新 Skill |
| `DELETE` | `/api/v1/skills/{id}` | 删除 Skill |
| `POST` | `/api/v1/skills/search` | 语义搜索 |
| `GET` | `/api/v1/skills/{id}/versions` | 版本历史 |
| `GET` | `/api/v1/skills/evolution-stats` | 演化统计指标 |
| `GET` | `/api/v1/graph` | 完整图谱数据 |
| `POST` | `/api/v1/graph/subgraph` | 子图查询 |
| `POST` | `/api/v1/graph/edges` | 添加关系边 |
| `GET` | `/api/v1/graph/{id}/dependencies` | 依赖链 |
| `GET` | `/api/v1/graph/{id}/execution-order` | 执行顺序（拓扑排序） |
| `GET` | `/api/v1/graph/stats/overview` | 图谱统计 |

---

## 关键文件

```
skillos/skillos/
├── models/
│   ├── skill_model.py          # Skill 核心数据模型
│   └── graph_model.py          # 图谱数据模型
├── layers/skill_repository/
│   ├── repository.py           # SkillWikiManager 接口定义
│   ├── graph_manager.py        # SkillGraphManager
│   └── indexing.py             # 检索引擎
├── api/
│   ├── memory_store.py         # 内存实现（demo 用）
│   └── routes/
│       ├── skills.py           # Skill CRUD 路由
│       └── graph.py            # 图谱路由
└── storage/
    ├── postgres_db.py          # PostgreSQL 适配器（生产用）
    └── neo4j_db.py             # Neo4j 适配器（生产用）
```

---

## 优化方向（Member A 任务）

1. **检索质量提升**：当前 `indexing.py` 使用简单关键词匹配，可引入 embedding 向量检索（如 sentence-transformers）
2. **图谱自动构建**：当前边需要手动添加，可在 Skill 创建时根据 `sub_skill_ids` 自动建立 `composes_with` 边
3. **持久化接入**：将 `MemoryWikiManager` 替换为 `PostgresWikiManager`（`storage/postgres_db.py` 已有框架）
4. **版本历史完善**：`get_version_history` 当前按 name 查询所有版本，可增加版本间 diff 的自动计算
5. **图谱可视化数据优化**：前端 SkillGraph 页面的节点大小/颜色逻辑可与后端统计数据更紧密联动

---

*更新此文档时请同步更新 `architecture.md` 中的接口关系表（联系负责人）*
