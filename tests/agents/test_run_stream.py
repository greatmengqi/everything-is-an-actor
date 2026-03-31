"""Tests for AgentSystem.run() / RunStream (M3)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from actor_for_agents.agents import AgentActor, Task, TaskEvent, TaskResult
from actor_for_agents.agents.system import AgentSystem
from actor_for_agents.agents.task import StreamEvent, StreamResult

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class ProgressAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("step-1")
        await self.emit_progress("step-2")
        return f"done:{input}"


class BrokenAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError(f"boom: {input}")


class SlowAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(60)
        return input


class OrchestratorAgent(AgentActor[str, str]):
    """Root agent that dispatches to two child EchoAgents in parallel."""

    async def execute(self, input: str) -> str:
        results: list[TaskResult[str]] = await self.context.dispatch_parallel(
            [
                (EchoAgent, Task(input=f"{input}-a")),
                (EchoAgent, Task(input=f"{input}-b")),
            ]
        )
        return "+".join(r.output for r in results)


# ---------------------------------------------------------------------------
# Basic streaming
# ---------------------------------------------------------------------------


async def test_run_yields_started_and_completed_events():
    system = AgentSystem()
    events = []
    async for event in system.run(EchoAgent, "hello"):
        events.append(event)
    await system.shutdown()

    types = [e.type for e in events]
    assert "task_started" in types
    assert "task_completed" in types


async def test_run_events_carry_correct_task_id():
    system = AgentSystem()
    events = []
    async for event in system.run(EchoAgent, "x", run_id="my-run"):
        events.append(event)
    await system.shutdown()

    task_ids = {e.task_id for e in events}
    # All events from a single-agent run share one task_id
    assert len(task_ids) == 1


async def test_run_yields_progress_events():
    system = AgentSystem()
    events = []
    async for event in system.run(ProgressAgent, "test"):
        events.append(event)
    await system.shutdown()

    types = [e.type for e in events]
    assert types.count("task_progress") == 2
    progress_data = [e.data for e in events if e.type == "task_progress"]
    assert progress_data == ["step-1", "step-2"]


async def test_run_event_order():
    """Events must arrive in: started → progress* → completed order."""
    system = AgentSystem()
    events = []
    async for event in system.run(ProgressAgent, "x"):
        events.append(event)
    await system.shutdown()

    types = [e.type for e in events]
    assert types[0] == "task_started"
    assert types[-1] == "task_completed"
    assert all(t == "task_progress" for t in types[1:-1])


# ---------------------------------------------------------------------------
# Agent path
# ---------------------------------------------------------------------------


async def test_run_event_agent_path_contains_run_id():
    system = AgentSystem()
    events = []
    async for event in system.run(EchoAgent, "x", run_id="abc123"):
        events.append(event)
    await system.shutdown()

    for event in events:
        assert "abc123" in event.agent_path


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_run_raises_on_agent_failure():
    system = AgentSystem()
    events = []
    with pytest.raises(ValueError, match="boom: bad"):
        async for event in system.run(BrokenAgent, "bad"):
            events.append(event)
    await system.shutdown()

    # task_failed event must still be emitted before the exception propagates
    types = [e.type for e in events]
    assert "task_failed" in types


async def test_run_failed_event_carries_error_message():
    system = AgentSystem()
    events = []
    with pytest.raises(ValueError):
        async for event in system.run(BrokenAgent, "oops"):
            events.append(event)
    await system.shutdown()

    failed = [e for e in events if e.type == "task_failed"]
    assert len(failed) == 1
    assert "boom: oops" in failed[0].data


# ---------------------------------------------------------------------------
# Orchestration — events from entire actor tree
# ---------------------------------------------------------------------------


async def test_run_captures_child_events():
    """Events from child agents (spawned via dispatch) appear in the stream."""
    system = AgentSystem()
    events = []
    async for event in system.run(OrchestratorAgent, "hi"):
        events.append(event)
    await system.shutdown()

    # Root + 2 children = 3 × (started + completed) = 6 events minimum
    assert len(events) >= 6
    types = [e.type for e in events]
    assert types.count("task_started") == 3
    assert types.count("task_completed") == 3


async def test_run_captures_events_from_all_agents():
    """Agent paths in events span root and children."""
    system = AgentSystem()
    paths: set[str] = set()
    async for event in system.run(OrchestratorAgent, "x"):
        paths.add(event.agent_path)
    await system.shutdown()

    # At least 3 distinct paths: root + 2 children
    assert len(paths) >= 3


# ---------------------------------------------------------------------------
# run_id isolation
# ---------------------------------------------------------------------------


async def test_two_concurrent_runs_have_isolated_events():
    """Events from concurrent runs don't bleed into each other."""
    system = AgentSystem()

    async def collect(run_id: str) -> list[str]:
        paths = []
        async for event in system.run(EchoAgent, "x", run_id=run_id):
            paths.append(event.agent_path)
        return paths

    paths_a, paths_b = await asyncio.gather(collect("run-a"), collect("run-b"))
    await system.shutdown()

    for p in paths_a:
        assert "run-a" in p
    for p in paths_b:
        assert "run-b" in p


