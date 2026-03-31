"""Tests for AgentActor (M1)."""

import asyncio
import warnings

import pytest

from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task, TaskResult, TaskStatus


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoAgent(AgentActor):
    async def execute(self, input):
        return input


class BrokenAgent(AgentActor):
    async def execute(self, input):
        raise ValueError(f"boom: {input}")


class ProgressAgent(AgentActor):
    def __init__(self):
        super().__init__()
        self.emitted: list = []

    async def execute(self, input):
        await self.emit_progress("step-1")
        await self.emit_progress("step-2")
        return f"done:{input}"


class LifecycleAgent(AgentActor):
    def __init__(self):
        super().__init__()
        self.events: list[str] = []

    async def on_started(self):
        self.events.append("started")

    async def execute(self, input):
        self.events.append(f"execute:{input}")
        return input

    async def on_stopped(self):
        self.events.append("stopped")


# ---------------------------------------------------------------------------
# Basic execute / TaskResult
# ---------------------------------------------------------------------------


async def test_execute_returns_task_result():
    system = ActorSystem("t")
    ref = await system.spawn(EchoAgent, "echo")
    result = await ref.ask(Task(input="hello"))
    assert isinstance(result, TaskResult)
    assert result.output == "hello"
    assert result.status == TaskStatus.COMPLETED
    assert result.error is None
    await system.shutdown()


async def test_task_id_preserved_in_result():
    system = ActorSystem("t")
    ref = await system.spawn(EchoAgent, "echo")
    task = Task(input="x", id="my-task-id")
    result = await ref.ask(task)
    assert result.task_id == "my-task-id"
    await system.shutdown()


async def test_execute_with_complex_input():
    system = ActorSystem("t")
    ref = await system.spawn(EchoAgent, "echo")
    payload = {"key": [1, 2, 3], "nested": {"a": True}}
    result = await ref.ask(Task(input=payload))
    assert result.output == payload
    await system.shutdown()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_execute_exception_propagates_to_caller():
    system = ActorSystem("t")
    ref = await system.spawn(BrokenAgent, "broken")
    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="bad"))
    await system.shutdown()


async def test_actor_still_alive_after_supervised_failure():
    """After a crash supervised by OneForOne, actor restarts and serves next task."""
    system = ActorSystem("t")

    class ParentAgent(AgentActor):
        async def on_started(self):
            self.child = await self.context.spawn(BrokenAgent, "broken")

        async def execute(self, input):
            if input == "crash":
                try:
                    return await self.child.ask(Task(input="bad"), timeout=2.0)
                except Exception:
                    return "caught"
            return "ok"

    ref = await system.spawn(ParentAgent, "parent")
    result = await ref.ask(Task(input="crash"))
    assert result.output == "caught"
    await system.shutdown()


# ---------------------------------------------------------------------------
# emit_progress
# ---------------------------------------------------------------------------


async def test_emit_progress_no_op_without_sink():
    """emit_progress with no event sink attached should not raise."""
    system = ActorSystem("t")
    ref = await system.spawn(ProgressAgent, "prog")
    result = await ref.ask(Task(input="x"))
    assert result.output == "done:x"
    await system.shutdown()


async def test_emit_progress_noop_outside_execute():
    """emit_progress called outside execute() is silently ignored."""
    system = ActorSystem("t")
    ref = await system.spawn(ProgressAgent, "prog")
    actor: ProgressAgent = ref._cell.actor  # type: ignore[assignment]
    await actor.emit_progress("should-not-crash")
    await system.shutdown()


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


async def test_on_started_and_on_stopped_called():
    system = ActorSystem("t")
    ref = await system.spawn(LifecycleAgent, "lc")
    actor: LifecycleAgent = ref._cell.actor  # type: ignore[assignment]
    await ref.ask(Task(input="go"))
    assert "started" in actor.events
    assert "execute:go" in actor.events
    await system.shutdown()
    assert "stopped" in actor.events


async def test_on_started_called_before_execute():
    order: list[str] = []

    class OrderedAgent(AgentActor):
        async def on_started(self):
            order.append("started")

        async def execute(self, input):
            order.append("execute")
            return input

    system = ActorSystem("t")
    ref = await system.spawn(OrderedAgent, "ordered")
    await ref.ask(Task(input="x"))
    assert order.index("started") < order.index("execute")
    await system.shutdown()


# ---------------------------------------------------------------------------
# Wrong message type
# ---------------------------------------------------------------------------


async def test_wrong_message_type_raises_type_error():
    system = ActorSystem("t")
    ref = await system.spawn(EchoAgent, "echo")
    with pytest.raises(Exception, match="expects Task"):
        await ref.ask("raw string, not a Task")
    await system.shutdown()


# ---------------------------------------------------------------------------
# on_receive override guard
# ---------------------------------------------------------------------------


def test_override_on_receive_emits_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        class BadAgent(AgentActor):
            async def on_receive(self, message):
                return message

    assert any("execute()" in str(w.message) for w in caught)
    assert any(issubclass(w.category, UserWarning) for w in caught)


def test_no_warning_for_clean_subclass():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        class GoodAgent(AgentActor):
            async def execute(self, input):
                return input

    assert not any("on_receive" in str(w.message) for w in caught)
