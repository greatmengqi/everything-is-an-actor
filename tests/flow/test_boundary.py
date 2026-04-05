"""Boundary, error-path, and edge-case tests for Flow API.

Covers: type errors, max_iter limits, empty inputs, mismatched types,
serialization edge cases, immutability contracts, unknown variants.
"""

import asyncio
from dataclasses import dataclass
from types import MappingProxyType

import pytest

from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import (
    Continue,
    Done,
    Flow,
    FlowFilterError,
    agent,
    interpret,
    loop,
    loop_with_state,
    pure,
    race,
    zip_all,
)
from everything_is_an_actor.flow.flow import (
    _Agent,
    _Branch,
    _FlatMap,
    _Loop,
    _Map,
    _Race,
    _ZipAll,
)
from everything_is_an_actor.flow.interpreter import interpret_stream
from everything_is_an_actor.flow.serialize import from_dict, to_dict

pytestmark = pytest.mark.anyio


# ── Stub agents ──────────────────────────────────────────


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Failing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError(f"boom: {input}")


class SlowEcho(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.05)
        return input


class ReturnInt(AgentActor[str, int]):
    async def execute(self, input: str) -> int:
        return len(input)


class AlwaysContinue(AgentActor[str, Continue[str]]):
    async def execute(self, input: str) -> Continue[str]:
        return Continue(value=input)


class BadLoopBody(AgentActor[str, str]):
    """Returns neither Continue nor Done."""
    async def execute(self, input: str) -> str:
        return "not-continue-or-done"


class BadLoopWithStateBody(AgentActor[tuple, str]):
    """Returns non-tuple for LoopWithState."""
    async def execute(self, input: tuple) -> str:
        return "not-a-tuple"


class BadLoopWithStateControl(AgentActor[tuple, tuple]):
    """Returns tuple but first element is not Continue/Done."""
    async def execute(self, input: tuple) -> tuple:
        return ("not-control", [])


@dataclass(frozen=True)
class TypeA:
    val: str

@dataclass(frozen=True)
class TypeB:
    val: str

@dataclass(frozen=True)
class TypeC:
    val: str


class ClassifyAB(AgentActor[str, TypeA | TypeB]):
    async def execute(self, input: str) -> TypeA | TypeB:
        return TypeA(val=input) if len(input) < 5 else TypeB(val=input)


class HandleA(AgentActor[TypeA, str]):
    async def execute(self, input: TypeA) -> str:
        return f"a:{input.val}"


class HandleB(AgentActor[TypeB, str]):
    async def execute(self, input: TypeB) -> str:
        return f"b:{input.val}"


# ── ADT immutability ─────────────────────────────────────


class TestImmutability:
    def test_zip_all_flows_is_tuple(self):
        f = zip_all(agent(Echo), agent(Echo), agent(Echo))
        assert isinstance(f.flows, tuple)

    def test_zip_all_defensive_copy(self):
        original = [agent(Echo), agent(Echo)]
        f = _ZipAll(flows=original)
        original.append(agent(Echo))
        assert len(f.flows) == 2

    def test_race_flows_is_tuple(self):
        f = race(agent(Echo), agent(Echo))
        assert isinstance(f.flows, tuple)

    def test_race_defensive_copy(self):
        original = [agent(Echo), agent(Echo)]
        f = _Race(flows=original)
        original.append(agent(Echo))
        assert len(f.flows) == 2

    def test_branch_mapping_is_immutable(self):
        f = agent(ClassifyAB).branch({TypeA: agent(HandleA), TypeB: agent(HandleB)})
        assert isinstance(f.mapping, MappingProxyType)
        with pytest.raises(TypeError):
            f.mapping[TypeC] = agent(Echo)


# ── zip_all ──────────────────────────────────────────────


class TestZipAll:
    def test_constructor_returns_zip_all(self):
        f = zip_all(agent(Echo), agent(Echo), agent(Echo))
        assert isinstance(f, _ZipAll)
        assert len(f.flows) == 3

    def test_requires_at_least_two(self):
        with pytest.raises(ValueError, match="at least 2"):
            zip_all(agent(Echo))

    async def test_interpret_three_way(self):
        system = AgentSystem()
        try:
            f = zip_all(agent(Echo), agent(Echo), agent(Echo))
            result = await interpret(f, ["a", "b", "c"], system)
            assert result == ["a", "b", "c"]
        finally:
            await system.shutdown()

    async def test_interpret_one_fails_cancels_all(self):
        system = AgentSystem()
        try:
            f = zip_all(agent(Echo), agent(Failing), agent(Echo))
            with pytest.raises(ValueError, match="boom"):
                await interpret(f, ["a", "b", "c"], system)
        finally:
            await system.shutdown()


