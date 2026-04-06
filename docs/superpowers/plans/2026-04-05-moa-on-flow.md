# MOA on Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reimplement MOA as a pattern library on top of Flow ADT, replacing the current metaprogramming-based MoABuilder.

**Architecture:** `moa/` depends on `flow/`, which depends on `agents/` and `core/`. Flow gets one new general-purpose combinator (`at_least`). MOA becomes a thin pattern library (`moa_layer`, `moa_tree`, `MoASystem`) that composes Flow primitives. `Interpreter` is internalized into `AgentSystem.run_flow`.

**Tech Stack:** Python 3.12, anyio, asyncio, dataclasses

**Spec:** `docs/superpowers/specs/2026-04-05-moa-on-flow-design.md`

---

### File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `everything_is_an_actor/flow/quorum.py` | `QuorumResult`, `at_least` combinator |
| Create | `tests/flow/test_quorum.py` | Tests for `at_least` |
| Modify | `everything_is_an_actor/flow/combinators.py` | Re-export `at_least` |
| Modify | `everything_is_an_actor/flow/__init__.py` | Export `QuorumResult`, `at_least` |
| Modify | `everything_is_an_actor/agents/system.py` | Add `run_flow`, `run_flow_stream` |
| Create | `tests/agents/test_run_flow.py` | Tests for `AgentSystem.run_flow` |
| Create | `everything_is_an_actor/moa/layer_output.py` | `LayerOutput` dataclass |
| Create | `everything_is_an_actor/moa/patterns.py` | `moa_layer`, `moa_tree` |
| Create | `everything_is_an_actor/moa/system.py` | `MoASystem` |
| Create | `tests/moa/test_patterns.py` | Tests for `moa_layer`, `moa_tree` |
| Create | `tests/moa/test_moa_system.py` | Tests for `MoASystem` |
| Modify | `everything_is_an_actor/moa/__init__.py` | Update public API |
| Delete | `everything_is_an_actor/moa/config.py` | Remove `MoANode`, `MoATree` |
| Delete | `everything_is_an_actor/moa/builder.py` | Remove `MoABuilder` |
| Delete | `tests/moa/test_config.py` | Remove config tests |
| Delete | `tests/moa/test_builder.py` | Remove builder tests |

---

### Task 1: `at_least` combinator — QuorumResult + at_least

**Files:**
- Create: `tests/flow/test_quorum.py`
- Create: `everything_is_an_actor/flow/quorum.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flow/test_quorum.py`:

```python
"""Tests for at_least quorum combinator."""

import asyncio

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import agent, pure
from everything_is_an_actor.flow.interpreter import Interpreter
from everything_is_an_actor.flow.quorum import QuorumResult, at_least

pytestmark = pytest.mark.anyio


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Upper(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class Failing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError("boom")


class SlowFailing(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.05)
        raise RuntimeError("slow boom")


# ── Validation ──────────────────────────────────


class TestAtLeastValidation:
    def test_n_less_than_1(self):
        with pytest.raises(ValueError, match="n must be >= 1"):
            at_least(0, agent(Echo))

    def test_n_exceeds_flows(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            at_least(3, agent(Echo), agent(Upper))

    def test_no_flows(self):
        with pytest.raises(ValueError, match="at least 1 flow"):
            at_least(1)


# ── Runtime ─────────────────────────────────────


class TestAtLeastRuntime:
    async def test_all_succeed(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(2, agent(Echo), agent(Upper))
            result = await interp.run(flow, "hello")
            assert isinstance(result, QuorumResult)
            assert len(result.succeeded) == 2
            assert "hello" in result.succeeded
            assert "HELLO" in result.succeeded
            assert result.failed == []
        finally:
            await system.shutdown()

    async def test_partial_failure_within_quorum(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo), agent(Failing))
            result = await interp.run(flow, "hi")
            assert len(result.succeeded) == 1
            assert result.succeeded[0] == "hi"
            assert len(result.failed) == 1
            assert isinstance(result.failed[0], ValueError)
        finally:
            await system.shutdown()

    async def test_quorum_not_met_raises(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(2, agent(Echo), agent(Failing), agent(Failing))
            with pytest.raises(RuntimeError, match="Quorum failed"):
                await interp.run(flow, "hi")
        finally:
            await system.shutdown()

    async def test_single_flow(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo))
            result = await interp.run(flow, "solo")
            assert result.succeeded == ["solo"]
            assert result.failed == []
        finally:
            await system.shutdown()

    async def test_all_fail_raises(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Failing), agent(Failing))
            with pytest.raises(RuntimeError, match="Quorum failed"):
                await interp.run(flow, "x")
        finally:
            await system.shutdown()

    async def test_memory_error_propagates(self):
        class MemoryBomb(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                raise MemoryError("out of memory")

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = at_least(1, agent(Echo), agent(MemoryBomb))
            with pytest.raises(MemoryError):
                await interp.run(flow, "x")
        finally:
            await system.shutdown()


# ── QuorumResult type ───────────────────────────


class TestQuorumResult:
    def test_frozen(self):
        qr = QuorumResult(succeeded=["a"], failed=[])
        with pytest.raises(AttributeError):
            qr.succeeded = []

    def test_fields(self):
        err = ValueError("e")
        qr = QuorumResult(succeeded=["ok"], failed=[err])
        assert qr.succeeded == ["ok"]
        assert qr.failed == [err]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/flow/test_quorum.py -v`
