# SkillWiki 中文使用指南

本指南说明如何启动和使用当前产品。完整 CLI 参数、请求格式与故障处理见
[英文用户指南](USER_GUIDE.md)，安装步骤见 [README](README.md#quick-start)。

## 1. 安装并启动

需要 Git、Python 3.10+，以及 Node.js 20.19+ 或 22.12+。先安装依赖；
Windows PowerShell 从仓库根目录执行：

```powershell
python -m venv skillwiki/.venv
& ./skillwiki/.venv/Scripts/python.exe -m pip install -e './skillwiki[api]'
Set-Location skillwiki-frontend
npm ci
Set-Location ..
```

然后双击仓库根目录的 `START_SKILLWIKI_DEMO.bat`。首次运行会询问模型地址、
模型名称和密钥。请填写自己账号可用的模型，不要提交密钥。
如果启动器选择了不正确的 Python，可以在本地 `skillwiki-launcher/config.local.ps1`
中设置 `$env:SKILLOS_PYTHON` 为刚才虚拟环境中 python.exe 的绝对路径。

默认前端地址是 <http://127.0.0.1:5174/wiki>，后端是
<http://127.0.0.1:8001>；端口被占用时，以启动器打印的地址为准。
停止服务使用 `STOP_SKILLWIKI_DEMO.bat`。
手动启动和 Linux/macOS 的命令见 README，不需要重复启动两套服务。

新仓库默认没有技能，导入后才会出现内容。Git 存储会保留技能和版本，正常重启后
无需重新导入。`RESTORE_SKILLWIKI_DEMO_STATE.bat` 是主动添加演示材料的工具，
可能执行验证或调用模型，不是恢复日常数据的必需步骤，也不是论文实验复现。

## 输出 Token 上限与回答截断

API 服务的单次模型回答默认上限为 **16,384 Token**，命令行启动和 Windows
一键启动使用同一默认值。如需调整，在启动后端的 PowerShell 中设置，或写入
一键启动器的本地 `skillwiki-launcher/config.local.ps1`：

```powershell
$env:LLM_MAX_TOKENS = "16384"
```

修改后重启后端。已有本地配置优先于新默认值；如果以前显式填过 2000，需同步
修改。手动启动不会自动加载 `.env.example` 或复制出的 `.env`，须将变量导入
启动终端；Docker Compose 则会把 shell/仓库根目录 `.env` 的此变量传入后端。

这是“每次回答最多可以写多长”，不是每次固定消耗量，也不是上下文容量。
支持百万 Token 上下文的模型，不代表可以一次输出百万 Token。产品配置当前接受
1–128,000，服务商或模型可能限制得更低；100 万会被本地校验拒绝。
独立预算机制的输出上限仍然有效，不会被这个设置绕过。

导入包含多个模型阶段，页面中的管线累计 Token 可能大于单次回答上限。
累计用量不等于费用，费用以模型实际输入/输出、服务商价格及账单为准；提高上限
也可能增加预算预留或允许更长、更贵的回答。

遇到 `ctx2skill: incomplete response (length)`，表示模型回答被截断。检查模型
支持的最大输出长度以及预算限制，调整到支持的值，重启后手动补试一次。
不要将截断内容保存成通过的技能。16,384 也不是任意材料不截断的保证；本调整
不会打开自动重试。

## 2. 按流程使用
单次模型请求等待时间由 `LLM_TIMEOUT` 控制，API 默认 **120 秒**，接受 1–600。
PowerShell 设置 `$env:LLM_TIMEOUT = "120"`（或写入启动器本地配置），Bash
设置 `export LLM_TIMEOUT=120`，然后重启。导入有多个模型阶段，总耗时可能超过
120 秒。提高输出 Token 上限不会自动延长等待时间；超时也不表示服务商没有计费，
应先查看记录再手动决定重试。


| 过程 | 前端怎么操作 | 对应 CLI |
|---|---|---|
| 导入材料 | Knowledge Import 选择来源、输入材料、查看并编辑候选、审核和创建 | `skillwiki ingest run`、`ingest audit-candidate`、`ingest create-candidate` |
| 查看技能 | Skill Wiki 打开详情，检查 ID、版本、状态、输入输出和来源 | `skillwiki skill list`、`skill get ID --full` |
| 验证技能 | 在 Harness Verification 选择 S2 技能和 Local，设置次数后运行 | `skillwiki verify ID --test-cases cases.json --json` |
| 发布和执行 | 检查发布资格，再在现有发布入口操作；Agent Execution 执行任务 | `skillwiki lifecycle release ID --file release-evidence.json`、`skill exec`、`run` |
| 版本和恢复 | Version Control 创建版本、保存快照、比较差异或创建恢复草稿 | `skillwiki lifecycle new-version`、`snapshot`、`diff`、`rollback` |
| 查看图和收据 | Knowledge Graph 切换视图，点节点查看来源、版本和验证结果 | `skillwiki graph show ID --view provenance --json`、`execution history` |
| 健康与维护 | Evolution 查看健康、提案，明确选择是否执行修复 | `skillwiki health`、`proposal list`、`repair`、`evolve` |

表中 `ID` 是实际技能 ID；具体子命令的参数见 `skillwiki <命令> --help`。
CLI 全局参数应放在子命令前，例如：

```powershell
& ./skillwiki/.venv/Scripts/skillwiki.exe --api-url http://127.0.0.1:8001 skill list
```

首次验证建议明确选择 **Local、1 次尝试、关闭自动修复、关闭自动晋级**，先查看
真实结果。网页验证使用技能已保存的评价用例；CLI 可以额外传入测试文件。
导入、审核、执行和修复中的模型操作可能收费；本地浏览已有数据不需要调用模型。

网页发布使用后端已保存证据的发布入口；CLI 的 `lifecycle release` 则提交完整
证据包，两者请求格式不同。网页没有为每一个研究用的证据字段增加编辑器。
需要精确提交这些内容时，用 CLI/API，不能复制旧技能的通过结论给新技能。

## 3. 图里展示的是什么

- **Skill-only**：技能和真实依赖、组合、演化关系，可同时出现多个历史版本。
- **Provenance**：来源、技能、版本，以及已有执行和验证记录。导入审核通过不等于任务完成。
- **Version impact**：由版本、共同来源和验证路径投影的关联；关联分数不是成功概率。

点开详情，核对技能 ID、版本、收据引用和内容绑定。图从仓库和已保存记录生成，
不会因为画出一条验证边就自动给技能发布资格。
新版和恢复草稿都是新的 S2 身份，需要重新验证；旧版记录继续保留。

## 4. 遇到问题与复现范围

一直加载或接口报错时，先看后端 `/health` 是否响应，再看前端
`/api/v1/skills?limit=1` 能否返回数据。手动启动必须在启动 Vite 前设置正确的
`SKILLOS_API_TARGET`。CLI 超时后先检查执行历史或 Harness 收据，避免重复消费。
发布被拒绝时先读拒绝原因，不能把状态改成已发布来跳过验证。

启动器数据在 `skillwiki-launcher/runtime/`，不要上传本地配置、密钥或自己的运行数据。
备份应同时保留技能存储、治理快照、执行记录和 Harness 收据，具体路径见英文指南。

以上可以复现产品操作。[公开实验材料与结果](EXPERIMENTS.zh-CN.md)提供任务输入、预期答案、来源及结果表，
可用 `python public-experiments/verify.py` 离线核对。完整重跑还需要归档的冻结代码、候选、原始回答、评分和
分析脚本；完整论文复现包尚未包含在这次产品 PR 中。演示材料不是论文数据，重新调用
模型得到的回答也不保证和历史逐字一致。当前依赖仍有已记录的安全告警，尚未在本轮升级。
