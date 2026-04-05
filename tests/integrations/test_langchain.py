"""Tests for LangChainAgent — mocked LLM."""

import pytest

pytestmark = pytest.mark.anyio
from unittest.mock import AsyncMock, MagicMock


class TestLangChainAgent:
    @pytest.mark.asyncio
    async def test_basic_invoke(self):
        from everything_is_an_actor.integrations.langchain.agent import LangChainAgent

        mock_model = AsyncMock()
        mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="hello response", tool_calls=None))

        class TestAgent(LangChainAgent[str, str]):
            model = mock_model
            system_prompt = "You are helpful."

        instance = TestAgent()
        result = await instance.execute("hello")
        assert result == "hello response"
        mock_model.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_call_loop(self):
        from everything_is_an_actor.integrations.langchain.agent import LangChainAgent

        # Round 1: model wants to call a tool
        tool_response = MagicMock(
            content="",
            tool_calls=[{"name": "search", "args": {"q": "test"}, "id": "tc1"}],
        )
        # Round 2: model produces final answer
        final_response = MagicMock(content="found: result", tool_calls=None)

        mock_model = AsyncMock()
        mock_model.ainvoke = AsyncMock(side_effect=[tool_response, final_response])
        mock_model.bind_tools = MagicMock(return_value=mock_model)

        mock_tool = AsyncMock()
        mock_tool.name = "search"
        mock_tool.ainvoke = AsyncMock(return_value="search result for test")

        class ToolAgent(LangChainAgent[str, str]):
            model = mock_model
            tools = [mock_tool]
            system_prompt = "Search and answer."

        result = await ToolAgent().execute("find test")
        assert result == "found: result"
        assert mock_model.ainvoke.call_count == 2
        mock_tool.ainvoke.assert_called_once_with({"q": "test"})

    @pytest.mark.asyncio
    async def test_with_output_parser(self):
        from everything_is_an_actor.integrations.langchain.agent import LangChainAgent

        mock_model = AsyncMock()
        mock_model.ainvoke = AsyncMock(return_value=MagicMock(content='{"key": "value"}', tool_calls=None))

        mock_parser = MagicMock()
        mock_parser.parse = MagicMock(return_value={"key": "value"})
        mock_parser.get_format_instructions = MagicMock(return_value="Return JSON")

        class TestAgent(LangChainAgent[str, dict]):
            model = mock_model
            system_prompt = "Extract data."
            output_parser = mock_parser

        result = await TestAgent().execute("parse this")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_override_execute(self):
        from everything_is_an_actor.integrations.langchain.agent import LangChainAgent

        mock_model = AsyncMock()

        class CustomAgent(LangChainAgent[str, str]):
            model = mock_model

            async def execute(self, input: str) -> str:
                return f"custom:{input}"

        result = await CustomAgent().execute("test")
        assert result == "custom:test"
        mock_model.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_model_raises(self):
        from everything_is_an_actor.integrations.langchain.agent import LangChainAgent

        class NoModelAgent(LangChainAgent[str, str]):
            pass

        with pytest.raises(ValueError, match="model is not set"):
            await NoModelAgent().execute("test")
