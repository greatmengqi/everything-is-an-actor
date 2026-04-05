"""LangChainAgent — declarative AgentActor backed by a LangChain ChatModel."""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

from everything_is_an_actor.agents.agent_actor import AgentActor

I = TypeVar("I")
O = TypeVar("O")


class LangChainAgent(AgentActor[I, O], Generic[I, O]):
    """AgentActor that delegates to a LangChain ChatModel.

    Class attributes configure the LLM; override execute() for custom logic.

    Example::

        class Summarizer(LangChainAgent[str, str]):
            model = ChatOpenAI(model="gpt-4o-mini")
            system_prompt = "Summarize the input."
    """

    model: ClassVar[Any] = None               # BaseChatModel
    tools: ClassVar[list] = []                 # list[BaseTool]
    system_prompt: ClassVar[str] = ""
    output_parser: ClassVar[Any] = None        # BaseOutputParser | None

    async def execute(self, input: I) -> O:
        """Default: construct messages, invoke model, optionally parse output."""
        if self.model is None:
            raise ValueError(f"{type(self).__name__}.model is not set")

        messages: list[dict[str, str]] = []

        if self.system_prompt:
            system_content = self.system_prompt
            if self.output_parser is not None and hasattr(self.output_parser, "get_format_instructions"):
                system_content += f"\n\n{self.output_parser.get_format_instructions()}"
            messages.append({"role": "system", "content": system_content})

        messages.append({"role": "user", "content": str(input)})

        bound_model = self.model.bind_tools(self.tools) if self.tools else self.model

        response = await bound_model.ainvoke(messages)
        content = response.content

        if self.output_parser is not None:
            return self.output_parser.parse(content)

        return content
