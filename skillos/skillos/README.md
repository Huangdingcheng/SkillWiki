# SkillOS - A Skill-Centric Operating System for Self-Evolving Agents

## 概述

SkillOS 是一个以 Skill 为中心的智能体系统，将 Skill 建模为具备版本、审计、图结构关系和自演化能力的知识对象。

### 核心特性

- **可生成**：从任务、轨迹、文档自动生成 Skill
- **可验证**：通过测试、审计、执行验证 Skill 质量
- **可组合**：支持 Skill 的横向选择和纵向展开
- **可版本化**：Git 风格的版本控制和治理
- **可演化**：自动修复、合并、拆分、淘汰

## 快速开始

### 前置要求

- Python 3.10+
- pip 或 conda

### 安装

#### Linux/Mac

```bash
# 克隆仓库
git clone <repo_url>
cd skillos

# 运行快速启动脚本
chmod +x quickstart.sh
./quickstart.sh
```

#### Windows

```bash
# 克隆仓库
git clone <repo_url>
cd skillos

# 运行快速启动脚本
quickstart.bat
```

### 手动安装

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 初始化配置（需要提供 API key）
python -m skillos.cli init --api-key "your_api_key_here"

# 测试配置
python -m skillos.cli test-config --api-key "your_api_key_here"
```

## 配置

### 命令行参数

所有命令都支持以下参数：

```bash
--api-key TEXT          # LLM API key（必须）
--api-url TEXT          # LLM API URL（默认：https://yunwu.ai）
--model TEXT            # LLM 模型名称（默认：gpt-5.4-nano）
--config TEXT           # 配置文件路径（默认：config.yaml）
```

### 配置文件

编辑 `config.yaml` 来配置：

- LLM 参数（温度、max_tokens 等）
- 数据库连接
- 日志设置
- Agent 级别的配置

### 环境变量

支持以下环境变量：

```bash
LLM_API_URL             # LLM API URL
LLM_MODEL               # LLM 模型名称
LLM_TEMPERATURE         # 温度参数
LLM_MAX_TOKENS          # 最大 token 数
DB_POSTGRES_HOST        # PostgreSQL 主机
DB_POSTGRES_PASSWORD    # PostgreSQL 密码
```

## 使用

### CLI 命令

#### 初始化

```bash
python -m skillos.cli init --api-key "your_key"
```

#### 测试配置

```bash
python -m skillos.cli test-config --api-key "your_key"
```

#### 获取 Agent 配置

```bash
python -m skillos.cli get-agent-config \
  --api-key "your_key" \
  --agent-type "trajectory_parser"
```

### Python API

```python
from skillos.config import ConfigManager

# 创建配置管理器
cli_args = {"api_key": "your_api_key_here"}
config_manager = ConfigManager("config.yaml", cli_args)

# 获取全局 LLM 配置
llm_config = config_manager.get_global_llm_config()

# 获取 Agent 特定配置
agent_config = config_manager.get_agent_llm_config("trajectory_parser")

# 获取数据库配置
db_config = config_manager.get_database_config()
```

## 项目结构

```
skillos/
├── skillos/
│   ├── __init__.py
│   ├── cli.py                     # CLI 工具
│   ├── config/                    # 配置模块
│   │   ├── __init__.py
│   │   ├── llm_config.py          # LLM 配置定义
│   │   └── config_manager.py      # 配置管理器
│   ├── utils/                     # 工具模块
│   │   ├── __init__.py
│   │   ├── logger.py              # 日志工具
│   │   └── validators.py          # 验证工具
│   ├── models/                    # 数据模型（待实现）
│   ├── storage/                   # 存储层（待实现）
│   ├── layers/                    # 五层架构（待实现）
│   └── api/                       # API 层（待实现）
├── tests/                         # 测试
├── config.yaml                    # 配置文件
├── .env.example                   # 环境变量示例
├── requirements.txt               # 依赖
├── quickstart.sh                  # Linux/Mac 快速启动脚本
├── quickstart.bat                 # Windows 快速启动脚本
├── USAGE_GUIDE.md                 # 使用指南
└── README.md                      # 本文件
```

## 文档

- [USAGE_GUIDE.md](USAGE_GUIDE.md) - 详细使用指南
- [DESIGN.md](../DESIGN.md) - 系统设计文档
- [DEVELOPMENT.md](../DEVELOPMENT.md) - 开发文档
- [IMPLEMENTATION_ROADMAP.md](../IMPLEMENTATION_ROADMAP.md) - 实现路线图

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_config.py

# 显示覆盖率
pytest --cov=skillos tests/
```

### 代码风格

```bash
# 格式化代码
black skillos/

# 检查代码风格
pylint skillos/

# 类型检查
mypy skillos/
```

## 配置优先级

配置值的优先级（从高到低）：

1. **命令行参数** - 最高优先级
2. **环境变量** - 中等优先级
3. **配置文件** - 低优先级
4. **默认值** - 最低优先级

## 常见问题

### Q: 为什么 API key 必须通过命令行参数提供？

A: 为了安全性。API key 是敏感信息，不应该存储在版本控制系统中。

### Q: 如何在 CI/CD 中使用？

A: 通过环境变量或 secrets 管理 API key：

```bash
python -m skillos.cli init --api-key "${{ secrets.LLM_API_KEY }}"
```

### Q: 如何为不同的 Agent 使用不同的模型？

A: 在 `config.yaml` 中为 Agent 配置不同的模型。

## 许可证

MIT

## 贡献

欢迎提交 Issue 和 Pull Request！

## 联系方式

- 项目主页：[GitHub](https://github.com/skillos/skillos)
- 文档：[Wiki](https://github.com/skillos/skillos/wiki)
