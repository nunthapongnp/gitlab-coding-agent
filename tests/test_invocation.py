from io import StringIO

import pytest

from main import main


CONFIG = {
    "GITLAB_TOKEN": "private-gitlab-value",
    "LLM_BASE_URL": "https://proxy.example.test/v1",
    "LLM_MODEL": "team-model",
    "LLM_API_KEY": "private-model-value",
}


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--project", "team/demo", "--issue", "1"],
        ["explore", "--project", "team/demo"],
        ["explore", "--project", "team/demo", "--issue", "0"],
        ["explore", "--project", " ", "--issue", "1"],
        ["implement", "--project", "team/demo", "--issue", "1"],
        ["implement", "--project", "team/demo", "--issue", "1", "--mr", "-2"],
        ["explore", "--project", "team/demo", "--issue", "1", "--mr", "2"],
    ],
)
def test_invalid_invocation_stops_before_startup(args):
    output = StringIO()
    assert main(args, environ={}, stderr=output) == 2
    assert "error" in output.getvalue().lower()
    assert "LLM_BASE_URL" not in output.getvalue()


@pytest.mark.parametrize("missing", ["GITLAB_TOKEN", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"])
def test_missing_configuration_is_actionable_and_never_prints_secrets(missing):
    config = {key: value for key, value in CONFIG.items() if key != missing}
    output = StringIO()
    assert main(["explore", "--project", "team/demo", "--issue", "4"],
                environ=config, stderr=output) == 3
    assert "configuration_error" in output.getvalue()
    assert missing in output.getvalue()
    assert "private-gitlab-value" not in output.getvalue()
    assert "private-model-value" not in output.getvalue()


@pytest.mark.parametrize("operation,project,issue,mr", [
    ("automatic", "team/demo", 1, None), ("explore", "", 1, None),
    ("explore", "team/demo", 0, None), ("explore", "team/demo", True, None),
    ("implement", "team/demo", 1, None), ("explore", "team/demo", 1, 2),
])
def test_application_validates_invocation_before_loading_configuration(operation, project, issue, mr):
    from src.workflow import Application, OperationInput, OutcomeStatus

    result = Application(environ={}).invoke(OperationInput(operation, project, issue, mr))
    assert result.status == OutcomeStatus.INVALID_INVOCATION
    assert result.exit_code == 2


@pytest.mark.parametrize("setting,value", [
    ("MAX_FIX_ATTEMPTS", "-1"), ("MAX_FIX_ATTEMPTS", "private-model-value"),
    ("EXECUTION_MAX_RUNTIME_SECONDS", "0"), ("OPENHANDS_MAX_ITERATIONS", "x"),
])
def test_invalid_limits_produce_configuration_errors_without_echoing_values(setting, value):
    output = StringIO()
    assert main(["explore", "--project", "team/demo", "--issue", "4"],
                environ={**CONFIG, setting: value}, stderr=output) == 3
    assert setting in output.getvalue()
    assert "private-model-value" not in output.getvalue()
