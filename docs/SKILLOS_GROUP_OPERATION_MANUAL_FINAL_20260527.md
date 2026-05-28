# SkillOS 组内最终操作手册

最后更新：2026-05-28
适用对象：组长、组员、答辩/组会演示同学
适用版本：`C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526`

这份手册的目标是让组员能从仓库启动 SkillOS、配置可选 LLM、导入五类输入、运行 P0 评测、查看 SkillsBench 状态、查看 harness 验证和星云图截图。不要把 API key 写进仓库或文档。

## 1. 准备仓库

如果是第一次拿项目：

```powershell
cd C:\Users\<你的用户名>\Desktop
git clone https://github.com/Huangdingcheng/skillos.git
cd .\skillos
```

如果使用本次最终收口 worktree：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526
git status --short --branch
```

期望分支类似：

```text
codex/pr11-version-lab-20260526
```

不要修改或运行：

```text
C:\Users\m1516\Desktop\SKILLOS\skillos-runtime-pr
```

## 2. 安装依赖

后端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos
python -m pip install -r requirements.txt
```

前端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos-frontend
npm install
```

快速确认：

```powershell
python --version
node --version
npm --version
```

## 3. 配置 LLM API

UI 演示和本地 deterministic harness 不强制要求真实 LLM key。要接 DeepSeek/OpenAI-compatible 模型时，复制本地配置：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526
copy .\skillos-one-click-launcher\config.example.ps1 .\skillos-one-click-launcher\config.local.ps1
```

打开 `skillos-one-click-launcher\config.local.ps1`，填自己的值：

```powershell
$env:LLM_API_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "deepseek-v4-flash"
$env:LLM_API_KEY = "replace-with-your-own-key"
```

注意：

- `config.local.ps1` 被 Git 忽略，不要提交。
- 不要把 key 发到 PR、Markdown、截图或聊天记录。
- 本次最终验证的 secret scan 对仓库源码无 key 命中。

## 4. 一键启动

在仓库根目录双击：

```text
START_SKILLOS_DEMO.bat
```

默认会启动：

- 后端：`http://127.0.0.1:8001`
- 前端：`http://127.0.0.1:5174`
- 默认页面：`http://127.0.0.1:5174/wiki`
- 默认存储：`memory`

停止：

```text
STOP_SKILLOS_DEMO.bat
```

如果端口被占用，脚本会自动往后找空闲端口。实际端口看：

```text
skillos-one-click-launcher\runtime\skillos-demo.pids.json
```

## 5. 手动启动

后端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos
python -m skillos.api.main --host 127.0.0.1 --port 8001 --repository-backend memory
```

前端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos-frontend
$env:SKILLOS_API_TARGET = "http://127.0.0.1:8001"
$env:VITE_SKILLOS_DISABLE_WS = "1"
npm run dev -- --host 127.0.0.1 --port 5174
```

打开：

```text
http://127.0.0.1:5174/wiki
```

## 6. Readiness 检查

推荐在独立测试后端上跑，避免 readiness 探针污染主演示状态。最终已验证的示例：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526
$env:SKILLOS_GOVERNANCE_REPO = "C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\artifacts\eval-readiness-runs\final-isolated-20260528\governance-repo"
python scripts\demo_readiness_check.py `
  --api-base http://127.0.0.1:8021/api/v1 `
  --frontend-base http://127.0.0.1:5181 `
  --run-root artifacts\eval-readiness-runs `
  --run-id final-isolated-8021-5181-20260528-green
```

最终报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_DEMO_READINESS_FINAL_20260528.md
```

结果：15/15，通过 backend health、frontend home、frontend proxy、LLM 配置状态、ingest parse、create candidate、execution plan、harness verify-loop、graph API、evaluation API、version diff/snapshot。

## 7. 五类输入 P0 语料

P0 语料位置：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527
```

Manifest：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527\manifests\input_skill_eval_manifest_p0_20260527.json
```

数量：

| 输入类型 | 数量 |
| --- | ---: |
| `trajectory` | 25 |
| `document` | 25 |
| `api_doc` | 25 |
| `script` | 25 |
| `past_skills` | 25 |

所有条目都有 `source_url` 和 `license_note`。语料来源包括 SkillsBench、Ctx2Skill、WebArena、browser-use、RCI-agent、Anthropic Skills、Kubernetes/Docker/GitHub Actions/OpenAPI 等公开材料或公开项目样例。

## 8. 运行输入转 Skill 评测

完整 P0 工作流已经跑过：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_INPUT_TO_SKILL_P0_FULL_WORKFLOW_REPORT.md
```

可复跑命令模板：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526
python scripts\run_input_skill_eval.py `
  --manifest C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527\manifests\input_skill_eval_manifest_p0_20260527.json `
  --fixture-root C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527 `
  --api-base http://127.0.0.1:8017/api/v1 `
  --run-root artifacts\input-skill-eval-runs `
  --run-id p0-full-workflow-memory-rerun `
  --create-candidates `
  --snapshot `
  --max-candidates-per-fixture 1 `
  --max-chars 12000
```

