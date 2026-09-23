from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")  # controller-side-only extra; not installed for the thin-client builds
pytest.importorskip("anthropic")

from journeydrive_automation.script import ScriptError, load_script


def write(tmp_path, data) -> str:
    path = tmp_path / "script.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(path)


VALID = {"name": "demo", "steps": [{"goal": "Open Notepad", "success": "Notepad is in front"}]}


def test_valid_script_parses_with_defaults(tmp_path) -> None:
    script = load_script(write(tmp_path, VALID))
    assert script.machine is None
    assert script.model == "claude-opus-5"
    assert script.monitor is None
    assert script.max_actions_per_step == 15
    assert script.max_attempts_per_step == 2
    assert script.max_actions_for(script.steps[0]) == 15


def test_step_max_actions_overrides_the_script_default(tmp_path) -> None:
    data = {**VALID, "steps": [{"goal": "g", "success": "s", "max_actions": 4}]}
    script = load_script(write(tmp_path, data))
    assert script.max_actions_for(script.steps[0]) == 4


@pytest.mark.parametrize("path", sorted(Path("examples").glob("automation*.example.json")), ids=lambda p: p.name)
def test_example_scripts_are_valid(path) -> None:
    script = load_script(path)
    assert script.steps


@pytest.mark.parametrize(
    "data",
    [
        {**VALID, "stesp": []},  # typo'd top-level key
        {**VALID, "steps": [{"goal": "g", "success": "s", "timeout": 5}]},  # unknown step key
        {**VALID, "steps": []},
        {**VALID, "steps": [{"goal": "g"}]},
        {**VALID, "steps": [{"goal": "", "success": "s"}]},
        {**VALID, "max_actions_per_step": 0},
        {"steps": VALID["steps"]},  # no name
    ],
)
def test_invalid_scripts_are_rejected(tmp_path, data) -> None:
    with pytest.raises(ScriptError):
        load_script(write(tmp_path, data))


def test_missing_file_and_bad_json_are_rejected(tmp_path) -> None:
    with pytest.raises(ScriptError, match="not found"):
        load_script(tmp_path / "nope.json")
    with pytest.raises(ScriptError, match="not valid JSON"):
        load_script(write(tmp_path, "{not json"))
