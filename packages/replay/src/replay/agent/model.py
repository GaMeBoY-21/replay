"""The model seam.

A hook cannot substitute a model response: `AfterModelCallEvent` lets a hook set
`retry` and nothing else, which `scripts/verify_sdk.py` re-checks against every
installed version. So the model call is intercepted where it is made, by a
`strands.models.Model` that wraps the real provider and routes its one
non-deterministic operation through the kernel gate.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from strands.models import Model

from replay_events import ModelEffect, Result

from ..kernel import ReplayError, RunContext, perform_async


class UnrecordedModelInput(ReplayError):
    """A model input that is not part of the fingerprint arrived non-empty.

    Replaying such a run would serve a recorded response to a request that may
    have been different, and the divergence detector cannot see it, because the
    input never reaches the shape it compares. Refusing is the only honest
    option until the input is recorded - loud and unsupported beats silent and
    wrong.
    """


def unrecorded_inputs(kwargs: dict[str, Any]) -> list[str]:
    """Which unfingerprinted inputs carry something. Both were empty on every
    run observed against strands-agents 1.56.0."""
    found = []
    if kwargs.get("model_state"):
        found.append("model_state")
    metadata = kwargs.get("agent_metadata")
    if metadata is not None:
        values = (dataclasses.asdict(metadata) if dataclasses.is_dataclass(metadata)
                  else dict(vars(metadata)))
        if any(value is not None for value in values.values()):
            found.append("agent_metadata")
    return found


def model_effect(
    messages: list[dict[str, Any]],
    tool_specs: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    tool_choice: dict[str, Any] | None = None,
    system_prompt_content: list[dict[str, Any]] | None = None,
) -> ModelEffect:
    """The effect a model call is recorded and compared as.

    Five inputs, all of which change what the model is asked. Leaving one out
    lets two different calls share a shape, and the divergence detector then
    serves a recorded response to a request it was never recorded for.
    """
    return ModelEffect(
        messages=list(messages),
        tool_specs=tool_specs,
        system_prompt=system_prompt,
        tool_choice=tool_choice,
        system_prompt_content=system_prompt_content,
    )


class ReplayModel(Model):
    """Wraps a real provider. Live it calls through; replaying it calls nothing."""

    def __init__(self, ctx: RunContext, inner: Model) -> None:
        self.ctx = ctx
        self.inner = inner

    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        *,
        tool_choice=None,
        system_prompt_content=None,
        **kwargs: Any,
    ):
        unrecorded = unrecorded_inputs(kwargs)
        if unrecorded:
            raise UnrecordedModelInput(
                f"{', '.join(unrecorded)} arrived non-empty. It is an unrecorded model input: it is "
                "not part of the fingerprint, so a replay of this run could serve a response recorded "
                "for a different request with no divergence raised."
            )
        effect = model_effect(messages, tool_specs, system_prompt, tool_choice, system_prompt_content)

        async def drain() -> Result:
            # `kwargs` carries invocation_state, cancel_signal, model_state and
            # agent_metadata. They reach the provider on a live call and are
            # never recorded: the SDK writes a fresh uuid4 into invocation_state
            # every cycle, and cancel_signal is a threading.Event.
            chunks = [
                chunk
                async for chunk in self.inner.stream(
                    messages,
                    tool_specs,
                    system_prompt,
                    tool_choice=tool_choice,
                    system_prompt_content=system_prompt_content,
                    **kwargs,
                )
            ]
            return Result(value=chunks)

        # One model call is one effect and one seq, whatever the chunk count.
        # Recording chunks individually would make sequence numbers depend on
        # tokenisation, and no two providers tokenise alike.
        result = await perform_async(self.ctx, effect, drain)
        self.ctx.step_boundary(label="model")
        for chunk in result.value:
            yield chunk

    def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        # The fourth abstract method, and a second path into the model that
        # bypasses `stream`. Inherited, it would call the provider un-gated and
        # replay would silently re-run it. It stays closed until it is gated.
        raise NotImplementedError(
            "structured_output is not recorded by Replay; use tool calls through stream instead"
        )

    def get_config(self) -> Any:
        return self.inner.get_config()

    def update_config(self, **model_config: Any) -> None:
        self.inner.update_config(**model_config)
