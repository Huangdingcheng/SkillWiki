# SkillOS Demo 完整网页录制流程 V2

日期：2026-05-06  
适用状态：前后端已经打开，浏览器可以访问 `http://127.0.0.1:5173/`。如果使用 2026-05-10 的 handoff 整合包，后端必须用 `--repository-backend memory` 启动。  
目标：给组长录一段完整、诚实、有功能含量的本地 demo，展示当前 SkillOS 已经能跑通什么，以及下一步应该补什么。

## 1. 先回答：这个 demo 现在值不值得录

可以录。

但要把它定位成：

```text
本地整合预览版，可以演示 SkillOS 的最小闭环和主要页面能力，但不是生产级完整产品。
```

目前已经实测能演示的能力：

- 从网页导入经验/轨迹，解析并创建候选 Skill。
- 在 Skill Wiki 中查看新 Skill 和已有 Skill 的详情、接口、实现、指标。
- 在 Lifecycle 页面推动 Skill 状态流转，例如 Candidate -> Draft -> Verified -> Released。
- 在 Version Control 页面创建 patch/minor/major 新版本，查看版本历史和 diff。
- 在 Agent Execution 页面输入任务目标，检索 Skill、生成计划、执行、记录经验。
- 在 Self-Evolution Demo 页面展示检索、规划、执行、经验记录、演化学习的闭环动画和结果。
- 在 Evolution 页面查看健康报告、成功率、使用次数、运行一次演化周期。
- 在 Knowledge Graph 页面查看技能节点、组合关系、依赖关系、子图和 Wiki 跳转。
- Dashboard 可以展示总览指标、健康状态、自演化指标和实时事件入口。

必须诚实说明的边界：

- 当前网页没有完整的手填 schema/code 的 Skill 编辑器；“创建新 Skill”的网页入口是 Knowledge Import，把轨迹/文档/API/脚本解析成候选 Skill。
- 当前打分不是人工五星评分，而是执行指标、成功率、健康评分和检索相关度。
- 当前稳定录屏建议使用 memory backend；版本页面展示的是本地 Git 式 Skill 版本模型、diff 和历史，不是 GitHub 远端提交历史。
- Git-backed repository 是 B 方向集成成果，但在网页主流程录屏里不建议直接切 Git backend，因为首次 seed 和文件/Git 操作可能卡顿。
- 当前 demo 用 fallback planner 保证本地稳定；接真实 API key 后才能完整展示 LLM 规划和更聪明的 Skill 生成命名。

## 2. 录屏前 30 秒检查

推荐从干净终端启动后端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos
$env:SKILLOS_FORCE_PLANNER_FALLBACK='1'
python -m skillos.api.main --api-key demo --port 8000 --repository-backend memory
```

前端启动：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos-frontend
npm ci
npm run dev
```

关键点：

- `--repository-backend memory` 必须加；不加时默认可能进入 Git backend，首次 seed / Git 文件操作可能卡住，不适合作为录屏路径。
- `SKILLOS_FORCE_PLANNER_FALLBACK=1` 用于保证 demo 不依赖真实 API key。
- 如果 8000 或 5173 已被占用，可以临时换端口验证，但正式录屏建议保持 8000 / 5173，避免前端代理和文档路径不一致。

浏览器打开：

```text
http://127.0.0.1:5173/
```

