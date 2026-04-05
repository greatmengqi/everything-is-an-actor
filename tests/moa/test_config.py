import pytest
from everything_is_an_actor.agents import AgentActor, Task, TaskResult


class StubAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class StubAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        return "aggregated"


class TestMoANode:
    def test_basic_construction(self):
        from everything_is_an_actor.moa.config import MoANode

        node = MoANode(proposers=[StubAgent], aggregator=StubAgg)
        assert len(node.proposers) == 1
        assert node.aggregator is StubAgg
        assert node.min_success == 1

    def test_min_success_validation(self):
        from everything_is_an_actor.moa.config import MoANode

        with pytest.raises(ValueError, match="min_success must be >= 1"):
            MoANode(proposers=[StubAgent], aggregator=StubAgg, min_success=0)

    def test_min_success_exceeds_proposers(self):
        from everything_is_an_actor.moa.config import MoANode

        with pytest.raises(ValueError, match="cannot exceed"):
            MoANode(proposers=[StubAgent], aggregator=StubAgg, min_success=2)

    def test_frozen(self):
        from everything_is_an_actor.moa.config import MoANode

        node = MoANode(proposers=[StubAgent], aggregator=StubAgg)
        with pytest.raises(AttributeError):
            node.min_success = 5

    def test_proposers_is_tuple(self):
        from everything_is_an_actor.moa.config import MoANode

        node = MoANode(proposers=[StubAgent, StubAgg], aggregator=StubAgg)
        assert isinstance(node.proposers, tuple)

    def test_proposers_defensive_copy(self):
        """Mutating original list after construction does not affect node."""
        from everything_is_an_actor.moa.config import MoANode

        original = [StubAgent]
        node = MoANode(proposers=original, aggregator=StubAgg)
        original.append(StubAgg)
        assert len(node.proposers) == 1


class TestMoATree:
    def test_basic_construction(self):
        from everything_is_an_actor.moa.config import MoANode, MoATree

        node = MoANode(proposers=[StubAgent], aggregator=StubAgg)
        tree = MoATree(nodes=[node])
        assert len(tree.nodes) == 1

    def test_repeated_factory(self):
        from everything_is_an_actor.moa.config import MoANode, MoATree

        node = MoANode(proposers=[StubAgent, StubAgent], aggregator=StubAgg)
        tree = MoATree.repeated(node, num_layers=3)
        assert len(tree.nodes) == 3
        assert all(n is node for n in tree.nodes)

    def test_nested_proposer(self):
        from everything_is_an_actor.moa.config import MoANode, MoATree

        subtree = MoATree(nodes=[
            MoANode(proposers=[StubAgent], aggregator=StubAgg),
        ])
        outer = MoANode(proposers=[StubAgent, subtree], aggregator=StubAgg)
        assert len(outer.proposers) == 2
        assert isinstance(outer.proposers[1], MoATree)

    def test_frozen(self):
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[MoANode(proposers=[StubAgent], aggregator=StubAgg)])
        with pytest.raises(AttributeError):
            tree.nodes = []
