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
from everything_is_an_actor.flow.combinators import agent, loop, loop_with_state, pure, race, zip_all
from everything_is_an_actor.flow.interpreter import Interpreter
from everything_is_an_actor.flow.serialize import from_dict, to_dict
from everything_is_an_actor.flow.system import FlowSystem
from everything_is_an_actor.flow.visualize import to_mermaid

__all__ = [
    "Flow",
    "Continue",
    "Done",
    "FlowFilterError",
    "FlowSystem",
    "Interpreter",
    "agent",
    "pure",
    "race",
    "loop",
    "loop_with_state",
    "to_dict",
    "from_dict",
    "to_mermaid",
    "zip_all",
]
