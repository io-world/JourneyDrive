"""The LangGraph state machine that runs a script, one goal step at a time.

    start_step -> observe -> guard -> agent -+- UI action -----> act -> observe
        ^                                    +- step_complete -> verify -+- met -----> step_passed -> start_step / finish
        |                                    |                           +- not met -> observe
        +------- retry (fresh context) <-----+- step_failed / out of budget / stuck -> attempt_failed -> fail

Anti-drift guardrails live here (see docs/AUTOMATION.md's "Keeping the model on
track"): every step starts from a fresh context (start_step), every observation
restates the goal and budget (observe), guard enforces the budget and catches
loops, and only the independent verifier can pass a step.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from typing import Any, TypedDict

import anthropic
from langgraph.graph import END, START, StateGraph
from PIL import Image as PILImage

from journeydrive_automation import prompts
from journeydrive_automation.llm import LLM, LLMRefusal, ToolCall, Usage
from journeydrive_automation.runlog import RunLog, redact_tool_input
from journeydrive_automation.script import Script
from journeydrive_automation.tools import (
    PREVIEW_CLICK,
    STEP_COMPLETE,
    STEP_FAILED,
    ToolExecutor,
    ToolOutcome,
    image_block,
)
from journeydrive_mcp.client import JourneyDriveError

logger = logging.getLogger(__name__)

# Stall detection: this many identical consecutive tool calls, or this many
# consecutive screenshots that look the same, count as being stuck.
_REPEAT_LIMIT = 3
# The first stall in an attempt gets a warning note; this many fail the attempt.
_MAX_STALLS = 2


class RunState(TypedDict, total=False):
    step_index: int
    attempt: int
    completed: list[str]  # one-line summary per passed step
    step_results: list[dict]
    retry_note: str | None
    status: str  # running | passed | failed
    # Per-attempt — reset by start_step:
    messages: list[dict]
    actions_used: int
    stalls: int
    notes: list[str]
    pending: dict | None  # what observe turns into the next user message
    last_call: ToolCall | None
    recent_calls: list[str]
    screen_sigs: list[str]
    failure: str | None


def screen_signature(png: bytes) -> str:
    """A coarse fingerprint of a screenshot: small, grayscale, quantized — so a
    ticking clock or cursor blink doesn't count as the screen changing."""
    with PILImage.open(io.BytesIO(png)) as img:
        thumb = img.convert("L").resize((64, 36))
        return hashlib.sha1(bytes(p // 16 for p in thumb.tobytes())).hexdigest()


def _call_signature(call: ToolCall) -> str:
    return call.name + json.dumps(call.input, sort_keys=True)


def build_graph(script: Script, executor: ToolExecutor, llm: LLM, runlog: RunLog, usage: Usage):
    def _step(state: RunState):
        return script.steps[state["step_index"]]

    def _max_actions(state: RunState) -> int:
        return script.max_actions_for(_step(state))

    def _where(state: RunState) -> dict:
        return {"step": state["step_index"] + 1, "attempt": state["attempt"]}

    async def start_step(state: RunState) -> dict:
        step = _step(state)
        logger.info("step %d/%d attempt %d: %s", state["step_index"] + 1, len(script.steps), state["attempt"], step.goal)
        runlog.event("step_start", **_where(state), goal=step.goal, success=step.success, retry_note=state.get("retry_note"))
        briefing = prompts.step_briefing(
            script, state["step_index"], state["completed"], state.get("retry_note"), _max_actions(state)
        )
        return {
            "messages": [],
            "actions_used": 0,
            "stalls": 0,
            "notes": [],
            "pending": {"kind": "briefing", "text": briefing},
            "last_call": None,
            "recent_calls": [],
            "screen_sigs": [],
            "failure": None,
        }

    async def observe(state: RunState) -> dict:
        pending = state["pending"]
        messages = list(state["messages"])
        screen_sigs = list(state["screen_sigs"])
        outcome: ToolOutcome | None = pending.get("outcome")

        blocks: list[dict] = []
        if outcome is not None:
            blocks.append({"type": "text", "text": outcome.text})
            blocks += [image_block(img) for img in outcome.images]
            for img in outcome.images:
                runlog.event("image", **_where(state), file=runlog.save_image(img, "preview"))
        if outcome is None or outcome.wants_screenshot:
            png = await executor.screenshot()
            runlog.event("observe", **_where(state), file=runlog.save_image(png, "observe"))
            screen_sigs.append(screen_signature(png))
            blocks.append(image_block(png))

        anchor = prompts.anchor(script, state["step_index"], state["actions_used"], _max_actions(state), state["notes"])
        if pending["kind"] == "tool_result":
            # Screenshots go inside the tool_result, so context editing clears old
            # ones; the goal anchor stays outside it and is never cleared.
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": pending["tool_use_id"], "content": blocks, "is_error": outcome.is_error},
                    {"type": "text", "text": anchor},
                ],
            })
        else:
            lead = pending["text"]
            messages.append({"role": "user", "content": [{"type": "text", "text": lead}, *blocks] + (
                [] if pending["kind"] == "briefing" else [{"type": "text", "text": anchor}]
            )})
        return {"messages": messages, "pending": None, "notes": [], "screen_sigs": screen_sigs}

    async def guard(state: RunState) -> dict:
        recent, sigs = state["recent_calls"], state["screen_sigs"]
        # Repeating the same scroll is normal progress through a long list; a
        # scroll that stops changing anything is caught by `frozen` instead.
        repeated = (
            len(recent) >= _REPEAT_LIMIT
            and len(set(recent[-_REPEAT_LIMIT:])) == 1
            and not recent[-1].startswith("scroll{")
        )
        frozen = len(sigs) > _REPEAT_LIMIT and len(set(sigs[-(_REPEAT_LIMIT + 1):])) == 1
        update: dict[str, Any] = {}
        note = None
        if repeated or frozen:
            stalls = state["stalls"] + 1
            why = "the same action repeated" if repeated else f"the screen unchanged after {_REPEAT_LIMIT} actions"
            runlog.event("stall", **_where(state), reason=why, stalls=stalls)
            logger.info("stall detected (%s), %d/%d", why, stalls, _MAX_STALLS)
            if stalls >= _MAX_STALLS:
                return {"stalls": stalls, "failure": f"stuck: {why}, twice"}
            update.update(stalls=stalls, recent_calls=[], screen_sigs=[])
            note = (
                f"You appear stuck ({why}). Stop and reconsider: try a clearly different approach, "
                "or call step_failed if the step can't be done from here."
            )
        if state["actions_used"] >= _max_actions(state):
            note = (note + "\n" if note else "") + (
                "You have no actions left for this step. Call step_complete if the success check is met, "
                "otherwise step_failed."
            )
        if note:
            # This user message hasn't been sent yet, so extending it isn't a
            # history edit.
            messages = list(state["messages"])
            last = dict(messages[-1])
            last["content"] = [*last["content"], {"type": "text", "text": f"Note: {note}"}]
            messages[-1] = last
            update["messages"] = messages
        return update

    def route_guard(state: RunState) -> str:
        return "attempt_failed" if state.get("failure") else "agent"

    async def agent(state: RunState) -> dict:
        try:
            reply = await llm.agent_turn(state["messages"])
        except LLMRefusal as e:
            runlog.event("refusal", **_where(state), explanation=str(e))
            return {"failure": f"the model declined: {e}", "last_call": None}
        usage.add(reply.usage)
        messages = [*state["messages"], {"role": "assistant", "content": reply.content}]
        call = reply.tool_call
        said = " ".join(b.text for b in reply.content if getattr(b, "type", None) == "text").strip()
        runlog.event(
            "agent",
            **_where(state),
            tool=call.name if call else None,
            input=redact_tool_input(call.name, call.input) if call else None,
            text=said or None,
            stop_reason=reply.stop_reason,
            usage=vars(reply.usage),
        )
        if call is None:
            # No tool call: nudge back toward the tools. Costs an action, so a
            # model that keeps chatting still runs out of budget.
            return {
                "messages": messages,
                "last_call": None,
                "actions_used": state["actions_used"] + 1,
                "pending": {"kind": "nudge", "text": "Take one action with a tool, or call step_complete or step_failed."},
            }
        logger.info("agent -> %s %s", call.name, redact_tool_input(call.name, call.input))
        return {"messages": messages, "last_call": call}

    def route_agent(state: RunState) -> str:
        call = state.get("last_call")
        if state.get("failure"):
            return "attempt_failed"
        if call is None:
            return "observe"
        if call.name == STEP_COMPLETE:
            return "verify"
        if call.name == STEP_FAILED:
            return "attempt_failed"
        if state["actions_used"] >= _max_actions(state):
            return "attempt_failed"
        return "act"

    async def act(state: RunState) -> dict:
        call = state["last_call"]
        outcome = await executor.execute(call.name, call.input)
        runlog.event("act", **_where(state), tool=call.name, is_error=outcome.is_error, result=outcome.text)
        recent = [*state["recent_calls"], _call_signature(call)]
        return {
            "actions_used": state["actions_used"] + 1,
            "recent_calls": recent,
            "pending": {"kind": "tool_result", "tool_use_id": call.id, "outcome": outcome},
        }

    async def verify(state: RunState) -> dict:
        step, call = _step(state), state["last_call"]
        png = await executor.screenshot()
        file = runlog.save_image(png, "verify")
        try:
            verdict = await llm.verify(image_block(png), prompts.verifier_request(step.goal, step.success))
        except LLMRefusal as e:
            runlog.event("refusal", **_where(state), explanation=str(e))
            return {"failure": f"the verifier declined: {e}"}
        usage.add(verdict.usage)
        runlog.event("verify", **_where(state), file=file, met=verdict.met, evidence=verdict.evidence, claimed=call.input.get("summary"))
        logger.info("verifier: met=%s — %s", verdict.met, verdict.evidence)
        if verdict.met:
            return {"completed": [*state["completed"], call.input.get("summary") or step.goal]}
        outcome = ToolOutcome(
            text=f"Not accepted yet. The reviewer looked at the screen and says: {verdict.evidence} "
            "Keep working on this step, or call step_failed if it can't be done.",
            wants_screenshot=False,
            images=[png],
        )
        return {
            "actions_used": state["actions_used"] + 1,
            "pending": {"kind": "tool_result", "tool_use_id": call.id, "outcome": outcome},
        }

    def route_verify(state: RunState) -> str:
        if state.get("failure"):
            return "attempt_failed"
        return "step_passed" if len(state["completed"]) > state["step_index"] else "observe"

    async def step_passed(state: RunState) -> dict:
        runlog.event("step_passed", **_where(state), actions=state["actions_used"])
        result = {
            "step": state["step_index"] + 1,
            "goal": _step(state).goal,
            "status": "passed",
            "attempts": state["attempt"],
            "actions": state["actions_used"],
            "summary": state["completed"][-1],
        }
        return {
            "step_results": [*state["step_results"], result],
            "step_index": state["step_index"] + 1,
            "attempt": 1,
            "retry_note": None,
        }

    def route_step_passed(state: RunState) -> str:
        return "start_step" if state["step_index"] < len(script.steps) else "finish"

    async def attempt_failed(state: RunState) -> dict:
        call = state.get("last_call")
        if state.get("failure"):
            reason = state["failure"]
        elif call is not None and call.name == STEP_FAILED:
            reason = f"the agent gave up: {call.input.get('reason', '')}"
        else:
            reason = f"used all {_max_actions(state)} actions without completing the step"
        runlog.event("attempt_failed", **_where(state), reason=reason)
        logger.info("attempt %d failed: %s", state["attempt"], reason)
        if state["attempt"] < script.max_attempts_per_step:
            return {"attempt": state["attempt"] + 1, "retry_note": reason, "failure": None}
        result = {
            "step": state["step_index"] + 1,
            "goal": _step(state).goal,
            "status": "failed",
            "attempts": state["attempt"],
            "actions": state["actions_used"],
            "reason": reason,
        }
        return {"step_results": [*state["step_results"], result], "failure": reason}

    def route_attempt_failed(state: RunState) -> str:
        return "fail" if state.get("failure") else "start_step"

    async def finish(state: RunState) -> dict:
        return {"status": "passed"}

    async def fail(state: RunState) -> dict:
        return {"status": "failed"}

    graph = StateGraph(RunState)
    for node in (start_step, observe, guard, agent, act, verify, step_passed, attempt_failed, finish, fail):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "start_step")
    graph.add_edge("start_step", "observe")
    graph.add_edge("observe", "guard")
    graph.add_conditional_edges("guard", route_guard, ["agent", "attempt_failed"])
    graph.add_conditional_edges("agent", route_agent, ["observe", "verify", "attempt_failed", "act"])
    graph.add_edge("act", "observe")
    graph.add_conditional_edges("verify", route_verify, ["step_passed", "observe", "attempt_failed"])
    graph.add_conditional_edges("step_passed", route_step_passed, ["start_step", "finish"])
    graph.add_conditional_edges("attempt_failed", route_attempt_failed, ["start_step", "fail"])
    graph.add_edge("finish", END)
    graph.add_edge("fail", END)
    return graph.compile()


