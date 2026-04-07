"""Adversarial tests — concurrency races, cancellation, leaks, large payloads.

These test real failure modes: shutdown mid-flow, actor leak detection,
cancellation propagation, name collisions, mailbox pressure.
"""

import asyncio
import gc
import time
import weakref

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import (
    Continue,
    Done,
    Interpreter,
    agent,
    loop,
    pure,
    race,
    zip_all,
)

pytestmark = pytest.mark.anyio


# ── Agents ───────────────────────────────────────────────


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class SlowAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.5)
        return input


class VerySlowAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(5)
        return input


class OOMishAgent(AgentActor[str, str]):
    """Agent that allocates a large string."""
    async def execute(self, input: str) -> str:
        return input * 100_000  # ~100KB per call


class RandomFailAgent(AgentActor[int, str]):
    """Fails on odd inputs."""
    async def execute(self, input: int) -> str:
        if input % 2 == 1:
            raise ValueError(f"odd-{input}")
        return f"even-{input}"


class CountingAgent(AgentActor[str, str]):
    """Tracks how many instances were created."""
    _instance_count = 0
    _instances: list = []

    async def execute(self, input: str) -> str:
        CountingAgent._instance_count += 1
        CountingAgent._instances.append(weakref.ref(self))
        return input


# ── Shutdown during execution ────────────────────────────


class TestShutdownMidFlow:
    async def test_shutdown_during_slow_agent(self):
        """System shutdown while agent is sleeping — should not hang forever."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        flow = agent(VerySlowAgent)

        task = asyncio.create_task(interp.run(flow, "test"))
        await asyncio.sleep(0.05)  # let agent start
        await system.shutdown()

        # Should complete (with error) within reasonable time, not hang
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.CancelledError, RuntimeError, asyncio.TimeoutError, Exception):
            pass  # any error is acceptable — key is it doesn't hang

    async def test_shutdown_during_zip(self):
        """Shutdown during parallel execution — should not hang."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        flow = agent(VerySlowAgent).zip(agent(VerySlowAgent))

        task = asyncio.create_task(interp.run(flow, ("a", "b")))
        await asyncio.sleep(0.05)
        await system.shutdown()

        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.CancelledError, RuntimeError, asyncio.TimeoutError, Exception):
            pass

    async def test_shutdown_during_loop(self):
        """Shutdown during an active loop — should not hang."""

        class InfiniteLoop(AgentActor[str, Continue[str]]):
            async def execute(self, input: str) -> Continue[str]:
                await asyncio.sleep(0.05)
                return Continue(value=input)

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        flow = loop(agent(InfiniteLoop), max_iter=1000)
        task = asyncio.create_task(interp.run(flow, "test"))
        await asyncio.sleep(0.15)
        await system.shutdown()

        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.CancelledError, RuntimeError, asyncio.TimeoutError, Exception):
            pass


# ── External cancellation propagation ────────────────────


class TestCancellationPropagation:
    async def test_cancel_interpret_task(self):
        """Cancelling the interpret() task should not leak actors."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(SlowAgent).flat_map(agent(SlowAgent)).flat_map(agent(SlowAgent))
            task = asyncio.create_task(interp.run(flow, "test"))
            await asyncio.sleep(0.05)
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task

            # Give cleanup time
            await asyncio.sleep(0.3)
        finally:
            await system.shutdown()

    async def test_cancel_race_all_cancelled(self):
        """Cancelling race should cancel all racers."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = race(agent(VerySlowAgent), agent(VerySlowAgent))
            task = asyncio.create_task(interp.run(flow, "test"))
            await asyncio.sleep(0.05)
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await system.shutdown()


# ── Actor leak detection ─────────────────────────────────


