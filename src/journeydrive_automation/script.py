from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ScriptError(Exception):
    pass


class Step(BaseModel):
    """One goal for the agent, plus the check the independent verifier judges it by."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, description="What to accomplish, in plain language.")
    success: str = Field(
        min_length=1,
        description="What the screen looks like once the goal is done — judged from a screenshot alone.",
    )
    max_actions: int | None = Field(default=None, ge=1, description="Overrides the script's max_actions_per_step.")


class Script(BaseModel):
    # extra="forbid" for the same reason as every other config in this repo: a
    # typo'd key is a hard error, not a silently ignored setting.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    machine: str | None = Field(default=None, description="Target machine_id; --machine on the CLI overrides it.")
    model: str = "claude-opus-5"
    monitor: int | None = Field(
        default=None, description="Monitor index to capture and aim at (default: the primary physical monitor)."
    )
    max_actions_per_step: int = Field(default=15, ge=1)
    max_attempts_per_step: int = Field(default=2, ge=1)
    steps: list[Step] = Field(min_length=1)

    def max_actions_for(self, step: Step) -> int:
        return step.max_actions if step.max_actions is not None else self.max_actions_per_step


def load_script(path: str | Path) -> Script:
    path = Path(path)
    if not path.is_file():
        raise ScriptError(f"Script file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ScriptError(f"Script file at {path} is not valid JSON: {e}") from e
    try:
        return Script.model_validate(data)
    except ValidationError as e:
        raise ScriptError(f"Script file at {path} is invalid:\n{e}") from e
