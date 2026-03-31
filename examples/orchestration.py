"""M3 example: streaming events from an orchestrated agent tree.

Run:
    python examples/orchestration.py
"""

from __future__ import annotations

import asyncio

from actor_for_agents.agents import AgentActor, Task, TaskResult
from actor_for_agents.agents.system import AgentSystem


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------


class SearchAgent(AgentActor[str, list[str]]):
    """Simulates a web search by returning fake results."""

    async def execute(self, input: str) -> list[str]:
        await self.emit_progress(f"searching: {input}")
        await asyncio.sleep(0.05)  # simulate I/O
        return [f"{input} result {i}" for i in range(3)]


class SummaryAgent(AgentActor[list[str], str]):
    """Summarizes a list of results into one string."""

    async def execute(self, input: list[str]) -> str:
        await self.emit_progress("summarizing results")
        await asyncio.sleep(0.02)
        return " | ".join(input[:3])


class ResearchOrchestrator(AgentActor[str, str]):
    """Root orchestrator: fans out to two searches, then summarizes."""

    async def execute(self, input: str) -> str:
        await self.emit_progress("starting research")

        # Fan out to two parallel searches
        search_results: list[TaskResult[list[str]]] = await self.context.dispatch_parallel(
            [
                (SearchAgent, Task(input=f"{input} news")),
                (SearchAgent, Task(input=f"{input} docs")),
            ]
        )

        combined = search_results[0].output + search_results[1].output

        # Sequential summarization
        summary: TaskResult[str] = await self.context.dispatch(SummaryAgent, Task(input=combined))
        return summary.output


# ---------------------------------------------------------------------------
# HTTP-style SSE handler (simulated)
# ---------------------------------------------------------------------------


def format_sse(event) -> str:
    path = event.agent_path
    if event.parent_agent_path:
        path = f"{event.parent_agent_path} → {event.agent_path}"
    return f"data: [{event.type}] {path} — {event.data or ''}\n"


async def handle_request(user_query: str) -> None:
    system = AgentSystem()

    print(f"=== Research run for: {user_query!r} ===\n")
    async for event in system.run(ResearchOrchestrator, user_query, run_id="demo-run"):
        print(format_sse(event), end="")

    print("\n=== Done ===")
    await system.shutdown()


if __name__ == "__main__":
    asyncio.run(handle_request("actor model"))
