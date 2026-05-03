# Module 01: Skill Repository Layer

负责人分支：`repo-dev`

## 职责概览

Skill Repository Layer 是 SkillOS 的 Skill 存储与关系底座，主要负责：

- SkillWiki：Skill 的创建、读取、更新、删除、版本列表和执行统计。
- Search：按自然语言、标签、类型、状态、领域和成功率检索 Skill。
- SkillGraph：维护 Skill 之间的依赖、组合、相似、演化等关系。
- Wiki / Graph API：为 C 执行层、D 自管理 Agent、E 前端展示、B 版本治理提供稳定数据入口。

当前第一阶段仍以 demo 可跑为优先，使用内存实现，不接 PostgreSQL / Neo4j。

## 当前实现

### SkillWiki

当前 demo 实现位于 `skillos/api/memory_store.py` 的 `MemoryWikiManager`，对齐生产版 `SkillWikiManager` 的核心行为：

```python
async def create(skill: Skill) -> Skill
async def get(skill_id: str) -> Optional[Skill]
async def get_by_name(name: str, version: Optional[str] = None) -> Optional[Skill]
async def get_many(skill_ids: List[str]) -> Dict[str, Optional[Skill]]
async def list(skill_type=None, state=None, tags=None, domain=None, name_like=None, limit=100, offset=0) -> List[Skill]
async def update(skill_id: str, **kwargs) -> Optional[Skill]
async def delete(skill_id: str) -> bool
async def get_version_history(name: str) -> List[Skill]
async def record_execution(skill_id: str, success: bool, latency_ms: float) -> None
async def get_overview_stats() -> Dict[str, Any]
```

第一阶段修正了 API 路由直接访问 `wiki.db` / `wiki.cache` 的问题。`PATCH /skills/{id}` 和 `DELETE /skills/{id}` 现在统一通过 `app.wiki.update()` / `app.wiki.delete()`，因此内存 demo 模式和未来持久化实现可以共享同一层接口。

### Search

检索契约仍以飞书 `SearchQuery` / `SearchResult` 为准：

```python
@dataclass
class SearchQuery:
    text: str = ""
    tags: List[str] = field(default_factory=list)
    skill_type: Optional[SkillType] = None
    domain: Optional[str] = None
    state: Optional[SkillState] = None
    min_success_rate: float = 0.0
    max_results: int = 20
    include_deprecated: bool = False

@dataclass
class SearchResult:
    skill: Skill
    score: float
    match_reasons: List[str] = field(default_factory=list)
```

当前阶段不引入 embedding，搜索仍使用规则型混合评分。`score` 会归一到 `[0, 1]`，`match_reasons` 使用可读文本，避免前端和 C/D 日志出现新的乱码。

第二阶段已将搜索评分抽成共享逻辑，内存 demo 搜索和未来持久化搜索共用同一套规则：

- 文本会同时匹配 `name`、`display_name`、`description`、`tags`、`domain`。
- `fill form` 可以匹配 `fill_form`，用于支持自然语言和 snake_case 之间的轻量映射。
- 精确名称匹配权重最高，名称 token 命中次之，描述/标签/领域命中作为补充分。
- `tags`、`skill_type`、`domain`、`state`、`min_success_rate`、`include_deprecated` 的过滤语义保持一致。
- 默认过滤 `DEPRECATED` / `ARCHIVED`，只有 `include_deprecated=True` 时才返回。
- 同名多版本 Skill 会保留最高分版本。
- 同分时按状态、成功率、使用次数、更新时间稳定排序。

### SkillGraph

当前 demo 实现位于 `MemoryGraphManager`，支持基础图谱能力：

```python
async def sync_skill(skill: Skill) -> None
async def create_edge(edge: SkillEdge) -> None
async def get_subgraph(skill_ids: Optional[List[str]] = None, depth: int = 2) -> SkillSubgraph
async def get_dependency_chain(skill_id: str) -> List[str]
async def get_execution_order(skill_ids: Union[str, List[str]]) -> List[str]
async def get_stats() -> Dict[str, Any]
```

第一阶段只保证基础边、子图、依赖链、执行顺序和统计可用；根据 `sub_skill_ids` 自动建边、相似关系发现、复杂图分析放到后续阶段。

## API 端点

现有 API 路径保持不变：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/skills` | 列出 Skill，支持状态、类型、标签、分页 |
| `POST` | `/api/v1/skills` | 创建 Skill |
| `GET` | `/api/v1/skills/{id}` | 获取 Skill 摘要 |
| `GET` | `/api/v1/skills/{id}/full` | 获取完整 Skill |
| `PATCH` | `/api/v1/skills/{id}` | 更新 description、tags、interface、implementation |
| `DELETE` | `/api/v1/skills/{id}` | 删除 Skill |
| `POST` | `/api/v1/skills/search` | 搜索 Skill |
| `GET` | `/api/v1/skills/{id}/versions` | 获取同名 Skill 版本历史 |
| `GET` | `/api/v1/graph` | 获取完整图谱数据 |
| `POST` | `/api/v1/graph/subgraph` | 获取指定 Skill 的局部子图 |
| `POST` | `/api/v1/graph/edges` | 添加关系边 |
| `GET` | `/api/v1/graph/{id}/dependencies` | 获取依赖链 |
| `GET` | `/api/v1/graph/{id}/execution-order` | 获取执行顺序 |
| `GET` | `/api/v1/graph/stats/overview` | 获取图谱统计 |

## 第一阶段完成项

- 稳定 `MemoryWikiManager` 的 CRUD、版本历史、过滤、执行统计和 overview stats。
- 稳定 `MemorySearchEngine` 对 `SearchQuery` 字段的支持。
- 稳定 `MemoryGraphManager` 的基础边、子图、依赖链、执行顺序和统计。
- 修正 Skill API 在内存模式下更新/删除会访问不存在 `wiki.db` / `wiki.cache` 的问题。
- 补充 `tests/test_skill_repository_phase1.py` 覆盖 Wiki、Search、Graph 和 API 冒烟。

## 第二阶段完成项

- 新增共享评分入口 `score_skill_match()` 和排序入口 `rank_search_results()`。
- `MemorySearchEngine` 改为复用共享评分逻辑，避免内存 demo 和生产搜索排序漂移。
- 搜索结果的 `match_reasons` 使用稳定英文原因，例如 `exact name match`、`tag match`、`domain match`。
- `/api/v1/skills/search` 支持 `domain`、`min_success_rate`、`include_deprecated` 请求字段。
- 补充 `tests/test_skill_repository_search_phase2.py` 覆盖搜索排序、过滤、去重、可读原因和 API 冒烟。

## 后续阶段

1. Graph 自动构建：Skill 创建/更新时自动同步节点，并根据 `sub_skill_ids` 建立 `composes_with` 边。
2. 持久化适配准备：整理 PostgreSQL / Neo4j 接入边界，保持内存 fallback 可用。
3. 联调交付：与 C Retriever、D Librarian / Evolution、E Wiki / Graph、B Snapshot API 做真实数据联调。

## 验证

第一阶段验证命令：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos\skillos
python -m compileall -q skillos\layers\skill_repository skillos\api\routes\skills.py skillos\api\routes\graph.py skillos\api\memory_store.py
python -m pytest tests\test_skill_repository_phase1.py -q
python -m pytest tests\test_skill_repository_search_phase2.py -q
git diff --check
```