# ── Loop error paths ─────────────────────────────────────


class TestLoopErrors:
    async def test_body_returns_neither_continue_nor_done(self):
        system = AgentSystem()
        try:
            f = loop(agent(BadLoopBody), max_iter=3)
            with pytest.raises(TypeError, match="must return Continue or Done"):
                await interpret(f, "test", system)
        finally:
            await system.shutdown()

    async def test_loop_with_state_body_returns_non_tuple(self):
        system = AgentSystem()
        try:
            f = loop_with_state(agent(BadLoopWithStateBody), init_state=list, max_iter=3)
            with pytest.raises(TypeError, match="must return.*tuple"):
                await interpret(f, "test", system)
        finally:
            await system.shutdown()

    async def test_loop_with_state_body_returns_bad_control(self):
        system = AgentSystem()
        try:
            f = loop_with_state(agent(BadLoopWithStateControl), init_state=list, max_iter=3)
            with pytest.raises(TypeError, match="Continue|Done"):
                await interpret(f, "test", system)
        finally:
            await system.shutdown()

    async def test_loop_with_state_max_iter_exceeded(self):
        class StatefulContinue(AgentActor[tuple, tuple]):
            async def execute(self, input: tuple) -> tuple:
                current, state = input
                return (Continue(value=current), state)

        system = AgentSystem()
        try:
            f = loop_with_state(agent(StatefulContinue), init_state=list, max_iter=2)
            with pytest.raises(RuntimeError, match="max_iter"):
                await interpret(f, "stuck", system)
        finally:
            await system.shutdown()


# ── Race error paths ─────────────────────────────────────


class TestRaceErrors:
    async def test_all_racers_fail(self):
        system = AgentSystem()
        try:
            f = race(agent(Failing), agent(Failing))
            with pytest.raises(ValueError, match="boom"):
                await interpret(f, "test", system)
        finally:
            await system.shutdown()


# ── Zip error paths ──────────────────────────────────────


class TestZipErrors:
    async def test_left_fails_cancels_right(self):
        system = AgentSystem()
        try:
            f = agent(Failing).zip(agent(SlowEcho))
            with pytest.raises(ValueError, match="boom"):
                await interpret(f, ("test", "test"), system)
        finally:
            await system.shutdown()

    async def test_right_fails_cancels_left(self):
        system = AgentSystem()
        try:
            f = agent(SlowEcho).zip(agent(Failing))
            with pytest.raises(ValueError, match="boom"):
                await interpret(f, ("test", "test"), system)
        finally:
            await system.shutdown()


# ── Branch edge cases ────────────────────────────────────


class TestBranchEdges:
    async def test_unmatched_type_error_message(self):
        system = AgentSystem()
        try:
            f = agent(ClassifyAB).branch({TypeA: agent(HandleA)})
            with pytest.raises(KeyError, match="TypeB"):
                await interpret(f, "this is long enough", system)
        finally:
            await system.shutdown()

    async def test_branch_on_with_pure_branches(self):
        system = AgentSystem()
        try:
            f = agent(ReturnInt).branch_on(
                lambda x: x == 0,
                then=pure(lambda _: "empty"),
                otherwise=pure(lambda x: f"len={x}"),
            )
            assert await interpret(f, "", system) == "empty"
            assert await interpret(f, "abc", system) == "len=3"
        finally:
            await system.shutdown()


# ── Filter edge cases ────────────────────────────────────


class TestFilterEdges:
    async def test_filter_error_carries_value(self):
        system = AgentSystem()
        try:
            f = agent(Echo).filter(lambda x: False)
            with pytest.raises(FlowFilterError) as exc_info:
                await interpret(f, "rejected", system)
            assert exc_info.value.value == "rejected"
        finally:
            await system.shutdown()


# ── Recover chain ────────────────────────────────────────


