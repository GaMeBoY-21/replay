"""What is true about the installed Strands SDK, checked rather than remembered.

    uv run python scripts/verify_sdk.py

`docs/SPIKE.md` records what was true on 2026-09-07. Strands ships often, and
every seam in `replay.agent` rests on a specific fact about it: which methods are
abstract, which hook fields are writable, what the default tool executor is.
This script re-establishes each of those facts against the installed version,
prints one line per check, and exits non-zero if any has moved.

It reads nothing from the network. No model is constructed that would call one.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import inspect
import pathlib
import re
import sys
import traceback
from typing import Any, Callable

RESULTS: list[tuple[int, str, bool, str]] = []


def check(number: int, title: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Run a check now, and record PASS with its detail or FAIL with the reason."""

    def run(body: Callable[[], str]) -> Callable[[], str]:
        try:
            detail = body()
            RESULTS.append((number, title, True, detail))
        except AssertionError as exc:
            RESULTS.append((number, title, False, str(exc) or "assertion failed"))
        except Exception as exc:  # noqa: BLE001 - a check that crashes has failed
            last = traceback.format_exception_only(type(exc), exc)[-1].strip()
            RESULTS.append((number, title, False, last))
        return body

    return run


def _try_write(instance: Any, field: str, value: Any) -> bool:
    try:
        setattr(instance, field, value)
        return True
    except AttributeError:
        return False


def _instance(cls: type) -> Any:
    """A real hook event, with placeholder values for every field.

    Writability is read by attempting the write on a constructed instance, which
    is the behaviour a hook actually meets, rather than from a private helper.
    """
    kwargs = {f.name: None for f in dataclasses.fields(cls) if f.init}
    return cls(**kwargs)


class _StubModel:
    """Filled in once `strands.models.Model` is known to import."""


# ------------------------------------------------------------------ 1 - 6: the model seam


@check(1, "strands imports and reports its version")
def _() -> str:
    import strands

    dist = importlib.metadata.version("strands-agents")
    attr = getattr(strands, "__version__", None)
    if attr is None:
        return f"strands-agents {dist} (strands.__version__ is not defined; version read from package metadata)"
    assert attr == dist, f"strands.__version__ {attr} disagrees with package metadata {dist}"
    return f"strands-agents {dist}"


@check(2, "strands.models.Model exists")
def _() -> str:
    from strands.models import Model

    assert inspect.isclass(Model)
    return f"{Model.__module__}.{Model.__qualname__}"


@check(3, "Model has exactly four abstract methods")
def _() -> str:
    from strands.models import Model

    expected = {"get_config", "stream", "update_config", "structured_output"}
    found = set(Model.__abstractmethods__)
    assert found == expected, (
        f"abstract methods are {sorted(found)}; expected {sorted(expected)}. "
        "A new one is a new un-gated path into the model."
    )
    return ", ".join(sorted(found))


@check(4, "Model.stream accepts every input the seam depends on")
def _() -> str:
    from strands.models import Model

    params = inspect.signature(Model.stream).parameters
    required = [
        "messages",
        "tool_specs",
        "system_prompt",
        "tool_choice",
        "system_prompt_content",
        "invocation_state",
        "cancel_signal",
    ]
    missing = [name for name in required if name not in params]
    assert not missing, f"Model.stream no longer accepts {missing}"
    extra = [
        name
        for name, p in params.items()
        if name not in required and name != "self" and p.kind is not inspect.Parameter.VAR_KEYWORD
    ]
    note = f"; also accepts {extra}" if extra else ""
    return f"all seven present{note}"


@check(5, "model streaming is an async generator")
def _() -> str:
    # The abstract method is a plain `def` annotated to return AsyncIterable, so
    # `isasyncgenfunction(Model.stream)` is False, and was on 1.54.0 and 1.55.1
    # too. What the seam depends on is that a provider streams with an async
    # generator and the SDK consumes it with `async for`, so that is what is
    # asserted. The literal result is printed so the difference stays visible.
    import collections.abc

    from strands.models import Model
    from strands.models.bedrock import BedrockModel

    literal = inspect.isasyncgenfunction(Model.stream)
    returns = inspect.signature(Model.stream).return_annotation
    origin = getattr(returns, "__origin__", returns)
    assert origin is collections.abc.AsyncIterable, f"Model.stream is annotated to return {returns}"
    assert inspect.isasyncgenfunction(BedrockModel.stream), "BedrockModel.stream is not an async generator"
    return (
        f"BedrockModel.stream is an async generator; Model.stream is declared -> AsyncIterable "
        f"(isasyncgenfunction(Model.stream) = {literal})"
    )


@check(6, "BedrockModel exists and is a Model")
def _() -> str:
    from strands.models import Model
    from strands.models.bedrock import BedrockModel

    assert issubclass(BedrockModel, Model)
    return f"{BedrockModel.__module__}.{BedrockModel.__qualname__}"


# ------------------------------------------------------------------ 7 - 14: the hook seam


@check(7, "HookProvider and HookRegistry import")
def _() -> str:
    from strands.hooks import HookProvider, HookRegistry

    assert hasattr(HookRegistry, "add_callback")
    return f"{HookProvider.__qualname__}, {HookRegistry.__qualname__}.add_callback"


@check(8, "BeforeToolCallEvent.selected_tool is writable")
def _() -> str:
    from strands.hooks import BeforeToolCallEvent

    assert _try_write(_instance(BeforeToolCallEvent), "selected_tool", object()), (
        "selected_tool is not writable - replay cannot swap in a stub tool"
    )
    return "writable"


