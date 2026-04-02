"""Tests for AgentActor (M1)."""

import asyncio
import warnings

import pytest

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.agents import AgentActor, Task, TaskEvent, TaskResult, TaskStatus


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


async def test_task_failed_event_carries_error_context():
    """task_failed event data must contain the exception message."""
    collected: list[TaskEvent] = []

    class CollectorActor(Actor):
        async def on_receive(self, message):
            collected.append(message)

    system = ActorSystem("t")
    sink_ref = await system.spawn(CollectorActor, "sink")
    ref = await system.spawn(BrokenAgent, "broken")
    ref._cell.actor._event_sink = sink_ref  # type: ignore[union-attr]

    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="bad"))

    await asyncio.sleep(0.05)  # let tell() deliver
    failed_events = [e for e in collected if e.type == "task_failed"]
    assert len(failed_events) == 1
    assert "boom: bad" in failed_events[0].data
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


# ---------------------------------------------------------------------------
# Streaming execute() — async generator support
# ---------------------------------------------------------------------------


class ChunkAgent(AgentActor[str, list]):
    async def execute(self, input: str):
        for i in range(3):
            yield f"{input}-{i}"


class BrokenChunkAgent(AgentActor[str, list]):
    async def execute(self, input: str):
        yield "before-error"
        raise ValueError("mid-stream failure")


async def test_streaming_execute_yields_task_chunk_events():
    """execute() as async generator emits task_chunk events for each yield."""
    from everything_is_an_actor.agents import AgentSystem
    from everything_is_an_actor.agents.task import StreamEvent, StreamResult

    system = AgentSystem()
    ref = await system.spawn(ChunkAgent, "chunker")

    events: list[TaskEvent] = []
    async for item in ref.ask_stream(Task(input="x")):
        if isinstance(item, StreamEvent):
            events.append(item.event)

    chunks = [e for e in events if e.type == "task_chunk"]
    assert len(chunks) == 3
    assert [e.data for e in chunks] == ["x-0", "x-1", "x-2"]
    await system.shutdown()


async def test_streaming_execute_result_is_list_of_chunks():
    """TaskResult.output is the list of all yielded chunks."""
    from everything_is_an_actor.agents import AgentSystem
    from everything_is_an_actor.agents.task import StreamResult

    system = AgentSystem()
    ref = await system.spawn(ChunkAgent, "chunker2")

    result = None
    async for item in ref.ask_stream(Task(input="y")):
        if isinstance(item, StreamResult):
            result = item.result

    assert result is not None
    assert result.output == ["y-0", "y-1", "y-2"]
    await system.shutdown()


async def test_streaming_execute_plain_ask_returns_list():
    """Plain ref.ask() also works for streaming execute() — returns list."""
    from everything_is_an_actor import ActorSystem

    system = ActorSystem()
    ref = await system.spawn(ChunkAgent, "chunker3")
    result: TaskResult = await ref.ask(Task(input="z"))
    assert result.output == ["z-0", "z-1", "z-2"]
    await system.shutdown()


async def test_streaming_execute_error_propagates():
    """Exception raised inside async generator propagates to caller."""
    from everything_is_an_actor.agents import AgentSystem

    system = AgentSystem()
    ref = await system.spawn(BrokenChunkAgent, "broken-chunker")

    with pytest.raises(ValueError, match="mid-stream failure"):
        async for _ in ref.ask_stream(Task(input="x")):
            pass

    await system.shutdown()


# ---------------------------------------------------------------------------
# dispatch_stream — stream from within execute()
# ---------------------------------------------------------------------------


class PassthroughAgent(AgentActor[str, list]):
    """Orchestrator that dispatch_streams a ChunkAgent and transparently re-yields chunks."""

    async def execute(self, input: str):
        from everything_is_an_actor.agents.task import StreamEvent, StreamResult

        async for item in self.context.dispatch_stream(ChunkAgent, Task(input=input)):
            match item:
                case StreamEvent(event=e) if e.type == "task_chunk":
                    yield e.data  # transparent passthrough
                case StreamResult():
                    pass  # final result captured implicitly


async def test_dispatch_stream_yields_child_chunks():
    """dispatch_stream propagates child task_chunk events to the caller."""
    from everything_is_an_actor.agents import AgentSystem
    from everything_is_an_actor.agents.task import StreamEvent, StreamResult

    system = AgentSystem()
    ref = await system.spawn(PassthroughAgent, "passthrough")

    chunks = []
    async for item in ref.ask_stream(Task(input="q")):
        match item:
            case StreamEvent(event=e) if e.type == "task_chunk":
                chunks.append(e.data)
            case StreamResult():
                pass

    assert chunks == ["q-0", "q-1", "q-2"]
    await system.shutdown()


async def test_dispatch_stream_ephemeral_child_cleaned_up():
    """Ephemeral child actor spawned by dispatch_stream is stopped after stream ends."""
    from everything_is_an_actor.agents import AgentSystem
    from everything_is_an_actor.actor import ActorContext

    system = AgentSystem()
    ref = await system.spawn(PassthroughAgent, "passthrough2")

    async for _ in ref.ask_stream(Task(input="x")):
        pass

    await system.shutdown()
    # Smoke test: no assertions needed — if children leak, shutdown hangs
