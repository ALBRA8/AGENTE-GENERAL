"""Render entry point for the domain-agnostic ALBRA seed API.

The seed boots with its deterministic Mock provider and a minimal generic
agent. External execution remains protected by the API's default-deny
authentication, policy, and security-perimeter checks.
"""

from albra_seed import Agent, AgentIdentity, Runtime, SeedConfig, create_app

runtime = Runtime(config=SeedConfig.from_env())
identity = AgentIdentity(
    name="albra-seed",
    capabilities=["text-generation"],
    providers=["mock"],
)
agent = Agent(identity=identity, runtime=runtime)
app = create_app(runtime, agents={identity.agent_id: agent})

# FastAPI runs these handlers for the ASGI lifespan. Without this, the
# deployed runtime stays UNINITIALIZED and readiness remains false.
app.add_event_handler("startup", runtime.startup)
app.add_event_handler("shutdown", runtime.shutdown)
