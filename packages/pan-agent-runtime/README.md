# PanAgent Runtime

<code>pan-agent-runtime</code> is a lightweight, business-agnostic Python agent execution core. It
safely turns "the model asked for a tool call" into "an observable task that can pause and
resume". It isn't tied to FastAPI, a database, a model provider or any business domain, so
PanWatch, BeeCount-Cloud and other projects can reuse it as a dependency.

> Current version: <code>0.1.0</code><br>
> Python: <code>>=3.10</code><br>
> Runtime dependencies: <code>pydantic>=2.0</code>

## Why a separate runtime

Business projects usually run into the same set of agent needs:

- the model needs to call the host project's lookup, write or external service tools;
- write tools must be confirmed by the user first, and continue from where they paused once confirmed;
- the frontend needs to see tokens, tool start/finish and approval requests live;
- a task needs limits on steps, tool calls, per-call timeouts and a total timeout;
- a model occasionally resubmits the same tool call and must be stopped quickly.

None of this depends on the business (stocks, bookkeeping, CRM...), so the runtime implements
it once. The business project only supplies the model adapter, tool implementations,
permission policy, event transport and persistence.

## Design boundaries

### The runtime handles

- provider-neutral contracts for messages, tools, permissions, approvals, checkpoints and events;
- a bounded, sequential agent loop;
- tool visibility and a permission decision for every call;
- human-in-the-loop pauses, partial approval and resume;
- tool timeouts, retries, a total timeout, a maximum step count and repeated-call detection;
- tool catalogue descriptions, deterministic retrieval and policy filtering (Tool Research);
- turning execution into stable, structured events.

### The host project handles

- the <code>ModelPort</code> adapter that calls OpenAI-compatible, Anthropic or local models;
- the concrete business tools and their results;
- persistence of users, tenants, roles and tool permissions;
- showing approval cards, saving approval decisions and persisting checkpoints;
- SSE, WebSocket, message queues or other UI transport;
- database transactions, caching, rate limiting and business audit.

The runtime never connects to a database and never decides for the host "who can run what".
The default policy only allows read tools that need no confirmation; writes and external side
effects need a policy the host injects explicitly.

### Persistence and deployment boundaries

`pan-agent-runtime` only produces provider-neutral `RunResult`, `AgentCheckpoint` and
`RuntimeEvent` objects; it has no built-in SQLite, Redis, queue, worker process or SSE. The
host can map these objects onto a relational database, object storage or another task system,
and decide whether cross-process execution is needed.

PanWatch's host adapter stores task snapshots, events and approval checkpoints in SQLite; a
browser refresh restores the view by replaying/tailing database events. That doesn't mean the
runtime owns any persistence itself.

## Installation

### Local install in the monorepo

From the repository root:

~~~bash
python -m pip install -e packages/pan-agent-runtime
~~~

In PowerShell:

~~~powershell
python -m pip install -e .\packages\pan-agent-runtime
~~~

### As a standalone package

Once published to a package index, other projects only need:

~~~bash
python -m pip install pan-agent-runtime
~~~

The import name is <code>pan_agent</code>, not the hyphenated distribution name:

~~~python
from pan_agent import AgentRuntime, ToolRegistry
~~~

## Five-minute example: a read-only agent

This example uses a fake model adapter to show the runtime's minimal surface. A real project
replaces <code>DemoModel</code> with an adapter for its model SDK.

~~~python
import asyncio
from datetime import UTC, datetime

from pan_agent import (
    AgentRuntime,
    ModelMessage,
    ModelTurn,
    ReadOnlyToolPolicy,
    RunRequest,
    RuntimeEvent,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolRisk,
)


class DemoModel:
    """In a real project, this translates ModelPort calls into the model provider's protocol."""

    async def run_turn(self, messages, tools, emit_token):
        # First return a tool call; next time return the final answer.
        if not any(message.role == "tool" for message in messages):
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="lookup_balance",
                        arguments={"account_id": "demo"},
                    )
                ]
            )
        await emit_token("Balance looked up.")
        return ModelTurn(content="Balance looked up.")


class ConsoleSink:
    async def publish(self, event: RuntimeEvent):
        print(event.type.value, event.data)


