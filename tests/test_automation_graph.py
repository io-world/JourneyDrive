from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as PILImage

pytest.importorskip("langgraph")  # controller-side-only extra; not installed for the thin-client builds
pytest.importorskip("anthropic")

from journeydrive_automation.graph import run_automation
from journeydrive_automation.llm import AgentReply, LLMRefusal, ToolCall, Usage, Verdict
from journeydrive_automation.runlog import RunLog
from journeydrive_automation.script import Script
from journeydrive_automation.tools import ToolExecutor
from journeydrive_mcp.client import JourneyDriveError

MACHINE = "office-pc"
# Primary physical monitor offset from the origin, so fx/fy resolution is
# checked against left/top rather than just width/height.
MONITORS = [
    {"index": 0, "left": 0, "top": 0, "width": 1100, "height": 550},
    {"index": 1, "left": 100, "top": 50, "width": 1000, "height": 500},
]


def _png(gray: int) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (100, 50), color=(gray, gray, gray)).save(buf, format="PNG")
    return buf.getvalue()


def make_client(*, frozen: bool = False) -> AsyncMock:
    client = AsyncMock()
    client.list_monitors.return_value = MONITORS
    counter = {"n": 0}

    async def screenshot(machine, format=None, quality=None, monitor=None):
        counter["n"] += 1
        # A visibly different screen on every capture unless frozen, so stall
        # detection only fires in the tests that want it to.
        gray = 128 if frozen else (counter["n"] * 37) % 256
        return _png(gray), "image/png"

    client.screenshot.side_effect = screenshot
    return client


def call(name: str, **tool_input) -> ToolCall:
    return ToolCall(id=f"toolu_{name}", name=name, input=tool_input)


def click(fx: float = 0.5, fy: float = 0.5) -> ToolCall:
    return call("click", fx=fx, fy=fy, button="left", clicks=1)


class FakeLLM:
    """Plays back scripted agent turns and verdicts; records what it was sent."""

    def __init__(self, turns: list, verdicts: list[bool] | None = None) -> None:
        self.turns = list(turns)
        self.verdicts = list(verdicts or [])
        self.agent_messages: list[list[dict]] = []
        self.verify_requests: list[str] = []

    async def agent_turn(self, messages: list[dict]) -> AgentReply:
        self.agent_messages.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if turn is None:
            return AgentReply(content=[SimpleNamespace(type="text", text="Thinking out loud.")], tool_call=None, stop_reason="end_turn")
        block = SimpleNamespace(type="tool_use", id=turn.id, name=turn.name, input=turn.input)
        return AgentReply(content=[block], tool_call=turn, stop_reason="tool_use", usage=Usage(input_tokens=10, output_tokens=5))

    async def verify(self, screenshot_block: dict, request_text: str) -> Verdict:
        self.verify_requests.append(request_text)
        met = self.verdicts.pop(0)
        return Verdict(met=met, evidence="looks done" if met else "the window is still closed", usage=Usage(input_tokens=3))


def make_script(**overrides) -> Script:
    data = {
        "name": "test script",
        "machine": MACHINE,
        "steps": [
            {"goal": "Open Notepad", "success": "Notepad is in front"},
            {"goal": "Type hello", "success": "hello is visible"},
        ],
    }
    data.update(overrides)
    return Script.model_validate(data)


async def run(script: Script, llm: FakeLLM, client: AsyncMock, tmp_path) -> tuple[dict, RunLog]:
    runlog = RunLog(tmp_path / "runs", script.name)
    summary = await run_automation(script, MACHINE, ToolExecutor(client, MACHINE, script.monitor), llm, runlog)
    return summary, runlog


def events(runlog: RunLog) -> list[dict]:
    return [json.loads(line) for line in (runlog.dir / "events.jsonl").read_text().splitlines()]


def all_text(messages: list[dict]) -> str:
    return json.dumps(messages, default=str)


@pytest.mark.asyncio
async def test_happy_path_passes_every_step_and_writes_the_run_log(tmp_path) -> None:
    client = make_client()
    llm = FakeLLM(
        [click(0.5, 0.5), call("step_complete", summary="Notepad opened"), call("type_text", text="hello"), call("step_complete", summary="typed hello")],
        verdicts=[True, True],
    )
    summary, runlog = await run(make_script(), llm, client, tmp_path)

    assert summary["status"] == "passed"
    assert [s["status"] for s in summary["steps"]] == ["passed", "passed"]
    assert summary["steps"][0]["summary"] == "Notepad opened"
    # fx/fy=0.5 on the offset primary monitor: 100 + 0.5*1000, 50 + 0.5*500.
    client.click_mouse.assert_awaited_once_with(MACHINE, button="left", clicks=1, x=600, y=300)
    assert json.loads((runlog.dir / "summary.json").read_text())["status"] == "passed"
    assert any(p.name.endswith("_verify.png") for p in runlog.dir.iterdir())
    assert any(p.name.endswith("_observe.png") for p in runlog.dir.iterdir())
    assert summary["usage"]["input_tokens"] > 0


