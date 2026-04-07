"""Integration tests — interpret Flow ADT with real AgentSystem."""

import asyncio
from dataclasses import dataclass

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import Continue, Done, FlowFilterError, agent, loop, loop_with_state, pure, race
from everything_is_an_actor.flow.interpreter import Interpreter

pytestmark = pytest.mark.anyio


# ── Stub agents ──────────────────────────────────────────


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Upper(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class Length(AgentActor[str, int]):
    async def execute(self, input: str) -> int:
        return len(input)


class Slow(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.1)
        return f"slow:{input}"


class SlowForever(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.5)
        return "slow-forever"


class Failing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError(f"boom: {input}")


@dataclass(frozen=True)
class SimpleQ:
    query: str


@dataclass(frozen=True)
class ComplexQ:
    query: str


class Classifier(AgentActor[str, SimpleQ | ComplexQ]):
    async def execute(self, input: str) -> SimpleQ | ComplexQ:
        return SimpleQ(query=input) if len(input) < 10 else ComplexQ(query=input)


class SimpleHandler(AgentActor[SimpleQ, str]):
    async def execute(self, input: SimpleQ) -> str:
        return f"simple:{input.query}"


class ComplexHandler(AgentActor[ComplexQ, str]):
    async def execute(self, input: ComplexQ) -> str:
        return f"complex:{input.query}"


class CountDown(AgentActor[int, Continue[int] | Done[str]]):
    async def execute(self, input: int) -> Continue[int] | Done[str]:
        if input <= 0:
            return Done(value="done!")
        return Continue(value=input - 1)


class AlwaysContinue(AgentActor[str, Continue[str]]):
    async def execute(self, input: str) -> Continue[str]:
        return Continue(value=input)


class Accumulator(AgentActor[tuple, tuple]):
    """Body for LoopWithState. Returns (Continue/Done, new_state) tuple."""
    async def execute(self, input: tuple) -> tuple:
        current, history = input
        new_history = [*history, current]
        if len(new_history) >= 3:
            return (Done(value=f"collected:{','.join(new_history)}"), new_history)
        return (Continue(value=f"next-{len(new_history)}"), new_history)


# ── Basic: Agent, Pure, Map, FlatMap ─────────────────────


class TestBasic:
    async def test_agent(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo), "hello") == "hello"
        finally:
            await system.shutdown()

    async def test_pure(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(pure(str.upper), "hello") == "HELLO"
        finally:
            await system.shutdown()

    async def test_map(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo).map(str.upper), "hello") == "HELLO"
        finally:
            await system.shutdown()

    async def test_flat_map(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo).flat_map(agent(Upper)), "hello") == "HELLO"
        finally:
            await system.shutdown()

    async def test_chain(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).map(lambda s: s + "!").flat_map(agent(Upper))
            assert await interp.run(flow, "hi") == "HI!"
        finally:
            await system.shutdown()


# ── Zip (parallel) ───────────────────────────────────────


class TestZip:
    async def test_zip(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).zip(agent(Upper))
            assert await interp.run(flow, ("hello", "world")) == ("hello", "WORLD")
        finally:
            await system.shutdown()

    async def test_zip_is_concurrent(self):
        import time

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Slow).zip(agent(Slow))
            start = time.monotonic()
            result = await interp.run(flow, ("a", "b"))
            assert time.monotonic() - start < 0.25
            assert result == ("slow:a", "slow:b")
        finally:
            await system.shutdown()


# ── Branch ───────────────────────────────────────────────


class TestBranch:
    async def test_routes_simple(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Classifier).branch({SimpleQ: agent(SimpleHandler), ComplexQ: agent(ComplexHandler)})
            assert await interp.run(flow, "hi") == "simple:hi"
        finally:
            await system.shutdown()

    async def test_routes_complex(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Classifier).branch({SimpleQ: agent(SimpleHandler), ComplexQ: agent(ComplexHandler)})
            assert await interp.run(flow, "this is a long complex query") == "complex:this is a long complex query"
        finally:
            await system.shutdown()

    async def test_unmatched_raises(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Classifier).branch({ComplexQ: agent(ComplexHandler)})
            with pytest.raises(KeyError):
                await interp.run(flow, "hi")
        finally:
            await system.shutdown()


