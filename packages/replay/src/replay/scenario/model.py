"""A scripted provider for the scenario.

The demo runs from recordings; this is what the canonical runs are recorded
against. It is a `strands.models.Model` like any other, so re-recording against
Bedrock is a provider swap: the tools and the memory shape do not know which
model produced the chunks.

It decides from the conversation it is given, as a model would:

- Run B, and Run A up to its halt, follow the plan in `RUN_B`, one response per
  assistant turn.
- Once a tool result carries a warning - which only a fork that replaced
  `fx_convert` produces - it reads the source currency out of the warning,
  corrects the record, and reconverts.
- Its final answer repeats the last composed report line, so the answer comes
  from state and not from this file.
"""

from __future__ import annotations

import json
import re
from typing import Any

from strands.models import Model

Call = tuple[str, dict[str, Any]]

VENDOR = {"name": "Meridian Supplies"}

# One entry per assistant turn. Each turn is one model step followed by one tool
# step per call, so the step numbers in SCENARIO.md fall out of this list.
RUN_B: list[list[Call]] = [
    [("list_invoices", {"vendor": "Meridian"}),                                  # 1 -> 2
     ("get_invoice_header", {"invoice_id": "INV-2291", "currency": "USD"}),      # 3  the poison
     ("get_line_items", {"invoice_id": "INV-2291"})],                            # 4
    [("format_line_items", {"start": 0, "end": 3}),                              # 5 -> 6
     ("label_amounts", {"start": 0, "end": 3})],                                 # 7
    [("label_amounts", {"start": 3, "end": 6})],                                 # 8 -> 9
    [("sum_line_items", {}),                                                     # 10 -> 11
     ("fx_convert", {"to": "USD"}),                                              # 12 the fork point
     ("record_total", {})],                                                      # 13
    [("get_payment_terms", {"invoice_id": "INV-2291"}),                          # 14 -> 15
     ("vendor_lookup", VENDOR)],                                                 # 16 retry 1
    [("vendor_lookup", VENDOR)],                                                 # 17 -> 18 retry 2
    [("vendor_lookup", VENDOR)],                                                 # 19 -> 20 retry 3
    [("vendor_lookup", VENDOR)],                                                 # 21 -> 22 retry 4
    [("vendor_lookup", VENDOR)],                                                 # 23 -> 24 retry 5
    [("draft_report_section", {"section": "line items"})],                       # 25 -> 26
    [("draft_report_section", {"section": "payment terms"})],                    # 27 -> 28
    [("draft_report_section", {"section": "vendor"})],                           # 29 -> 30
    [("draft_report_section", {"section": "totals"}),                            # 31 -> 32
     ("check_total", {})],                                                       # 33 agrees with itself
    [("format_report", {"style": "summary"})],                                   # 34 -> 35
    [("format_report", {"style": "ledger"})],                                    # 36 -> 37
    [("compose_report", {})],                                                    # 38 -> 39
]                                                                                # 40 the answer


def _corrected(source_currency: str) -> list[list[Call]]:
    return [
        [("set_invoice_currency", {"currency": source_currency}),
         ("fx_convert", {"to": "USD"}),
         ("record_total", {})],
        [("compose_report", {})],
    ]


USAGE = {"metadata": {"usage": {"inputTokens": 120, "outputTokens": 24, "totalTokens": 144},
                      "metrics": {"latencyMs": 40}}}


def _tool_response(turn: int, calls: list[Call]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = [{"messageStart": {"role": "assistant"}}]
    for index, (name, arguments) in enumerate(calls):
        chunks += [
            {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": f"tooluse-{turn}-{index}"}}}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments, sort_keys=True)}}}},
            {"contentBlockStop": {}},
        ]
    chunks += [{"messageStop": {"stopReason": "tool_use"}}, USAGE]
    return chunks


def _text_response(text: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": text}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
        USAGE,
    ]


def _tool_results(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [block["toolResult"] for block in message.get("content", []) if "toolResult" in block]


def _text(result: dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in result.get("content", []))


class ScenarioModel(Model):
    def get_config(self) -> dict[str, Any]:
        return {"provider": "scenario"}

    def update_config(self, **model_config: Any) -> None:
        pass

    def structured_output(self, *args, **kwargs):
        raise NotImplementedError("the scenario provider does not produce structured output")

    def respond(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        turn = sum(1 for message in messages if message["role"] == "assistant")

        warned_at = None
        source = None
        for index, message in enumerate(messages):
            for result in _tool_results(message):
                found = re.search(r"source currency (\w+)", _text(result))
                if found:
                    warned_at, source = index, found.group(1)

        plan = RUN_B
        position = turn
        if warned_at is not None:
            plan = _corrected(source)
            position = sum(1 for message in messages[warned_at:] if message["role"] == "assistant")

        if position < len(plan):
            return _tool_response(turn, plan[position])
        composed = [_text(r) for m in messages for r in _tool_results(m) if _text(r).startswith("Total: ")]
        return _text_response(composed[-1] if composed else "No total could be composed.")

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        for chunk in self.respond(messages):
            yield chunk