async def lookup_balance(_request, arguments):
    return ToolResult.success(
        summary=f"Account {arguments['account_id']} has a balance of 100.00.",
        data={"account_id": arguments["account_id"], "balance": 100.0},
        sources=[{"name": "demo ledger"}],
        observed_at=datetime.now(UTC),
    )


async def main():
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="lookup_balance",
            title="Look up balance",
            description="Look up an account's current balance.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["account_id"],
                "properties": {"account_id": {"type": "string"}},
            },
        ),
        lookup_balance,
    )

    request = RunRequest(
        run_id="demo-run-1",
        messages=[ModelMessage(role="user", content="What's my balance?")],
    )
    runtime = AgentRuntime(DemoModel(), tools, policy=ReadOnlyToolPolicy())
    result = await runtime.run(request, ConsoleSink())
    print(result.status, result.answer)


asyncio.run(main())
~~~

A tool executor supplied by the host must be an async callable with this signature:

~~~python
async def executor(request: RunRequest, arguments: dict) -> ToolResult:
    ...
~~~

<code>ToolResult.summary</code> is a short summary for the model and the UI; <code>data</code>
holds the structured data the model needs for later reasoning; <code>sources</code> is for
showing where it came from. Keep summaries short and data serialisable, and avoid dumping
whole database tables or raw responses into the context.

## Run lifecycle

One <code>AgentRuntime.run()</code> goes like this:

~~~text
RunRequest
   │
   ├─ RUN_CREATED
   ├─ model turn (sees only the tools the policy exposes)
   ├─ tool permission decision
   │    ├─ ALLOW → TOOL_STARTED → TOOL_COMPLETED
   │    ├─ DENY  → a permission_denied tool result; the model can continue
   │    └─ ASK   → APPROVAL_REQUIRED + AgentCheckpoint; pause
   ├─ ANSWER_TOKEN (emitted continuously by the model adapter)
   └─ RUN_COMPLETED / RUN_FAILED
~~~

## Optional runtime extensions

The runtime only defines a generic `RuntimeExtension` protocol; it has no built-in Tool
Research, memory, MCP or specific observability implementation. Before each model turn, an
extension can read the request, the messages and the tools that already passed the policy,
choose registered tools, provide virtual extension tools, and handle calls to those virtual
tools. It can't widen the permission boundary.

~~~python
from pan_agent import AgentRuntime

runtime = AgentRuntime(
    model,
    tools,
    policy=policy,
    extensions=[my_extension],
)
~~~

Extensions send generic `extension_event`s through `emit_event()`; the event data holds the
extension name, event name and payload. When an extension fails, the runtime emits a
fallback event and carries on with the default tool set. Tool Research is a separate optional
package that PanWatch wires in explicitly; without it, `pan-agent-runtime` still runs on its
own.

When the model returns several tool calls, the runtime handles them in order. If any call
needs approval, the task returns <code>WAITING_FOR_APPROVAL</code>, and calls not yet
approved don't run.

## Human-in-the-loop approval

### The default read-only policy

<code>ReadOnlyToolPolicy</code> only exposes tools with <code>risk=read</code> and
<code>confirmation_required=False</code>. It suits public lookups, offline analysis, or a safe
default before the host's permission system is connected.

### Custom host policies

For writes or external side effects, the host implements <code>ToolPolicy</code>. A policy has
at least two methods:

~~~python
from pan_agent import ToolPermissionDecision, ToolRisk


class MyPolicy:
    def is_tool_visible(self, request, tool):
        # Decide which tools the model can see this turn
        return True

    async def decide(self, request, tool, call):
        # Decide whether this call is allow, ask or deny
        if tool.risk is ToolRisk.READ and not tool.confirmation_required:
            return ToolPermissionDecision.allow()
        return ToolPermissionDecision.ask("This action changes data and needs the user's confirmation")
~~~

In production, <code>decide</code> usually reads the current user, tenant and tool permission
settings, and returns <code>ask</code> for writes, deletes, external sends and the like. Don't
let the model override the policy result through arguments.

### Pause and resume

When an approval request comes back, the host should persist <code>RunResult.checkpoint</code>
and turn its <code>pending_approvals</code> into UI cards. Once the user decides, call
<code>resume</code>:

~~~python
from pan_agent import ApprovalDecision, RunStatus

