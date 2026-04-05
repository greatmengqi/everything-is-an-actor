import pytest
from typing import Any
from everything_is_an_actor.agents import AgentActor, Task, TaskResult, TaskStatus


class StubAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class StubAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        return "aggregated"


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Echo] {input}"


class EchoAgent2(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Echo2] {input}"


class JoinAggregator(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        outputs = [r.get_or_raise() for r in input if r.is_success()]
        return " | ".join(outputs)


class FailingAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError("I always fail")


# --- Type tests ---

class TestResolvedNode:
    def test_construction(self):
        from everything_is_an_actor.moa.builder import ResolvedNode

        rn = ResolvedNode(proposers=[StubAgent], aggregator=StubAgg, min_success=1, proposer_timeout=30.0)
        assert rn.proposers == [StubAgent]
        assert rn.aggregator is StubAgg

    def test_frozen(self):
        from everything_is_an_actor.moa.builder import ResolvedNode

        rn = ResolvedNode(proposers=[StubAgent], aggregator=StubAgg, min_success=1, proposer_timeout=30.0)
        with pytest.raises(AttributeError):
            rn.min_success = 2


class TestLayerOutput:
    def test_with_directive(self):
        from everything_is_an_actor.moa.builder import LayerOutput

        lo = LayerOutput(result="hello", directive="focus on X")
        assert lo.result == "hello"
        assert lo.directive == "focus on X"

    def test_without_directive(self):
        from everything_is_an_actor.moa.builder import LayerOutput

        lo = LayerOutput(result="hello")
        assert lo.directive is None


# --- Builder tests ---

class TestMoABuilder:
    def test_build_returns_agent_class(self):
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[StubAgent, StubAgent], aggregator=StubAgg),
        ])
        result_cls = MoABuilder().build(tree)
        assert isinstance(result_cls, type)
        assert issubclass(result_cls, AgentActor)

    def test_resolve_flattens_subtree(self):
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        subtree = MoATree(nodes=[
            MoANode(proposers=[StubAgent], aggregator=StubAgg),
        ])
        outer_tree = MoATree(nodes=[
            MoANode(proposers=[StubAgent, subtree], aggregator=StubAgg),
        ])
        result_cls = MoABuilder().build(outer_tree)
        assert issubclass(result_cls, AgentActor)

    def test_build_multi_layer(self):
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[StubAgent], aggregator=StubAgg),
            MoANode(proposers=[StubAgent, StubAgent], aggregator=StubAgg),
        ])
        result_cls = MoABuilder().build(tree)
        assert issubclass(result_cls, AgentActor)


# --- Runtime tests ---

