from __future__ import annotations

import os
from pathlib import Path

from .config import Settings

PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "base_prompt.md"

def make_agent_prompt(
    issue: dict,
    project: dict,
    branch: str,
    *,
    repair_context: str | None = None,
    repair_attempt: int | None = None,
) -> str:
    title = str(issue.get("title") or "Untitled issue")
    description = str(issue.get("description") or "No description provided.")
    labels = issue.get("labels") or []
    labels_text = ", ".join(str(label) for label in labels) or "none"
    issue_url = str(issue.get("web_url") or "(not provided)")
    project_name = str(project.get("path_with_namespace") or project.get("name") or "repository")

    read_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    base_prompt = read_prompt.format(
            project_name=project_name,
            title=title,
            issue_url=issue_url,
            labels_text=labels_text,
            description=description,
            branch=branch,
        )
        
    if not repair_context:
        return base_prompt
    return f"""{base_prompt}

REPAIR CYCLE {repair_attempt or 1}
The orchestrator ran the validation gates after your previous changes. They did not
all pass. Diagnose the output below, fix the underlying implementation issue, and
avoid changing tests/build configuration merely to bypass the failure. Preserve
existing behavior and add/update tests when appropriate. Do not report success;
the orchestrator will rerun the same checks independently.

--- BUILD / TEST FAILURE REPORT ---
{repair_context}
--- END FAILURE REPORT ---
""".strip()


def run_openhands(
    repo_dir: Path,
    issue: dict,
    project: dict,
    branch: str,
    settings: Settings,
    *,
    repair_context: str | None = None,
    repair_attempt: int | None = None,
) -> str:
    try:
        from openhands.sdk import Agent, Conversation, LLM, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.terminal import TerminalTool
        from openhands.tools.task_tracker import TaskTrackerTool
    except ImportError as exc:
        raise RuntimeError(
            "OpenHands SDK is not installed. Run: pip install -r requirements.txt"
        ) from exc

    llm = LLM(
        model=f"openai/{settings.ollama_model}",
        api_key=settings.ollama_api_key,
        base_url=f"{settings.ollama_base_url}/v1",
        timeout=settings.timeout_seconds,
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(repo_dir.resolve()),
        max_iteration_per_run=settings.max_iterations,
    )
    conversation.send_message(
        make_agent_prompt(
            issue, project, branch,
            repair_context=repair_context,
            repair_attempt=repair_attempt,
        )
    )

    hidden = {key: os.environ.pop(key) for key in ("GITLAB_TOKEN", "OLLAMA_API_KEY") if key in os.environ}
    try:
        conversation.run()
    finally:
        os.environ.update(hidden)
    summary_file = repo_dir / ".agent-summary.md"
    if summary_file.is_file():
        return summary_file.read_text(encoding="utf-8")
    return "OpenHands conversation completed. No .agent-summary.md was generated; review the Git diff and agent logs."
