"""One process and in-memory SDK conversation per accepted Execute Run."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
from typing import Literal

from .coding import CodingTools
from .runtime import LocalRuntime


class CoderSession:
    def __init__(self, context):
        import openhands.sdk as sdk
        from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor, register_tool

        self.result = {}
        tools = CodingTools(context['workspace'], agreement=context['agreement'], issue=context['issue'],
                            runtime=LocalRuntime(), timeout=context['model_options']['timeout'])
        self.tools = tools
        result = self.result

        class CodingAction(Action):
            operation: Literal['list', 'read', 'search', 'write', 'delete', 'command', 'submit']
            path: str = '.'
            query: str = ''
            content: str = ''
            reason: str = ''
            acceptance_ids: list[str] = []
            approval_reference: str = ''
            argv: list[str] | None = None
            outcome: dict | None = None

        class CodingObservation(Observation):
            pass

        class CodingExecutor(ToolExecutor):
            def __call__(self, action, conversation=None):
                try:
                    if action.operation == 'submit':
                        if not isinstance(action.outcome, dict):
                            raise ValueError('Submit requires a structured outcome')
                        result.clear()
                        result.update(action.outcome)
                        return CodingObservation.from_text('Outcome received. Finish the conversation.')
                    output = tools.call(action.operation, action.path, action.query,
                        content=action.content, reason=action.reason, acceptance_ids=action.acceptance_ids,
                        approval_reference=action.approval_reference, argv=action.argv)
                    return CodingObservation.from_text(output)
                except (OSError, ValueError) as exc:
                    return CodingObservation.from_text(str(exc), is_error=True)

        class CodingTool(ToolDefinition):
            @classmethod
            def create(cls, conv_state=None, **params):
                return [cls(description='Read source; write/delete relevant scoped files with reason and acceptance_ids. '
                    'Configuration/dependency/schema changes need approval_reference quoted from the Issue/Approved Plan. '
                    'Run explicit repository argv in path as cwd; normal local dependency setup allowed, '
                    'no host upgrades or coordinator/publication commands. Submit outcome={summary,blockers}. '
                    'Agent Artifacts must stay outside source; no plan changes or interactive approval.',
                    action_type=CodingAction, observation_type=CodingObservation, executor=CodingExecutor())]

        register_tool(CodingTool.name, CodingTool)
        agent = sdk.Agent(llm=sdk.LLM(**context['model_options']), tools=[sdk.Tool(name=CodingTool.name)])
        workspace = Path(context['artifacts']) / 'coder-sdk-workspace'
        workspace.mkdir(exist_ok=True)
        self.conversation = sdk.Conversation(agent=agent, workspace=str(workspace),
            max_iteration_per_run=context['max_iterations'])

    def run(self, prompt):
        self.result.clear()
        self.conversation.send_message(prompt)
        self.conversation.run()
        if not self.result:
            raise ValueError('Coder finished without structured output')
        return {**self.result, 'changes': list(self.tools.changes.values()), 'commands': self.tools.commands,
                'tool_blockers': self.tools.blockers}

    def close(self):
        self.conversation.close()


def main():
    session = None
    try:
        for line in sys.stdin:
            context = json.loads(line)
            # Keep SDK diagnostics and source/model secrets out of the protocol and disk.
            with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                try:
                    if session is None:
                        session = CoderSession(context)
                    message = {'result': session.run(context['prompt'])}
                except Exception:
                    message = {'error': 'Coder SDK execution failed'}
            sys.stdout.write(json.dumps(message) + '\n')
            sys.stdout.flush()
    finally:
        if session:
            session.close()


if __name__ == '__main__':
    main()
