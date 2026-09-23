"""Prompts for the agent and the verifier.

The two system prompts are frozen strings — they sit in the cached prompt prefix,
so nothing volatile (step text, budgets, timestamps) goes in them. Everything
per-step or per-turn is built by the functions below and sent in messages.
"""

from __future__ import annotations

from journeydrive_automation.script import Script

AGENT_SYSTEM = """\
You operate a real computer through screenshots and mouse/keyboard tools, one \
action per turn, to complete one step of a scripted task.

How to work:
- Look at the latest screenshot, pick the single most direct action toward the \
current step's goal, and take it. You get a fresh screenshot after every action.
- Give every position as fractions of the screenshot (fx, fy from 0.0 to 1.0). \
Never estimate pixel coordinates.
- For small or crowded targets, call preview_click first and adjust until the \
marker sits on the target.
- Stay on the current step. Earlier steps are already done; later steps are not \
yours yet. Don't tidy up, explore, or do anything the step doesn't ask for.
- When the step's success check is visibly met, call step_complete. An \
independent reviewer checks the screen, so only call it when it's really true.
- If the step can't be done from where you are, or you notice you're repeating \
yourself, call step_failed with the reason instead of trying ever more \
elaborate workarounds. The step will be retried from a clean start.
"""

VERIFIER_SYSTEM = """\
You review a screenshot to decide whether one step of a scripted computer task \
succeeded. Judge only what is visible in the screenshot against the success \
check. You are not told what actions were taken, and you shouldn't guess. If \
the screenshot doesn't clearly show the success check is met, answer met=false \
and say what you see instead.
"""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "met": {"type": "boolean"},
        "evidence": {"type": "string", "description": "One or two sentences on what the screenshot shows."},
    },
    "required": ["met", "evidence"],
    "additionalProperties": False,
}


def step_briefing(
    script: Script,
    step_index: int,
    completed_summaries: list[str],
    retry_note: str | None,
    max_actions: int,
) -> str:
    """Opening message of a step's fresh context: the whole task as a checklist
    (so the agent knows where it is) with only this step open."""
    lines = [f"Task: {script.name}", "", "Checklist:"]
    for i, step in enumerate(script.steps):
        if i < step_index:
            mark, suffix = "[x]", f" — done: {completed_summaries[i]}"
        elif i == step_index:
            mark, suffix = "[>]", "  <- CURRENT STEP"
        else:
            mark, suffix = "[ ]", ""
        lines.append(f"{mark} {i + 1}. {step.goal}{suffix}")
    step = script.steps[step_index]
    lines += [
        "",
        f"Current step {step_index + 1} of {len(script.steps)}: {step.goal}",
        f"Done when: {step.success}",
        f"You have at most {max_actions} actions for this step.",
    ]
    if retry_note:
        lines += ["", f"A previous attempt at this step failed: {retry_note}", "Start fresh from the current screen."]
    return "\n".join(lines)


def anchor(script: Script, step_index: int, actions_used: int, max_actions: int, notes: list[str]) -> str:
    """Restated on every observation, so the goal is never more than one message away."""
    step = script.steps[step_index]
    text = (
        f"Current step {step_index + 1}/{len(script.steps)}: {step.goal}\n"
        f"Done when: {step.success}\n"
        f"Actions used: {actions_used}/{max_actions}."
    )
    if notes:
        text += "\n\n" + "\n".join(f"Note: {n}" for n in notes)
    return text


def verifier_request(goal: str, success: str) -> str:
    return f"Step goal: {goal}\nSuccess check: {success}\n\nIs the success check met in this screenshot?"
