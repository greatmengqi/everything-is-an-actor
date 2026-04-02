"""Agent layer — higher-level abstractions for AI agent systems.

Progressive API levels:

    # Level 1: plain class (zero actor knowledge)
    class MyAgent:
        async def execute(self, input): ...

    # Level 2: with lifecycle hooks
    class MyAgent:
        async def on_started(self): ...
        async def execute(self, input): ...
        async def on_stopped(self): ...

    # Level 3: with actor config
    class MyAgent:
        __actor__ = ActorConfig(mailbox_size=64, max_restarts=5)
        async def execute(self, input): ...

    # Level 4: full AgentActor (supervision + emit_progress)
    class MyAgent(AgentActor):
        async def execute(self, input): ...
        async def emit_progress(self, data): ...

    # Level 5: raw Actor (infrastructure components)
    class MyRouter(Actor):
        async def on_receive(self, message): ...

Levels 1-3 require AgentSystem (M3). Level 4-5 work with plain ActorSystem.
"""

from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.run_stream import RunStream
from everything_is_an_actor.agents.system import AgentSystem
from everything_is_an_actor.agents.task import (
    ActorConfig,
    Task,
    TaskError,
    TaskEvent,
    TaskResult,
    TaskStatus,
)

__all__ = [
    "ActorConfig",
    "AgentActor",
    "AgentSystem",
    "RunStream",
    "Task",
    "TaskError",
    "TaskEvent",
    "TaskResult",
    "TaskStatus",
]
