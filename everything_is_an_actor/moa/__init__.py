"""Mixture of Agents (MOA) — composable multi-agent orchestration pattern.

Usage::

    from everything_is_an_actor.moa import MoATree, MoANode, MoABuilder

    tree = MoATree(nodes=[
        MoANode(proposers=[AgentA, AgentB], aggregator=Agg),
    ])
    MoAAgent = MoABuilder().build(tree)

    async for event in system.run(MoAAgent, "query"):
        print(event)
"""

from everything_is_an_actor.moa.config import MoANode, MoATree
from everything_is_an_actor.moa.builder import LayerOutput, MoABuilder, ResolvedNode
from everything_is_an_actor.moa.utils import format_references

__all__ = [
    "MoATree",
    "MoANode",
    "MoABuilder",
    "LayerOutput",
    "ResolvedNode",
    "format_references",
]
