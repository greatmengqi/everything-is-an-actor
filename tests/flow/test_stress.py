"""Stress tests for Flow API — deep chains, wide parallelism, nested composition."""

import asyncio
import time

import pytest

from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import (
    Continue,
    Done,
    agent,
    interpret,
    loop,
    pure,
    race,
    zip_all,
)

pytestmark = pytest.mark.anyio


# ── Stub agents ──────────────────────────────────────────


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Append(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input + "."


class CountDown(AgentActor[int, Continue[int] | Done[str]]):
    async def execute(self, input: int) -> Continue[int] | Done[str]:
        if input <= 0:
            return Done(value="done")
        return Continue(value=input - 1)


class SlowEcho(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.01)
        return input


# ── Deep chain stress ────────────────────────────────────


class TestDeepChain:
    async def test_flat_map_chain_50(self):
        """50-step sequential pipeline."""
        system = AgentSystem()
        try:
            f = agent(Echo)
            for _ in range(49):
                f = f.flat_map(agent(Append))
            result = await interpret(f, "start", system)
            assert result == "start" + "." * 49
        finally:
            await system.shutdown()

    async def test_map_chain_100(self):
        """100 pure map transformations — no actor overhead."""
        system = AgentSystem()
        try:
            f = agent(Echo)
            for i in range(100):
                f = f.map(lambda x, i=i: f"{x}+{i}")
            result = await interpret(f, "s", system)
            # Verify all 100 maps applied
            assert result.count("+") == 100
        finally:
            await system.shutdown()

    async def test_nested_recover_chain_20(self):
        """20 nested recovers — only the innermost triggers."""

        class FailN(AgentActor[int, int]):
            async def execute(self, input: int) -> int:
                if input > 0:
                    raise ValueError(f"fail-{input}")
                return input

        system = AgentSystem()
        try:
            f = agent(FailN)
            for i in range(20):
                f = f.recover(lambda e: 0)  # recover to 0, which won't fail
            result = await interpret(f, 5, system)
            assert result == 0
        finally:
            await system.shutdown()


# ── Wide parallelism stress ──────────────────────────────


class TestWideParallel:
    async def test_zip_all_10(self):
        """10-way parallel execution."""
        system = AgentSystem()
        try:
            flows = [agent(Echo) for _ in range(10)]
            f = zip_all(*flows)
            inputs = [f"item-{i}" for i in range(10)]
            result = await interpret(f, inputs, system)
            assert result == inputs
        finally:
            await system.shutdown()

    async def test_zip_all_concurrency(self):
        """10 slow agents should complete in ~0.01s not ~0.1s."""
        system = AgentSystem()
        try:
            flows = [agent(SlowEcho) for _ in range(10)]
            f = zip_all(*flows)
            inputs = [f"item-{i}" for i in range(10)]
            start = time.monotonic()
            result = await interpret(f, inputs, system)
            elapsed = time.monotonic() - start
            assert len(result) == 10
            assert elapsed < 0.15  # parallel, not 10 * 0.01 = 0.1
        finally:
            await system.shutdown()

    async def test_race_10(self):
        """10-way race — instant agent wins."""
        system = AgentSystem()
        try:
            instant = agent(Echo)
            slow_agents = [agent(SlowEcho) for _ in range(9)]
            f = race(instant, *slow_agents)
            result = await interpret(f, "fast", system)
            assert result == "fast"
        finally:
            await system.shutdown()


# ── Nested composition stress ────────────────────────────


class TestNestedComposition:
    async def test_zip_of_flat_maps(self):
        """Parallel branches each containing sequential chains."""
        system = AgentSystem()
        try:
            left = agent(Echo).flat_map(agent(Append)).flat_map(agent(Append))
            right = agent(Echo).flat_map(agent(Append))
            f = left.zip(right)
            result = await interpret(f, ("a", "b"), system)
            assert result == ("a..", "b.")
        finally:
            await system.shutdown()

    async def test_flat_map_of_zips(self):
        """Sequential chain where each step is a parallel operation."""
        system = AgentSystem()
        try:
            step1 = agent(Echo).zip(agent(Echo))
            merge = pure(lambda pair: f"{pair[0]}+{pair[1]}")
            step2 = agent(Append)
            f = step1.map(lambda pair: f"{pair[0]}+{pair[1]}").flat_map(step2)
            result = await interpret(f, ("hello", "world"), system)
            assert result == "hello+world."
        finally:
            await system.shutdown()

    async def test_branch_inside_loop(self):
        """Loop body contains a branch."""

        class Classifier(AgentActor[int, Continue[int] | Done[str]]):
            async def execute(self, input: int) -> Continue[int] | Done[str]:
                if input <= 0:
                    return Done(value="reached-zero")
                return Continue(value=input - 1)

        system = AgentSystem()
        try:
            f = loop(agent(Classifier), max_iter=20)
            result = await interpret(f, 5, system)
            assert result == "reached-zero"
        finally:
            await system.shutdown()

    async def test_recover_inside_loop(self):
        """Loop body that sometimes fails, recovered each iteration."""
        call_count = 0

        class FlakeyAgent(AgentActor[int, Continue[int] | Done[str]]):
            async def execute(self, input: int) -> Continue[int] | Done[str]:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ValueError("flakey")
                return Done(value="stabilized")

        system = AgentSystem()
        try:
            body = agent(FlakeyAgent).recover(lambda e: Continue(value=0))
            f = loop(body, max_iter=10)
            result = await interpret(f, 0, system)
            assert result == "stabilized"
        finally:
            await system.shutdown()


# ── Loop stress ──────────────────────────────────────────


class TestLoopStress:
    async def test_loop_100_iterations(self):
        """Loop that counts down from 100."""
        system = AgentSystem()
        try:
            f = loop(agent(CountDown), max_iter=200)
            result = await interpret(f, 100, system)
            assert result == "done"
        finally:
            await system.shutdown()

    async def test_loop_exactly_at_max_iter(self):
        """Loop that terminates on exactly the last allowed iteration."""

        class ExactTerminator(AgentActor[int, Continue[int] | Done[str]]):
            _calls = 0
            async def execute(self, input: int) -> Continue[int] | Done[str]:
                ExactTerminator._calls += 1
                if ExactTerminator._calls >= 5:
                    return Done(value="exactly-5")
                return Continue(value=input)

        ExactTerminator._calls = 0
        system = AgentSystem()
        try:
            f = loop(agent(ExactTerminator), max_iter=5)
            result = await interpret(f, 0, system)
            assert result == "exactly-5"
        finally:
            await system.shutdown()

    async def test_loop_max_iter_1(self):
        """max_iter=1: body gets exactly one chance."""
        system = AgentSystem()
        try:
            # CountDown(0) → Done immediately
            assert await interpret(loop(agent(CountDown), max_iter=1), 0, system) == "done"
            # CountDown(1) → Continue(0), then max_iter exceeded
            with pytest.raises(RuntimeError, match="max_iter"):
                await interpret(loop(agent(CountDown), max_iter=1), 1, system)
        finally:
            await system.shutdown()


# ── Concurrent flow execution ────────────────────────────


class TestConcurrentFlows:
    async def test_multiple_flows_on_same_system(self):
        """Run multiple independent flows concurrently on one AgentSystem."""
        system = AgentSystem()
        try:
            f1 = agent(Echo).map(lambda x: f"flow1:{x}")
            f2 = agent(Echo).map(lambda x: f"flow2:{x}")
            f3 = agent(Echo).map(lambda x: f"flow3:{x}")

            r1, r2, r3 = await asyncio.gather(
                interpret(f1, "a", system),
                interpret(f2, "b", system),
                interpret(f3, "c", system),
            )
            assert r1 == "flow1:a"
            assert r2 == "flow2:b"
            assert r3 == "flow3:c"
        finally:
            await system.shutdown()

    async def test_same_flow_reused_multiple_times(self):
        """Same Flow object interpreted multiple times (Flow is data, stateless)."""
        system = AgentSystem()
        try:
            f = agent(Echo).map(str.upper)
            results = [await interpret(f, s, system) for s in ["a", "b", "c"]]
            assert results == ["A", "B", "C"]
        finally:
            await system.shutdown()


# ── Composition law smoke tests ──────────────────────────


class TestCompositionLaws:
    async def test_flat_map_left_identity(self):
        """pure(x).flat_map(f) ≡ f(x) — left identity of Kleisli composition."""
        system = AgentSystem()
        try:
            f = agent(Append)
            lhs = pure(lambda _: "hello").flat_map(f)
            rhs = f
            assert await interpret(lhs, "ignored", system) == await interpret(rhs, "hello", system)
        finally:
            await system.shutdown()

    async def test_map_identity(self):
        """flow.map(id) ≡ flow — functor identity law."""
        system = AgentSystem()
        try:
            f = agent(Echo)
            mapped = f.map(lambda x: x)
            assert await interpret(f, "test", system) == await interpret(mapped, "test", system)
        finally:
            await system.shutdown()

    async def test_recover_no_error_identity(self):
        """flow.recover(handler) ≡ flow when no error — recover is transparent on success."""
        system = AgentSystem()
        try:
            f = agent(Echo)
            recovered = f.recover(lambda e: "should-not-reach")
            assert await interpret(f, "test", system) == await interpret(recovered, "test", system)
        finally:
            await system.shutdown()
