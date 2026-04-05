"""Tests for Flow ADT — control types, variants, method-chain composition."""

import pytest
from dataclasses import FrozenInstanceError

from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow.flow import (
    Continue,
    Done,
    Flow,
    _Agent,
    _AndThen,
    _Branch,
    _BranchOn,
    _DivertTo,
    _FallbackTo,
    _Filter,
    _FlatMap,
    _Loop,
    _LoopWithState,
    _Map,
    _Pure,
    _Race,
    _Recover,
    _RecoverWith,
    _Zip,
)


class StubAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class StubScorer(AgentActor[str, int]):
    async def execute(self, input: str) -> int:
        return len(input)


# ── Control types ────────────────────────────────────────


class TestControlTypes:
    def test_continue_holds_value(self):
        c = Continue(value="feedback")
        assert c.value == "feedback"

    def test_done_holds_value(self):
        d = Done(value="result")
        assert d.value == "result"

    def test_continue_frozen(self):
        c = Continue(value="x")
        with pytest.raises(FrozenInstanceError):
            c.value = "y"

    def test_done_frozen(self):
        d = Done(value="x")
        with pytest.raises(FrozenInstanceError):
            d.value = "y"

    def test_continue_done_are_distinct(self):
        c = Continue(value="x")
        d = Done(value="x")
        assert not isinstance(c, Done)
        assert not isinstance(d, Continue)


# ── ADT variants ─────────────────────────────────────────


class TestFlowVariants:
    def test_agent(self):
        f = _Agent(cls=StubAgent)
        assert f.cls is StubAgent

    def test_pure(self):
        fn = lambda x: x.upper()
        f = _Pure(f=fn)
        assert f.f is fn

    def test_flat_map(self):
        a = _Agent(cls=StubAgent)
        b = _Agent(cls=StubScorer)
        fm = _FlatMap(first=a, next=b)
        assert fm.first is a
        assert fm.next is b

    def test_zip(self):
        a = _Agent(cls=StubAgent)
        b = _Agent(cls=StubScorer)
        z = _Zip(left=a, right=b)
        assert z.left is a and z.right is b

    def test_map(self):
        fn = lambda x: x.upper()
        m = _Map(source=_Agent(cls=StubAgent), f=fn)
        assert m.f is fn

    def test_branch(self):
        mapping = {str: _Agent(cls=StubAgent)}
        b = _Branch(source=_Agent(cls=StubAgent), mapping=mapping)
        assert str in b.mapping

    def test_branch_on(self):
        pred = lambda x: x > 5
        b = _BranchOn(source=_Agent(cls=StubScorer), predicate=pred, then=_Agent(cls=StubAgent), otherwise=_Agent(cls=StubAgent))
        assert b.predicate is pred

    def test_race(self):
        r = _Race(flows=[_Agent(cls=StubAgent), _Agent(cls=StubAgent)])
        assert len(r.flows) == 2

    def test_recover(self):
        h = lambda e: "fallback"
        r = _Recover(source=_Agent(cls=StubAgent), handler=h)
        assert r.handler is h

    def test_recover_with(self):
        hf = _Agent(cls=StubAgent)
        r = _RecoverWith(source=_Agent(cls=StubAgent), handler=hf)
        assert r.handler is hf

    def test_fallback_to(self):
        f = _FallbackTo(source=_Agent(cls=StubScorer), fallback=_Agent(cls=StubAgent))
        assert f.fallback.cls is StubAgent

    def test_divert_to(self):
        pred = lambda x: len(x) > 10
        d = _DivertTo(source=_Agent(cls=StubAgent), side=_Agent(cls=StubAgent), when=pred)
        assert d.when is pred

    def test_loop(self):
        lp = _Loop(body=_Agent(cls=StubAgent), max_iter=5)
        assert lp.max_iter == 5

    def test_loop_with_state(self):
        lp = _LoopWithState(body=_Agent(cls=StubAgent), init_state=[], max_iter=3)
        assert lp.init_state == [] and lp.max_iter == 3

    def test_and_then(self):
        cb = lambda x: None
        at = _AndThen(source=_Agent(cls=StubAgent), callback=cb)
        assert at.callback is cb

    def test_filter(self):
        pred = lambda x: len(x) > 0
        ft = _Filter(source=_Agent(cls=StubAgent), predicate=pred)
        assert ft.predicate is pred


# ── Method-chain API ───────────────────────────────────��─


class TestMethodChain:
    def test_map(self):
        assert isinstance(_Agent(cls=StubAgent).map(str.upper), _Map)

    def test_flat_map(self):
        assert isinstance(_Agent(cls=StubAgent).flat_map(_Agent(cls=StubScorer)), _FlatMap)

    def test_zip(self):
        assert isinstance(_Agent(cls=StubAgent).zip(_Agent(cls=StubScorer)), _Zip)

    def test_chain_builds_nested_tree(self):
        f = _Agent(cls=StubAgent).map(str.upper).flat_map(_Agent(cls=StubScorer))
        assert isinstance(f, _FlatMap)
        assert isinstance(f.first, _Map)
        assert isinstance(f.first.source, _Agent)

    def test_recover(self):
        assert isinstance(_Agent(cls=StubAgent).recover(lambda e: "x"), _Recover)

    def test_fallback_to(self):
        assert isinstance(_Agent(cls=StubAgent).fallback_to(_Agent(cls=StubAgent)), _FallbackTo)

    def test_divert_to(self):
        assert isinstance(_Agent(cls=StubAgent).divert_to(_Agent(cls=StubAgent), when=lambda x: True), _DivertTo)

    def test_and_then(self):
        assert isinstance(_Agent(cls=StubAgent).and_then(print), _AndThen)

    def test_filter(self):
        assert isinstance(_Agent(cls=StubAgent).filter(lambda x: True), _Filter)

    def test_branch(self):
        assert isinstance(_Agent(cls=StubAgent).branch({str: _Agent(cls=StubAgent)}), _Branch)

    def test_branch_on(self):
        assert isinstance(
            _Agent(cls=StubScorer).branch_on(lambda x: x > 5, then=_Agent(cls=StubAgent), otherwise=_Agent(cls=StubAgent)),
            _BranchOn,
        )
