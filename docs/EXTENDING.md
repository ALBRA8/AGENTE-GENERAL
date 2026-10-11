# Extending the Seed — How to Add Tools / Skills / Providers / Plugins

This guide covers adding **universal** (non-domain) extensions to the Seed.
For domain specialization, see [`SPECIALIZATION.md`](SPECIALIZATION.md).

---

## 1. Adding a Tool

### 1.1 Implement the ToolContract

```python
# src/albra_seed/tools/my_tool.py  (or in specialization)
from albra_seed import Tool, ToolContract, ToolResult, RiskLevel

class MyTool(Tool):
    def __init__(self):
        self.contract = ToolContract(
            tool_id="tool.my_tool",
            name="my_tool",
            description="Does something useful",
            version="1.0.0",
            input_schema={
                "input_value": {"type": "string", "required": True},
            },
            output_schema={
                "result": {"type": "string"},
            },
            risk_level=RiskLevel.LOW,
            permissions=["tool.my_tool.execute"],
            timeout_seconds=30,
            retry_policy={"max": 2, "backoff_base": 0.5},
            evidence_required=False,
            is_dry_run_capable=True,
            is_external=False,
            availability="available",
        )

    async def execute(self, arguments, *, context=None, dry_run=False):
        if dry_run:
            return ToolResult(
                call_id=arguments.get("_call_id", "dry-run"),
                success=True,
                output={"result": "[dry-run] would process: " + arguments.get("input_value", "")},
            )
        # real execution
        result = arguments["input_value"].upper()
        return ToolResult(
            call_id=arguments.get("_call_id", ""),
            success=True,
            output={"result": result},
        )
```

### 1.2 Register it

```python
runtime.register_tool(MyTool())
```

### 1.3 Use it

The agent can invoke it via tool_call:

```python
response = await agent.run("Use my_tool to uppercase 'hello'")
```

Or directly:

```python
result = await runtime.get_component("tool_registry").get("my_tool").execute({"input_value": "hello"})
```

---

## 2. Adding a Skill

```python
# src/albra_seed/skills/my_skill.py
from albra_seed import Skill, RiskLevel, ExecutionStatus

class MySkill(Skill):
    skill_id: str = "skill.my_skill"
    name: str = "my_skill"
    version: str = "1.0.0"
    objective: str = "Accomplish X by doing Y"
    inputs: dict = {"input": "string"}
    outputs: dict = {"output": "string"}
    dependencies: list[str] = ["tool.my_tool"]
    permissions: list[str] = ["skill.my_skill.execute"]
    risk_level: RiskLevel = RiskLevel.LOW
    evidence_required: bool = False
    tests: list[str] = ["tests/unit/test_my_skill.py"]
    status: ExecutionStatus = ExecutionStatus.PENDING

    async def execute(self, arguments, *, context=None):
        # call dependent tool
        tool = context.runtime.get_component("tool_registry").get("my_tool")
        result = await tool.execute({"input_value": arguments["input"]}, context=context)
        return {"output": result.output["result"]}
```

Register:
```python
runtime.register_skill(MySkill())
```

---

## 3. Adding a Provider

```python
# src/albra_seed/providers/my_provider.py
from albra_seed import LLMProvider, AgentResponse, Message, RunContext

class MyProvider(LLMProvider):
    name = "my_provider"
    is_mock = False
    is_external = True

    def __init__(self, api_key: str):
        if not api_key:
            raise NotImplementedError("MyProvider requires API key")
        self.api_key = api_key
        # lazy-import SDK
        try:
            import my_sdk
        except ImportError as e:
            raise NotImplementedError(f"my_sdk not installed: {e}")
        self.client = my_sdk.Client(api_key=api_key)

    async def complete(self, messages, *, model=None, temperature=0.2,
                       max_tokens=2048, tools=None, context=None):
        # implementation
        response_text = await self.client.chat(...)
        return AgentResponse(
            content=response_text,
            execution_id=context.execution_id if context else "",
            correlation_id=context.correlation_id if context else "",
        )

    async def health(self) -> bool:
        try:
            await self.client.ping()
            return True
        except Exception:
            return False
```

Register:
```python
provider = MyProvider(api_key=secrets.get("MY_API_KEY"))
runtime.register_provider(provider)
runtime.get_component("provider_registry").set_default("my_provider")
```

---

## 4. Adding a Plugin

Plugins contribute tools/skills/providers/interfaces without modifying core.

### 4.1 Plugin manifest

```python
# src/my_plugin/manifest.py
from albra_seed import Plugin, PluginManifest

manifest = PluginManifest(
    plugin_id="plugin.my_plugin",
    name="my_plugin",
    version="1.0.0",
    contributes_tools=["my_tool"],
    contributes_skills=["my_skill"],
    contributes_providers=[],
    contributes_interfaces=[],
    enabled=True,
    health_check_entrypoint="my_plugin.health:check",
)
```

### 4.2 Plugin entrypoints

```python
# src/my_plugin/__init__.py
from albra_seed import Tool, Skill

def register_tools(runtime):
    runtime.register_tool(MyTool())

def register_skills(runtime):
    runtime.register_skill(MySkill())

def check_health() -> bool:
    return True
```

### 4.3 Install

```python
from albra_seed.plugins import PluginLoader, PluginManager

manager = PluginManager(runtime)
loader = PluginLoader(search_paths=["./plugins"])
for manifest in loader.discover():
    manager.register(manifest)
manager.enable("plugin.my_plugin")
```