最终结果：125 条 fixture 全部 parse、audit、create S1、graph present、business diff、snapshot、schema、Ctx2Skill evidence、SkillX layer 检查通过；overall score `0.91`。

## 9. SkillsBench 状态

SkillsBench sparse subset 已下载在：

```text
C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\artifacts\skillsbench-runs\skillsbench-sparse-p0
```

官方 task metadata check 已通过 5/5：

- `citation-check`
- `sales-pivot-analysis`
- `software-dependency-audit`
- `court-form-filling`
- `dialogue-parser`

报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_SKILLSBENCH_P0_REPORT.md
```

边界：官方 oracle/no_skill/generated_skill sandbox 得分没有宣称，因为当前机器没有 Docker/Compose，BenchFlow oracle 尝试被 `[WinError 2] 系统找不到指定的文件` 阻塞。现在可以讲“官方任务元数据有效，SkillOS 已完成 40 个 fixture-task 映射，本地完整工作流分数 0.91”，不能讲“已经得到官方 SkillsBench 分数”。

## 10. Harness 正负例验证

代表性 harness 报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\SKILLOS_HARNESS_P0_REPORT.md
```

覆盖：

- 每类输入 3 个代表样例；
- 共 15 个 generated Skills；
- positive pass rate `1.0`；
- negative rejection rate `1.0`；
- 全部 positive 进入 S3；
- evidence path 写入 `artifacts\harness-runs\...`。

边界：这是 deterministic local contract，不等于真实开放环境里的完整语义正确性。

## 11. Graph Nebula 页面

Graph 页面：

```text
http://127.0.0.1:5174/graph
```

新增能力：

- 默认小节点；
- Nebula / Readable / Debug presets；
- node size；
- edge width；
- edge opacity；
- node label mode；
- edge label mode；
- charge strength；
- link distance；
- dense mode；
- localStorage 持久化。

截图报告：

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527\screenshots\SKILLOS_GRAPH_UI_SCREENSHOT_REPORT.md
```

截图覆盖 Nebula、Readable、Debug、selected subgraph、mobile layout。大图实测 API 规模：skill-only 193 nodes / 7 edges，provenance 630 nodes / 505 edges。

## 12. 前端手动演示路线

推荐顺序：

1. `Wiki`：展示生成的 Skills 和 S3 harness evidence。
2. `Knowledge Import`：展示五个输入 tab，重点点 Document 和 Past Skills。
3. `Harness Verification`：展示 S2 -> execute -> verifier -> S3。
4. `Graph`：切 Nebula / Readable / Debug，展示 relation strength。
5. `Evaluation`：展示本地 benchmark/eval。
6. `Execution`：展示 planner 选择 Skill。
7. `Version Control`：展示 Version Lab、business diff、snapshot/rollback。

## 13. 最终验证命令

后端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos
python -m pytest tests\test_input_skill_eval_runner.py tests\test_prepare_skillsbench_subset.py tests\test_skill_governance_lifecycle_api.py tests\test_skill_governance_snapshot_diff.py tests\test_models.py tests\test_harness_api.py tests\test_report_skillsbench_mapping.py tests\test_p0_harness_eval_runner.py tests\test_ingest_candidate_review.py -q --no-cov
```

结果：`135 passed`，只有既有 deprecation warnings。

Compile：

```powershell
python -m compileall -q skillos benchmarks ..\scripts\demo_readiness_check.py ..\scripts\run_input_skill_eval.py ..\scripts\prepare_skillsbench_subset.py ..\scripts\report_skillsbench_mapping.py ..\scripts\run_p0_harness_eval.py
```

前端：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526\skillos-frontend
npm run lint
npm run build
```

结果：lint/build 通过，build 只有 Vite 大 chunk warning。

Secret scan：

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526
rg -n 'sk-[A-Za-z0-9]{16,}|LLM_API_KEY\s*=\s*"sk-' --glob '!node_modules/**' --glob '!dist/**' --glob '!artifacts/**' .
```

结果：无命中。

## 14. VPN / 网络说明

当前 P0 外部语料和 SkillsBench subset 已下载到本地。现在做本地评测、截图、文档、PR 准备不需要 VPN。

需要重新打开 VPN 的情况：

- 重新下载 GitHub/HuggingFace 语料；
- 重新 clone SkillsBench/BenchFlow；
- 安装缺失的 npm/pip/uv 依赖；
- 后续安装 Docker Desktop 或跑官方远程依赖。

## 15. 不要过度宣称

可以讲：

- 五类输入能生成 SkillOS S1 Candidate；
- Document 有 Ctx2Skill-inspired evidence；
- Past Skills 有 SkillX-style 三层映射和图关系；
- Harness 能把代表性 generated Skills 从 S2 推进到 S3；
- Version Lab 支持 business diff、editable version、snapshot；
- Graph Nebula 能看 100+ 节点和异构证据图。

不要讲：

- 完整复现 Ctx2Skill 大规模 self-play；
- 完整复现 SkillX 自动 SkillKB；
- 已经有官方 SkillsBench generated-skill 分数；
- 已经有真实浏览器 WebXSkill benchmark；
- memory backend 数据是永久数据。
