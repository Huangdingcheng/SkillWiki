# Copy this file to config.local.ps1 and fill local values.
# config.local.ps1 is ignored by Git and must not be committed.

# Optional: point to a Python executable that already has SkillWiki backend dependencies.
# $env:SKILLOS_PYTHON = "C:\Python314\python.exe"

# DeepSeek/OpenAI-compatible LLM endpoint. The launcher prompts for these
# values automatically when config.local.ps1 does not exist.
# $env:LLM_API_URL = "https://api.deepseek.com"
# Default demo model identifier.
# $env:LLM_MODEL = "deepseek-flash"
# $env:LLM_API_KEY = "replace-with-your-own-key"

# Per-call output ceiling, not the context window or actual token usage.
# Confirm your provider supports this value; local supported range is 1..128000.
$env:LLM_MAX_TOKENS = "16384"
$env:LLM_TIMEOUT = "120"

# Optional: override default behavior.
# $env:SKILLOS_DEMO_REPOSITORY_BACKEND = "git"
# $env:SKILLOS_DEMO_BACKEND_PORT = "8001"
# $env:SKILLOS_DEMO_FRONTEND_PORT = "5174"

# Optional: Evaluation dashboard results, generated with the documented helper.
# $env:SKILLWIKI_EVALUATION_RESULTS_DIR = "C:\path\to\evaluation-results"
