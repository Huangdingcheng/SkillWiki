# SkillOS Phase 0 - 直接执行命令

## 🚀 快速启动（推荐）

### Linux/Mac

```bash
cd E:/NLP/skill\ wiki/skillos
chmod +x quickstart.sh
./quickstart.sh
```

### Windows

```bash
cd E:\NLP\skill wiki\skillos
quickstart.bat
```

---

## 📋 手动步骤（如果不用快速启动脚本）

### 1. 进入项目目录

```bash
cd E:/NLP/skill\ wiki/skillos  # Linux/Mac
# 或
cd E:\NLP\skill wiki\skillos   # Windows
```

### 2. 创建虚拟环境

```bash
python -m venv venv
```

### 3. 激活虚拟环境

**Linux/Mac：**
```bash
source venv/bin/activate
```

**Windows：**
```bash
venv\Scripts\activate
```

### 4. 安装依赖

```bash
pip install -r requirements.txt
```

### 5. 初始化配置（需要提供 API key）

```bash
python -m skillos.cli init --api-key "your_api_key_here"
```

### 6. 测试配置

```bash
python -m skillos.cli test-config --api-key "your_api_key_here"
```

### 7. 获取 Agent 配置

```bash
python -m skillos.cli get-agent-config \
  --api-key "your_api_key_here" \
  --agent-type "trajectory_parser"
```

---

## 🔍 验证安装

### 检查项目结构

```bash
ls -la skillos/  # Linux/Mac
# 或
dir skillos\     # Windows
```

### 查看配置文件

```bash
cat config.yaml  # Linux/Mac
# 或
type config.yaml # Windows
```

### 查看日志

```bash
tail -f logs/skillos.log  # Linux/Mac
# 或
type logs\skillos.log     # Windows
```

### 运行测试

```bash
pytest tests/
```

---

## 📝 配置示例

### 使用自定义 API URL 和模型

```bash
python -m skillos.cli init \
  --api-key "your_key" \
  --api-url "https://yunwu.ai" \
  --model "gpt-5.4-nano"
```

### 使用自定义配置文件

```bash
python -m skillos.cli init \
  --api-key "your_key" \
  --config "custom_config.yaml"
```

### 设置温度和 max_tokens

```bash
python -m skillos.cli init \
  --api-key "your_key" \
  --temperature 0.8 \
  --max-tokens 4000
```

---

## 🎯 完整工作流

### 第一次使用

```bash
# 1. 进入项目目录
cd skillos

# 2. 快速启动（自动完成以下步骤）
./quickstart.sh  # Linux/Mac
# 或
quickstart.bat   # Windows

# 按提示输入 API key
```

### 后续使用

```bash
# 1. 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 2. 运行命令
python -m skillos.cli test-config --api-key "your_key"
```

---

## 📊 文件清单

已生成的文件：

```
skillos/
├── skillos/
│   ├── __init__.py
│   ├── cli.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── llm_config.py
│   │   └── config_manager.py
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       └── validators.py
├── tests/
│   └── test_config.py
├── config.yaml
├── .env.example
├── requirements.txt
├── quickstart.sh
├── quickstart.bat
├── README.md
├── USAGE_GUIDE.md
└── PHASE_0_SUMMARY.md
```

---

## ✅ 验证清单

运行以下命令验证一切正常：

```bash
# 1. 检查 Python 版本
python --version

# 2. 创建虚拟环境
python -m venv venv

# 3. 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 4. 安装依赖
pip install -r requirements.txt

# 5. 初始化配置
python -m skillos.cli init --api-key "test_key"

# 6. 测试配置
python -m skillos.cli test-config --api-key "test_key"

# 7. 查看日志
cat logs/skillos.log  # Linux/Mac
# 或
type logs\skillos.log # Windows
```

---

## 🎉 完成！

Phase 0 已完成。现在可以：

1. ✅ 使用 CLI 工具管理配置
2. ✅ 为不同 Agent 配置不同的模型
3. ✅ 通过命令行参数安全地传递 API key
4. ✅ 查看日志和验证配置

**下一步：Phase 1 - 核心数据模型和存储层**

