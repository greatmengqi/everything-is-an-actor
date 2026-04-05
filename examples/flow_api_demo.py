"""
Categorical Flow API — composable agent orchestration over LangChain.

Six concurrency primitives from symmetric monoidal category + coproduct + trace:

    seq:     >>          morphism composition         (A→B, B→C) → A→C
    par:     *           tensor product ⊗             (A→B, C→D) → (A,C)→(B,D)
    alt:     |           coproduct dispatch            (A→C, B→C) → (A|B)→C
    race:    race()      competitive parallelism       (A→B, A→B) → A→B
    loop:    loop()      trace / feedback              ((A,S)→(B,S)) → A→B
    recover: recover()   error recovery                (A→E|B, E→B) → A→B

Flow[I, O] is a morphism I → O — data, not execution.
Build as syntax tree, interpret into actor topology at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from lanactor import Flow, agent, pure, race, loop, recover
from lanactor.langchain import LangChainAgent


# ── Tools ─────────────────────────────────────────────────

@tool
def web_search(query: str) -> str:
    """Search the web for information."""
    ...


@tool
def doc_search(query: str) -> str:
    """Search internal documents."""
    ...


# ── Agents (actors backed by LangChain) ──────────────────

class Researcher(LangChainAgent[str, str]):
    """Web research — search and summarise."""
    model = ChatOpenAI(model="gpt-4o")
    tools = [web_search]
    system_prompt = "Search the web. Return a concise summary of findings."


class DocAnalyst(LangChainAgent[str, str]):
    """Internal document analysis."""
    model = ChatOpenAI(model="gpt-4o")
    tools = [doc_search]
    system_prompt = "Analyse internal documents. Return relevant excerpts."


class Writer(LangChainAgent[str, str]):
    """Synthesise research into a report."""
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Write a comprehensive report from the provided research."


class QuickAnswer(LangChainAgent[str, str]):
    """Fast, shallow answer for simple questions."""
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Give a brief, direct answer."


class Fallback(LangChainAgent[str, str]):
    """Last-resort simple answer."""
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Provide a best-effort answer."


# ── Critic with typed output (sum type for loop control) ──

@dataclass(frozen=True)
class Revise:
    """Loop continues — critic wants another pass."""
    feedback: str


@dataclass(frozen=True)
class Accept:
    """Loop terminates — critic approves."""
    text: str


CriticVerdict = Union[Revise, Accept]


class Critic(LangChainAgent[str, CriticVerdict]):
    """Review a draft. Accept or request revision."""
    model = ChatOpenAI(model="gpt-4o")
    system_prompt = (
        "Review the draft. If it's good, output Accept with the final text. "
        "If it needs work, output Revise with specific feedback."
    )


# ── Route classifier (sum type for conditional dispatch) ──

@dataclass(frozen=True)
class SimpleQ:
    query: str


@dataclass(frozen=True)
class ComplexQ:
    query: str


QueryType = Union[SimpleQ, ComplexQ]


class Classifier(LangChainAgent[str, QueryType]):
    """Classify query complexity."""
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Is this a simple factual question or a complex research question?"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flow composition — all six primitives
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 1. par (⊗) — parallel research
#    Researcher * DocAnalyst : str → (str, str)
#    then merge into one context
parallel_research: Flow[str, str] = (
    agent(Researcher) * agent(DocAnalyst)
    >> pure(lambda pair: f"Web:\n{pair[0]}\n\nDocs:\n{pair[1]}")
)

# 2. seq (>>) — sequential pipeline
#    research → write
draft_pipeline: Flow[str, str] = parallel_research >> agent(Writer)

# 3. loop (trace) — iterative refinement
#    Critic outputs Revise | Accept.
#    On Revise: feed back to Writer. On Accept: exit.
refine: Flow[str, str] = loop(
    agent(Writer) >> agent(Critic),
    max_iter=3,
)

# 4. recover — error handling (supervision 的范畴表达)
#    If the pipeline fails, fall back to a simple answer
safe_pipeline: Flow[str, str] = recover(
    parallel_research >> refine,
    handler=agent(Fallback),
)

# 5. alt (coproduct) — conditional routing
#    Classifier produces SimpleQ | ComplexQ
#    Route to the matching handler
smart_router: Flow[str, str] = (
    agent(Classifier)
    >> (agent(QuickAnswer) | safe_pipeline)
    #   SimpleQ → str   |  ComplexQ → str
    #   ─────────────────────────────────
    #   SimpleQ | ComplexQ → str          ← coproduct codiagonal
)

# 6. race — competitive parallelism
#    Two models race, first response wins, other cancelled
fast_answer: Flow[str, str] = race(
    agent(Researcher),
    agent(DocAnalyst),
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Nested composition — Flows are values, compose arbitrarily
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Wrap the whole thing in another layer
full_system: Flow[str, str] = recover(
    smart_router,
    handler=fast_answer,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Run — Flow is data until you interpret it
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    from lanactor import ActorSystem, FlowSystem

    actor_system = ActorSystem()
    system = FlowSystem(actor_system)

    # One-shot execution — FlowSystem is the subject
    result = await system.run(full_system, "What is quantum computing?")
    print(result)

    # Streaming — events flow through the actor topology
    async for event in system.run_stream(full_system, "Compare RLHF vs DPO"):
        print(event)

    await actor_system.shutdown()

    # Inspect — because Flow is data, not execution
    print(full_system.visualize())      # → mermaid diagram
    print(full_system.type_signature)   # → str → str
