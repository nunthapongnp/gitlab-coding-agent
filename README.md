# GitLab → LangGraph → OpenHands → Ollama

## Environment

```.env

# Required
GITLAB_URL=https://gitlab.com
GITLAB_TOKEN=glpat-xxxx
GITLAB_GIT_USERNAME=agent-bot # Git HTTPS username

OLLAMA_BASE_URL=https://localhost:11434
OLLAMA_MODEL=ollama/llama3.2:3b
OLLAMA_API_KEY=sk-xxxx

# Optional
OPENHANDS_MAX_ITERATIONS=35 # OpenHands iterations per agent run
OPENHANDS_TIMEOUT_SECONDS=1800 # LLM request timeout

MAX_FIX_ATTEMPTS=3 # Retry Build/Test failed iterations
CHECK_TIMEOUT_SECONDS=1200 # Build/Test timeout

SETUP_COMMAND= # Override install command
BUILD_COMMAND= # Override build command
TEST_COMMAND= # Override unit test command

GIT_AUTHOR_NAME=agent-bot
GIT_AUTHOR_EMAIL=ai@agent.bot
```

## Run on local
```bash
python -m venv .venv

# activate visual env
source .venv/bin/activate # linux
.venv/Scripts/activate.bat # windows

python -m pip install -r requirements.txt

# run script
python main.py --project "my-group/my-project" --issue 12

python main.py --project "echo6604438/my-project" --issue 1
```
