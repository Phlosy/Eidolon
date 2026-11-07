"""The canonical provider requests: recorded once against a real model by
``scripts/record_provider_fixtures.py``, replayed by tests forever after.
Changing any byte of a request changes its fingerprint, so re-record then."""

from eidolon.agent.providers import ChatRequest, Message, ToolCall, ToolSpec

DEEPSEEK_MODEL = "deepseek-flash"
ANTHROPIC_MODEL = "claude-opus-5-5"

WEATHER_TOOL = ToolSpec(
    "get_weather",
    "Get the current weather for a city",
    {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
        "additionalProperties": False,
    },
)


def cases(model: str) -> dict[str, ChatRequest]:
    return {
        "ping": ChatRequest(
            model=model,
            system="You are a terse assistant.",
            messages=(Message.user("Reply with exactly one word: pong"),),
            max_tokens=64,  # thinking-mode models spend output tokens before the word
            temperature=0,
        ),
        "tool_call": ChatRequest(
            model=model,
            messages=(
                Message.user("What is the weather in Beijing right now? Use the get_weather tool."),
            ),
            tools=(WEATHER_TOOL,),
            max_tokens=128,
            temperature=0,
        ),
        "tool_roundtrip": ChatRequest(
            model=model,
            messages=(
                Message.user("What is the weather in Beijing right now? Use the get_weather tool."),
                Message.assistant(
                    None,
                    ToolCall("call_fixture_1", "get_weather", {"city": "Beijing"}),
                    # DeepSeek thinking mode requires the chain back with the turn;
                    # Anthropic ignores an unknown key (no thinking_blocks here).
                    provider_state={
                        "reasoning_content": "The user wants Beijing's weather; call get_weather."
                    },
                ),
                Message.tool("call_fixture_1", '{"temperature_c": 22, "sky": "sunny"}'),
            ),
            tools=(WEATHER_TOOL,),
            max_tokens=64,
            temperature=0,
        ),
    }


# What a correct answer looks like, whichever model produced it.
EXPECTATIONS = {
    "ping": lambda text, calls, done: "pong" in text.lower() and done.stop_reason == "end_turn",
    "tool_call": lambda text, calls, done: (
        len(calls) == 1
        and calls[0].name == "get_weather"
        and "beijing" in str(calls[0].arguments.get("city", "")).lower()
        and done.stop_reason == "tool_use"
    ),
    "tool_roundtrip": lambda text, calls, done: "22" in text and not calls,
}
