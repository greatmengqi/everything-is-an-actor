"""Tests for Flow constructors — agent(), pure(), race(), loop()."""

import pytest
from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow.flow import _Agent, _Loop, _LoopWithState, _Pure, _Race


class StubAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class TestAgent:
    def test_returns_agent_node(self):
        from everything_is_an_actor.flow.combinators import agent

        f = agent(StubAgent)
        assert isinstance(f, _Agent)
        assert f.cls is StubAgent


class TestPure:
    def test_returns_pure_node(self):
        from everything_is_an_actor.flow.combinators import pure

        fn = lambda x: x.upper()
        f = pure(fn)
        assert isinstance(f, _Pure)
        assert f.f is fn


class TestRace:
    def test_returns_race_node(self):
        from everything_is_an_actor.flow.combinators import agent, race

        f = race(agent(StubAgent), agent(StubAgent))
        assert isinstance(f, _Race)
        assert len(f.flows) == 2

    def test_requires_at_least_two(self):
        from everything_is_an_actor.flow.combinators import agent, race

        with pytest.raises(ValueError, match="at least 2"):
            race(agent(StubAgent))


class TestLoop:
    def test_returns_loop_node(self):
        from everything_is_an_actor.flow.combinators import agent, loop

        f = loop(agent(StubAgent), max_iter=5)
        assert isinstance(f, _Loop) and f.max_iter == 5

    def test_default_max_iter(self):
        from everything_is_an_actor.flow.combinators import agent, loop

        assert loop(agent(StubAgent)).max_iter == 10

    def test_loop_with_state(self):
        from everything_is_an_actor.flow.combinators import agent, loop_with_state

        f = loop_with_state(agent(StubAgent), init_state=[], max_iter=3)
        assert isinstance(f, _LoopWithState) and f.init_state == [] and f.max_iter == 3


class TestPublicAPI:
    def test_all_symbols_importable(self):
        from everything_is_an_actor.flow import (
            Continue,
            Done,
            Flow,
            FlowFilterError,
            agent,
            loop,
            loop_with_state,
            pure,
            race,
        )

        assert all(x is not None for x in [Flow, Continue, Done, FlowFilterError, agent, pure, race, loop, loop_with_state])
