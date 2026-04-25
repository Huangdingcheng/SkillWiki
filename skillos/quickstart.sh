#!/bin/bash
# SkillOS 快速启动脚本

set -e

echo "=========================================="
echo "SkillOS 快速启动"
echo "=========================================="
echo ""

# 检查 Python 版本
echo "✓ 检查 Python 版本..."
python_version=$(python --version 2>&1 | awk '{print $2}')
echo "  Python 版本: $python_version"
echo ""

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "✓ 创建虚拟环境..."
    python -m venv venv
    echo "  虚拟环境已创建"
else
    echo "✓ 虚拟环境已存在"
fi
echo ""

# 激活虚拟环境
echo "✓ 激活虚拟环境..."
source venv/bin/activate
echo "  虚拟环境已激活"
echo ""

# 安装依赖
echo "✓ 安装依赖..."
pip install -q -r requirements.txt
echo "  依赖已安装"
echo ""

# 创建日志目录
echo "✓ 创建日志目录..."
mkdir -p logs
echo "  日志目录已创建"
echo ""

# 提示用户输入 API key
echo "=========================================="
echo "配置 SkillOS"
echo "=========================================="
echo ""
read -p "请输入 LLM API key: " api_key

if [ -z "$api_key" ]; then
    echo "✗ API key 不能为空"
    exit 1
fi

# 初始化配置
echo ""
echo "✓ 初始化配置..."
python -m skillos.cli init --api-key "$api_key"
echo ""

# 测试配置
echo "✓ 测试配置..."
python -m skillos.cli test-config --api-key "$api_key"
echo ""

echo "=========================================="
echo "✓ SkillOS 初始化完成！"
echo "=========================================="
echo ""
echo "下一步："
echo "1. 查看配置: cat config.yaml"
echo "2. 查看日志: tail -f logs/skillos.log"
echo "3. 开始开发: python -m pytest tests/"
echo ""
