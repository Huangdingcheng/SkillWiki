@echo off
REM SkillOS 快速启动脚本 (Windows)

setlocal enabledelayedexpansion

echo.
echo ==========================================
echo SkillOS 快速启动
echo ==========================================
echo.

REM 检查 Python 版本
echo 检查 Python 版本...
python --version
echo.

REM 创建虚拟环境
if not exist "venv" (
    echo 创建虚拟环境...
    python -m venv venv
    echo 虚拟环境已创建
) else (
    echo 虚拟环境已存在
)
echo.

REM 激活虚拟环境
echo 激活虚拟环境...
call venv\Scripts\activate.bat
echo 虚拟环境已激活
echo.

REM 安装依赖
echo 安装依赖...
pip install -q -r requirements.txt
echo 依赖已安装
echo.

REM 创建日志目录
echo 创建日志目录...
if not exist "logs" mkdir logs
echo 日志目录已创建
echo.

REM 提示用户输入 API key
echo ==========================================
echo 配置 SkillOS
echo ==========================================
echo.
set /p api_key="请输入 LLM API key: "

if "!api_key!"=="" (
    echo 错误: API key 不能为空
    exit /b 1
)

REM 初始化配置
echo.
echo 初始化配置...
python -m skillos.cli init --api-key "!api_key!"
echo.

REM 测试配置
echo 测试配置...
python -m skillos.cli test-config --api-key "!api_key!"
echo.

echo ==========================================
echo SkillOS 初始化完成！
echo ==========================================
echo.
echo 下一步：
echo 1. 查看配置: type config.yaml
echo 2. 查看日志: type logs\skillos.log
echo 3. 开始开发: python -m pytest tests\
echo.
pause