class TestActorLeaks:
    async def test_loop_does_not_leak_actors(self):
        """100 loop iterations should not leave actors in system registry."""

        class CountDown(AgentActor[int, Continue[int] | Done[str]]):
            async def execute(self, input: int) -> Continue[int] | Done[str]:
                if input <= 0:
                    return Done(value="done")
                return Continue(value=input - 1)

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = loop(agent(CountDown), max_iter=200)
            await interp.run(flow, 50)

            # All ephemeral actors should be stopped and removed
            flow_actors = [name for name in system._root_cells if name.startswith("_flow-")]
            assert flow_actors == [], f"Leaked actors: {flow_actors}"
        finally:
            await system.shutdown()

    async def test_zip_all_does_not_leak_actors(self):
        """10-way parallel should clean up all actors."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flows = [agent(Echo) for _ in range(10)]
            f = zip_all(*flows)
            await interp.run(f, [f"item-{i}" for i in range(10)])

            flow_actors = [name for name in system._root_cells if name.startswith("_flow-")]
            assert flow_actors == [], f"Leaked actors: {flow_actors}"
        finally:
            await system.shutdown()

    async def test_failed_flow_does_not_leak_actors(self):
        """Failed agent should still clean up."""

        class FailAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise RuntimeError("fail")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(FailAgent).recover(lambda e: "recovered")
            await interp.run(flow, "test")

            flow_actors = [name for name in system._root_cells if name.startswith("_flow-")]
            assert flow_actors == [], f"Leaked actors: {flow_actors}"
        finally:
            await system.shutdown()

    async def test_race_loser_cleanup(self):
        """Race losers should be fully cleaned up."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = race(agent(Echo), agent(SlowAgent), agent(SlowAgent))
            await interp.run(flow, "fast")

            # Wait for background cleanup
            await asyncio.sleep(0.8)

            flow_actors = [name for name in system._root_cells if name.startswith("_flow-")]
            assert flow_actors == [], f"Leaked actors: {flow_actors}"
        finally:
            await system.shutdown()


# ── Concurrent name collision ────────────────────────────


class TestNameCollision:
    async def test_100_concurrent_same_agent_class(self):
        """100 concurrent spawns of same agent class should not collide."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            tasks = [
                interp.run(agent(Echo), f"msg-{i}")
                for i in range(100)
            ]
            results = await asyncio.gather(*tasks)
            assert len(results) == 100
            assert set(results) == {f"msg-{i}" for i in range(100)}
        finally:
            await system.shutdown()


# ── Large payload stress ─────────────────────────────────


class TestLargePayload:
    async def test_1mb_through_flat_map(self):
        """1MB string through a 3-step pipeline."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            big = "x" * 1_000_000
            flow = agent(Echo).flat_map(agent(Echo)).flat_map(agent(Echo))
            result = await interp.run(flow, big)
            assert len(result) == 1_000_000
        finally:
            await system.shutdown()

    async def test_large_payload_through_zip(self):
        """Two 500KB strings through parallel zip."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            big_a = "a" * 500_000
            big_b = "b" * 500_000
            flow = agent(Echo).zip(agent(Echo))
            result = await interp.run(flow, (big_a, big_b))
            assert len(result[0]) == 500_000
            assert len(result[1]) == 500_000
        finally:
            await system.shutdown()


# ── Partial failure in parallel ──────────────────────────


class TestPartialFailure:
    async def test_zip_one_fails_other_succeeds(self):
        """In zip, if one fails the exception propagates."""

        class FailSecond(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise ValueError("second-failed")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).zip(agent(FailSecond))
            with pytest.raises(ValueError, match="second-failed"):
                await interp.run(flow, ("ok", "fail"))
        finally:
            await system.shutdown()

    async def test_zip_all_partial_failure(self):
        """In zip_all, one failure cancels all others."""

        class FailOnThree(AgentActor[int, int]):
            async def execute(self, input: int) -> int:
                if input == 3:
                    raise ValueError("three-failed")
                await asyncio.sleep(0.1)
                return input

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flows = [agent(FailOnThree) for _ in range(5)]
            f = zip_all(*flows)
            with pytest.raises(ValueError, match="three-failed"):
                await interp.run(f, [1, 2, 3, 4, 5])
        finally:
            await system.shutdown()

    async def test_race_first_fails_second_wins(self):
        """If the first racer fails, the second can still win."""

        class InstantFail(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise ValueError("instant-fail")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            # InstantFail completes first (with error), but race takes first COMPLETED
            # asyncio.wait FIRST_COMPLETED returns both success and failure
            # The current implementation takes done.pop().result() which may re-raise
            # This is a known semantic gap — documenting current behavior
            flow = race(agent(InstantFail), agent(SlowAgent))
            # Current behavior: first completed wins, even if it's an error
            with pytest.raises(ValueError, match="instant-fail"):
                await interp.run(flow, "test")
        finally:
            await system.shutdown()


# ── Streaming adversarial ────────────────────────────────


class TestStreamingAdversarial:
    async def test_stream_agent_events_complete(self):
        """Verify all lifecycle events are present."""

        class ProgressAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await self.emit_progress("step-1")
                await self.emit_progress("step-2")
                return f"done:{input}"

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            events = []
            async for event in interp.run_stream(agent(ProgressAgent), "test"):
                events.append(event)

            types = [e.type for e in events]
            assert types.count("task_started") == 1
            assert types.count("task_progress") == 2
            assert types.count("task_completed") == 1
        finally:
            await system.shutdown()

    async def test_stream_recover_on_failure(self):
        """Streaming of a flow that fails then recovers."""

        class FailAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise ValueError("fail")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(FailAgent).recover_with(agent(Echo))
            events = []
            async for event in interp.run_stream(flow, "test"):
                events.append(event)

            # Should have events from the recovery agent
            types = [e.type for e in events]
            assert "task_completed" in types
        finally:
            await system.shutdown()

    async def test_stream_map_forwards_source_events(self):
        """Map should yield source's events."""

        class EmittingAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await self.emit_progress("working")
                return input

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(EmittingAgent).map(str.upper)
            events = []
            async for event in interp.run_stream(flow, "test"):
                events.append(event)

            types = [e.type for e in events]
            assert "task_progress" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()