如果页面打不开，先不要录。用这三个地址确认服务：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:5173/api/v1/graph/stats/overview
http://127.0.0.1:5173/api/v1/repository/status
```

当前已验证的预期：

```json
{"status":"ok"}
```

```json
{"nodes":21,"edges":5}
```

说明：旧录屏环境里图谱统计可能是 21 nodes / 7 edges；2026-05-10 handoff 整合包实测是 21 nodes / 5 edges。只要 `fill_form` 子图能展开出 `click_element` 和 `type_text`，录屏主线不受影响。

```json
{"backend":"memory","is_git_repo":false,"dirty":false}
```

建议录屏前重启一次 backend，让 memory 数据更干净。你现在如果已经打开，也可以直接录，只是 Dashboard 和执行历史里会有之前测试留下的记录。

## 3. 总体录制结构

建议录 8 到 12 分钟。不要每个按钮都点一遍，要围绕一条主线：

```text
经验输入 -> 自动创建候选 Skill -> Wiki 管理 -> 生命周期流转 -> 版本演化 -> Agent 执行 -> 经验反馈/健康评分 -> 图谱关系 -> 下一步规划
```

录屏顺序：

1. Dashboard 总览
2. Knowledge Import 创建新 Skill
3. Skill Wiki 查看新 Skill
4. Lifecycle 状态流转
5. Version Control 创建新版本、看 diff、发布
6. Agent Execution 执行任务并记录经验
7. Self-Evolution Demo 展示完整闭环
8. Evolution 健康评分和演化周期
9. Knowledge Graph 图谱关系
10. 回到 Dashboard 总结

## 4. 开场话术

打开 Dashboard 后先说：

```text
这里展示的是 SkillOS 本地整合预览版，不涉及 GitHub push，也没有动团队已有 PR。
这次主要看一个最小可用闭环：从经验输入生成候选 Skill，到 Wiki 管理、生命周期、版本记录、Agent 执行、经验反馈和图谱组织。
目前为了录屏稳定，后端使用 memory backend 和本地 fallback planner；真实 LLM 和 Git backend 是下一阶段要接通和优化的重点。
```

## 5. Dashboard：先给组长建立全局认知

页面：

```text
Dashboard
```

停留展示：

- Total Skills
- Released
- Total Executions
- Skill Types
- State Distribution
- System Health
- Self-Evolution Metrics
- Agent 实时动态区域

讲法：

```text
Dashboard 是总览页。这里能看到当前 Skill 数量、发布状态、执行次数、健康度和自演化指标。
这说明现在不是单页面 mock，而是前端通过 API 读后端的 Skill、执行、健康和演化数据。
```

注意：

- 如果 Total Executions 不为 0，是因为本地测试已经跑过执行任务。
- 如果实时动态为空，可以说 WebSocket 事件入口已经在页面上，后面执行/演化时这里会接事件。

## 6. Knowledge Import：从经验创建候选 Skill

页面：

```text
Knowledge Import
```

选择左上 tab：

```text
操作轨迹 / trajectory
```

在文本框粘贴这段英文，稳定性比中文更好：

```text
1. Open a browser and go to https://example.com/login
2. Click the username input field
3. Type demo_user
4. Click the password input field
5. Type demo_password
6. Click the login button
7. Wait for the dashboard page and confirm login success
```

先点：

```text
解析预览
```

展示：

- Extractor
- Normalizer
- Summarizer
- Indexer
- 经验单元
- proposed skill name
- confidence
- keywords

讲法：

```text
这里模拟把一次真实操作轨迹输入给 SkillOS。系统会经过 Extractor、Normalizer、Summarizer、Indexer，把原始经验变成结构化经验单元，并给出候选 Skill 的名称、描述、置信度和检索关键词。
```

然后点：

```text
解析并创建 Skill
```

展示右侧成功提示和创建出来的 Skill 标签。

讲法：

```text
这一步把候选 Skill 写入 Skill Wiki。当前 fallback 模式下命名可能比较朴素，例如 skill_from_trajectory；这正好说明下一步接真实 LLM 后要提升命名、抽象和 schema 生成质量。
```

重要边界：

```text
当前网页创建 Skill 的入口是“从经验导入沉淀 Skill”，不是完整手写 Skill IDE。
```

## 7. Skill Wiki：查看和管理 Skill

从 Knowledge Import 的创建结果点击新 Skill 标签，或者进入：

```text
Skill Wiki
```

搜索：

```text
skill_from_trajectory
```

如果刚才生成的名字不一样，就搜索：

```text
trajectory
```

打开详情抽屉后，依次展示：

- 基本信息：ID、版本、描述、标签、创建时间
- 接口：输入/输出参数
- 实现：prompt template 或 code/sub skills
- 指标：执行次数、成功次数、失败次数、成功率、平均延迟

讲法：

```text
Wiki 是 Skill 的知识库视图。一个 Skill 不只是名字，还包含生命周期状态、版本、标签、接口、实现和运行指标。
刚才从轨迹沉淀出来的候选 Skill 已经进入 Wiki，后面就可以被生命周期、版本和执行模块管理。
```

再搜索一个已有可执行组合技能：

```text
fill_form
```

打开详情，切到实现 tab，展示：

- `sub_skill_ids`
- `click_element`
- `type_text`

讲法：

```text
这个 fill_form 是组合 Skill，它通过子技能 click_element 和 type_text 组合完成表单填写。
这对应 SkillOS 的一个核心思路：复杂能力可以由更小粒度的 Skill 组合出来。
```

如果要演示状态按钮：

- S2 状态可能出现发布按钮。
- S4 状态可能出现废弃按钮。

录屏不建议在 Wiki 里废弃核心 demo skill，例如 `fill_form`。

## 8. Lifecycle：演示 Skill 状态机

页面：

```text
Lifecycle
```

在下拉框选择刚才创建的新 Skill，通常是：

```text
skill_from_trajectory
```

按当前状态逐步点击可用流转。常见路线：

```text
S1 Candidate -> S2 Draft -> S3 Verified -> S4 Released
```

每点一次停一下，展示：

- 状态机主流程
- 当前状态说明
- 可执行状态转换按钮
- 右侧状态转换历史

讲法：

```text
Lifecycle 页面展示的是 SkillOS 的八状态模型。
一个 Skill 从原始经验或候选能力开始，经过草稿、验证、发布，后续也可能退化、废弃和归档。
这能帮助团队把 Skill 当成有生命周期的工程资产，而不是一次性 prompt 或脚本。
```

如果某一步按钮不可用：

- 说明当前后端只允许合法状态转移。
- 不要硬点，直接讲“状态机约束了可用流转”。

## 9. Version Control：演示 Git 式版本记录

页面：

```text
Version Control
```

下拉选择刚才创建的新 Skill：

```text
skill_from_trajectory
```

先展示：

- 版本历史表
- 右侧版本时间线
- 当前版本信息

然后点击：

```text
patch
```

等待出现新版本，例如：

```text
v1.0.1
```

再点击：

```text
查看 Diff
```

展示：

- 变更历史
- from_version / to_version
- change_type
- diff 区域

如果新版本处于 Draft/Verified，页面可能出现发布按钮。可以点击发布新版本，展示 State 变为 Released。

讲法：

```text
这里展示的是 Git 式 Skill 版本管理。Skill 不只是被覆盖更新，而是可以创建新版本、保留历史、查看 diff，并管理发布状态。
当前录屏使用 memory backend，所以这里展示的是本地 Skill 版本模型和 diff，不是 GitHub 远端 commit log。
B 方向的 Git-backed repository 已经接入过，但网页录屏主流程暂时不用它，避免首次 seed 和 Git 文件操作影响演示稳定性。
```

边界一定要说清：

```text
这部分可以说明“Git 式版本记录能力已经有雏形”，不要说“已经完整展示 GitHub 远端版本协作流程”。
```

## 10. Agent Execution：执行任务、检索 Skill、记录经验

页面：

```text
Agent Execution
```

输入：

```text
fill form web login click type
```

执行参数建议：

```text
max_skills = 3
```

2026-05-10 handoff 整合包实测：`max_skills=3` 会稳定得到 `status=success`，步骤是 `fill_form -> click_element -> type_text`。如果把 `max_skills` 调高，系统可能额外检索到实验性的图谱测试 Skill 或 prompt-template Skill，导致总体状态变成 `partial`，不适合作为录屏主线。

点击：

```text
执行
```

展示：

- 最近执行历史新增一条记录
- 检索到的 Skill
- 相关度 score
- 执行摘要：状态、总步骤、成功数、总耗时
- 执行步骤
- 最终状态 JSON
- 经验已记录提示

可以展开第一条执行步骤，看 `sub_results` 或 result 内容。

讲法：

```text
这一页是 Agent 调用 Skill 的运行时视图。输入目标后，系统会先检索相关 Skill，再生成计划并执行。
本地 demo 中 fill_form 会组合 click_element 和 type_text，所以可以看到步骤展开和最终状态。
执行结束后，系统会记录本次经验，用于后续健康评分、复用率和演化决策。
```

注意：

- 推荐只用 `fill form web login click type`。
- 不要临场输入很复杂的中文任务，因为 fallback planner 可能命中不可执行的 prompt-template skill。

## 11. Self-Evolution Demo：展示完整闭环动画

页面：

```text
Self-Evolution Demo
```

输入同一个目标：

```text
fill form web login click type
```

点击：

```text
启动演化闭环
```

停留展示五个阶段：

1. Skill 检索
2. 计划生成
3. 执行
4. 经验记录
5. 演化学习

展示右侧：

- 步骤数
- 成功数
- 耗时
- 执行状态
- 经验已记录
- 演化学习卡片

点击检索结果里的：

```text
Wiki
Graph
```

分别跳到 Wiki 和 Graph，展示 Skill 可以跨页面联动。

讲法：

```text
这个页面把 SkillOS 的核心闭环放在一个演示视图里：目标输入、Skill 检索、计划生成、执行、经验记录、学习信号更新。
它和刚才 Agent Execution 用的是同一套后端执行接口，只是这里更适合向组长解释概念闭环。
```

## 12. Evolution：健康评分、打分、演化周期

页面：

```text
Evolution
```

展示顶部统计：

- Total
- Healthy
- Degraded
- Critical
- Stale
- Health Ratio

展示表格：

- Skill
- Status
- Success Rate
- 执行次数
- 平均延迟
- 问题
- 建议

点击：

```text
运行演化周期
```

展示返回结果：

- tasks completed
- repaired
- deprecated
- merged
- split
- failed

讲法：

```text
这里就是当前 demo 的“打分”和反馈演化能力。
它不是人工评分，而是根据执行次数、成功率、延迟等指标生成健康状态和建议。
运行演化周期会触发系统扫描 Skill 健康状况，决定是否需要修复、废弃、合并或拆分。
当前本地数据大多是 healthy，所以演化周期可能显示任务数为 0，这说明没有触发修复条件，不是页面坏了。
```

如果需要演示修复按钮：

- 只有 degraded 或 critical 的 Skill 会出现在“需要关注”的区域。
- 当前数据全 healthy 时，不要强行演示修复。

## 13. Knowledge Graph：展示 Skill 关系网络

页面：

```text
Knowledge Graph
```

先展示全图：

- 当前烟测约 21 个节点
- 约 7 条边
- 边类型包括 `composes_with`、`depends_on`、`similar_to`

推荐点击节点：

```text
fill_form
```

右侧展示：

- state
- version
- success rate
- usage count
- tags

点击：

```text
展开关联
```

depth 保持 2。当前已验证 `fill_form` 子图是：

```text
3 nodes / 2 edges
```

也就是：

```text
fill_form -> click_element
fill_form -> type_text
```

再点击：

```text
在 Wiki 中查看
```

讲法：

```text
图谱页面展示 Skill 之间的结构关系。
fill_form 通过 composes_with 边连接到底层 click_element 和 type_text，这说明系统已经能把组合 Skill 和原子 Skill 的关系可视化。
未来这块可以进一步支持更复杂的异构图检索、相似技能聚类和自动依赖分析。
```

可以展示的图谱操作：

- 过滤边类型
- 放大/缩小
- 适配视图
- 布局设置
- 子图展开
- Wiki 跳转

## 14. 回到 Dashboard：收尾总结

最后回到：

```text
Dashboard
```

刷新一下。

展示：

- Total Executions 变化
- Recent Activity
- Health Overview
- Self-Evolution Metrics

收尾话术：

```text
这一版 demo 已经能展示 SkillOS 的最小可用闭环：
第一，从经验或轨迹创建候选 Skill；
第二，进入 Wiki 做结构化管理；
第三，通过生命周期状态机和版本控制管理 Skill 资产；
第四，Agent 可以检索并执行 Skill；
第五，执行结果会反馈到经验记录、健康评分和演化周期；
第六，图谱能把 Skill 的组合、依赖和相似关系可视化。

