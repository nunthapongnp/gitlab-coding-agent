# Coding Agent

This project turns a GitLab Issue into a reviewed execution plan and implements an approved plan in the Issue's own repository.

## Language

**Execution Plan**:
A single complete, unversioned agreement describing one Issue's requirement, affected modules, implementation checklist, acceptance criteria, and intended verification.
_Avoid_: Conversation history, agent memory

**Approved Plan**:
The single executable Execution Plan for an Issue while that Issue carries `agent::approved` with no conflicting agent status label.
_Avoid_: Approved conversation, approved workspace

**Task**:
A checklist item inside the Execution Plan that connects intended implementation work to acceptance criteria.
_Avoid_: Scheduling unit, publication unit, run, iteration

**Execute Run**:
One attempt to implement the whole Issue's Approved Plan in its explicit human-created MR after execution preflight succeeds. A new run starts independently of earlier runs.
_Avoid_: Per-Task run, resumed conversation

**Product Change**:
A change to the Issue's codebase needed to satisfy its Approved Plan, including relevant tests and explicitly scoped configuration or schema files.
_Avoid_: Agent artifact

**Agent Artifact**:
Material produced for the agent's own operation or reporting, such as memory, summaries, logs, and execution reports. It is separate from a Product Change.
_Avoid_: Product output, implementation file

**Execution Report**:
The final account of one Execute Run: task outcomes, verification evidence, commits, push result, and next action.
_Avoid_: Plan, progress comment
