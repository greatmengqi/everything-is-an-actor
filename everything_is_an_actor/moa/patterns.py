"""MOA pattern functions — convenience combinators for Mixture-of-Agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from everything_is_an_actor.flow import agent, at_least, pure
from everything_is_an_actor.flow.flow import Flow
from everything_is_an_actor.moa.layer_output import LayerOutput

if TYPE_CHECKING:
    from everything_is_an_actor.agents import AgentActor


def _inject_directive(state: tuple[object, str | None]) -> object:
    """Unpack (input, directive); inject directive into input if present."""
    input_val, directive = state
    if directive is None:
        return input_val
    return {"input": input_val, "directive": directive}


def _extract_directive(output: object) -> tuple[object, str | None]:
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
