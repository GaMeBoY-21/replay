"""The tool seam.

Unlike the model, a tool can be intercepted with hooks, and this is what they
are for. A tool effect opens at `BeforeToolCallEvent` and closes at
`AfterToolCallEvent`, because the SDK hands over a tool's result only after the
tool has run - which is why the kernel gate is split into two halves.
"""

from __future__ import annotations

from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)
from strands.types.tools import AgentTool

from replay_events import ErrorInfo, Result, ToolEffect

from ..kernel import BreakerTripped, RunContext, begin_effect, complete_effect


class RecordedTool(AgentTool):
    """Stands in for a real tool during replay, and yields what it recorded.

    It yields a bare `ToolResult`. Wrapping it as `{"toolResult": ...}` nests it
    twice once the SDK wraps it again, and the next model call then receives a
    message the recording never contained.
    """

    def __init__(self, name: str, spec: dict[str, Any], recorded: Result) -> None:
        super().__init__()
        self._name = name
        self._spec = spec
        self._recorded = recorded

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> dict[str, Any]:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "replay"

    async def stream(self, tool_use, invocation_state, **kwargs):
        yield self._recorded.value


class ReplayHooks(HookProvider):
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        # toolUseId -> the seq a live tool call holds open until its result
        # arrives. A retry re-fires BeforeToolCallEvent with the same id and
        # opens a new seq, which is correct: it is a second execution. The entry
        # is removed when the first closes, so one seq is never closed twice.
        self._open: dict[str, int] = {}
        self.halted: BreakerTripped | None = None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        # A tool breaker has already cancelled its own call. Cancelling the next
        # model call is what stops the run, rather than handing the model an
        # error and letting it try again.
        if self.halted is not None:
            event.cancel = f"halted by the {self.halted.name} breaker: {self.halted.detail}"

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        effect = ToolEffect(
            name=tool_use["name"],
            arguments=dict(tool_use.get("input") or {}),
            tool_use_id=tool_use.get("toolUseId"),
        )
        try:
            begun = begin_effect(self.ctx, effect)
        except BreakerTripped as trip:
            # The kernel has already appended the trip, and the seq stays claimed.
            self.halted = trip
            event.cancel_tool = f"halted by the {trip.name} breaker: {trip.detail}"
            return

        if begun.live:
            self._open[tool_use["toolUseId"]] = begun.seq
            return

        # Replay: the log answered, so the real tool must not run.
        original = event.selected_tool
        spec = original.tool_spec if original is not None else {"name": effect.name}
        event.selected_tool = RecordedTool(effect.name, spec, begun.result)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        seq = self._open.pop(event.tool_use["toolUseId"], None)
        if seq is not None:
            error = None
            if event.exception is not None:
                error = ErrorInfo(type=type(event.exception).__name__, message=str(event.exception))
            # The result, and only the result. `duration` is display metadata
            # and a replay would never reproduce it.
            complete_effect(self.ctx, seq, Result(value=event.result, error=error))
        self.ctx.step_boundary(label=event.tool_use["name"])