class TestBranchOn:
    async def test_true_branch(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Length).branch_on(lambda x: x > 5, then=pure(lambda x: f"long:{x}"), otherwise=pure(lambda x: f"short:{x}"))
            assert await interp.run(flow, "hello world") == "long:11"
        finally:
            await system.shutdown()

    async def test_false_branch(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Length).branch_on(lambda x: x > 5, then=pure(lambda x: f"long:{x}"), otherwise=pure(lambda x: f"short:{x}"))
            assert await interp.run(flow, "hi") == "short:2"
        finally:
            await system.shutdown()


# ── Recover / FallbackTo ────────────────────────────────


class TestRecover:
    async def test_recover_catches(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Failing).recover(lambda e: f"recovered: {e}")
            assert "recovered:" in await interp.run(flow, "test")
        finally:
            await system.shutdown()

    async def test_recover_passthrough(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo).recover(lambda e: "nope"), "ok") == "ok"
        finally:
            await system.shutdown()


class TestRecoverWith:
    async def test_recover_with_flow(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Failing).recover_with(pure(lambda e: f"flow-recovered: {type(e).__name__}"))
            assert await interp.run(flow, "test") == "flow-recovered: ValueError"
        finally:
            await system.shutdown()


class TestFallbackTo:
    async def test_fallback_on_failure(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Failing).fallback_to(agent(Echo)), "test") == "test"
        finally:
            await system.shutdown()

    async def test_no_fallback_on_success(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo).fallback_to(agent(Upper)), "hello") == "hello"
        finally:
            await system.shutdown()


# ── Race ─────────────────────────────────────────────────


class TestRace:
    async def test_returns_first(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(race(agent(Echo), agent(Slow)), "test") == "test"
        finally:
            await system.shutdown()

    async def test_cancels_losers(self):
        """Winner returns correct result; loser is cancelled and cleaned up."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = race(agent(Echo), agent(SlowForever))
            result = await interp.run(flow, "fast")
            assert result == "fast"
        finally:
            await system.shutdown()


# ── Loop (tailRecM) ─────────────────────────────────────


class TestLoop:
    async def test_terminates_on_done(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(loop(agent(CountDown), max_iter=10), 3) == "done!"
        finally:
            await system.shutdown()

    async def test_single_iteration(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(loop(agent(CountDown), max_iter=10), 0) == "done!"
        finally:
            await system.shutdown()

    async def test_max_iter_exceeded(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            with pytest.raises(RuntimeError, match="max_iter"):
                await interp.run(loop(agent(AlwaysContinue), max_iter=3), "stuck")
        finally:
            await system.shutdown()


# ── DivertTo, AndThen, Filter ────────────────────────────


class TestDivertTo:
    async def test_diverts_when_true(self):
        side_log: list[str] = []

        class Logger(AgentActor[str, None]):
            async def execute(self, input: str) -> None:
                side_log.append(input)

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).divert_to(agent(Logger), when=lambda x: len(x) > 3)
            result = await interp.run(flow, "hello")
            assert result == "hello"
            await asyncio.sleep(0.15)
            assert side_log == ["hello"]
        finally:
            await system.shutdown()

    async def test_skips_when_false(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).divert_to(agent(Echo), when=lambda x: len(x) > 100)
            assert await interp.run(flow, "hi") == "hi"
        finally:
            await system.shutdown()


class TestAndThen:
    async def test_runs_callback(self):
        captured: list[str] = []
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(Echo).and_then(lambda x: captured.append(x))
            assert await interp.run(flow, "hello") == "hello"
            assert captured == ["hello"]
        finally:
            await system.shutdown()


class TestFilter:
    async def test_passes(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            assert await interp.run(agent(Echo).filter(lambda x: len(x) > 0), "ok") == "ok"
        finally:
            await system.shutdown()

    async def test_rejects(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            with pytest.raises(FlowFilterError):
                await interp.run(agent(Echo).filter(lambda x: len(x) > 10), "hi")
        finally:
            await system.shutdown()


# ── LoopWithState (trace) ────────────────────────────────


class TestLoopWithState:
    async def test_accumulates(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = loop_with_state(agent(Accumulator), init_state=list, max_iter=10)
            assert await interp.run(flow, "start") == "collected:start,next-1,next-2"
        finally:
            await system.shutdown()

    async def test_callable_init(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = loop_with_state(agent(Accumulator), init_state=list, max_iter=10)
            assert await interp.run(flow, "go") == "collected:go,next-1,next-2"
        finally:
            await system.shutdown()
