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

        rn = ResolvedNode(proposers=[StubAgent], aggregator=StubAgg, min_success=1)
        assert rn.proposers == [StubAgent]
        assert rn.aggregator is StubAgg

    def test_frozen(self):
        from everything_is_an_actor.moa.builder import ResolvedNode

        rn = ResolvedNode(proposers=[StubAgent], aggregator=StubAgg, min_success=1)
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
