"""Tests for A2A: AgentCard + discover()."""

import pytest

from everything_is_an_actor import ActorSystem, AgentCard, AgentSystem
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task

pytestmark = pytest.mark.anyio


class TranslateAgent(AgentActor[str, str]):
    __card__ = AgentCard(
        name="translator",
        description="Translates documents between languages",
        skills=("translation", "summarization"),
    )

    async def execute(self, input: str) -> str:
        return f"translated: {input}"


class SearchAgent(AgentActor[str, str]):
    __card__ = AgentCard(
        name="searcher",
        description="Searches the web for information",
        skills=("search", "retrieval"),
    )

    async def execute(self, input: str) -> str:
        return f"found: {input}"


class PlainAgent(AgentActor[str, str]):
    """No __card__ — not discoverable."""

    async def execute(self, input: str) -> str:
        return input


# ── AgentCard ────────────────────────────────────────────


class TestAgentCard:
    def test_default_card_is_empty(self):
        card = AgentCard()
        assert card.name == ""
        assert card.description == ""
        assert card.skills == ()

    def test_card_is_frozen(self):
        card = AgentCard(name="test", skills=("a",))
        with pytest.raises(AttributeError):
            card.name = "other"  # type: ignore[misc]

    def test_card_on_agent_class(self):
        assert TranslateAgent.__card__.name == "translator"
        assert "translation" in TranslateAgent.__card__.skills

    def test_default_card_on_base_class(self):
        assert AgentActor.__card__ == AgentCard()

    def test_plain_agent_has_default_card(self):
        assert PlainAgent.__card__ == AgentCard()


# ── discover() ───────────────────────────────────────────


class TestDiscoverAll:
    async def test_filter_by_skill(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")
        await system.spawn(PlainAgent, "p1")

        results = system.discover_all(lambda agents: [(r, c) for r, c in agents if "translation" in c.skills])
        assert len(results) == 1
        assert results[0][0].name == "t1"

        await system.shutdown()

    async def test_filter_by_description(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        results = system.discover_all(lambda agents: [(r, c) for r, c in agents if "web" in c.description.lower()])
        assert len(results) == 1
        assert results[0][0].name == "s1"

        await system.shutdown()

    async def test_multiple_matches(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        results = system.discover_all(lambda agents: [(r, c) for r, c in agents if len(c.skills) >= 2])
        assert len(results) == 2

        await system.shutdown()

    async def test_no_match(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")

        results = system.discover_all(lambda agents: [(r, c) for r, c in agents if "nonexistent" in c.skills])
        assert results == []

        await system.shutdown()

    async def test_combined_predicate(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        results = system.discover_all(
            lambda agents: [(r, c) for r, c in agents if "translation" in c.skills and "summarization" in c.skills]
        )
        assert len(results) == 1
        assert results[0][0].name == "t1"

        await system.shutdown()


class TestDiscoverOne:
    async def test_select_by_skill(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        result = system.discover_one(lambda agents: next(((r, c) for r, c in agents if "search" in c.skills), None))
        assert result is not None
        assert result[0].name == "s1"

        await system.shutdown()

    async def test_select_best_by_score(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        result = system.discover_one(lambda agents: max(agents, key=lambda rc: len(rc[1].skills)) if agents else None)
        assert result is not None

        await system.shutdown()

    async def test_no_match_returns_none(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")

        result = system.discover_one(
            lambda agents: next(((r, c) for r, c in agents if "nonexistent" in c.skills), None)
        )
        assert result is None

        await system.shutdown()


# ── Multi-turn pattern (no framework change, just usage) ─


class StatefulTranslateAgent(AgentActor[str, str]):
    """Multi-turn agent: first call stores input, second call does work."""

    __card__ = AgentCard(skills=("translation",))

    def __init__(self) -> None:
        super().__init__()
        self._pending: str | None = None

    async def execute(self, input: str) -> str:
        if self._pending is None:
            self._pending = input
            return "NEED_LANG: Chinese or English?"
        else:
            result = f"{input}: {self._pending}"
            self._pending = None
            return result


class TestMultiTurnPattern:
    async def test_multi_turn_via_ask_loop(self):
        system = AgentSystem(ActorSystem())
        ref = await system.spawn(StatefulTranslateAgent, "mt")

        # First round — agent needs more info
        result = await system.ask(ref, Task(input="hello world"))
        assert result.output.startswith("NEED_LANG:")

        # Second round — provide the answer
        result = await system.ask(ref, Task(input="Chinese"))
        assert result.output == "Chinese: hello world"

        await system.shutdown()
