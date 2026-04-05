"""MOA Config ADT — recursive tree structure for Mixture-of-Agents pipelines.

Type hierarchy:
    ProposerSpec = type[AgentActor] | MoATree    # sum type: leaf | subtree
    MoANode      — product type: one layer (proposers + aggregator)
    MoATree      — pipeline: ordered sequence of MoANodes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from everything_is_an_actor.agents.agent_actor import AgentActor

# Sum type: leaf (AgentActor class) or subtree (nested MoATree)
ProposerSpec = Union[type["AgentActor"], "MoATree"]


@dataclass(frozen=True)
class MoANode:
    """A single layer: parallel proposers followed by an aggregator.

    Args:
        proposers: Agent classes or nested MoATrees to run in parallel.
        aggregator: Agent class that synthesizes proposer outputs.
        min_success: Minimum proposers that must succeed (Validated semantics).
    """

    proposers: list[ProposerSpec]
    aggregator: type[AgentActor]
    min_success: int = 1
    proposer_timeout: float = 30.0

    def __post_init__(self):
        if self.min_success < 1:
            raise ValueError("min_success must be >= 1")
        if self.min_success > len(self.proposers):
            raise ValueError(
                f"min_success ({self.min_success}) cannot exceed "
                f"number of proposers ({len(self.proposers)})"
            )


@dataclass(frozen=True)
class MoATree:
    """A pipeline of MoANodes executed sequentially.

    Each node's aggregator output becomes the next node's proposer input.

    Args:
        nodes: Ordered list of MoANodes forming the pipeline.
    """

    nodes: list[MoANode]

    @classmethod
    def repeated(cls, node: MoANode, num_layers: int) -> MoATree:
        """Create a tree by repeating the same node configuration N times."""
        return cls(nodes=[node] * num_layers)