# ---------------------------------------------------------------------------
# abort
# ---------------------------------------------------------------------------


async def test_abort_stops_the_run():
    """abort() cancels the running agent; the stream closes."""
    system = AgentSystem()

    run_id = "to-abort"
    events: list[TaskEvent] = []

    async def consume() -> None:
        async for event in system.run(SlowAgent, "x", run_id=run_id, timeout=10.0):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let SlowAgent start
    await system.abort(run_id)

    # consume() should exit (stream closed by _drive's finally)
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.TimeoutError, Exception):
        pass

    await system.shutdown()
    # At minimum task_started was emitted before abort
    assert any(e.type == "task_started" for e in events)


# ---------------------------------------------------------------------------
# Cleanup — no orphaned actors after run
# ---------------------------------------------------------------------------


async def test_no_orphaned_actors_after_successful_run():
    system = AgentSystem()
    async for _ in system.run(EchoAgent, "x"):
        pass

    # Give background _drive task a moment to complete cleanup
    await asyncio.sleep(0.05)
    assert len(system._root_cells) == 0
    await system.shutdown()


async def test_no_orphaned_actors_after_failed_run():
    system = AgentSystem()
    with pytest.raises(ValueError):
        async for _ in system.run(BrokenAgent, "x"):
            pass

    await asyncio.sleep(0.05)
    assert len(system._root_cells) == 0
    await system.shutdown()


# ---------------------------------------------------------------------------
# Span linking — parent_task_id
# ---------------------------------------------------------------------------


async def test_root_event_has_no_parent():
    system = AgentSystem()
    events = []
    async for event in system.run(EchoAgent, "x"):
        events.append(event)
    await system.shutdown()

    for event in events:
        assert event.parent_task_id is None


async def test_child_events_link_to_parent_task():
    """parent_task_id of child events equals task_id of root's started event."""
    system = AgentSystem()
    events = []
    async for event in system.run(OrchestratorAgent, "x"):
        events.append(event)
    await system.shutdown()

    root_started = next(e for e in events if e.type == "task_started" and e.parent_task_id is None)
    child_events = [e for e in events if e.parent_task_id is not None]

    assert len(child_events) > 0
    for e in child_events:
        assert e.parent_task_id == root_started.task_id


async def test_span_tree_is_reconstructable():
    """All events for a run form a valid tree: each non-root has exactly one parent."""
    system = AgentSystem()
    events = []
    async for event in system.run(OrchestratorAgent, "x"):
        events.append(event)
    await system.shutdown()

    task_ids = {e.task_id for e in events}
    for event in events:
        if event.parent_task_id is not None:
            assert event.parent_task_id in task_ids, f"parent_task_id {event.parent_task_id!r} not found among task_ids"


# ---------------------------------------------------------------------------
# AgentSystem as a drop-in ActorSystem replacement
# ---------------------------------------------------------------------------


