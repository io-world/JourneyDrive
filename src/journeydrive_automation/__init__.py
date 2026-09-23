from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from journeydrive_automation.graph import run_automation
from journeydrive_automation.llm import ClaudeLLM
from journeydrive_automation.runlog import RunLog
from journeydrive_automation.script import ScriptError, load_script
from journeydrive_automation.tools import ToolExecutor
from journeydrive_mcp.client import JourneyDriveClient, JourneyDriveError
from journeydrive_mcp.config import ConfigError, load_settings
from journeydrive_mcp.logging_setup import configure_logging
from journeydrive_windows_thinclient.tls_pinning import CertificateFingerprintMismatch

__all__ = ["main"]

PROG = "journeydrive-automation"

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=PROG, description="Run a JSON goal script on a thin client through the broker.")
    parser.add_argument("script", help="Path to the automation script (see examples/automation.example.json).")
    parser.add_argument(
        "--config",
        default=None,
        help="Broker connection config — the same file the MCP server uses (e.g. scripts/mcp/mcp_config.json). "
        "Falls back to the JOURNEYDRIVE_BROKER_* environment variables when omitted.",
    )
    parser.add_argument("--machine", default=None, help="Target machine_id; overrides the script's own `machine`.")
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Where everything the automation writes goes: journeydrive-automation.log plus one folder per run "
        "(default: logs/).",
    )
    return parser.parse_args(argv)


def _fail(message: str) -> None:
    # Logged as well as printed, so a run that never starts still leaves a
    # trace in journeydrive-automation.log.
    logger.error("%s", message)
    print(f"{PROG}: {message}", file=sys.stderr)
    sys.exit(1)


async def _run(args: argparse.Namespace) -> dict:
    # ANTHROPIC_API_KEY from a gitignored .env in the directory it's run from
    # (or a parent — normally the repo root). A variable already set in the
    # shell wins over the file.
    load_dotenv(find_dotenv(usecwd=True), override=False)
    try:
        settings = load_settings(args.config)
    except ConfigError as e:
        _fail(str(e))
    try:
        script = load_script(args.script)
    except ScriptError as e:
        _fail(str(e))
    machine = args.machine or script.machine
    if not machine:
        _fail("no target machine: set `machine` in the script or pass --machine")
    llm = ClaudeLLM(script.model)
    if not llm.has_credentials():
        _fail(
            "no Claude API credential found. Add ANTHROPIC_API_KEY=sk-ant-... to the .env file in the repo root, "
            "export it in your shell, or run `ant auth login` once."
        )

    try:
        client = JourneyDriveClient(settings)
    except CertificateFingerprintMismatch as e:
        _fail(str(e))
    try:
        # Broker-pushed timeout, same as the MCP server applies at startup —
        # a long type_text can outlast the 10s default.
        try:
            pushed = await client.get_mcp_config()
        except JourneyDriveError as e:
            logger.warning("could not fetch broker-pushed config, using local settings only: %s", e)
            pushed = {}
        if "timeout" in pushed:
            client.set_timeout(pushed["timeout"])
        try:
            await client.health(machine)
        except JourneyDriveError as e:
            _fail(f"machine {machine!r} isn't reachable through the broker: {e}")

        runlog = RunLog(args.log_dir, script.name)
        with runlog.capture_console():
            logger.info("running %r on %s with %s — logging to %s", script.name, machine, script.model, runlog.dir)
            summary = await run_automation(
                script, machine, ToolExecutor(client, machine, script.monitor), llm, runlog
            )
            _report(summary)
        return summary
    finally:
        await client.aclose()


def _report(summary: dict) -> None:
    """The end-of-run result, logged (so it lands in the console, run.log, and
    journeydrive-automation.log alike) rather than printed."""
    for step in summary["steps"]:
        detail = step.get("summary") or step.get("reason", "")
        logger.info(
            "  step %s: %s (%s attempt(s), %s action(s)) — %s",
            step["step"], step["status"], step["attempts"], step["actions"], detail,
        )
    cost = summary["estimated_cost_usd"]
    logger.info(
        "%s: %s%s%s — log: %s",
        summary["script"],
        summary["status"].upper(),
        f" — {summary['error']}" if summary["error"] else "",
        f" — ~${cost:.2f}" if cost is not None else "",
        summary["run_dir"],
    )


def main() -> None:
    args = parse_args()
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(log_file=str(log_dir / "journeydrive-automation.log"))
    try:
        summary = asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.warning("interrupted — stopped before the run finished")
        sys.exit(130)
    except Exception:
        # Anything unexpected goes into the log with its traceback, not just
        # the terminal.
        logger.exception("unexpected error — the run stopped")
        sys.exit(1)
    sys.exit(0 if summary["status"] == "passed" else 1)