Expected: ImportError — `cannot import name 'QuorumResult' from 'everything_is_an_actor.flow.quorum'`

- [ ] **Step 3: Implement QuorumResult and at_least**

Create `everything_is_an_actor/flow/quorum.py`:

```python
"""Quorum combinator — N-way parallel with minimum success threshold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from everything_is_an_actor.flow.combinators import pure, zip_all
from everything_is_an_actor.flow.flow import Flow

I = TypeVar("I")
O = TypeVar("O")


@dataclass(frozen=True)
class QuorumResult(Generic[O]):
    """Quorum execution result, separating successes from failures."""

    succeeded: list[O]
    failed: list[Exception]


# ── Internal tagged union for result collection ─


@dataclass(frozen=True)
class _Ok(Generic[O]):
    value: O


@dataclass(frozen=True)
class _Err:
    error: Exception


def _wrap_ok(value: object) -> _Ok:
    return _Ok(value)


def _wrap_err(e: Exception) -> _Err:
    if isinstance(e, MemoryError):
        raise
    return _Err(e)


def _split_and_validate(n: int):
    def validate(results: list) -> QuorumResult:
        succeeded = [r.value for r in results if isinstance(r, _Ok)]
        failed = [r.error for r in results if isinstance(r, _Err)]
        if len(succeeded) < n:
            raise RuntimeError(
                f"Quorum failed: {len(succeeded)} succeeded, {n} required"
            )
        return QuorumResult(succeeded=succeeded, failed=failed)

    return validate


def at_least(n: int, *flows: Flow[I, O]) -> Flow[I, QuorumResult[O]]:
    """N-way parallel execution with quorum validation.

    Runs all *flows* concurrently with the same input.
    Succeeds if at least *n* flows succeed; raises ``RuntimeError`` otherwise.
    Domain exceptions are collected into ``QuorumResult.failed``;
    system exceptions (``MemoryError``) propagate immediately.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if len(flows) < 1:
        raise ValueError("at_least requires at least 1 flow")
    if n > len(flows):
        raise ValueError(
            f"n ({n}) cannot exceed number of flows ({len(flows)})"
        )

    wrapped = [f.map(_wrap_ok).recover(_wrap_err) for f in flows]

    if len(wrapped) == 1:
        return wrapped[0].map(lambda r: [r]).map(_split_and_validate(n))

    k = len(wrapped)
    return (
        pure(lambda x, _k=k: tuple(x for _ in range(_k)))
        .flat_map(zip_all(*wrapped))
        .map(_split_and_validate(n))
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/flow/test_quorum.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/flow/quorum.py tests/flow/test_quorum.py
git commit -m "feat(flow): add at_least quorum combinator"
```

