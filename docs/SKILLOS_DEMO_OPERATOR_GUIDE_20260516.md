# SkillOS 组内演示操作手册

版本日期：2026-05-16
适用对象：组员、组长、助教现场复现者
适用环境：Windows + PowerShell + Python + Node.js/npm

这份手册的目标是让任何组员从一个干净仓库开始，能完成四件事：

1. 配置可选的真实 LLM API。
2. 一键启动 SkillOS 后端、前端和网页。
3. 导入公开测试语料，恢复可展示的 Skill、S3 harness 验证样例和图关系样例。
4. 在网页里按顺序演示本轮 demo-paper prototype 的关键能力。

## 1. 拉取仓库

```powershell
cd C:\Users\<你的用户名>\Desktop
git clone https://github.com/Huangdingcheng/skillos.git
cd .\skillos
```

如果已经 clone 过：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos
git fetch origin
git checkout demo-paper-ready-20260514
git pull
```

如果 PR 合并后再使用，直接切到 `main` 并 `git pull` 即可。

## 2. 安装依赖

后端：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos
python -m pip install -r requirements.txt
```

前端：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos-frontend
npm install
```

确认版本：

```powershell
python --version
node --version
npm --version
```

## 3. 配置真实 LLM API

如果只看 UI 和本地 demo，可以跳过这一节。一键启动脚本会自动填入 placeholder，不会因为没有 key 而启动失败。

如果要接入 DeepSeek/OpenAI-compatible 模型，复制配置文件：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos
copy .\skillos-one-click-launcher\config.example.ps1 .\skillos-one-click-launcher\config.local.ps1
```

打开 `skillos-one-click-launcher\config.local.ps1`，填写自己的值：

```powershell
$env:LLM_API_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "your-model-id"
$env:LLM_API_KEY = "your-api-key"
```

注意：

- `config.local.ps1` 已被 `.gitignore` 忽略，不要提交。
- 不要把 API key 写入 Markdown、截图、issue、PR 描述或聊天记录。
- 如果要换成别的 OpenAI-compatible 服务，只改 URL、model 和 key。

## 4. 一键启动 SkillOS

从仓库根目录双击：

```text
START_SKILLOS_DEMO.bat
```

或者用 PowerShell：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos
.\START_SKILLOS_DEMO.bat
```

默认启动结果：

- 后端：`http://127.0.0.1:8001`
- 前端：`http://127.0.0.1:5174`
- 默认打开：`http://127.0.0.1:5174/wiki`
- 默认后端存储：`memory`
- 默认关闭 WebSocket：减少 Windows 本地演示时的偶发卡顿

停止服务：

```text
STOP_SKILLOS_DEMO.bat
```

## 5. 恢复演示状态和导入测试语料

因为默认使用 `memory` backend，后端重启后导入的 demo 样例会清空。每次重启后，运行：

```text
RESTORE_SKILLOS_DEMO_STATE.bat
```

它会自动做三件事：

1. 通过 `/ingest/parse -> /ingest/create-candidate` 导入 7 个公开 demo candidate。
2. 通过 `/harness/{skill_id}/verify-loop` 验证并恢复两个 S3 样例：
   - `script_dry_run_analyzer`
   - `legacy_login_flow_imported`
3. 导入 7 个相关 login workflow Skills，并验证：
   - Skill-only 图上的 `depends_on`
   - `composes_with`
   - `evolved_from`
   - 异构 provenance 图
   - version-impact 投影视图

运行完成后会打印报告目录，位置类似：

```text
skillos-one-click-launcher\runtime\demo-state-runs\restore-demo-state-YYYYMMDD-HHMMSS\REPORT.md
```

这些报告在 `runtime` 下，只作为本地演示证据，不提交到 Git。

## 6. 测试语料在哪里

公开小语料放在：

```text
docs\demo-fixtures
```

文件用途：

| 文件 | 输入类型 | 用途 |
| --- | --- | --- |
| `approved_past_skills.json` | Past Skills | 模拟已有 Skills 转成 SkillOS schema |
| `document_ctx2skill_sample.md` | Document | 展示 Ctx2Skill-lite 文档到 Skill |
| `script_dry_run_sample.md` | Script | 展示脚本分析 Skill |
| `script_shell_installer.sh` | Script harness input | 正负例验证用的 shell 文本 |
| `legacy_login_past_skill.json` | Past Skills | 展示依赖、组合、继承关系 |
| `related_login_graph_pack.json` | Past Skills | 展示 7 个相关 Skill 的图关系 |

手动导入方式：

1. 打开 `http://127.0.0.1:5174/import`。
2. 选择对应 tab：`Trajectory`、`Document`、`API Doc`、`Script`、`Past Skills`。
3. 把文件拖进输入框，或复制粘贴内容。
4. 点击 `Parse for Candidate Review`。
5. 在 Candidate Review 中查看：
   - `Ctx2Skill Evidence`
   - `SkillX Layering`
   - `Graph Relation Preview`
