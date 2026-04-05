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
