"""Contract tests for the `StreamAdapter` Protocol in `core/ref.py`.

`core/` does not know what streaming items look like (those live in `agents/`),
but the Protocol still needs to preserve item-type information so concrete
adapters (e.g. `AgentStreamAdapter` over `StreamItem`) carry typed iterators
end-to-end instead of degrading to `AsyncIterator[Any]` mid-chain.

Per CLAUDE.md: 'Generic type arguments on public APIs must be preserved — do
not widen to Any mid-chain.' A non-generic Protocol forces every implementer
to widen, which violates that rule at the lowest layer.
"""

from __future__ import annotations

from typing import AsyncIterator, get_type_hints

from everything_is_an_actor.core.ref import StreamAdapter


def test_stream_adapter_protocol_is_generic_in_item_type():
    """`StreamAdapter` must be `Generic[ItemT]`.

    `__parameters__` on a Generic protocol is the tuple of its `TypeVar`s.
    A plain `Protocol` (no Generic) reports an empty tuple, meaning the
    protocol cannot carry item-type information forward to implementers.
    """
    params = getattr(StreamAdapter, "__parameters__", ())
    assert len(params) >= 1, (
        f"StreamAdapter expected to be Generic[ItemT], but __parameters__ = {params!r}. "
        "Without a TypeVar the protocol forces every adapter to return "
        "`AsyncIterator[Any]`, breaking end-to-end type flow."
    )


def test_stream_adapter_is_subscriptable_with_concrete_type():
    """Subscripting with a concrete type must succeed (proves Generic wiring)."""
    typed = StreamAdapter[str]
    assert typed is not None


def test_stream_adapter_protocol_return_annotation_uses_item_typevar():
    """The protocol's `ask_stream` return annotation must reference the
    item-type `TypeVar`, not be hard-wired to `Any`.

    This is what lets static checkers see `AsyncIterator[StreamItem]` (rather
    than `AsyncIterator[Any]`) when a subclass binds the parameter.
    """
    hints = get_type_hints(StreamAdapter.ask_stream)
    return_type = hints.get("return")
    assert return_type is not None, "ask_stream must have a return annotation"

    # Walk into the AsyncIterator[...] generic to inspect its argument.
    args = getattr(return_type, "__args__", ())
    assert args, f"ask_stream return type {return_type!r} should be parameterised"

    # The single arg must be a TypeVar (parameterised by the Protocol),
    # not a concrete `Any`. After binding, type checkers substitute concrete
    # types in; right now we just need to confirm the slot exists.
    item_arg = args[0]
    from typing import TypeVar

    assert isinstance(item_arg, TypeVar), (
        f"ask_stream return arg should be a TypeVar (so the protocol parameter "
        f"propagates), but got {item_arg!r}. Hard-wired Any defeats the point of "
        "making StreamAdapter generic."
    )


def test_stream_adapter_typed_subclass_records_binding():
    """A concrete `StreamAdapter[int]` subclass must record the `int` binding
    in `__orig_bases__` so static checkers (and reflective code) can read it.
    """

    class IntAdapter(StreamAdapter[int]):
        def ask_stream(  # noqa: ANN001
            self, ref, message, *, timeout
        ) -> AsyncIterator[int]:
            async def _gen() -> AsyncIterator[int]:
                yield 1

            return _gen()

    bases = getattr(IntAdapter, "__orig_bases__", ())
    args_seen = []
    for base in bases:
        args_seen.extend(getattr(base, "__args__", ()))
    assert int in args_seen, (
        f"IntAdapter(StreamAdapter[int]) should bind int into __orig_bases__, "
        f"but saw: {bases!r}"
    )
