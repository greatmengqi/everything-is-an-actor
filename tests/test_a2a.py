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


class TestDiscover:
    async def test_discover_by_skill(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")
        await system.spawn(PlainAgent, "p1")

        refs = system.discover(lambda c: "translation" in c.skills)
        assert len(refs) == 1
        assert refs[0].name == "t1"

        await system.shutdown()

    async def test_discover_by_description(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        refs = system.discover(lambda c: "web" in c.description.lower())
        assert len(refs) == 1
        assert refs[0].name == "s1"

        await system.shutdown()

    async def test_discover_multiple_matches(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        refs = system.discover(lambda c: len(c.skills) >= 2)
        assert len(refs) == 2

        await system.shutdown()

    async def test_discover_no_match(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")

        refs = system.discover(lambda c: "nonexistent" in c.skills)
        assert refs == []

        await system.shutdown()

    async def test_discover_skips_agents_without_card(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(PlainAgent, "p1")

        refs = system.discover(lambda c: True)
        # PlainAgent has default empty card — predicate matches, but skills empty
        # It IS discoverable (has default card), just has no skills
        assert len(refs) == 1

        await system.shutdown()

    async def test_discover_combined_predicate(self):
        system = AgentSystem(ActorSystem())
        await system.spawn(TranslateAgent, "t1")
        await system.spawn(SearchAgent, "s1")

        refs = system.discover(lambda c: "translation" in c.skills and "summarization" in c.skills)
        assert len(refs) == 1
        assert refs[0].name == "t1"

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