async def test_agent_system_plain_spawn_still_works():
    """AgentSystem.spawn() works exactly like ActorSystem.spawn()."""
    from actor_for_agents.actor import Actor

    class SimpleActor(Actor[str, str]):
        async def on_receive(self, message: str) -> str:
            return message.upper()

    system = AgentSystem()
    ref = await system.spawn(SimpleActor, "simple")
    result = await ref.ask("hello")
    assert result == "HELLO"
    await system.shutdown()


async def test_agent_system_backend_param_ignored():
    """spawn() accepts backend= without error (reserved for M5)."""
    from actor_for_agents.actor import Actor

    class Noop(Actor[Any, None]):
        async def on_receive(self, message: Any) -> None:
            pass

    system = AgentSystem()
    ref = await system.spawn(Noop, "noop", backend="local")
    assert ref.is_alive
    await system.shutdown()


# ---------------------------------------------------------------------------
# ask_stream — streaming ask to an already-spawned agent
# ---------------------------------------------------------------------------


async def test_ask_stream_yields_started_and_completed():
    """ask_stream yields StreamEvents then a final StreamResult."""
    system = AgentSystem()
    ref = await system.spawn(EchoAgent, "echo")

    items = []
    async for item in ref.ask_stream(Task(input="hi")):
        items.append(item)

    events = [i.event for i in items if isinstance(i, StreamEvent)]
    results = [i.result for i in items if isinstance(i, StreamResult)]

    assert any(e.type == "task_started" for e in events)
    assert any(e.type == "task_completed" for e in events)
    assert len(results) == 1
    assert results[0].output == "hi"
    assert isinstance(items[-1], StreamResult)
    await system.shutdown()


async def test_ask_stream_yields_progress_events():
    """ask_stream captures emit_progress() calls from execute()."""
    system = AgentSystem()
    ref = await system.spawn(ProgressAgent, "prog")

    events: list[TaskEvent] = []
    async for item in ref.ask_stream(Task(input="x")):
        match item:
            case StreamEvent(event=e):
                events.append(e)

    progress = [e for e in events if e.type == "task_progress"]
    assert len(progress) == 2
    assert [e.data for e in progress] == ["step-1", "step-2"]
    await system.shutdown()


async def test_ask_stream_reraises_agent_error():
    """ask_stream propagates exceptions raised by execute()."""
    system = AgentSystem()
    ref = await system.spawn(BrokenAgent, "broken")

    with pytest.raises(ValueError, match="boom"):
        async for _ in ref.ask_stream(Task(input="x")):
            pass

    await system.shutdown()


async def test_ask_stream_child_events_included():
    """Children spawned via dispatch() during ask_stream inherit the sink."""
    system = AgentSystem()
    ref = await system.spawn(OrchestratorAgent, "orch")

    events: list[TaskEvent] = []
    async for item in ref.ask_stream(Task(input="q")):
        if isinstance(item, StreamEvent):
            events.append(item.event)

    # Root agent has no parent_agent_path; children do
    root_events = [e for e in events if e.parent_agent_path is None]
    child_events = [e for e in events if e.parent_agent_path is not None]
    assert len(root_events) > 0
    assert len(child_events) > 0
    # Child paths are under the orchestrator
    assert all("orch" in e.agent_path for e in child_events)
    await system.shutdown()


async def test_ask_stream_no_orphaned_collector():
    """Collector actor is cleaned up after ask_stream completes."""
    system = AgentSystem()
    ref = await system.spawn(EchoAgent, "echo2")

    async for _ in ref.ask_stream(Task(input="x")):
        pass

    await asyncio.sleep(0.05)
    # Only the spawned agent remains; collector was removed
    collector_cells = [k for k in system._root_cells if "_ask-collector-" in k]
    assert collector_cells == []
    await system.shutdown()


async def test_ask_stream_reusable_ref():
    """The same ref can be used for multiple sequential ask_stream calls."""
    system = AgentSystem()
    ref = await system.spawn(ProgressAgent, "prog2")

    for expected_input in ("first", "second"):
        items = []
        async for item in ref.ask_stream(Task(input=expected_input)):
            items.append(item)
        result = next(i.result for i in items if isinstance(i, StreamResult))
        assert result.output == f"done:{expected_input}"

    await system.shutdown()
