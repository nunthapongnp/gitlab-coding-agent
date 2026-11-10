# Coding Agent

This project turns a GitLab Issue into a reviewed execution plan and implements an approved plan in the Issue's own repository.

## Language

**Execution Plan**:
A complete, versioned description of the requirement, affected modules, tasks, dependencies, acceptance criteria, and intended verification for one Issue.
_Avoid_: Conversation history, agent memory

**Approved Plan**:
The latest published Execution Plan, provided it is executable and its Issue carries `agent::approved` with no conflicting agent status label. Regenerating the plan (a new `agent::plan` request together with one prior review/approval state) invalidates a prior approval: the Issue returns to `agent::needs-review` and must be approved again.
_Avoid_: Approved conversation, approved workspace

**Task**:
A unit of work within one Execution Plan, with a purpose, dependencies, acceptance criteria, and verification expectations. Task identity belongs to that plan version.
_Avoid_: Run, iteration

**Execute Run**:
One attempt to implement an Approved Plan after execution preflight succeeds. A new run starts independently of earlier runs.
_Avoid_: Plan version, resumed conversation

**Product Change**:
A change to the Issue's codebase needed to satisfy an approved Task, including relevant tests and explicitly scoped configuration or schema files.
_Avoid_: Agent artifact

**Agent Artifact**:
Material produced for the agent's own operation or reporting, such as memory, summaries, logs, and execution reports. It is separate from a Product Change.
_Avoid_: Product output, implementation file

**Execution Report**:
The final account of one Execute Run: task outcomes, verification evidence, commits, push result, and next action.
_Avoid_: Plan, progress comment
