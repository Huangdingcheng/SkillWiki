# SkillOS 使用指南

## 快速开始

### 1. 安装依赖

```bash
cd skillos
pip install -r requirements.txt
```

### 2. 初始化配置

使用命令行参数提供 API key：

```bash
# 基础初始化（使用默认配置）
python -m skillos.cli init --api-key "your_api_key_here"

# 自定义 API URL 和模型
python -m skillos.cli init \
  --api-key "your_api_key_here" \
  --api-url "https://api.deepseek.com" \
  --model "deepseek-v4-pro"

# 使用自定义配置文件
python -m skillos.cli init \
  --api-key "your_api_key_here" \
  --config "custom_config.yaml"
```

### Runtime benchmark

Run the formal runtime evaluation suite and print scores in the terminal. Scoring details are documented in `../docs/runtime-benchmark.md`:

```bash
python -m skillos.cli benchmark-runtime --api-key "YOUR_DEEPSEEK_API_KEY"
```

API key fill location:
- Command line: replace `YOUR_DEEPSEEK_API_KEY` in the command above.
- CI or local shell: provide the same value through your secret manager or environment, then pass it as `--api-key`.

Default DeepSeek settings:

```yaml
llm:
  api_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
```
### 3. 测试配置

```bash
python -m skillos.cli test-config --api-key "your_api_key_here"
```

### 4. 获取 Agent 配置

```bash
python -m skillos.cli get-agent-config \
  --api-key "your_api_key_here" \
  --agent-type "trajectory_parser"
```

---

## 配置优先级

配置值的优先级（从高到低）：

1. **命令行参数** - 最高优先级
   ```bash
   --api-key "key" --model "deepseek-v4-pro"
   ```

2. **环境变量** - 中等优先级
   ```bash
   export LLM_MODEL=deepseek-v4-pro
   ```

3. **配置文件** - 低优先级
   ```yaml
   llm:
     model: "deepseek-v4-pro"
   ```

4. **默认值** - 最低优先级
   ```python
   model: str = Field(default="deepseek-v4-pro")
   ```

---

## 命令行参数详解

### init 命令

初始化 SkillOS 配置

```bash
python -m skillos.cli init [OPTIONS]
```

**必需参数：**
- `--api-key TEXT` - LLM API key（必须提供）

**可选参数：**
- `--api-url TEXT` - LLM API URL（默认：https://api.deepseek.com）
- `--model TEXT` - LLM 模型名称（默认：deepseek-v4-pro）
- `--config TEXT` - 配置文件路径（默认：config.yaml）

**示例：**
```bash
python -m skillos.cli init --api-key "sk_test_123456"
```

### test-config 命令

测试配置是否有效

```bash
python -m skillos.cli test-config [OPTIONS]
```

**必需参数：**
- `--api-key TEXT` - LLM API key

**可选参数：**
- `--config TEXT` - 配置文件路径（默认：config.yaml）

**示例：**
```bash
python -m skillos.cli test-config --api-key "sk_test_123456"
```

### get-agent-config 命令

获取特定 Agent 的配置

```bash
python -m skillos.cli get-agent-config [OPTIONS]
```

**必需参数：**
- `--api-key TEXT` - LLM API key
- `--agent-type TEXT` - Agent 类型

**可选参数：**
- `--config TEXT` - 配置文件路径（默认：config.yaml）

**示例：**
```bash
python -m skillos.cli get-agent-config \
  --api-key "sk_test_123456" \
  --agent-type "trajectory_parser"
```

---

## 在 Python 代码中使用

### 基础用法

```python
from skillos.config import ConfigManager

# 创建配置管理器（需要提供 API key）
cli_args = {"api_key": "your_api_key_here"}
config_manager = ConfigManager("config.yaml", cli_args)

# 获取全局 LLM 配置
llm_config = config_manager.get_global_llm_config()
print(f"API URL: {llm_config.api_url}")
print(f"Model: {llm_config.model}")
```

### 获取 Agent 特定配置

```python
# 获取 trajectory_parser 的配置
agent_config = config_manager.get_agent_llm_config("trajectory_parser")
print(f"Temperature: {agent_config.temperature}")
print(f"Max Tokens: {agent_config.max_tokens}")
```

### 设置 Agent 配置

```python
from skillos.config import LLMConfig

# 为特定 Agent 设置配置
new_config = LLMConfig(
    api_url="https://api.deepseek.com",
    model="deepseek-v4-pro",
    api_key="your_api_key_here",
    temperature=0.9,
    max_tokens=5000
)
config_manager.set_agent_llm_config("custom_agent", new_config)
```

### 获取其他配置

```python
# 获取数据库配置
db_config = config_manager.get_database_config()
print(f"PostgreSQL Host: {db_config.postgres_host}")

# 获取日志配置
log_config = config_manager.get_logging_config()
print(f"Log Level: {log_config.level}")

# 获取全局配置
global_config = config_manager.get_global_config()
print(f"Environment: {global_config.environment}")
```

---

## 配置文件示例

### config.yaml

```yaml
# 全局 LLM 配置
llm:
  api_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  # api_key: 通过命令行参数 --api-key 提供
  temperature: 0.7
  max_tokens: 2000
  timeout: 30
  retry_count: 3

# 数据库配置
database:
  postgres_host: "localhost"
  postgres_port: 5432
  postgres_database: "skillos"
  postgres_user: "postgres"
  postgres_password: "your_password"

  neo4j_uri: "bolt://localhost:7687"
  neo4j_user: "neo4j"
  neo4j_password: "your_password"

  mongodb_uri: "mongodb://localhost:27017"
  mongodb_database: "skillos"

  redis_host: "localhost"
  redis_port: 6379
  redis_db: 0

# 日志配置
logging:
  level: "INFO"
  format: "json"
  file: "logs/skillos.log"
  max_size: "100MB"
  backup_count: 10

# 全局设置
debug: false
environment: "development"

# Agent 级别的 LLM 配置
agents:
  trajectory_parser:
    temperature: 0.5
    max_tokens: 3000

  skill_generator:
    temperature: 0.8
    max_tokens: 4000

  validator:
    temperature: 0.3
    max_tokens: 2000
```

---

## 常见问题

### Q1: 为什么 API key 不能在配置文件中？

**A:** 为了安全性。API key 是敏感信息，不应该存储在版本控制系统中。通过命令行参数传递可以：
- 避免意外提交到 Git
- 支持不同环境使用不同的 key
- 便于 CI/CD 集成

### Q2: 如何在 CI/CD 中使用？

**A:** 在 CI/CD 环境中，通过环境变量或 secrets 管理 API key：

```bash
# GitHub Actions 示例
python -m skillos.cli init --api-key "${{ secrets.LLM_API_KEY }}"
```

### Q3: 如何为不同的 Agent 使用不同的模型？

**A:** 在 config.yaml 中为 Agent 配置不同的模型：

```yaml
agents:
  trajectory_parser:
    model: "deepseek-v4-pro"
    temperature: 0.5

  skill_generator:
    model: "deepseek-v4-pro"
    temperature: 0.8
```

### Q4: 如何重新加载配置？

**A:** 使用 `reload()` 方法：

```python
config_manager.reload()
```

---

## 下一步

1. 运行 `python -m skillos.cli init --api-key "your_key"`
2. 运行 `python -m skillos.cli test-config --api-key "your_key"`
3. 查看 `logs/skillos.log` 确认日志正常工作
4. 开始实现 Phase 1 的数据模型