---

### Task 2: Export at_least from flow/

**Files:**
- Modify: `everything_is_an_actor/flow/combinators.py`
- Modify: `everything_is_an_actor/flow/__init__.py`

- [ ] **Step 1: Add re-export to combinators.py**

At the end of `everything_is_an_actor/flow/combinators.py` (~line 57), add:

```python
from everything_is_an_actor.flow.quorum import QuorumResult, at_least  # noqa: F401
```

- [ ] **Step 2: Add to flow/__init__.py exports**

In `everything_is_an_actor/flow/__init__.py`, add `QuorumResult` and `at_least` to the imports and `__all__`:

```python
from everything_is_an_actor.flow.quorum import QuorumResult, at_least
```

Add `"QuorumResult"` and `"at_least"` to the `__all__` list.

- [ ] **Step 3: Verify import works**

Run: `python -c "from everything_is_an_actor.flow import at_least, QuorumResult; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Re-run quorum tests via public import**

Run: `python -m pytest tests/flow/test_quorum.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/flow/combinators.py everything_is_an_actor/flow/__init__.py
git commit -m "feat(flow): export at_least and QuorumResult from flow public API"
```

---

### Task 3: AgentSystem.run_flow / run_flow_stream

**Files:**
- Create: `tests/agents/test_run_flow.py`
- Modify: `everything_is_an_actor/agents/system.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agents/test_run_flow.py`:

```python
"""Tests for AgentSystem.run_flow and run_flow_stream."""

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import agent, pure