paused = await runtime.run(request, sink)
if paused.status is RunStatus.WAITING_FOR_APPROVAL:
    checkpoint = paused.checkpoint
    # You can decide just one card; the rest stay in the new checkpoint.
    decisions = {
        checkpoint.pending_approvals[0].call_id: ApprovalDecision.APPROVED,
    }
    resumed = await runtime.resume(request, checkpoint, decisions, sink)
~~~

<code>resume</code> doesn't need the same runtime instance, so as long as the host has
persisted the checkpoint it can resume in a new runtime instance or process. The runtime
doesn't start tasks, hold leases, retry, or guarantee automatic continuation after a process
restart; those belong to the host's task runner/queue layer. Persist checkpoints as JSON, and
use the task ID, user ID and a version number for concurrency checks so an approval card is
never consumed twice.

## Core public API

### Context engineering

Context control for long conversations is provided generically in `pan_agent.context`,
independent of PanWatch's database, FastAPI or model vendors. It includes:

- `ContextBudget`: maximum tokens, soft/hard thresholds and the recent-message window;
- `ContextUsage`: usage broken down into system instructions, history, recent messages, page context and summary;
- `ContextSummary`: goal, constraints, decisions, facts, current state, open items and tool findings;
- `ContextEngine`: automatic compression and forced compression with `force_compress=True`;
- `ContextSummarizer`: the protocol for plugging in any summary model;
- `ExtractiveContextSummarizer`: a deterministic fallback when no model is available.

Token counting has a pluggable boundary too: the runtime only defines the `TokenMeter` and
`TokenMeasurement` protocols and by default uses a dependency-free rough estimate, with no
tokenizer bound. For more accurate estimates or normalised provider usage, the host can
install the optional `pan-agent-token-meter` package: `TiktokenTokenMeter` provides tokenizer
counts through an optional dependency, and `normalize_provider_usage()` turns different
providers' responses into a uniform `ModelUsage`. Actual provider usage is reported through
`model_usage` events but never changes a context compression decision already made.

Example:

~~~python
from pan_agent import ContextBudget, ContextEngine

result = await ContextEngine(my_summarizer).prepare(
    messages,
    budget=ContextBudget(
        max_tokens=12_000,
        soft_limit_tokens=8_400,
        hard_limit_tokens=10_200,
        keep_recent_messages=8,
        summary_max_tokens=800,
    ),
)
next_request_messages = result.messages
~~~

The runtime only owns these provider-neutral contracts. The choice of summary model,
snapshot persistence, HTTP/SSE and the UI are injected by the host project. See
[`docs/architecture.md`](docs/architecture.md) and [`docs/context.md`](docs/context.md) for
the full boundaries.

| Type | Purpose |
| --- | --- |
| <code>AgentRuntime</code> | Start, pause and resume the bounded agent loop |
| <code>ToolRegistry</code> | Register tools, expose them by policy, run them |
| <code>ToolSpec</code> | Tool name, description, risk level, exposure tier and JSON Schema |
| <code>ToolResult</code> | Tool success/failure, summary, structured data and sources |
| <code>ToolPolicy</code> | The host's tool visibility and per-call permissions |
| <code>ReadOnlyToolPolicy</code> | The safe default policy; only read tools without confirmation |
| <code>ModelPort</code> | The async protocol for plugging in a model provider |
| <code>EventSink</code> | The async protocol for receiving structured run events |
| <code>RunRequest</code> | One run's messages, context and limits |
| <code>RunLimits</code> | Limits on steps, tool calls, timeouts and retries |
| <code>AgentCheckpoint</code> | Persistable resume state after an approval pause |
| <code>RunResult</code> | Run status, answer, error code and checkpoint |
| <code>RuntimeEvent</code> | The uniform event used by SSE/WebSocket and other transports |
| <code>RuntimeExtension</code> | The optional per-turn extension protocol |
| <code>ToolExposureDecision</code> | An extension choosing registered tools and providing virtual tool schemas |
| <code>TokenMeter</code> | The optional context token estimate protocol |
| <code>ModelUsage</code> | A provider's actual usage for one turn |

### Risk and permissions

<code>ToolRisk</code> currently has:

- <code>read</code>: reads data, with no business side effects;
- <code>write</code>: creates or changes data; usually ask the user;
- <code>external</code>: side effects such as sending messages or calling external systems;
- <code>destructive</code>: deletes or irreversible actions; deny by default or require a second confirmation in the host policy.

