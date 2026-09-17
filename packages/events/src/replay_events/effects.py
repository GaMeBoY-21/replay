"""The effect taxonomy — every hole through which non-determinism enters.

An effect describes a *request*: what the agent asked for. It never carries the
answer; that is a `Result`, recorded separately, because the two are appended at
different moments (see `EffectRequested` / `EffectCompleted`).

Every effect exposes `shape()`. Two effects have the same shape when they are
the same request. Replay compares the recorded shape against the live one at
every step; a mismatch means the recording was not deterministic, and it is the
only detector for an entire class of silent drift.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator

from .canonical import canonical

# Message fields the SDK mints fresh on every run. They are stripped before the
# model ever sees them, so replay is correct with them dropped — but a raw
# comparison that keeps them fails on a *correct* replay, every time.
VOLATILE_MESSAGE_FIELDS = ("tracking_id", "metadata")


def normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the per-run volatile fields from a message list, recursively.

    Applied at the contract boundary rather than at each call site, so there is
    exactly one place this can be forgotten.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: strip(v)
                for k, v in node.items()
                if k not in VOLATILE_MESSAGE_FIELDS
            }
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return [strip(m) for m in messages]


class BaseEffect(BaseModel):
    """Common behaviour. Subclasses declare the fields their shape is built from."""

    def shape(self) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        """A short human label for the UI and for assertion messages."""
        raise NotImplementedError


class ModelEffect(BaseEffect):
    """One model invocation, whatever the chunk count.

    The fingerprint covers five inputs, not three. `tool_choice` and
    `system_prompt_content` both change what the model is being asked for, and a
    fingerprint that omits them lets the divergence detector pass when it should
    fail — the failure mode being a replay that quietly serves the wrong
    recorded response.
    """

    effect_kind: Literal["model"] = "model"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_specs: list[dict[str, Any]] | None = None
    system_prompt: str | None = None
    tool_choice: dict[str, Any] | None = None
    system_prompt_content: list[dict[str, Any]] | None = None

    @field_validator("messages")
    @classmethod
    def _normalise(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Normalise on construction, so a caller cannot build an effect that
        # carries volatile fields into the log.
        return normalise_messages(messages)

    def shape(self) -> str:
        return canonical(
            {
                "kind": "model",
                "messages": self.messages,
                "tool_specs": self.tool_specs,
                "system_prompt": self.system_prompt,
                "tool_choice": self.tool_choice,
                "system_prompt_content": self.system_prompt_content,
            }
        )

    def describe(self) -> str:
        return f"model({len(self.messages)} messages)"


class ToolEffect(BaseEffect):
    """One tool invocation.

    `tool_use_id` is recorded for display and correlation but is deliberately
    outside the shape: the SDK mints it, and on the direct-call path it is built
    with `random.randint`. Including it would make every replay diverge.
    """

    effect_kind: Literal["tool"] = "tool"
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None

    def shape(self) -> str:
        return canonical({"kind": "tool", "name": self.name, "arguments": self.arguments})

    def describe(self) -> str:
        return f"tool({self.name})"


class ClockEffect(BaseEffect):
    """A read of the wall clock. Recorded, never regenerated."""

    effect_kind: Literal["clock"] = "clock"
    label: str | None = None

    def shape(self) -> str:
        return canonical({"kind": "clock", "label": self.label})

    def describe(self) -> str:
        return "clock()" if self.label is None else f"clock({self.label})"


class RandomEffect(BaseEffect):
    """A draw from a random source, including `uuid4`."""

    effect_kind: Literal["random"] = "random"
    method: str = "uuid4"
    args: list[Any] = Field(default_factory=list)

    def shape(self) -> str:
        return canonical({"kind": "random", "method": self.method, "args": self.args})

    def describe(self) -> str:
        return f"random({self.method})"


Effect = Annotated[
    Union[ModelEffect, ToolEffect, ClockEffect, RandomEffect],
    Field(discriminator="effect_kind"),
]


class ErrorInfo(BaseModel):
    """A tool that raised in-process. A normal outcome, recorded as one.

    This is not a crash: the exception happened, the agent saw it, and a replay
    must reproduce it. Only a *missing* completion means the process died.
    """

    type: str
    message: str


class Result(BaseModel):
    """What an effect produced.

    `value` may legitimately be `None`, which is why nothing anywhere tests the
    payload to decide whether an effect ran. A payload is data, never a control
    signal.
    """

    value: Any = None
    error: ErrorInfo | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None