6. 点击创建 candidate 后，到 Wiki 和 Graph 页面查看结果。

## 7. 推荐演示顺序

1. `Skill Wiki`
   - 展示已有 Skill 列表。
   - 找到 `script_dry_run_analyzer` 和 `legacy_login_flow_imported`。
   - 说明它们已经有 S3 harness-backed evidence。

2. `Knowledge Import`
   - 展示五种输入：Trajectory、Document、API Doc、Script、Past Skills。
   - 重点展示 Document 的 Ctx2Skill Evidence。
   - 展示 Past Skills 的 SkillX 分层和图关系预览。

3. `Harness Verification`
   - 展示 S2 Draft 经过 local SkillOS harness 执行、postcondition verifier 检查、失败修复、重试，最后进入 S3。
   - 说明这是本轮比上一次提交最关键的执行闭环。

4. `Knowledge Graph`
   - 切到 `Skill-only` 看 `depends_on` 和 `composes_with`。
   - 切到 `Provenance` 看 Source -> Skill -> Execution -> Validation -> Version。
   - 切到 `Version impact` 看投影视图。
   - 强调页面里的 `Relation strength`：`similar_to` 是弱投影关系，不是执行依赖。

5. `Evaluation`
   - 展示 demo benchmark 和真实 LLM planner eval 的结果。

6. `Agent Execution`
   - 展示 execution plan 能从 Skill repository 中选择 Skill。

7. `Version Control` / `Lifecycle`
   - 展示 Skill 生命周期从 Candidate、Draft、Verified 到 Released 的治理路径。

## 8. 手动启动方式

如果一键启动失败，可以手动启动。

后端：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos
python -m skillos.api.main --host 127.0.0.1 --port 8001 --repository-backend memory
```

前端：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos-frontend
$env:SKILLOS_API_TARGET = "http://127.0.0.1:8001"
$env:VITE_SKILLOS_DISABLE_WS = "1"
npm run dev -- --host 127.0.0.1 --port 5174
```

打开：

```text
http://127.0.0.1:5174/wiki
```

## 9. 验证命令

后端重点测试：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos
python -m pytest tests\test_harness_local.py tests\test_harness_codex_cli.py tests\test_harness_verifier_loop.py tests\test_harness_api.py tests\test_ingest_candidate_review.py tests\test_heterogeneous_graph_p0.py tests\test_evaluation_dashboard_api.py tests\test_skill_runtime_verifier_specs.py -q --no-cov
```

前端：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos\skillos-frontend
npm run lint
npm run build
```

导入/恢复演示状态：

```powershell
cd C:\Users\<你的用户名>\Desktop\skillos
.\RESTORE_SKILLOS_DEMO_STATE.bat
```

## 10. 常见问题

### 启动后页面一直 loading

先检查后端：

```powershell
Invoke-WebRequest http://127.0.0.1:8001/health -UseBasicParsing
```

再检查前端代理：

```powershell
Invoke-WebRequest http://127.0.0.1:5174/api/v1/skills?limit=1 -UseBasicParsing
```

如果失败，运行：

```text
STOP_SKILLOS_DEMO.bat
START_SKILLOS_DEMO.bat
```

### 端口被占用

启动脚本会从 8001 和 5174 开始自动找空闲端口。实际端口记录在：

```text
skillos-one-click-launcher\runtime\skillos-demo.pids.json
```

### npm 参数报 Unused args

正确命令是：

```powershell
npm run dev -- --host 127.0.0.1 --port 5174
```

不要写成：

```powershell
npm run dev -- --host 127.0.0.1 5174
```

### 看到 deprecation 或 CRLF warning

这类 warning 表示依赖库或 Git 换行风格提醒，不等于功能失败。当前关键测试通过即可。

### RESTORE 脚本导入后有同名 S2 和 S3

这是正常的 harness 证据：原始 S2 Draft 被保留，修复后的版本进入 S3。恢复脚本会优先识别 S3，不会重复制造新版本。

## 11. 演示时的口径边界

可以说：

- 现在系统已经能从五类输入生成 S1 Candidate Skill。
- Document 已经加入 Ctx2Skill-lite challenge/rubric/judge/proposer 证据。
- Past Skills 能映射到 SkillX-style atomic/functional/strategic 分层。
- Harness loop 能把 S2 Draft 执行、验证、修复并推进到 S3。
- Graph 同时支持 Skill-only 图、异构 provenance 图和投影视图。

不要说：

- 已经完整复现 Ctx2Skill 的大规模 self-play 训练。
- 已经完整复现 SkillX 的自动 SkillKB 构建算法。
- 已经完成 WebXSkill 真实浏览器环境 benchmark。
- `similar_to` 是强依赖关系。
- memory backend 的导入结果是永久存储。