<code>PermissionMode</code> has three results: <code>allow</code>, <code>ask</code> and
<code>deny</code>. The risk level is only the tool's declaration; the final decision always
belongs to the host's <code>ToolPolicy</code>.

### Run limits

<code>RunLimits</code> defaults:

| Limit | Default | Allowed range |
| --- | ---: | ---: |
| <code>max_steps</code> | 6 | 1–32 |
| <code>max_tool_calls</code> | 8 | 1–64 |
| <code>tool_timeout_seconds</code> | 20 | 1–120 |
| <code>run_timeout_seconds</code> | 90 | 1–600 |
| <code>step_retry_count</code> | 1 | 0–3 |

The runtime also detects consecutive identical tool calls. At the threshold it returns
<code>repeated_tool_call</code>, so a model can't loop forever on wrong arguments.

## Events and streaming

<code>RuntimeEvent.type</code> can be:

| Event | Typical use |
| --- | --- |
| <code>run_created</code> | Create the frontend task state |
| <code>plan_created</code> | Reserved for hosts that show a plan |
| <code>step_updated</code> | Show the current agent step |
| <code>extension_event</code> | Persist structured facts from optional extensions |
| <code>tool_started</code> | Show that a tool started |
| <code>tool_completed</code> | Show the tool's result summary and error code |
| <code>model_usage</code> | Record a provider's actual usage for one turn |
| <code>answer_token</code> | Render the model's answer incrementally |
| <code>approval_required</code> | Create one or more approval cards |
| <code>run_completed</code> | The task finished successfully |
| <code>run_failed</code> | The task ended with an error or a partial result |

The runtime doesn't implement SSE itself. In simple cases a FastAPI host can write events to an
<code>asyncio.Queue</code> in <code>EventSink.publish()</code>; when refreshes, reconnects or
audit matter, the host should persist events to an event store first and let the SSE layer
replay/tail them. That keeps the browser transport format, the event storage policy and the
runtime's execution logic decoupled.

## Integrating with BeeCount-Cloud

Split the integration into five layers:

1. **Model adapter**: in <code>ModelPort.run_turn()</code>, turn the streamed tokens, tool calls
   and finish reason of BeeCount-Cloud's current model client into a <code>ModelTurn</code>.
2. **Business tools**: register bookkeeping, account, report and other tools in Cloud's own
   modules; tool implementations depend only on Cloud's service/repository and never enter
   <code>pan-agent-runtime</code>.
3. **Permission policy**: implement <code>ToolPolicy</code> to return
   <code>allow/ask/deny</code> from the user, tenant, role and tool settings. Default writes,
   deletes and external notifications to <code>ask</code>.
4. **Persistence**: map <code>RunRequest</code>, <code>RuntimeEvent</code>, <code>RunResult</code>
   and <code>AgentCheckpoint</code> onto Cloud's own task/message/approval tables.
5. **Transport**: connect the <code>EventSink</code> to the existing SSE or WebSocket channel;
   the frontend only consumes uniform events and doesn't need to know the model provider.

Suggested layout:

~~~text
beecount-cloud/
├─ src/modules/assistant/
│  ├─ model_adapter.py       # ModelPort
│  ├─ policy.py              # ToolPolicy + user permissions
│  ├─ tools.py               # Cloud business tool registration
│  ├─ event_sink.py          # SSE/WebSocket event bridge
│  └─ repository.py          # checkpoint / approval persistence
└─ pyproject.toml            # depends on pan-agent-runtime
~~~

Don't pass a FastAPI <code>Request</code>, a SQLAlchemy <code>Session</code>, Cloud's model
classes or business exceptions into the runtime package's public interface. That way,
changing the database or model provider, or publishing the runtime to other projects later,
doesn't create reverse coupling.

## Context control as tools grow

The runtime calls <code>ToolRegistry.model_tools(request, policy)</code> every turn, so by
default it "hands the model every tool definition the policy allows". That's simplest with few
tools; as tools grow, tool definitions and tool results both become context cost.

The runtime already supports progressive tool exposure: `ToolSpec.exposure` can be `direct`,
`deferred` or `hidden`. By default only direct tools go to the model; optional extensions such
as Tool Research can discover and load deferred tools through a virtual tool. However a tool is
discovered, execution still goes through the host `ToolPolicy` and the Registry.

Further optimisations, in order:

### 1. Expose tools dynamically by capability domain

In <code>ToolPolicy.is_tool_visible()</code>, expose only the tools relevant to the current
request context, for example:

- the user asks about a balance: only account and holdings lookups;
- the user asks about bills: only transaction and category tools;
- the user wants to change data: expose the matching write tool for that request only.

Larger systems can expose just one "tool catalogue/search" tool: the model searches for a
capability first, and the extension then adds the matching deferred tools to later turns.
Virtual extension tools provide their schema through `ToolExposureDecision.additional_tools`
and are handled by `RuntimeExtension.handle_tool_call()`, with no need to register the
extension's executor in the business Tool Registry.

### 2. Summarise tool results

<code>ToolResult.summary</code> is for quick understanding; <code>data</code> keeps only the
fields later reasoning needs. List queries return IDs, names and key status, and details are
fetched by ID in a later tool call. Don't copy full quote data, full logs or a whole billing
table into the conversation history every time.

### 3. Tiered history

- keep the last few rounds of raw messages and tool results;
- compress earlier conversation into a stable summary;
- put reusable facts in the host's structured context or external storage and retrieve them on demand;
- set a maximum input length per task.

### 4. Caching and repeated-call protection

Cache read-only lookups with the same user, symbol, time window and arguments for a short
TTL; deduplicate concurrent requests in the tool layer or the host. The runtime already stops
consecutive identical tool calls, and the host can add finer idempotency keys in
<code>ToolPolicy</code> or the tool adapter layer.

### 5. Budgets and observability

Set <code>RunLimits</code> by task type, and record per-step tokens, tool durations, retries
and error codes. When tool calls grow unusually, check the model prompt, tool descriptions and
permission policy first rather than simply raising <code>max_steps</code>.

## Errors and states

Tool exceptions are converted into stable <code>ToolResult</code>/<code>RunResult</code> values
at the runtime boundary, so database stack traces never reach the model or the browser. Common
final states:

| State | Meaning |
| --- | --- |
| <code>completed</code> | The model returned a final answer |
| <code>waiting_for_approval</code> | There are approval cards pending |
| <code>partial</code> | A step/tool/timeout limit was reached, or a tool failed |
| <code>failed</code> | An unexpected error at the runtime boundary |
| <code>cancelled</code> | The host cancelled the task |
| <code>pending</code> / <code>running</code> | Intermediate states the host persists or shows |

External interfaces should use <code>RunResult.error_code</code> for machine decisions and the
event <code>summary</code> for human-readable messages. Don't rely on exception text as a
frontend protocol.

## Testing and local development

Run the runtime tests from the repository root:

~~~bash
python -m pytest packages/pan-agent-runtime/tests -q
~~~

Host projects should at least cover:

- read tools are exposed and run;
- write tools produce an approval instead of running directly;
- a rejected approval causes no business write;
- partial approval runs only the calls that were decided;
- a checkpoint can be serialised and resumed in a new process;
- tool timeouts, retries, repeated calls and unknown tools give the expected error codes;
- the event order matches the frontend streaming protocol.

## Standalone release checklist

Before releasing a new version:

1. update <code>version</code> in <code>pyproject.toml</code>;
2. check the <code>README.md</code> examples match the public API;
3. run <code>python -m pytest packages/pan-agent-runtime/tests -q</code>;
4. build the wheel and source distribution:

   ~~~bash
   python -m pip install build
   python -m build packages/pan-agent-runtime
   ~~~

5. install the built wheel in a clean virtual environment and run the minimal example;
6. upload through PyPI Trusted Publishing or the project's release pipeline;
7. bump the major version for breaking API changes, or document the migration clearly.

## Versioning principles

During <code>0.x</code>, minor versions may adjust details that aren't stable yet, but these
boundaries should stay stable:

- the field semantics of <code>ToolSpec</code>, <code>ToolResult</code>, <code>RunRequest</code>
  and <code>RunResult</code>;
- the async calling conventions of <code>ModelPort</code>, <code>ToolPolicy</code> and
  <code>EventSink</code>;
- the event types and core fields of <code>RuntimeEvent</code>;
- whether a checkpoint can be resumed by a host on the same version.

Business projects shouldn't depend on private functions or unexported details of
<code>pan_agent.runtime</code>; import the public API only from the top-level
<code>pan_agent</code>.