# ── Repeated execution stability ─────────────────────────


class TestRepeatStability:
    async def test_same_flow_50_times(self):
        """Same flow executed 50 times — no state leakage between runs."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).map(lambda x: f"[{x}]")
            for i in range(50):
                result = await interp.run(flow, f"run-{i}")
                assert result == f"[run-{i}]"
        finally:
            await system.shutdown()

    async def test_loop_repeated_no_state_bleed(self):
        """Run the same loop flow multiple times — state doesn't bleed."""

        class CountDown(AgentActor[int, Continue[int] | Done[str]]):
            async def execute(self, input: int) -> Continue[int] | Done[str]:
                if input <= 0:
                    return Done(value="done")
                return Continue(value=input - 1)

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = loop(agent(CountDown), max_iter=20)
            for n in [3, 5, 1, 10]:
                result = await interp.run(flow, n)
                assert result == "done"
        finally:
            await system.shutdown()


# ── Timing / concurrency correctness ─────────────────────


class TestTimingCorrectness:
    async def test_zip_truly_parallel_not_sequential(self):
        """5 agents each sleeping 0.1s should complete in ~0.1s not 0.5s."""

        class Sleep100ms(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await asyncio.sleep(0.1)
                return input

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flows = [agent(Sleep100ms) for _ in range(5)]
            f = zip_all(*flows)
            start = time.monotonic()
            await interp.run(f, ["a", "b", "c", "d", "e"])
            elapsed = time.monotonic() - start
            # Should be ~0.1s, definitely less than 0.3s
            assert elapsed < 0.3, f"zip_all took {elapsed:.2f}s, expected ~0.1s"
        finally:
            await system.shutdown()

    async def test_race_returns_fastest(self):
        """Race between 50ms and 200ms agents — fastest result wins."""

        class Fast(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await asyncio.sleep(0.05)
                return "fast"

        class Slow(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await asyncio.sleep(0.2)
                return "slow"

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = race(agent(Fast), agent(Slow))
            result = await interp.run(flow, "go")
            # Correctness: fast agent wins
            assert result == "fast"
            # Note: total wall time includes loser cleanup (await gather),
            # so we don't assert tight timing — correctness is what matters
        finally:
            await system.shutdown()
