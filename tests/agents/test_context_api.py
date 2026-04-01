"""Tests for ActorContext concurrency primitives: ask, sequence, traverse, race, zip, stream."""

import asyncio

import pytest

from actor_for_agents.agents import AgentActor, AgentSystem, Task, TaskResult
from actor_for_agents.agents.task import StreamEvent, StreamResult

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class SlowAgent(AgentActor[str, str]):
    """Returns after a short delay — used to test race ordering."""

    async def execute(self, input: str) -> str:
        delay, value = input.split(":")
        await asyncio.sleep(float(delay))
        return value


class FailAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError(f"fail:{input}")


class ChunkAgent(AgentActor[str, list]):
    async def execute(self, input: str):
        for i in range(3):
            yield f"{input}-{i}"


class UpperAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


# ---------------------------------------------------------------------------
# ask — single dispatch
# ---------------------------------------------------------------------------


async def test_ask_returns_result():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.ask(EchoAgent, Task(input=input))
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="hello"))
    assert result.output == "hello"
    await system.shutdown()


async def test_ask_ephemeral_child_cleaned_up():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.ask(EchoAgent, Task(input=input))
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    await ref.ask(Task(input="x"))
    await system.shutdown()  # hangs if child leaked


async def test_ask_propagates_exception():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.ask(FailAgent, Task(input=input))
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    with pytest.raises(ValueError, match="fail:boom"):
        await ref.ask(Task(input="boom"))
    await system.shutdown()


# ---------------------------------------------------------------------------
# sequence — parallel all, results in order
# ---------------------------------------------------------------------------


async def test_sequence_returns_all_results_in_order():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.sequence([
                (EchoAgent, Task(input="a")),
                (EchoAgent, Task(input="b")),
                (EchoAgent, Task(input="c")),
            ])
            return [r.output for r in results]

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == ["a", "b", "c"]
    await system.shutdown()


async def test_sequence_empty_returns_empty_list():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.context.sequence([])

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == []
    await system.shutdown()


async def test_sequence_cancels_siblings_on_failure():
    """When one task fails, siblings are cancelled and exception propagates."""
    side_effects: list[str] = []

    class SlowSideEffectAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            await asyncio.sleep(2)
            side_effects.append(input)
            return input

    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            await self.context.sequence([
                (FailAgent, Task(input="x")),
                (SlowSideEffectAgent, Task(input="y")),
            ])
            return "unreachable"

    ref = await system.spawn(OrchestratorAgent, "orch")
    with pytest.raises(ValueError, match="fail:x"):
        await ref.ask(Task(input="go"))

    assert side_effects == []  # sibling was cancelled before completing
    await system.shutdown()


# ---------------------------------------------------------------------------
# traverse — map list through same agent
# ---------------------------------------------------------------------------


async def test_traverse_maps_inputs_through_agent():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.traverse(["a", "b", "c"], UpperAgent)
            return [r.output for r in results]

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == ["A", "B", "C"]
    await system.shutdown()


async def test_traverse_empty_returns_empty():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.context.traverse([], UpperAgent)

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == []
    await system.shutdown()


# ---------------------------------------------------------------------------
# race — first wins, cancel rest
# ---------------------------------------------------------------------------


async def test_race_returns_fastest_result():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.race([
                (SlowAgent, Task(input="0.05:slow")),
                (SlowAgent, Task(input="0.001:fast")),
            ])
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="go"))
    assert result.output == "fast"
    await system.shutdown()


async def test_race_cancels_losers():
    """Losing actors are cancelled — no side effects after race resolves."""
    completed: list[str] = []

    class TrackingAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            delay, value = input.split(":")
            await asyncio.sleep(float(delay))
            completed.append(value)
            return value

    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.race([
                (TrackingAgent, Task(input="0.001:winner")),
                (TrackingAgent, Task(input="10:loser")),
            ])
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="go"))
    assert result.output == "winner"
    assert "loser" not in completed
    await system.shutdown()


async def test_race_propagates_exception_if_first_fails():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.race([
                (FailAgent, Task(input="bad")),
                (SlowAgent, Task(input="10:never")),
            ])
            return r.output

    ref = await system.spawn(OrchestratorAgent, "orch")
    with pytest.raises(ValueError, match="fail:bad"):
        await ref.ask(Task(input="go"))
    await system.shutdown()


async def test_race_raises_on_empty():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            await self.context.race([])
            return "unreachable"

    ref = await system.spawn(OrchestratorAgent, "orch")
    with pytest.raises(ValueError, match="race"):
        await ref.ask(Task(input="go"))
    await system.shutdown()


# ---------------------------------------------------------------------------
# zip — exactly two, typed pair
# ---------------------------------------------------------------------------


async def test_zip_returns_pair():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, tuple]):
        async def execute(self, input: str) -> tuple:
            a, b = await self.context.zip(
                (EchoAgent, Task(input="hello")),
                (UpperAgent, Task(input="world")),
            )
            return (a.output, b.output)

    ref = await system.spawn(OrchestratorAgent, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == ("hello", "WORLD")
    await system.shutdown()


async def test_zip_cancels_on_failure():
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, tuple]):
        async def execute(self, input: str) -> tuple:
            return await self.context.zip(
                (FailAgent, Task(input="bad")),
                (SlowAgent, Task(input="10:never")),
            )

    ref = await system.spawn(OrchestratorAgent, "orch")
    with pytest.raises(ValueError, match="fail:bad"):
        await ref.ask(Task(input="x"))
    await system.shutdown()


# ---------------------------------------------------------------------------
# stream — streaming dispatch
# ---------------------------------------------------------------------------


async def test_stream_yields_child_chunks():
    """stream() propagates child task_chunk events to the caller."""
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str):
            async for item in self.context.stream(ChunkAgent, Task(input=input)):
                match item:
                    case StreamEvent(event=e) if e.type == "task_chunk":
                        yield e.data
                    case StreamResult():
                        pass

    ref = await system.spawn(OrchestratorAgent, "orch")
    chunks = []
    async for item in ref.ask_stream(Task(input="q")):
        match item:
            case StreamEvent(event=e) if e.type == "task_chunk":
                chunks.append(e.data)
            case StreamResult():
                pass

    assert chunks == ["q-0", "q-1", "q-2"]
    await system.shutdown()


async def test_stream_ephemeral_child_cleaned_up():
    """Ephemeral child is stopped after stream ends — no leaks."""
    system = AgentSystem()

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str):
            async for item in self.context.stream(ChunkAgent, Task(input=input)):
                match item:
                    case StreamEvent(event=e) if e.type == "task_chunk":
                        yield e.data
                    case StreamResult():
                        pass

    ref = await system.spawn(OrchestratorAgent, "orch")
    async for _ in ref.ask_stream(Task(input="x")):
        pass

    await system.shutdown()  # hangs if child leaked