目前不足也很清楚：
创建 Skill 的质量还依赖真实 LLM；
网页还缺完整 Skill 编辑器；
人工评分/审核工作流还没做完；
Git backend 需要继续优化到能稳定支撑网页实时操作；
下一步应该接真实 API key、打磨 Git backend 性能、补 Skill 编辑/评审页面，再规划团队分支合并。
```

## 15. 推荐录屏节奏

如果只录 8 分钟：

- 0:00 Dashboard 总览
- 0:50 Knowledge Import 创建 Skill
- 2:10 Skill Wiki 查看详情
- 3:00 Lifecycle 流转
- 4:00 Version Control 新版本和 diff
- 5:10 Agent Execution 执行任务
- 6:20 Self-Evolution Demo 闭环
- 7:20 Evolution + Graph 快速展示
- 8:00 总结下一步

如果录 12 分钟：

- 每个页面都按上面流程慢一点讲。
- Graph 可以多展示子图和过滤。
- Version Control 可以完整展示 patch、diff、release。

## 16. 录屏时不要做的事

- 不要 push。
- 不要打开 GitHub 做远端操作。
- 不要碰 C 同学的 `runtime-dev` 或 `skillos-runtime-pr`。
- 不要录 Git backend 主流程，除非已经单独验证不会卡。
- 不要把当前版本说成生产可用。
- 不要说已经完成完整人工评分系统；当前是指标型健康评分。
- 不要说已经完成 GitHub 协作版本记录；当前录屏展示的是本地 Git 式 Skill 版本模型。
- 不要临场输入复杂中文任务作为主 demo。

## 17. 如果录屏中出问题

页面没数据：

```text
刷新页面，确认 backend 8000 和 frontend 5173 都还在。
```

Knowledge Import 生成名字很普通：

```text
这是 fallback 模式预期结果。讲成“候选 Skill 已经沉淀，命名和抽象质量下一步接 LLM 提升”。
```

Evolution 周期显示 0 个任务：

```text
当前数据健康，没有触发修复/废弃条件。
```

Graph 空白：

```text
等 3 秒，点刷新全图，或点击适配视图。
```

Version diff 为空：

```text
先点 patch 创建新版本，再点查看 Diff。
```

页面中文显示乱码：

```text
源码本身是 UTF-8，PowerShell 乱码不代表浏览器乱码。
浏览器若乱码，刷新或重启 Vite。
```

## 18. 当前已实测的本地证据

服务：

```text
Backend: http://127.0.0.1:8000
Frontend: http://127.0.0.1:5173
```

已验证：

- `/health` 返回 ok
- 2026-05-10 handoff 整合包：`/graph/stats/overview` 返回 21 nodes / 5 edges
- 旧录屏环境可能显示 21 nodes / 7 edges；这不影响 `fill_form` 子图主线
- `/execution/plan` 对 `fill form web login click type` 且 `max_skills=3` 返回 success
- 成功执行链路为 `fill_form -> click_element -> type_text`
- Knowledge Import 的 `parse-and-create` 能创建 S1 Candidate Skill
- Lifecycle 能把临时 Skill 从 S1 转到 S2
- Version Control 能创建 `v1.0.1`，diff history 返回 2 条记录
- Release 能把新版本发布到 S4
- Evolution cycle 能返回周期结果
- `fill_form` 子图能返回 3 nodes / 2 edges
- 临时验证 Skill 已删除，不影响正式录屏

## 19. 给组长看的下一步规划

建议最后明确下一步路线：

```text
第一，接真实 API key，把 Knowledge Import、Planner、Reviewer 的 LLM 能力打开。
第二，补完整 Skill 编辑器，让用户能手写/修改 schema、实现、测试用例。
第三，补人工评分和审核页面，把健康评分、人工反馈、自动演化串起来。
第四，优化 Git backend 的网页路径性能，让版本历史、diff、snapshot、rollback 都能稳定在网页展示。
第五，确认团队分支和 PR 合并策略，避免影响已有 PR 和 C 同学工作。
```

这一版录屏的核心不是证明“已经做完”，而是证明：

```text
SkillOS 的数据模型、页面、API 和运行时闭环已经有了可演示的雏形，下一步可以围绕真实 LLM、Git backend、编辑评审工作流继续推进。
```