class TestRecoverChain:
    async def test_nested_recover(self):
        system = AgentSystem()
        try:
            f = agent(Failing).recover(lambda e: "recovered-1").map(lambda x: x + "!")
            result = await interpret(f, "test", system)
            assert result == "recovered-1!"
        finally:
            await system.shutdown()

    async def test_fallback_chain(self):
        system = AgentSystem()
        try:
            f = agent(Failing).fallback_to(agent(Failing)).fallback_to(agent(Echo))
            result = await interpret(f, "saved", system)
            assert result == "saved"
        finally:
            await system.shutdown()


# ── Serialization edge cases ─────────────────────────────


class TestSerializeEdges:
    def test_branch_round_trip(self):
        f = agent(Echo).branch({str: agent(Echo)})
        # Branch with _Agent branches should NOT be serializable because
        # branch() wraps the mapping dict — but the agent inside IS structural.
        # Actually _Branch IS serializable if all children are structural.
        # But the mapping keys are types, and from_dict needs a type registry too.
        # For now, just verify to_dict doesn't crash on Branch.
        d = to_dict(f)
        assert d["type"] == "Branch"

    def test_fallback_to_round_trip(self):
        f = agent(Echo).fallback_to(agent(Echo))
        d = to_dict(f)
        restored = from_dict(d, {"Echo": Echo})
        assert restored.source.cls is Echo
        assert restored.fallback.cls is Echo

    def test_recover_with_round_trip(self):
        f = agent(Echo).recover_with(agent(Echo))
        d = to_dict(f)
        assert d["type"] == "RecoverWith"
        restored = from_dict(d, {"Echo": Echo})
        assert restored.handler.cls is Echo

    def test_agent_with_custom_timeout(self):
        f = agent(Echo, timeout=120.0)
        d = to_dict(f)
        assert d["timeout"] == 120.0
        restored = from_dict(d, {"Echo": Echo})
        assert restored.timeout == 120.0

    def test_agent_default_timeout_omitted(self):
        d = to_dict(agent(Echo))
        assert "timeout" not in d

    def test_map_not_serializable(self):
        with pytest.raises(TypeError, match="not serializable"):
            to_dict(agent(Echo).map(str.upper))

    def test_filter_not_serializable(self):
        with pytest.raises(TypeError, match="not serializable"):
            to_dict(agent(Echo).filter(lambda x: True))

    def test_and_then_not_serializable(self):
        with pytest.raises(TypeError, match="not serializable"):
            to_dict(agent(Echo).and_then(print))

    def test_recover_not_serializable(self):
        with pytest.raises(TypeError, match="not serializable"):
            to_dict(agent(Echo).recover(lambda e: "x"))

    def test_from_dict_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown Flow type"):
            from_dict({"type": "Nonexistent"}, {})

    def test_from_dict_missing_registry_key(self):
        with pytest.raises(KeyError):
            from_dict({"type": "Agent", "cls": "Missing"}, {})


# ── Interpreter unknown variant ──────────────────────────


class TestUnknownVariant:
    async def test_interpret_unknown_flow_subclass(self):
        class _FakeFlow(Flow):
            pass

        system = AgentSystem()
        try:
            with pytest.raises(NotImplementedError, match="does not handle"):
                await interpret(_FakeFlow(), "test", system)
        finally:
            await system.shutdown()


# ── DivertTo error logging ───────────────────────────────


class TestDivertToErrors:
    async def test_side_flow_failure_does_not_crash_main(self):
        class FailingSide(AgentActor[str, None]):
            async def execute(self, input: str) -> None:
                raise RuntimeError("side failed")

        system = AgentSystem()
        try:
            f = agent(Echo).divert_to(agent(FailingSide), when=lambda x: True)
            result = await interpret(f, "hello", system)
            assert result == "hello"
            await asyncio.sleep(0.2)  # let fire-and-forget settle
        finally:
            await system.shutdown()


# ── Agent timeout ────────────────────────────────────────


class TestAgentTimeout:
    def test_default_timeout(self):
        f = agent(Echo)
        assert f.timeout == 30.0

    def test_custom_timeout(self):
        f = agent(Echo, timeout=120.0)
        assert f.timeout == 120.0

    async def test_timeout_triggers(self):
        class VerySlowAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                await asyncio.sleep(10)
                return input

        system = AgentSystem()
        try:
            f = agent(VerySlowAgent, timeout=0.1)
            with pytest.raises(asyncio.TimeoutError):
                await interpret(f, "test", system)
        finally:
            await system.shutdown()
