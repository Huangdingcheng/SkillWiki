# Module 05: Frontend, REST API & Experience Pipeline

负责人分支：`frontend-dev`

本模块负责 SkillOS 的前端体验、E 侧 REST API 适配、知识导入与 Experience Pipeline 展示闭环。当前实现遵循飞书 `interfaces.md` 中的跨模块契约，不直接修改 `docs/interfaces.md` 或 `docs/architecture.md`。

## 5.1 当前完成状态

| 页面 | 路径 | 当前状态 |
| --- | --- | --- |
| Dashboard | `/` | 已支持首次加载、手动刷新、15 秒自动刷新、健康摘要和事件 Feed。WebSocket 兼容 `{ type, payload, timestamp }` 与旧格式 `{ event, data }`。 |
| Knowledge Import | `/ingest` | 已支持 `trajectory`、`document`、`api_doc`、`script` 四类输入，调用 `/ingest/parse` 与 `/ingest/parse-and-create`，创建后展示可跳转 Wiki 的 Skill 链接。 |
| Agent Execution | `/execution` | 已支持执行任务、展示检索 Skill、步骤结果和最近执行历史。历史字段按 `ExecutionHistoryItem` 消费。 |
| Skill Wiki | `/wiki` | 已支持列表、详情抽屉和 `skill_id` query 自动打开详情。兼容后端 `input_schema` / `output_schema` 字段。 |
| Skill Graph | `/graph` | 已支持节点点击、选中态、详情摘要、子图展开、返回全图，以及 `/graph?skill_id=...` 自动加载相关子图。 |
| Self-Evolution Demo | `/demo` | 已作为 E 任务核心演示入口，串起 Skill 检索、计划生成、执行、经验记录、演化学习，并联动 Wiki/Graph/Execution。 |
| Evolution | `/evolution` | 已支持系统健康报告、演化周期摘要、单 Skill repair 入口、空状态和错误兜底。 |
| Lifecycle / Versions | `/lifecycle`, `/versions` | 保留辅助演示能力，用于展示生命周期状态和版本管理。 |

前端已完成路由级懒加载；`AppLayout`、主题和全局 store 保持同步加载，页面组件按路由访问时加载。

## 5.2 E 侧 API 消费

前端 API 客户端统一通过 `skillos-frontend/src/api/client.ts` 调用后端 `/api/v1`：

| API client | 主要用途 |
| --- | --- |
| `skillsApi` | Skill 列表、详情、搜索、版本、删除 |
| `graphApi` | 完整图、子图、图谱统计 |
| `executionApi` | 执行单个 Skill、执行计划、读取状态、重置状态、执行历史 |
| `evolutionApi` | 系统健康、单 Skill 健康、repair、evolution cycle |
| `ingestApi` | 知识导入解析预览、解析并创建候选 Skill |
| `statsApi` | Dashboard 聚合统计和演化统计 |

错误处理通过统一 helper 解析 FastAPI `detail`、后端 `error`、Axios/network error 和 fallback 文案。E 主线页面在后端不可用时应显示错误提示，不应白屏。

## 5.3 Experience Pipeline

当前 pipeline 位于 `skillos/skillos/layers/input_knowledge/`，主流程是：

```text
Raw input
  -> Extractor Agent
  -> Normalizer Agent
  -> Summarizer Agent
  -> Indexer Agent
  -> PipelineResult
```

支持的输入类型保持飞书接口约定：

| `source_type` | 用途 |
| --- | --- |
| `trajectory` | 浏览器操作轨迹或自然语言步骤 |
| `document` | 技术文档、操作说明、Markdown 文档 |
| `api_doc` | API 文档或 OpenAPI/Swagger 描述 |
| `script` | Python、JavaScript/TypeScript、Shell 脚本 |

`PipelineResult` 保持如下字段：

```python
@dataclass
class PipelineResult:
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: list[str]
    units: list[StructuredExperience]
```

`StructuredExperience` 继续输出：

```python
@dataclass
class StructuredExperience:
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: list[str]
    normalized_actions: list[dict]
    summary: str
    proposed_skill_name: str | None
    proposed_description: str | None
    proposed_type: str | None
    confidence: float
    index_keywords: list[str]
    index_embedding_hint: str
```

本阶段已将 LLM-facing prompt 改为稳定英文 ASCII，避免已有中文编码异常影响模型输出。Prompt 要求 LLM 只输出 JSON，并明确：

- `proposed_skill_name` 使用 `snake_case`。
- `proposed_type` 只能是 `atomic`、`functional`、`strategic`。
- `confidence` 必须归一到 `[0, 1]`。
- LLM 失败时继续走可读 fallback，不产生乱码描述。

Pipeline 内部增加了轻量归一化：

- 非法 `skill_type` 回退为 `atomic`。
- `confidence` clamp 到 `[0, 1]`。
- 空 skill 名称回退为 `skill_from_<source_type>`。
- `token_usage` 尽量累计 LLM response usage；拿不到时保持 `0`。

## 5.4 Ingest API

`skillos/skillos/api/routes/ingest.py` 提供两个端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/ingest/parse` | 解析预览，不写入 Wiki |
| `POST` | `/ingest/parse-and-create` | 解析输入并创建 S1 候选 Skill |

请求体：

```json
{
  "source_type": "trajectory",
  "content": "raw input text",
  "metadata": {}
}
```

行为约定：

- `source_type` 仅接受 `trajectory`、`document`、`api_doc`、`script`，非法值返回 `400 detail`。
- `content` 为空返回 `400 detail`。
- `parse-and-create` 保留成功创建的 `created_skills`。
- 单个 Skill 创建失败时，错误写入 `errors`，不再静默吞掉。
- 不新增跨组强制接口，不要求 A/C/D 修改后端契约。

## 5.5 跨组依赖与剩余风险

E 侧已经完成展示链路和接口兼容，但最终效果仍依赖其他组补齐真实数据：

- C 侧需要持续产生真实执行历史，供 `/execution` 和 `/demo` 展示。
- C/D 侧需要推送真实 WebSocket 事件，供 Dashboard 事件 Feed 和轻量刷新使用。
- D 侧需要补齐真实健康报告、repair 和 evolution cycle 结果。
- A/D 侧补齐真实 Skill 关系边后，Graph 展示会更完整。
- Vite 大 chunk warning 仍主要来自 G6/公共依赖，当前不影响功能，可放到性能专项处理。

## 5.6 验证建议

后端：

```bash
cd C:\Users\m1516\Desktop\SKILLOS\skillos\skillos
python -m compileall -q skillos\api skillos\layers\input_knowledge
python -m pytest skillos\tests\test_layers.py -q
```

前端：

```bash
cd C:\Users\m1516\Desktop\SKILLOS\skillos\skillos-frontend
npm run build
```

浏览器验收重点：

- `/ingest` 四类输入可解析，错误时有提示。
- `parse-and-create` 成功后能跳转 `/wiki?skill_id=...`。
- `/demo`、`/execution`、`/wiki`、`/graph`、`/evolution` 无白屏。
- 后端不可用时页面显示错误兜底，不崩溃。