@pytest.mark.asyncio
async def test_each_step_starts_from_a_fresh_context_with_only_summaries_of_earlier_steps(tmp_path) -> None:
    llm = FakeLLM(
        [click(), call("step_complete", summary="Notepad opened"), call("step_complete", summary="typed")],
        verdicts=[True, True],
    )
    await run(make_script(), llm, make_client(), tmp_path)

    second_step_first_turn = llm.agent_messages[2]
    assert len(second_step_first_turn) == 1  # just the briefing, no step 1 transcript
    briefing = all_text(second_step_first_turn)
    assert "[x] 1. Open Notepad" in briefing and "done: Notepad opened" in briefing
    assert "CURRENT STEP" in briefing and "Type hello" in briefing


@pytest.mark.asyncio
async def test_every_observation_restates_the_goal_and_budget(tmp_path) -> None:
    llm = FakeLLM([click(), call("step_complete", summary="ok")], verdicts=[True])
    await run(make_script(steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}]), llm, make_client(), tmp_path)

    after_click = llm.agent_messages[1][-1]["content"]
    assert after_click[0]["type"] == "tool_result"
    assert "Current step 1/1: Open Notepad" in after_click[-1]["text"]
    assert "Actions used: 1/15" in after_click[-1]["text"]


@pytest.mark.asyncio
async def test_verifier_rejection_goes_back_to_the_agent_with_the_evidence(tmp_path) -> None:
    llm = FakeLLM(
        [call("step_complete", summary="done?"), call("step_complete", summary="done now")],
        verdicts=[False, True],
    )
    summary, _ = await run(make_script(steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}]), llm, make_client(), tmp_path)

    assert summary["status"] == "passed"
    assert "the window is still closed" in all_text(llm.agent_messages[1][-1]["content"])


@pytest.mark.asyncio
async def test_exhausted_budget_retries_from_a_fresh_context_then_fails(tmp_path) -> None:
    llm = FakeLLM([click(0.1), click(0.2), click(0.3), click(0.4), click(0.5), click(0.6)])
    script = make_script(max_actions_per_step=2, max_attempts_per_step=2)
    summary, runlog = await run(script, llm, make_client(), tmp_path)

    assert summary["status"] == "failed"
    assert summary["steps"] == [
        {"step": 1, "goal": "Open Notepad", "status": "failed", "attempts": 2, "actions": 2, "reason": "used all 2 actions without completing the step"}
    ]
    # The last-chance note was shown once the budget ran out.
    assert "no actions left" in all_text(llm.agent_messages[2])
    # Attempt 2 starts with just a briefing that explains attempt 1's failure.
    retry_first_turn = llm.agent_messages[3]
    assert len(retry_first_turn) == 1
    assert "A previous attempt at this step failed: used all 2 actions" in all_text(retry_first_turn)
    assert [e["attempt"] for e in events(runlog) if e["event"] == "attempt_failed"] == [1, 2]


@pytest.mark.asyncio
async def test_step_failed_retries_the_step(tmp_path) -> None:
    llm = FakeLLM(
        [call("step_failed", reason="no Notepad icon"), call("step_complete", summary="opened")],
        verdicts=[True],
    )
    summary, _ = await run(make_script(steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}]), llm, make_client(), tmp_path)

    assert summary["status"] == "passed"
    assert summary["steps"][0]["attempts"] == 2
    assert "the agent gave up: no Notepad icon" in all_text(llm.agent_messages[1])


@pytest.mark.asyncio
async def test_repeated_identical_action_warns_then_fails_the_attempt(tmp_path) -> None:
    llm = FakeLLM([click()] * 6)
    script = make_script(max_attempts_per_step=1, steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}])
    summary, runlog = await run(script, llm, make_client(), tmp_path)

    assert "You appear stuck (the same action repeated)" in all_text(llm.agent_messages[3])
    assert summary["status"] == "failed"
    assert summary["steps"][0]["reason"] == "stuck: the same action repeated, twice"
    assert [e["stalls"] for e in events(runlog) if e["event"] == "stall"] == [1, 2]


@pytest.mark.asyncio
async def test_repeated_scroll_is_not_a_stall_while_the_screen_changes(tmp_path) -> None:
    scroll = call("scroll", dy=-3, dx=0)
    llm = FakeLLM([scroll, scroll, scroll, scroll, call("step_complete", summary="found it")], verdicts=[True])
    summary, runlog = await run(make_script(steps=[{"goal": "Find it", "success": "It is visible"}]), llm, make_client(), tmp_path)

    assert summary["status"] == "passed"
    assert not [e for e in events(runlog) if e["event"] == "stall"]


@pytest.mark.asyncio
async def test_unchanged_screen_after_several_actions_is_a_stall(tmp_path) -> None:
    llm = FakeLLM([click(0.1), click(0.2), click(0.3), call("step_complete", summary="ok")], verdicts=[True])
    summary, _ = await run(
        make_script(steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}]), llm, make_client(frozen=True), tmp_path
    )

    assert "screen unchanged after 3 actions" in all_text(llm.agent_messages[3])
    assert summary["status"] == "passed"


