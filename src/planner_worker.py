"""Disposable SDK process; only structured output is sent back over stdout."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys
import time
from typing import Literal

from .runtime import ReadOnlyTools
from .redaction import ObservationFilter


def run_sdk(context, *, sdk=None):
    if sdk is None:
        import openhands.sdk as sdk
    from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor, register_tool

    result = {}
    tools = ReadOnlyTools(context['workspace'], observation_filter=ObservationFilter(
        context.get('_observation_fingerprints', ())))

    class InspectionAction(Action):
        operation: Literal['list', 'read', 'search', 'submit']
        path: str = '.'
        query: str = ''
        observations: dict | None = None

    class InspectionObservation(Observation):
        pass

    class InspectionExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):
            try:
                if action.operation == 'submit':
                    if not isinstance(action.observations, dict):
                        raise ValueError('Submit requires structured observations')
                    result.clear()
                    result.update(action.observations)
                    return InspectionObservation.from_text('Structured observations received. Finish the conversation.')
                return InspectionObservation.from_text(tools.call(action.operation, action.path, action.query))
            except (OSError, ValueError):
                return InspectionObservation.from_text('Invalid or inaccessible read-only operation', is_error=True)

    class InspectionTool(ToolDefinition):
        @classmethod
        def create(cls, conv_state=None, **params):
            return [cls(description='Read-only list/read/search inside source. Submit the final structured '
                        'observations using operation=submit and observations=<object>. No commands or edits.',
                        action_type=InspectionAction, observation_type=InspectionObservation,
                        executor=InspectionExecutor())]

    register_tool(InspectionTool.name, InspectionTool)
    llm = sdk.LLM(**context['model_options'])
    def clip_request(_event=None):
        remaining = (context['deadline'] - time.monotonic()) if context.get('deadline') is not None else float('inf')
        if remaining <= 0:
            raise TimeoutError('Role deadline expired')
        timeout = int(min(context['model_options']['timeout'], remaining))
        if timeout < 1:
            raise TimeoutError('Insufficient remaining budget for a bounded model request')
        llm.timeout = timeout
    agent = sdk.Agent(llm=llm, tools=[sdk.Tool(name=InspectionTool.name)])
    # SDK events and conversations may contain repository secrets. Keep persistence
    # in memory; the coordinator retains only redacted structured observations.
    sdk_workspace = Path(context['artifacts']) / 'sdk-workspace'
    sdk_workspace.mkdir(exist_ok=True)
    conversation = sdk.Conversation(agent=agent, workspace=str(sdk_workspace),
                                    max_iteration_per_run=context['max_iterations'], callbacks=[clip_request])
    try:
        clip_request()
        conversation.send_message(context['prompt'])
        conversation.run()
        status = conversation.state.execution_status
        if getattr(status, 'value', status) != 'finished':
            raise ValueError('Read-only role did not finish; submitted observations cannot authorize approval')
    finally:
        conversation.close()
    if not result:
        raise ValueError('Read-only role finished without structured observations')
    return result


def main():
    context = json.loads(sys.stdin.read())
    # SDK diagnostics cannot accidentally become protocol output or stored secrets.
    import os
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = run_sdk(context)
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
