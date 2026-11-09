import asyncio
from types import SimpleNamespace

from claude_agent_sdk import ToolResultBlock
from core.firm_runtime_support import AttemptInputStream, tool_control_failure


def test_input_stays_open_until_explicit_completion():
    async def run():
        events = []
        channel = AttemptInputStream("fixture", lambda name, **details: events.append(name))
        stream = channel.messages()
        assert (await anext(stream))["message"]["content"] == "fixture"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(.02)
        assert not pending.done()  # An old SDK cannot close stdin on input EOF.
        channel.release("result")
        try:
            await pending
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("The released input stream must end")
        assert events[-1] == "input_stream_closed"
        assert channel.reason == "result"
    asyncio.run(run())


def test_input_cancellation_closes_generator():
    async def run():
        events = []
        channel = AttemptInputStream("fixture", lambda name, **details: events.append(name))
        stream = channel.messages()
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        channel.release("cancelled")
        await stream.aclose()
        assert events[-1] == "input_stream_release_requested"
        assert "input_stream_closed" in events
    asyncio.run(run())


def test_finish_waits_for_sdk_consumer_instead_of_closing_running_generator():
    async def run():
        channel = AttemptInputStream("fixture")
        stream = channel.messages()
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        assert stream.ag_running
        await channel.finish(stream)
        assert channel.closed.is_set()
        result = await asyncio.gather(pending, return_exceptions=True)
        assert isinstance(result[0], StopAsyncIteration)
    asyncio.run(run())


def test_control_failure_only_classifies_actual_failed_tool_receipts():
    text = "Tool permission request failed: Error: Stream closed"
    assert tool_control_failure(SimpleNamespace(content=[ToolResultBlock("T", text, True)]))
    assert not tool_control_failure(SimpleNamespace(content=[ToolResultBlock("T", text, False)]))
    assert not tool_control_failure(SimpleNamespace(content=text))
    assert not tool_control_failure(SimpleNamespace(content=[SimpleNamespace(text=text)]))
    assert not tool_control_failure(SimpleNamespace(content=[ToolResultBlock("T", "Business validation rejected order", True)]))
