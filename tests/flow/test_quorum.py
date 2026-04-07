"""Tests for at_least quorum combinator."""

import asyncio

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import agent, pure
from everything_is_an_actor.flow.interpreter import Interpreter
from everything_is_an_actor.flow.quorum import QuorumResult, at_least

pytestmark = pytest.mark.anyio


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Upper(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class Failing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError("boom")


class SlowFailing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.05)
        raise RuntimeError("slow boom")


# ── Validation ──────────────────────────────────


class TestAtLeastValidation:
    def test_n_less_than_1(self):
        with pytest.raises(ValueError, match="n must be >= 1"):
            at_least(0, agent(Echo))

    def test_n_exceeds_flows(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            at_least(3, agent(Echo), agent(Upper))

    def test_no_flows(self):
        with pytest.raises(ValueError, match="at least 1 flow"):
            at_least(1)


# ── Runtime ─────────────────────────────────────


class TestAtLeastRuntime:
    async def test_all_succeed(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(2, agent(Echo), agent(Upper))
            result = await interp.run(flow, "hello")
            assert isinstance(result, QuorumResult)
            assert len(result.succeeded) == 2
            assert "hello" in result.succeeded
            assert "HELLO" in result.succeeded
            assert result.failed == ()
        finally:
            await system.shutdown()

    async def test_partial_failure_within_quorum(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo), agent(Failing))
            result = await interp.run(flow, "hi")
            assert len(result.succeeded) == 1
            assert result.succeeded[0] == "hi"
            assert len(result.failed) == 1
            assert isinstance(result.failed[0], ValueError)
        finally:
            await system.shutdown()

    async def test_quorum_not_met_raises(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(2, agent(Echo), agent(Failing), agent(Failing))
            with pytest.raises(RuntimeError, match="Quorum failed"):
                await interp.run(flow, "hi")
        finally:
            await system.shutdown()

    async def test_single_flow(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo))
            result = await interp.run(flow, "solo")
            assert result.succeeded == ("solo",)
            assert result.failed == ()
        finally:
            await system.shutdown()

    async def test_all_fail_raises(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Failing), agent(Failing))
            with pytest.raises(RuntimeError, match="Quorum failed"):
                await interp.run(flow, "x")
        finally:
            await system.shutdown()

    async def test_memory_error_propagates(self):
        class MemoryBomb(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise MemoryError("out of memory")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo), agent(MemoryBomb))
            with pytest.raises(MemoryError):
                await interp.run(flow, "x")
        finally:
            await system.shutdown()


# ── QuorumResult type ───────────────────────────


class TestQuorumResult:
    def test_frozen(self):
        qr = QuorumResult(succeeded=("a",), failed=())
        with pytest.raises(AttributeError):
            qr.succeeded = ()

    def test_fields(self):
        err = ValueError("e")
        qr = QuorumResult(succeeded=("ok",), failed=(err,))
        assert qr.succeeded == ("ok",)
        assert qr.failed == (err,)
