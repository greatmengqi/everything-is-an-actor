"""Tests for Flow serialization — to_dict / from_dict round-trip."""

import pytest
from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow import agent, pure, race, loop
from everything_is_an_actor.flow.serialize import from_dict, to_dict


class StubA(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class StubB(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


REGISTRY = {"StubA": StubA, "StubB": StubB}


class TestRoundTrip:
    def test_agent(self):
        d = to_dict(agent(StubA))
        assert d == {"type": "Agent", "cls": "StubA"}
        assert from_dict(d, REGISTRY).cls is StubA

    def test_flat_map(self):
        d = to_dict(agent(StubA).flat_map(agent(StubB)))
        assert d["type"] == "FlatMap"
        restored = from_dict(d, REGISTRY)
        assert restored.first.cls is StubA and restored.next.cls is StubB

    def test_zip(self):
        d = to_dict(agent(StubA).zip(agent(StubB)))
        restored = from_dict(d, REGISTRY)
        assert restored.left.cls is StubA and restored.right.cls is StubB

    def test_race(self):
        d = to_dict(race(agent(StubA), agent(StubB)))
        assert len(from_dict(d, REGISTRY).flows) == 2

    def test_loop(self):
        d = to_dict(loop(agent(StubA), max_iter=5))
        assert from_dict(d, REGISTRY).max_iter == 5

    def test_pure_not_serializable(self):
        with pytest.raises(TypeError, match="not serializable"):
            to_dict(pure(lambda x: x))
