"""Flow ADT — syntax tree for composable agent workflows.

Flow[I, O] is a morphism I -> O in a free symmetric monoidal category.
All combinators build ADT nodes (data) — no execution until interpreted.

Concurrency primitives (categorical):
    seq:     flat_map    — Monad bind / Kleisli composition
    par:     zip         — Tensor product
    map:     map         — Functor
    alt:     branch      — Coproduct dispatch
    race:    race        — First completed wins
    recover: recover     — Supervision
    divert:  divert_to   — Akka-style side-channel
    loop:    loop        — tailRecM / trace
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar, Union

if TYPE_CHECKING:
    from everything_is_an_actor.agents.agent_actor import AgentActor

A = TypeVar("A")
B = TypeVar("B")
I = TypeVar("I")
O = TypeVar("O")
O2 = TypeVar("O2")
I2 = TypeVar("I2")
O3 = TypeVar("O3")
S = TypeVar("S")


# ── Control types (loop) ─────────────────────────────────

@dataclass(frozen=True)
class Continue(Generic[A]):
    """Loop continues — feed value back as next iteration's input."""

    value: A


@dataclass(frozen=True)
class Done(Generic[B]):
    """Loop terminates — produce final result."""

    value: B


# ── Flow base ────────────────────────────────────────────


class Flow(Generic[I, O]):
    """Base class for all Flow ADT variants.

    Flow[I, O] is a morphism I -> O — data, not execution.
    Use method-chain API (Scala Future style) to compose.
    """

    # -- Functor --

    def map(self, f: Callable[[O], O2]) -> Flow[I, O2]:
        """Post-compose with a pure function."""
        return _Map(source=self, f=f)

    # -- Monad --

    def flat_map(self, next_flow: Flow[O, O2]) -> Flow[I, O2]:
        """Sequential composition. Output of self feeds as input to next."""
        return _FlatMap(first=self, next=next_flow)

    # -- Tensor --

    def zip(self, other: Flow[I2, O2]) -> Flow[tuple[I, I2], tuple[O, O2]]:
        """Parallel composition (tensor product)."""
        return _Zip(left=self, right=other)

    # -- Coproduct --

    def branch(self, mapping: dict[type, Flow]) -> Flow[I, Any]:
        """Route by output type (isinstance dispatch)."""
        return _Branch(source=self, mapping=mapping)

    def branch_on(
        self,
        predicate: Callable[[O], bool],
        then: Flow[O, O2],
        otherwise: Flow[O, O2],
    ) -> Flow[I, O2]:
        """Binary predicate branch."""
        return _BranchOn(source=self, predicate=predicate, then=then, otherwise=otherwise)

    # -- Error recovery (supervision) --

    def recover(self, handler: Callable[[Exception], O]) -> Flow[I, O]:
        """Recover from errors with a pure handler function."""
        return _Recover(source=self, handler=handler)

    def recover_with(self, handler: Flow[Exception, O]) -> Flow[I, O]:
        """Recover from errors with another Flow."""
        return _RecoverWith(source=self, handler=handler)

    def fallback_to(self, other: Flow[I, O]) -> Flow[I, O]:
        """If self fails, try other with the original input."""
        return _FallbackTo(source=self, fallback=other)

    # -- Side-channel --

    def divert_to(self, side: Flow[O, Any], when: Callable[[O], bool]) -> Flow[I, O]:
        """Fire-and-forget to side flow when predicate matches."""
        return _DivertTo(source=self, side=side, when=when)

    # -- Utilities --

    def and_then(self, callback: Callable[[O], None]) -> Flow[I, O]:
        """Tap — side-effect callback, value passes through unchanged."""
        return _AndThen(source=self, callback=callback)

    def filter(self, predicate: Callable[[O], bool]) -> Flow[I, O]:
        """Guard — raise FlowFilterError if predicate fails."""
        return _Filter(source=self, predicate=predicate)


