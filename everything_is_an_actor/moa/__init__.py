"""MOA (Mixture-of-Agents) — pattern library on top of Flow.

Provides convenience functions for the MOA orchestration pattern:
parallel proposers → quorum validation → aggregator, chained in layers.

Usage::

    from everything_is_an_actor.moa import MoASystem, moa_layer, moa_tree

    system = MoASystem()
    result = await system.run(
        moa_tree([
            moa_layer(proposers=[Agent1, Agent2], aggregator=Agg, min_success=1),
        ]),
        "query",
    )
    await system.shutdown()
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
