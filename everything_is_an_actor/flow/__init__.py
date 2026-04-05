"""Flow — composable agent orchestration with categorical concurrency primitives.

Usage::

    from everything_is_an_actor.flow import agent, pure, race, loop, Flow, Continue, Done

    pipeline = (
        agent(Researcher)
        .zip(agent(Analyst))
        .map(merge)
        .flat_map(agent(Writer))
        .recover_with(agent(Fallback))
    )
"""

from everything_is_an_actor.flow.flow import Continue, Done, Flow, FlowFilterError
from everything_is_an_actor.flow.combinators import agent, loop, loop_with_state, pure, race

__all__ = [
    "Flow",
    "Continue",
    "Done",
    "FlowFilterError",
    "agent",
    "pure",
    "race",
    "loop",
    "loop_with_state",
]
