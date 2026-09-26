# PanAgent Tool Research

`pan-agent-tool-research` is an optional plugin for `pan-agent-runtime` that adds
progressive tool discovery as the number of tools grows. It isn't part of the runtime core; M0 connects to no database, Redis, vector service
or model provider.

## Capabilities

- `ToolDescriptor`: a tool's purpose, keywords, aliases, capability domain, risk and data freshness;
- `ToolCatalog`: an in-process, versioned catalogue of descriptor metadata;
- `KeywordToolRetriever`: keyword and alias retrieval with no model calls;
- `ToolResearchService`: returns candidates after applying enablement, capability domains and the host `ToolPolicy`;
- `ToolResearchPlugin`: plugs into the runtime in shadow or active mode; active mode provides a model-callable
  `tool_search` virtual tool.

## Integration

```python
from pan_agent import AgentRuntime
from pan_agent_tool_research import ToolResearchPlugin, ToolResearchService

research = ToolResearchService(
    registry,
    descriptors=my_tool_descriptors,
)
runtime = AgentRuntime(
    model,
    registry,
    policy=policy,
    extensions=[ToolResearchPlugin(research, mode="active")],
)
```

`shadow` mode only emits an `extension_event` and doesn't change the tool set the model sees; `active` mode:

1. keeps the tools the Registry marks as `direct`;
2. exposes a model-callable `tool_search`;
3. puts search results into the next round of messages and exposes the chosen deferred tools with their full schemas;
4. still has every execution checked again by the runtime policy and the Registry.

Tools in the Registry can be set to `direct`, `deferred` or `hidden` through `ToolSpec.exposure`:

```python
from pan_agent import ToolExposure, ToolSpec

ToolSpec(
    name="get_special_report",
    title="Special report",
    description="Look up a special report.",
    exposure=ToolExposure.DEFERRED,
)
```

If the plugin fails, it falls back to the current direct tool set by default, so a catalogue or retrieval failure never empties the model's tools.
M0's active retrieval still uses in-process keywords, aliases and structured metadata, with no intent model required.

## Events

The plugin emits through the runtime's generic extension events:

- `extension=tool_research, event=started`;
- `exposure`: the current direct tools and the tools already loaded;
- `candidates_scored`;
- `completed`;
- `searched`: the result after the model actually calls `tool_search`;
- `fallback`.

The host can write `RuntimeEvent`s straight into its own task event table, or ignore plugin events. The plugin never writes
the user's original text into events; search events record only the query hash, candidate count, tool names and versions.

## Development

```bash
python -m pip install -e packages/pan-agent-runtime
python -m pip install -e packages/pan-agent-tool-research
python -m pytest packages/pan-agent-tool-research/tests -q
```