pytestmark = pytest.mark.anyio


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Upper(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class TestRunFlow:
    async def test_simple_agent(self):
        system = AgentSystem(ActorSystem())
        try:
            result = await system.run_flow(agent(Echo), "hello")
            assert result == "hello"
        finally:
            await system.shutdown()

    async def test_composed_flow(self):
        system = AgentSystem(ActorSystem())
        try:
            flow = agent(Echo).flat_map(agent(Upper))
            result = await system.run_flow(flow, "hello")
            assert result == "HELLO"
        finally:
            await system.shutdown()

    async def test_pure_flow(self):
        system = AgentSystem(ActorSystem())
        try:
            result = await system.run_flow(pure(str.upper), "hello")
            assert result == "HELLO"
        finally:
            await system.shutdown()


class TestRunFlowStream:
    async def test_stream_emits_events(self):
        system = AgentSystem(ActorSystem())
        try:
            events = []
            async for event in system.run_flow_stream(agent(Echo), "hello"):
                events.append(event)
            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agents/test_run_flow.py -v`
Expected: AttributeError — `'AgentSystem' object has no attribute 'run_flow'`

- [ ] **Step 3: Implement run_flow and run_flow_stream**

In `everything_is_an_actor/agents/system.py`, add after the `shutdown` method (~line 89):

```python
    # ── Flow execution ────────────────────────────────

    async def run_flow(self, flow: "Flow", input: object) -> object:
        """Execute a Flow program, returning the final result.

        Internally creates an Interpreter — callers need not import it.
        """
        from everything_is_an_actor.flow.interpreter import Interpreter

        return await Interpreter(self).run(flow, input)

    async def run_flow_stream(self, flow: "Flow", input: object):  # type: ignore[return]
        """Execute a Flow program, yielding TaskEvent streams."""
        from everything_is_an_actor.flow.interpreter import Interpreter

        async for event in Interpreter(self).run_stream(flow, input):
            yield event
```

Add a `TYPE_CHECKING` block after the existing imports (after line 20):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from everything_is_an_actor.flow.flow import Flow
```

Note: `from __future__ import annotations` is already present at line 7.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agents/test_run_flow.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/agents/system.py tests/agents/test_run_flow.py
git commit -m "feat(agents): add run_flow / run_flow_stream to AgentSystem"
```

---

### Task 4: LayerOutput migration

**Files:**
- Create: `everything_is_an_actor/moa/layer_output.py`

- [ ] **Step 1: Create LayerOutput**

Create `everything_is_an_actor/moa/layer_output.py`:

```python
"""LayerOutput — directive carrier for MOA layer-to-layer communication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

O = TypeVar("O")


@dataclass(frozen=True)
class LayerOutput(Generic[O]):
    """Aggregator output carrying a directive for the next layer.

    If an aggregator returns a plain value, directive is cleared.
    If it returns ``LayerOutput``, ``.result`` is the value and
    ``.directive`` is injected into the next layer's proposer input
    as ``{"input": ..., "directive": ...}``.
    """

    result: O
    directive: str | None = None
```

- [ ] **Step 2: Verify import**

Run: `python -c "from everything_is_an_actor.moa.layer_output import LayerOutput; print(LayerOutput(result='ok', directive='x'))"`
Expected: `LayerOutput(result='ok', directive='x')`

- [ ] **Step 3: Commit**

```bash
git add everything_is_an_actor/moa/layer_output.py
git commit -m "feat(moa): add LayerOutput to new location"
```

---

### Task 5: moa_layer and moa_tree

**Files:**
- Create: `tests/moa/test_patterns.py`
- Create: `everything_is_an_actor/moa/patterns.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/moa/test_patterns.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/moa/test_patterns.py -v`
Expected: ImportError — `cannot import name 'moa_layer' from 'everything_is_an_actor.moa.patterns'`

- [ ] **Step 3: Implement moa_layer and moa_tree**

Create `everything_is_an_actor/moa/patterns.py`:

```python
"""MOA pattern functions — convenience combinators for Mixture-of-Agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from everything_is_an_actor.flow import agent, at_least, pure
from everything_is_an_actor.flow.flow import Flow
from everything_is_an_actor.moa.layer_output import LayerOutput

if TYPE_CHECKING:
    from everything_is_an_actor.agents import AgentActor


def _inject_directive(state: tuple) -> object:
    """Unpack (input, directive); inject directive into input if present."""
    input_val, directive = state
    if directive is None:
        return input_val
    return {"input": input_val, "directive": directive}


def _extract_directive(output: object) -> tuple:
    """Extract directive from LayerOutput; clear for plain values."""
    match output:
        case LayerOutput(result=r, directive=d):
            return (r, d)
        case _:
            return (output, None)


def moa_layer(
    proposers: list[type[AgentActor]],
    aggregator: type[AgentActor],
    min_success: int = 1,
    timeout: float = 30.0,
) -> Flow:
    """Single MOA layer: parallel proposers + quorum + aggregator.

    Input:  ``(input, directive)`` tuple (``moa_tree`` ensures first layer
            receives ``(input, None)``).
    Output: ``(result, next_directive)`` tuple.

    Internally:
    1. Injects directive into proposer input if present.
    2. Runs proposers via ``at_least(min_success, ...)``.
    3. Feeds ``QuorumResult`` to aggregator.
    4. Extracts directive from ``LayerOutput`` (if returned).
    """
    proposer_flows = [agent(p, timeout=timeout) for p in proposers]
    return (
        pure(_inject_directive)
        .flat_map(at_least(min_success, *proposer_flows))
        .flat_map(agent(aggregator))
        .map(_extract_directive)
    )


def moa_tree(layers: list[Flow]) -> Flow:
    """Multi-layer MOA pipeline with directive passing.

    Wraps input as ``(input, None)`` for the first layer,
    chains layers via ``flat_map``, and unwraps the final result.

    Each *layer* should be a ``Flow[(I, directive), (O, directive)]``
    — typically produced by ``moa_layer()``.
    """
    if not layers:
        raise ValueError("moa_tree requires at least one layer")

    pipeline = pure(lambda x: (x, None)).flat_map(layers[0])
    for layer in layers[1:]:
        pipeline = pipeline.flat_map(layer)
    return pipeline.map(lambda state: state[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/moa/test_patterns.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/moa/patterns.py tests/moa/test_patterns.py
git commit -m "feat(moa): add moa_layer and moa_tree pattern functions"
```

---

### Task 6: MoASystem

**Files:**
- Create: `tests/moa/test_moa_system.py`
- Create: `everything_is_an_actor/moa/system.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/moa/test_moa_system.py`:

```python
"""Tests for MoASystem high-level entry point."""

import pytest

from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow.quorum import QuorumResult
from everything_is_an_actor.moa.system import MoASystem
from everything_is_an_actor.moa.patterns import moa_layer, moa_tree

pytestmark = pytest.mark.anyio


class EchoProposer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Echo] {input}"


class JoinAggregator(AgentActor[QuorumResult[str], str]):
    async def execute(self, input: QuorumResult[str]) -> str:
        return " | ".join(input.succeeded)


class TestMoASystem:
    async def test_run(self):
        system = MoASystem()
        try:
            result = await system.run(
                moa_tree([
                    moa_layer(
                        proposers=[EchoProposer],
                        aggregator=JoinAggregator,
                    ),
                ]),
                "hello",
            )
            assert "[Echo]" in result
        finally:
            await system.shutdown()

    async def test_run_stream(self):
        system = MoASystem()
        try:
            events = []
            async for event in system.run_stream(
                moa_tree([
                    moa_layer(
                        proposers=[EchoProposer],
                        aggregator=JoinAggregator,
                    ),
                ]),
                "hello",
            ):
                events.append(event)
            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/moa/test_moa_system.py -v`
Expected: ImportError — `cannot import name 'MoASystem'`

- [ ] **Step 3: Implement MoASystem**

Create `everything_is_an_actor/moa/system.py`:

```python
"""MoASystem — high-level entry point for MOA pipelines."""

from __future__ import annotations

from collections.abc import AsyncIterator

from everything_is_an_actor.agents import AgentSystem
from everything_is_an_actor.agents.task import TaskEvent
from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.flow.flow import Flow


class MoASystem:
    """One-stop MOA entry point for users who don't need low-level control.

    Owns the full ``ActorSystem → AgentSystem`` lifecycle.
    Delegates flow execution to ``AgentSystem.run_flow``.
    """

    __slots__ = ("_actor_system", "_agent_system")

    def __init__(self) -> None:
        self._actor_system = ActorSystem()
        self._agent_system = AgentSystem(self._actor_system)

    async def run(self, flow: Flow, input: object) -> object:
        """Execute a MOA pipeline, returning the final result."""
        return await self._agent_system.run_flow(flow, input)

    async def run_stream(self, flow: Flow, input: object) -> AsyncIterator[TaskEvent]:
        """Execute a MOA pipeline, yielding TaskEvent streams."""
        async for event in self._agent_system.run_flow_stream(flow, input):
            yield event

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Shut down the underlying actor system."""
        await self._agent_system.shutdown(timeout=timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/moa/test_moa_system.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/moa/system.py tests/moa/test_moa_system.py
git commit -m "feat(moa): add MoASystem high-level entry point"
```

---

### Task 7: Update moa/__init__.py and delete old files

**Files:**
- Modify: `everything_is_an_actor/moa/__init__.py`
- Delete: `everything_is_an_actor/moa/config.py`
- Delete: `everything_is_an_actor/moa/builder.py`
- Delete: `tests/moa/test_config.py`
- Delete: `tests/moa/test_builder.py`

- [ ] **Step 1: Check for imports of old API across the codebase**

Run: `grep -r "from everything_is_an_actor.moa.config import\|from everything_is_an_actor.moa.builder import\|from everything_is_an_actor.moa import MoABuilder\|from everything_is_an_actor.moa import MoANode\|from everything_is_an_actor.moa import MoATree" --include="*.py" .`

Fix any references found outside `tests/moa/test_config.py` and `tests/moa/test_builder.py` before proceeding.

- [ ] **Step 2: Rewrite moa/__init__.py**

Replace the entire content of `everything_is_an_actor/moa/__init__.py`:

```python
"""MOA (Mixture-of-Agents) — pattern library on top of Flow.

Provides convenience functions for the MOA orchestration pattern:
parallel proposers → quorum validation → aggregator, chained in layers.
"""

from everything_is_an_actor.moa.layer_output import LayerOutput
from everything_is_an_actor.moa.patterns import moa_layer, moa_tree
from everything_is_an_actor.moa.system import MoASystem
from everything_is_an_actor.moa.utils import format_references

__all__ = [
    "LayerOutput",
    "MoASystem",
    "format_references",
    "moa_layer",
    "moa_tree",
]
```

- [ ] **Step 3: Delete old files**

```bash
rm everything_is_an_actor/moa/config.py
rm everything_is_an_actor/moa/builder.py
rm tests/moa/test_config.py
rm tests/moa/test_builder.py
```

- [ ] **Step 4: Run all moa tests to verify nothing breaks**

Run: `python -m pytest tests/moa/ -v`
Expected: All remaining tests PASS (test_patterns.py, test_moa_system.py, test_utils.py). Deleted tests gone.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --timeout=30`
Expected: All PASS. No other code imports the deleted modules.

- [ ] **Step 6: Commit**

```bash
git add everything_is_an_actor/moa/__init__.py
git rm everything_is_an_actor/moa/config.py everything_is_an_actor/moa/builder.py
git rm tests/moa/test_config.py tests/moa/test_builder.py
git commit -m "refactor(moa): remove MoABuilder/MoANode/MoATree, update public API"
```

---

### Task 8: Clean up old moa tests

**Files:**
- Review: `tests/moa/test_adversarial.py`
- Review: `tests/moa/test_stress.py`
- Review: `tests/moa/test_timeout.py`

These test files may import from deleted modules (`MoABuilder`, `MoANode`, `MoATree`). Each file must be checked and either rewritten to use the new API or deleted if no longer applicable.

- [ ] **Step 1: Check each file for imports from deleted modules**

Run: `grep -l "MoABuilder\|MoANode\|MoATree\|from everything_is_an_actor.moa.config\|from everything_is_an_actor.moa.builder" tests/moa/test_adversarial.py tests/moa/test_stress.py tests/moa/test_timeout.py`

For each file that imports deleted code:
- If the test validates behavior now covered by `test_patterns.py` or `test_moa_system.py` → delete it
- If the test validates unique behavior (e.g., adversarial edge cases, stress load, timeout handling) → rewrite to use `moa_layer` / `moa_tree` / `MoASystem`

- [ ] **Step 2: Rewrite or delete each affected file**

For each file, the rewrite pattern is:

```python
# Before:
from everything_is_an_actor.moa.builder import MoABuilder
from everything_is_an_actor.moa.config import MoANode, MoATree

tree = MoATree(nodes=[MoANode(proposers=[P1, P2], aggregator=A1)])
MoAAgent = MoABuilder().build(tree)
async for event in system.run(MoAAgent, "input"):
    ...

# After:
from everything_is_an_actor.moa import moa_layer, moa_tree, MoASystem

pipeline = moa_tree([moa_layer(proposers=[P1, P2], aggregator=A1)])
system = MoASystem()
result = await system.run(pipeline, "input")
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/moa/
git commit -m "test(moa): migrate remaining tests to new MOA-on-Flow API"
```

---

### Task 9: Update documentation

**Files:**
- Modify: `docs/moa-on-flow.md`

- [ ] **Step 1: Update docs/moa-on-flow.md**

Replace the content with the finalized design, marking all implementation items as complete. Update code examples to use the new API (`moa_layer`, `moa_tree`, `MoASystem`, `at_least`). Remove references to `MoABuilder`, `MoANode`, `MoATree`.

- [ ] **Step 2: Commit**

```bash
git add docs/moa-on-flow.md
git commit -m "docs: update moa-on-flow to reflect new implementation"
```