@check(9, "BeforeToolCallEvent.cancel_tool is writable")
def _() -> str:
    from strands.hooks import BeforeToolCallEvent

    assert _try_write(_instance(BeforeToolCallEvent), "cancel_tool", "halted"), "cancel_tool is not writable"
    return "writable"


@check(10, "AfterToolCallEvent.result is writable")
def _() -> str:
    from strands.hooks import AfterToolCallEvent

    assert _try_write(_instance(AfterToolCallEvent), "result", {}), "result is not writable"
    return "writable"


@check(11, "AfterToolCallEvent exposes duration")
def _() -> str:
    from strands.hooks import AfterToolCallEvent

    names = {f.name for f in dataclasses.fields(AfterToolCallEvent)}
    assert "duration" in names, "duration is gone"
    return "present - display metadata, never part of the replayable record"


@check(12, "BeforeModelCallEvent.cancel is writable")
def _() -> str:
    from strands.hooks import BeforeModelCallEvent

    assert _try_write(_instance(BeforeModelCallEvent), "cancel", "halted"), "cancel is not writable"
    return "writable"


@check(13, "AfterModelCallEvent exposes no writable response field")
def _() -> str:
    from strands.hooks import AfterModelCallEvent

    event = _instance(AfterModelCallEvent)
    writable = [
        f.name
        for f in dataclasses.fields(AfterModelCallEvent)
        if f.name != "agent" and _try_write(event, f.name, getattr(event, f.name))
    ]
    response_like = [name for name in writable if name not in ("retry",)]
    assert not response_like, (
        f"AfterModelCallEvent now lets a hook write {response_like}. The model seam "
        "could be a hook, and ReplayModel may no longer be necessary - re-read ARCHITECTURE.md §8."
    )
    return f"only {writable} is writable - a hook cannot substitute a response, so the seam is a Model"


@check(14, "MessageAddedEvent exists")
def _() -> str:
    from strands.hooks import MessageAddedEvent

    names = [f.name for f in dataclasses.fields(MessageAddedEvent)]
    return f"fields {names}"


# ------------------------------------------------------------------ 15 - 16: the agent


def _stub_agent(**kwargs: Any):
    """An Agent with a model that is never called, so constructing it touches no
    provider SDK and no network."""
    from strands import Agent
    from strands.models import Model

    class Unused(Model):
        def get_config(self):
            return {}

        def update_config(self, **config):
            pass

        async def stream(self, *args, **kwargs):
            raise AssertionError("verify_sdk.py must never call a model")
            yield

        async def structured_output(self, *args, **kwargs):
            raise AssertionError("verify_sdk.py must never call a model")
            yield

    return Agent(model=Unused(), callback_handler=None, **kwargs)


@check(15, "the default executor is concurrent, and a sequential one can replace it")
def _() -> str:
    from strands import Agent
    from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor

    assert "tool_executor" in inspect.signature(Agent.__init__).parameters, "Agent() no longer accepts tool_executor="
    default = _stub_agent().tool_executor
    assert isinstance(default, ConcurrentToolExecutor), f"the default executor is now {type(default).__name__}"
    replaced = _stub_agent(tool_executor=SequentialToolExecutor()).tool_executor
    assert isinstance(replaced, SequentialToolExecutor)
    return (
        "default ConcurrentToolExecutor; strands.tools.executors.SequentialToolExecutor "
        "accepted via Agent(tool_executor=)"
    )


@check(16, "agent.state is assignable, and AgentState.get's signature")
def _() -> str:
    from strands.agent.state import AgentState

    for method in ("get", "set", "delete"):
        assert callable(getattr(AgentState, method, None)), f"AgentState.{method} is gone"
    signature = inspect.signature(AgentState.get)
    agent = _stub_agent()

    class Wrapper:
        def __init__(self, inner):
            self.inner = inner

    agent.state = Wrapper(agent.state)
    assert isinstance(agent.state, Wrapper), "assigning agent.state did not stick"
    takes_default = "default" in signature.parameters
    return (
        f"assignable; AgentState is {AgentState.__module__}.{AgentState.__qualname__}; "
        f"get{signature} - {'takes' if takes_default else 'takes NO'} default"
    )


# ------------------------------------------------------------------ 17: silent non-determinism

INVOCATION_PATH = [
    "agent",
    "event_loop",
    "tools/executors",
    "tools/_caller.py",
    "hooks",
    "types/content.py",
    "models/model.py",
]
SUSPECT = re.compile(r"\buuid4\b|\btime\.|\bdatetime\.now\b|\brandom\b")
HITS: list[str] = []


@check(17, "sources of non-determinism on the invocation path")
def _() -> str:
    import strands

    root = pathlib.Path(strands.__file__).parent
    scanned = 0
    for entry in INVOCATION_PATH:
        target = root / entry
        files = [target] if target.is_file() else sorted(target.rglob("*.py")) if target.is_dir() else []
        for path in files:
            scanned += 1
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if SUSPECT.search(line):
                    HITS.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert scanned, "no invocation-path files found - the SDK layout moved"
    return f"{len(HITS)} hits in {scanned} files - every one listed below"


# ------------------------------------------------------------------ report


def main() -> int:
    width = max(len(title) for _, title, _, _ in RESULTS)
    for number, title, passed, detail in RESULTS:
        print(f"{'PASS' if passed else 'FAIL'}  {number:2d}  {title:<{width}}  {detail}")

    print("\ncheck 17 - every hit, as file:line")
    for hit in HITS:
        print(f"  {hit}")

    failed = [number for number, _, passed, _ in RESULTS if not passed]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks pass on strands-agents "
          f"{importlib.metadata.version('strands-agents')}")
    if len(RESULTS) != 17:
        print(f"expected 17 checks, ran {len(RESULTS)}")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