---

## 5. Adding a Workflow

```python
from albra_seed.workflows import WorkflowGraph, WorkflowStep, Workflow

workflow = Workflow(
    name="my_workflow",
    version="1.0.0",
    steps=[
        {"id": "step1", "name": "gather", "type": "tool",
         "tool": "my_tool", "inputs": {"input_value": "${input.x}}"},
         "retry_policy": {"max": 3, "backoff_base": 1.0}},
        {"id": "step2", "name": "process", "type": "skill",
         "skill": "my_skill", "inputs": {"input": "${step1.output.result}}"},
         "hitl_required": True},
        {"id": "step3", "name": "respond", "type": "action",
         "handler": "my_handler", "inputs": {"data": "${step2.output.output}}"}},
    ],
    edges=[
        {"from": "step1", "to": "step2"},
        {"from": "step2", "to": "step3"},
    ],
)

runtime.get_component("workflow_registry").register(workflow)
```

Execute (with HITL pause/resume):

```python
engine = runtime.get_component("workflow_engine")
state = await engine.start("my_workflow", input={"x": "hello"})

if state.paused and state.hitl_pending:
    approval = await runtime.get_component("approval_engine").request(
        action="workflow.my_workflow.step2",
        context=ctx,
        deadline=now + timedelta(minutes=10),
    )
    # ... wait for approval ...
    state = await engine.resume(state.execution_id, approval_id=approval.id)
```

---

## 6. Adding a Guardrail

```python
from albra_seed import Guardrail, Message, AgentResponse, RunContext

class NoProfanityGuardrail(Guardrail):
    name = "no_profanity"

    async def check_input(self, message, *, context=None):
        banned = ["bad_word_1", "bad_word_2"]
        return not any(w in message.content.lower() for w in banned)

    async def check_output(self, response, *, context=None):
        banned = ["bad_word_1", "bad_word_2"]
        return not any(w in response.content.lower() for w in banned)

runtime.register_guardrail(NoProfanityGuardrail())
```

The agent loop calls `check_input` before reasoning and `check_output` before
responding. Failed checks block the execution and log to audit.

---

## 7. Adding a Communication Channel Adapter

```python
from albra_seed.communication import ChannelAdapter, CommunicationMessage

class VoiceChannelAdapter(ChannelAdapter):
    name = "voice"
    is_default = False

    async def send(self, message: CommunicationMessage) -> bool:
        # use TTS to convert text to voice, send to channel
        ...

    async def receive(self) -> CommunicationMessage:
        # use ASR to convert voice to text
        ...

runtime.get_component("channel_registry").register(VoiceChannelAdapter())
```

---

## 8. Adding a Multimodal Adapter

```python
from albra_seed.multimodal import ModalityAdapter, ModalityType

class ASRAdapter(ModalityAdapter):
    modality = ModalityType.VOICE

    def __init__(self):
        try:
            import whisper
            self.model = whisper.load_model("base")
        except ImportError as e:
            raise NotImplementedError(f"whisper not installed: {e}")

    async def encode(self, audio_bytes: bytes) -> str:
        # transcribe
        return self.model.transcribe(audio_bytes)["text"]

    async def decode(self, text: str) -> bytes:
        # synthesize (use TTS)
        ...

    async def validate(self, content) -> bool:
        return isinstance(content, bytes) and len(content) > 0

runtime.get_component("multimodal_registry").register(ASRAdapter())
```

---

## 9. Adding an MCP Server

```python
from albra_seed.mcp import MCPServer, MCPCapability

class MyMCPServer(MCPServer):
    async def list_capabilities(self) -> list[MCPCapability]:
        return [
            MCPCapability(name="search", description="Search something"),
            MCPCapability(name="compute", description="Compute something"),
        ]

    async def handle_request(self, request):
        if request.method == "search":
            return {"results": [...]}
        elif request.method == "compute":
            return {"result": ...}
        raise ValueError(f"Unknown method: {request.method}")

runtime.get_component("mcp_registry").register_server(MyMCPServer())
```

---

## 10. Hooking into the Agent Loop

Subscribe to phase events:

```python
async def on_phase(event: PhaseEvent):
    print(f"Phase {event.phase} -> {event.status} (took {event.duration_ms}ms)")

runtime.add_phase_hook(on_phase)

# Also: subscribe to lifecycle events
async def on_lifecycle(event: LifecycleEvent):
    print(f"Lifecycle: {event.from_phase} -> {event.to_phase}")

runtime.lifecycle.add_listener(on_lifecycle)
```

---

## 11. When NOT to Extend the Seed

If you find yourself wanting to add something to `src/albra_seed/`, ask:

- Is this **transversal** (useful for ALL future ALBRA agents)?
- Is this **domain-agnostic** (no legal/financial/sales/... assumptions)?
- Is this **justified** (not just "could be useful")?

If NO to any → put it in your **specialization** package, not in the seed.

---

## 12. Testing Extensions

Every extension should have:

1. **Unit tests** — for the extension's own logic
2. **Contract tests** — verify it implements the right interface
3. **Integration tests** — verify it works with the rest of the seed
4. **Security tests** — verify it doesn't bypass firewall/policy
5. **Clean-room test** — verify the seed still passes clean-room with the extension installed
