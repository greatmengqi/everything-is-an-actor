"""MOA Builder — interpreter that resolves MoATree config into runnable AgentActors.

Type flow: MoATree -> [MoANode] -> build() -> [ResolvedNode] -> _MoAAgent
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task, TaskResult, TaskStatus
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.moa.config import MoANode, MoATree

O = TypeVar("O")

# System-level exceptions that should NOT be recovered.
# recover() catches Exception; these are the Exception subclasses
# that indicate infrastructure failure rather than domain errors.
_SYSTEM_ERRORS = (MemoryError, SystemExit)


@dataclass(frozen=True)
class ResolvedNode:
    """A fully resolved layer — all subtrees compiled to AgentActor classes."""

    proposers: tuple[type[AgentActor], ...]
    aggregator: type[AgentActor]
    min_success: int
    proposer_timeout: float


@dataclass(frozen=True)
class LayerOutput(Generic[O]):
    """Optional aggregator output carrying a directive for the next layer.

    If an aggregator returns a plain value, it's used as-is.
    If it returns LayerOutput, .result is the value and .directive
    is injected into the next layer's proposer input.
    """

    result: O
    directive: str | None = None


class MoABuilder:
    """Interpreter: resolves a MoATree into a runnable AgentActor class."""

    def build(self, tree: MoATree) -> type[AgentActor]:
        """Recursively resolve tree and produce an AgentActor subclass."""
        resolved_nodes = [self._resolve_node(n) for n in tree.nodes]

        class _MoAAgent(AgentActor[Any, Any]):
            _moa_nodes: list[ResolvedNode] = resolved_nodes

            async def execute(self, input: Any) -> Any:
                current = input
                directive = None

                for node in self._moa_nodes:
                    proposer_tasks: list[tuple[type[AgentActor], Task]] = []
                    for cls in node.proposers:
                        task_input = current if directive is None else {"input": current, "directive": directive}
                        proposer_tasks.append((cls, Task(input=task_input)))

                    results = await self._run_proposers(proposer_tasks, node.min_success, node.proposer_timeout)

                    agg_output = (await self.context.ask(node.aggregator, Task(input=results))).get_or_raise()

                    match agg_output:
                        case LayerOutput(result=r, directive=d):
                            current, directive = r, d
                        case _:
                            current, directive = agg_output, None

                return current

            async def _run_proposers(
                self,
                tasks: list[tuple[type[AgentActor], Task]],
                min_success: int,
                proposer_timeout: float = 30.0,
            ) -> list[TaskResult]:
                """Run proposers in parallel with Validated semantics.

                Domain errors are recovered into failed TaskResults.
                System errors (MemoryError, etc.) propagate immediately.
                """

                def _recover_handler(e: Exception, tid: str) -> TaskResult:
                    if isinstance(e, _SYSTEM_ERRORS):
                        raise e
                    return TaskResult(
                        task_id=tid,
                        error=e,
                        status=TaskStatus.FAILED,
                    )

                futures = [
                    self.context.ask(target, msg, timeout=proposer_timeout).recover(
                        lambda e, tid=msg.id: _recover_handler(e, tid)
                    )
                    for target, msg in tasks
                ]
                all_results = await ComposableFuture.sequence(futures)

                successes = [r for r in all_results if r.is_success()]
                if len(successes) < min_success:
                    failed = [r for r in all_results if r.is_failure()]
                    raise RuntimeError(
                        f"MOA: {len(successes)}/{len(tasks)} proposers succeeded, "
                        f"need >= {min_success}. "
                        f"Failures: {[r.error for r in failed]}"
                    )
                return all_results

        return _MoAAgent

    def _resolve_node(self, node: MoANode) -> ResolvedNode:
        """Resolve ProposerSpecs: recursively build nested MoATrees."""
        resolved_proposers: list[type[AgentActor]] = []
        for spec in node.proposers:
            match spec:
                case type() as cls:
                    resolved_proposers.append(cls)
                case MoATree() as subtree:
                    resolved_proposers.append(self.build(subtree))
        return ResolvedNode(
            proposers=tuple(resolved_proposers),
            aggregator=node.aggregator,
            min_success=node.min_success,
            proposer_timeout=node.proposer_timeout,
        )