@pytest.mark.asyncio
async def test_no_tool_call_is_nudged_and_costs_an_action(tmp_path) -> None:
    llm = FakeLLM([None, call("step_complete", summary="ok")], verdicts=[True])
    summary, _ = await run(make_script(steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}]), llm, make_client(), tmp_path)

    assert summary["status"] == "passed"
    assert "Take one action with a tool" in all_text(llm.agent_messages[1][-1])
    assert "Actions used: 1/15" in all_text(llm.agent_messages[1][-1])


@pytest.mark.asyncio
async def test_typed_and_clipboard_text_is_logged_as_a_count_only(tmp_path) -> None:
    secret = "hunter2-password"
    llm = FakeLLM(
        [call("type_text", text=secret), call("set_clipboard", text=secret), call("step_complete", summary="ok")],
        verdicts=[True],
    )
    _, runlog = await run(make_script(steps=[{"goal": "Log in", "success": "Logged in"}]), llm, make_client(), tmp_path)

    log_text = (runlog.dir / "events.jsonl").read_text()
    assert secret not in log_text
    typed = [e for e in events(runlog) if e["event"] == "agent" and e["tool"] in ("type_text", "set_clipboard")]
    assert [e["input"] for e in typed] == [{"chars": len(secret)}, {"chars": len(secret)}]


@pytest.mark.asyncio
async def test_preview_click_returns_the_marked_screenshot_without_clicking(tmp_path) -> None:
    client = make_client()
    llm = FakeLLM([call("preview_click", fx=0.5, fy=0.5), call("step_complete", summary="ok")], verdicts=[True])
    _, runlog = await run(make_script(steps=[{"goal": "Aim", "success": "Aimed"}]), llm, client, tmp_path)

    client.click_mouse.assert_not_awaited()
    result = llm.agent_messages[1][-1]["content"][0]
    assert result["type"] == "tool_result"
    assert [b["type"] for b in result["content"]] == ["text", "image"]  # the marked image, no extra screenshot
    assert any(p.name.endswith("_preview.png") for p in runlog.dir.iterdir())


@pytest.mark.asyncio
async def test_out_of_range_fraction_is_an_error_result_not_a_crash(tmp_path) -> None:
    client = make_client()
    llm = FakeLLM([click(1.5, 0.5), call("step_complete", summary="ok")], verdicts=[True])
    summary, _ = await run(make_script(steps=[{"goal": "Aim", "success": "Aimed"}]), llm, client, tmp_path)

    client.click_mouse.assert_not_awaited()
    result = llm.agent_messages[1][-1]["content"][0]
    assert result["is_error"] is True
    assert summary["status"] == "passed"


@pytest.mark.asyncio
async def test_refusal_fails_the_attempt(tmp_path) -> None:
    llm = FakeLLM([LLMRefusal("declined")])
    script = make_script(max_attempts_per_step=1, steps=[{"goal": "Open Notepad", "success": "Notepad is in front"}])
    summary, _ = await run(script, llm, make_client(), tmp_path)

    assert summary["status"] == "failed"
    assert summary["steps"][0]["reason"] == "the model declined: declined"


@pytest.mark.asyncio
async def test_losing_the_machine_aborts_the_run(tmp_path) -> None:
    client = make_client()
    client.screenshot.side_effect = JourneyDriveError("GET /machines/office-pc/screenshot -> 404: not connected")
    summary, runlog = await run(make_script(), FakeLLM([]), client, tmp_path)

    assert summary["status"] == "failed"
    assert "not connected" in summary["error"]
    assert json.loads((runlog.dir / "summary.json").read_text())["error"] == summary["error"]


@pytest.mark.asyncio
async def test_run_folder_captures_the_console_log_of_that_run_only(tmp_path) -> None:
    import logging

    runlog = RunLog(tmp_path / "logs", "capture")
    with runlog.capture_console():
        logging.getLogger("journeydrive_automation.test").warning("inside the run")
    logging.getLogger("journeydrive_automation.test").warning("after the run")

    text = (runlog.dir / "run.log").read_text()
    assert "inside the run" in text
    assert "after the run" not in text


def test_startup_failure_is_written_to_the_log_folder(tmp_path, monkeypatch) -> None:
    import logging
    import sys

    from journeydrive_automation import main

    log_dir = tmp_path / "logs"
    monkeypatch.setattr(sys, "argv", ["journeydrive-automation", str(tmp_path / "missing.json"), "--log-dir", str(log_dir)])
    for var in ("JOURNEYDRIVE_BROKER_HOST", "JOURNEYDRIVE_BROKER_API_KEY"):
        monkeypatch.setenv(var, "x" * 32)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        with pytest.raises(SystemExit) as exc:
            main()
    finally:
        for handler in root.handlers[len(before):]:
            root.removeHandler(handler)
            handler.close()

    assert exc.value.code == 1
    assert "Script file not found" in (log_dir / "journeydrive-automation.log").read_text()