# ── ADT variants (all frozen dataclasses) ────────────────


@dataclass(frozen=True)
class _Agent(Flow[I, O]):
    """Leaf — wraps an AgentActor class."""

    cls: type[AgentActor[I, O]]  # type: ignore[type-arg]
    timeout: float = 30.0


@dataclass(frozen=True)
class _Pure(Flow[I, O]):
    """Lift a pure function into Flow."""

    f: Callable[[I], O]


@dataclass(frozen=True)
class _FlatMap(Flow[I, O]):
    """Sequential composition: first then next."""

    first: Flow  # Flow[I, M]
    next: Flow   # Flow[M, O]


@dataclass(frozen=True)
class _Zip(Flow):
    """Parallel composition (tensor product)."""

    left: Flow   # Flow[I, O]
    right: Flow  # Flow[I2, O2]


@dataclass(frozen=True)
class _Map(Flow[I, O]):
    """Post-compose with a pure function."""

    source: Flow  # Flow[I, M]
    f: Callable   # Callable[[M], O]


@dataclass(frozen=True)
class _Branch(Flow[I, O]):
    """Coproduct dispatch — route by isinstance on output type."""

    source: Flow  # Flow[I, T] where T is the union
    mapping: MappingProxyType  # {SubType: Flow[SubType, O]}

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, MappingProxyType):
            object.__setattr__(self, "mapping", MappingProxyType(dict(self.mapping)))


@dataclass(frozen=True)
class _BranchOn(Flow[I, O]):
    """Binary predicate branch."""

    source: Flow
    predicate: Callable
    then: Flow
    otherwise: Flow


@dataclass(frozen=True)
class _ZipAll(Flow):
    """N-way parallel composition — all flows run concurrently."""

    flows: tuple[Flow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.flows, tuple):
            object.__setattr__(self, "flows", tuple(self.flows))


@dataclass(frozen=True)
class _Race(Flow[I, O]):
    """Competitive parallelism — first to complete wins."""

    flows: tuple[Flow[I, O], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.flows, tuple):
            object.__setattr__(self, "flows", tuple(self.flows))


@dataclass(frozen=True)
class _Recover(Flow[I, O]):
    """Recover from errors with a pure handler."""

    source: Flow[I, O]
    handler: Callable[[Exception], O]


@dataclass(frozen=True)
class _RecoverWith(Flow[I, O]):
    """Recover from errors with another Flow."""

    source: Flow[I, O]
    handler: Flow[Exception, O]


@dataclass(frozen=True)
class _FallbackTo(Flow[I, O]):
    """If source fails, run fallback with original input."""

    source: Flow[I, O]
    fallback: Flow[I, O]


@dataclass(frozen=True)
class _DivertTo(Flow[I, O]):
    """Side-channel — fire-and-forget when predicate matches."""

    source: Flow[I, O]
    side: Flow[O, Any]
    when: Callable[[O], bool]


@dataclass(frozen=True)
class _Loop(Flow[I, O]):
    """tailRecM — iterate body until Done, max_iter safety bound."""

    body: Flow[I, Union[Continue[I], Done[O]]]
    max_iter: int = 10


@dataclass(frozen=True)
class _LoopWithState(Flow[I, O]):
    """Trace — loop with explicit feedback state S."""

    body: Flow  # Flow[(I, S), (Continue[I] | Done[O], S)]
    init_state: Any = None
    max_iter: int = 10


@dataclass(frozen=True)
class _AndThen(Flow[I, O]):
    """Tap — side-effect callback, passes value through unchanged."""

    source: Flow[I, O]
    callback: Callable[[O], None]


@dataclass(frozen=True)
class _Filter(Flow[I, O]):
    """Guard — raise FlowFilterError if predicate fails."""

    source: Flow[I, O]
    predicate: Callable[[O], bool]


class FlowFilterError(Exception):
    """Raised when a filter predicate fails."""

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(f"Flow filter rejected value: {value!r}")
