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
