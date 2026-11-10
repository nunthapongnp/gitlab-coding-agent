import os
import sys
from types import SimpleNamespace

from src.config import Settings
from src.openhands_runner import run_openhands


def test_role_adapter_uses_proxy_configuration_without_changing_api_root_or_model_prefix(monkeypatch, tmp_path):
    observed = {}

    def llm(**kwargs):
        observed.update(kwargs)
        return object()

    class Conversation:
        def __init__(self, **kwargs):
            pass

        def send_message(self, message):
            assert "private-key" not in message

        def run(self):
            assert "LLM_API_KEY" not in os.environ
            assert "GITLAB_TOKEN" not in os.environ

    monkeypatch.setitem(sys.modules, "openhands.sdk", SimpleNamespace(
        Agent=lambda **kw: object(), Conversation=Conversation,
        LLM=llm, Tool=lambda **kw: object()))
    for module, symbol in [("file_editor", "FileEditorTool"),
                           ("terminal", "TerminalTool"), ("task_tracker", "TaskTrackerTool")]:
        monkeypatch.setitem(sys.modules, f"openhands.tools.{module}",
                            SimpleNamespace(**{symbol: SimpleNamespace(name=module)}))
    monkeypatch.setenv("LLM_API_KEY", "private-key")
    monkeypatch.setenv("GITLAB_TOKEN", "private-token")
    settings = Settings.from_env(environ={
        "GITLAB_TOKEN": "private-token", "LLM_BASE_URL": "https://proxy.test/custom/v1",
        "LLM_MODEL": "openai/team-alias", "LLM_API_KEY": "private-key",
    })
    run_openhands(tmp_path, {"title": "Example"}, {}, "existing-source", settings)
    assert observed["model"] == "openai/team-alias"
    assert observed["base_url"] == "https://proxy.test/custom/v1"
    assert observed["api_key"] == "private-key"
    assert settings.max_fix_attempts == 3
    assert settings.execution_max_runtime_seconds == 3600
    assert "private-key" not in repr(settings)
    assert os.environ["LLM_API_KEY"] == "private-key"
