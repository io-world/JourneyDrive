"""The two Claude calls the graph makes — the agent's turn and the verifier's
verdict. Kept behind this small class so tests inject a scripted fake instead
of calling the API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from journeydrive_automation.prompts import AGENT_SYSTEM, VERDICT_SCHEMA, VERIFIER_SYSTEM
from journeydrive_automation.tools import TOOLS

logger = logging.getLogger(__name__)

# Server-side refusal fallbacks: a classifier decline is re-run on Anthropic's
# recommended fallback model inside the same call instead of failing the step.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_CONTEXT_EDITING_BETA = "context-management-2025-06-27"

# Old screenshots are the bulk of a step's context and only the recent ones
# matter — once 6 tool results have piled up, clear all but the latest 3.
_CONTEXT_EDITS = {
    "edits": [
        {
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "tool_uses", "value": 6},
            "keep": {"type": "tool_uses", "value": 3},
        }
    ]
}

# USD per million tokens (input, output), for the run summary's cost estimate.
# Cache reads bill at 0.1x input and 5-minute cache writes at 1.25x.
_PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class LLMRefusal(Exception):
    """The request was declined even after server-side fallbacks."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens

    def cost_usd(self, model: str) -> float | None:
        if model not in _PRICES:
            return None
        price_in, price_out = _PRICES[model]
        return (
            self.input_tokens * price_in
            + self.cache_read_input_tokens * price_in * 0.1
            + self.cache_creation_input_tokens * price_in * 1.25
            + self.output_tokens * price_out
        ) / 1_000_000

    @classmethod
    def from_response(cls, usage: Any) -> Usage:
        return cls(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        )


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class AgentReply:
    content: list  # the assistant turn to append to the step's messages, unchanged
    tool_call: ToolCall | None
    stop_reason: str | None
    usage: Usage = field(default_factory=Usage)


@dataclass
class Verdict:
    met: bool
    evidence: str
    usage: Usage = field(default_factory=Usage)


class LLM(Protocol):
    async def agent_turn(self, messages: list[dict]) -> AgentReply: ...

    async def verify(self, screenshot_block: dict, request_text: str) -> Verdict: ...


def _check_refusal(response: Any) -> None:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        explanation = getattr(details, "explanation", None) if details else None
        raise LLMRefusal(explanation or "the model declined the request")


class ClaudeLLM:
    def __init__(self, model: str, client: anthropic.AsyncAnthropic | None = None) -> None:
        self.model = model
        # Credentials come from ANTHROPIC_API_KEY or an `ant auth login` profile —
        # the SDK resolves them itself.
        self._client = client or anthropic.AsyncAnthropic()

    def has_credentials(self) -> bool:
        """Whether the SDK resolved any credential — checked before a run starts,
        since the SDK itself only fails (with a TypeError) at the first request.
        Mirrors the SDK's own check: an API key, an auth token, or a profile's
        token cache (the SDK has no public accessor for the last one)."""
        client = self._client
        return bool(client.api_key or client.auth_token or getattr(client, "_token_cache", None) is not None)

    async def agent_turn(self, messages: list[dict]) -> AgentReply:
        response = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": AGENT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            # One action per turn: every action gets its own fresh screenshot.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            cache_control={"type": "ephemeral"},
            context_management=_CONTEXT_EDITS,
            fallbacks="default",
            betas=[_FALLBACK_BETA, _CONTEXT_EDITING_BETA],
            messages=messages,
        )
        _check_refusal(response)
        tool_call = next(
            (ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in response.content if b.type == "tool_use"),
            None,
        )
        return AgentReply(
            content=list(response.content),
            tool_call=tool_call,
            stop_reason=response.stop_reason,
            usage=Usage.from_response(response.usage),
        )

    async def verify(self, screenshot_block: dict, request_text: str) -> Verdict:
        # A separate conversation with no access to the agent's history or
        # reasoning — it judges the screen alone.
        response = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=VERIFIER_SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            fallbacks="default",
            betas=[_FALLBACK_BETA],
            messages=[{"role": "user", "content": [screenshot_block, {"type": "text", "text": request_text}]}],
        )
        _check_refusal(response)
        usage = Usage.from_response(response.usage)
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return Verdict(met=False, evidence=f"verifier returned no verdict (stop_reason={response.stop_reason})", usage=usage)
        try:
            data = json.loads(text)
            return Verdict(met=bool(data["met"]), evidence=str(data["evidence"]), usage=usage)
        except (json.JSONDecodeError, KeyError, TypeError):
            return Verdict(met=False, evidence=f"verifier returned an unreadable verdict: {text[:200]}", usage=usage)