def _recursion_limit(script: Script) -> int:
    # Each action is ~4 node visits (agent, act, observe, guard); budget for
    # every step using every action on every attempt, plus per-step overhead.
    per_attempt = max(script.max_actions_for(s) for s in script.steps) * 4 + 12
    return len(script.steps) * script.max_attempts_per_step * per_attempt + 10


async def run_automation(script: Script, machine: str, executor: ToolExecutor, llm: LLM, runlog: RunLog) -> dict:
    """Run the whole script; returns (and writes) the run summary."""
    usage = Usage()
    graph = build_graph(script, executor, llm, runlog, usage)
    runlog.event("run_start", script=script.name, machine=machine, model=script.model, steps=len(script.steps))
    initial: RunState = {"step_index": 0, "attempt": 1, "completed": [], "step_results": [], "retry_note": None, "status": "running"}
    error = None
    final: dict = initial
    try:
        final = await graph.ainvoke(initial, config={"recursion_limit": _recursion_limit(script)})
    except (JourneyDriveError, anthropic.APIError) as e:
        # The machine or the API became unreachable — not something a retry of
        # the step would fix, so the run stops here.
        error = str(e)
        logger.error("run aborted: %s", e)
        runlog.event("run_aborted", error=error)
    status = final.get("status") if error is None else "failed"
    summary = {
        "script": script.name,
        "machine": machine,
        "model": script.model,
        "status": status,
        "steps": final.get("step_results", []),
        "error": error,
        "usage": vars(usage),
        "estimated_cost_usd": usage.cost_usd(script.model),
        "run_dir": str(runlog.dir),
    }
    runlog.event("run_end", status=status)
    runlog.write_summary(summary)
    return summary