@pytest.mark.asyncio
class TestMoARuntime:
    async def test_single_layer_end_to_end(self):
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent2], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hello"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            # The final completed event is the MoAAgent's result
            result = completed[-1].data
            assert "[Echo]" in result
            assert "[Echo2]" in result
        finally:
            await system.shutdown()

    async def test_multi_layer(self):
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent2], aggregator=JoinAggregator),
            MoANode(proposers=[EchoAgent], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hello"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
        finally:
            await system.shutdown()

    async def test_partial_success_validated(self):
        """One proposer fails but min_success=1, should still succeed."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, FailingAgent],
                aggregator=JoinAggregator,
                min_success=1,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hello"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
        finally:
            await system.shutdown()

    async def test_min_success_not_met_raises(self):
        """All proposers fail, min_success=1 not met."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(
                proposers=[FailingAgent, FailingAgent],
                aggregator=JoinAggregator,
                min_success=1,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            with pytest.raises(RuntimeError, match="MOA:"):
                async for event in system.run(MoAAgent, "hello"):
                    pass
        finally:
            await system.shutdown()

    async def test_min_success_exact_threshold(self):
        """3 proposers, 1 fails, min_success=2 — should succeed with exactly 2."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, EchoAgent2, FailingAgent],
                aggregator=JoinAggregator,
                min_success=2,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hello"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            assert "[Echo]" in result
            assert "[Echo2]" in result
        finally:
            await system.shutdown()

    async def test_multi_layer_data_flows_through(self):
        """Verify layer 1 output actually becomes layer 2 input."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        class PrefixAgg(AgentActor[list, str]):
            async def execute(self, input: list) -> str:
                outputs = [r.get_or_raise() for r in input if r.is_success()]
                return "LAYER1:" + ",".join(outputs)

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=PrefixAgg),
            MoANode(proposers=[EchoAgent], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hi"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # Layer 2's EchoAgent receives Layer 1's "LAYER1:..." output
            assert "LAYER1:" in result
        finally:
            await system.shutdown()

    async def test_single_proposer(self):
        """Edge case: only one proposer in a layer."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "solo"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            assert "[Echo] solo" in completed[-1].data
        finally:
            await system.shutdown()

    async def test_repeated_tree_end_to_end(self):
        """MoATree.repeated() runs N identical layers."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        node = MoANode(proposers=[EchoAgent], aggregator=JoinAggregator)
        tree = MoATree.repeated(node, num_layers=3)
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "x"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            # After 3 layers of Echo wrapping: [Echo] [Echo] [Echo] x
            result = completed[-1].data
            assert result.count("[Echo]") == 3
        finally:
            await system.shutdown()

    async def test_nested_moa_tree_end_to_end(self):
        """A proposer that is itself a MoATree runs correctly."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        subtree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent2], aggregator=JoinAggregator),
        ])
        tree = MoATree(nodes=[
            MoANode(proposers=[subtree, EchoAgent], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "nested"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            # Subtree produces "[Echo] nested | [Echo2] nested"
            # Outer EchoAgent produces "[Echo] nested"
            # JoinAggregator joins them
            assert "[Echo]" in result
            assert "[Echo2]" in result
        finally:
            await system.shutdown()

    async def test_streaming_observability(self):
        """All intermediate events (proposer/aggregator) are visible in event stream."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent2], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "observe"):
                events.append(event)
            # Should have events from: MoAAgent + 2 proposers + 1 aggregator
            started = [e for e in events if e.type == "task_started"]
            completed = [e for e in events if e.type == "task_completed"]
            # At least 4 started events (moa + 2 proposers + aggregator)
            assert len(started) >= 4
            assert len(completed) >= 4
        finally:
            await system.shutdown()

    async def test_no_directive_does_not_inject(self):
        """When aggregator returns plain value (no LayerOutput), next layer gets raw input."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        class TypeCheckProposer(AgentActor[Any, str]):
            async def execute(self, input: Any) -> str:
                # If directive was NOT injected, input should be a plain string
                assert isinstance(input, str), f"Expected str, got {type(input)}: {input}"
                return f"[ok] {input}"

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=JoinAggregator),
            MoANode(proposers=[TypeCheckProposer], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "plain"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            assert "[ok]" in completed[-1].data
        finally:
            await system.shutdown()


# --- Directive tests ---

@pytest.mark.asyncio
class TestDirective:
    async def test_directive_passed_to_next_layer(self):
        from everything_is_an_actor.moa.builder import MoABuilder, LayerOutput
        from everything_is_an_actor.moa.config import MoANode, MoATree
        from everything_is_an_actor.agents import AgentSystem

        class DirectiveAggregator(AgentActor[list, LayerOutput]):
            async def execute(self, input: list) -> LayerOutput:
                outputs = [r.get_or_raise() for r in input if r.is_success()]
                return LayerOutput(
                    result=" | ".join(outputs),
                    directive="focus on disagreements",
                )

        class DirectiveAwareProposer(AgentActor[Any, str]):
            async def execute(self, input: Any) -> str:
                match input:
                    case {"input": data, "directive": d}:
                        return f"[directed:{d}] {data}"
                    case _:
                        return f"[plain] {input}"

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=DirectiveAggregator),
            MoANode(proposers=[DirectiveAwareProposer], aggregator=JoinAggregator),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "hello"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            assert "directed:" in result
            assert "focus on disagreements" in result
        finally:
            await system.shutdown()
