"""Tests for moa_layer and moa_tree pattern functions."""

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow.interpreter import Interpreter
from everything_is_an_actor.flow.quorum import QuorumResult
from everything_is_an_actor.moa.layer_output import LayerOutput
from everything_is_an_actor.moa.patterns import moa_layer, moa_tree

pytestmark = pytest.mark.anyio


# ── Stub agents ──────────────────────────────────


class EchoProposer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Echo] {input}"


class UpperProposer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Upper] {input.upper()}"


class FailingProposer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError("I always fail")


class JoinAggregator(AgentActor[QuorumResult[str], str]):
    async def execute(self, input: QuorumResult[str]) -> str:
        return " | ".join(input.succeeded)


class DirectiveAggregator(AgentActor[QuorumResult[str], LayerOutput[str]]):
    async def execute(self, input: QuorumResult[str]) -> LayerOutput[str]:
        return LayerOutput(
            result=" | ".join(input.succeeded),
            directive="focus on details",
        )


class DirectiveAwareProposer(AgentActor[object, str]):
    """Proposer that reads directive from dict input."""

    async def execute(self, input: object) -> str:
        if isinstance(input, dict) and "directive" in input:
            return f"[Aware] {input['directive']}: {input['input']}"
        return f"[Aware] {input}"


class FinalAggregator(AgentActor[QuorumResult[str], str]):
    async def execute(self, input: QuorumResult[str]) -> str:
        return f"Final: {input.succeeded[0]}"


# ── moa_layer ────────────────────────────────────


class TestMoaLayer:
    async def test_single_layer_no_directive(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            layer = moa_layer(
                proposers=[EchoProposer, UpperProposer],
                aggregator=JoinAggregator,
                min_success=1,
            )
            # moa_layer expects (input, directive) tuple
            result = await interp.run(layer, ("hello", None))
            output, directive = result
            assert "[Echo]" in output
            assert "[Upper]" in output
            assert directive is None  # JoinAggregator returns plain str
        finally:
            await system.shutdown()

    async def test_layer_with_directive_output(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            layer = moa_layer(
                proposers=[EchoProposer],
                aggregator=DirectiveAggregator,
                min_success=1,
            )
            result = await interp.run(layer, ("hello", None))
            output, directive = result
            assert "[Echo]" in output
            assert directive == "focus on details"
        finally:
            await system.shutdown()

    async def test_layer_with_directive_input(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            layer = moa_layer(
                proposers=[DirectiveAwareProposer],
                aggregator=JoinAggregator,
                min_success=1,
            )
            result = await interp.run(layer, ("hello", "be concise"))
            output, directive = result
            assert "be concise" in output
            assert "hello" in output
        finally:
            await system.shutdown()

    async def test_layer_partial_failure(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            layer = moa_layer(
                proposers=[EchoProposer, FailingProposer],
                aggregator=JoinAggregator,
                min_success=1,
            )
            result = await interp.run(layer, ("hello", None))
            output, _ = result
            assert "[Echo]" in output
        finally:
            await system.shutdown()

    async def test_layer_quorum_not_met(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            layer = moa_layer(
                proposers=[FailingProposer, FailingProposer],
                aggregator=JoinAggregator,
                min_success=1,
            )
            with pytest.raises(RuntimeError, match="Quorum failed"):
                await interp.run(layer, ("hello", None))
        finally:
            await system.shutdown()


# ── moa_tree ─────────────────────────────────────


class TestMoaTree:
    async def test_single_layer_tree(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            tree = moa_tree([
                moa_layer(
                    proposers=[EchoProposer, UpperProposer],
                    aggregator=JoinAggregator,
                    min_success=1,
                ),
            ])
            # moa_tree wraps input as (input, None) and unwraps output
            result = await interp.run(tree, "hello")
            assert "[Echo]" in result
            assert "[Upper]" in result
        finally:
            await system.shutdown()

    async def test_multi_layer_tree(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            tree = moa_tree([
                moa_layer(
                    proposers=[EchoProposer, UpperProposer],
                    aggregator=JoinAggregator,
                    min_success=1,
                ),
                moa_layer(
                    proposers=[EchoProposer],
                    aggregator=FinalAggregator,
                    min_success=1,
                ),
            ])
            result = await interp.run(tree, "hello")
            assert "Final:" in result
        finally:
            await system.shutdown()

    async def test_directive_passes_between_layers(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            tree = moa_tree([
                moa_layer(
                    proposers=[EchoProposer],
                    aggregator=DirectiveAggregator,
                    min_success=1,
                ),
                moa_layer(
                    proposers=[DirectiveAwareProposer],
                    aggregator=FinalAggregator,
                    min_success=1,
                ),
            ])
            result = await interp.run(tree, "hello")
            assert "focus on details" in result
        finally:
            await system.shutdown()

    async def test_empty_layers_raises(self):
        with pytest.raises(ValueError, match="at least one layer"):
            moa_tree([])
