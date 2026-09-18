"""Nothing in the scenario's tools steers the model toward the failure.

Tool names, signatures and descriptions are part of the prompt. A tool that took
the currency as an argument and recorded whatever it was given made the demo's
mistake unavoidable by construction. These tests hold, structurally, that no tool
can carry the poison except the one whose stated purpose is recording what the
agent concluded - and that no text the model reads hints at what to conclude.
"""

from __future__ import annotations

import inspect

import pytest

from replay.scenario import data, tools

pytestmark = pytest.mark.single_backend

# Every tool's parameters, exactly. Adding one is a decision a reviewer must see.
SIGNATURES = {
    "list_invoices": ["vendor"],
    "get_invoice_header": ["invoice_id"],
    "get_line_items": ["invoice_id"],
    "get_remittance_details": ["invoice_id"],
    "lookup_vendor": ["name"],
    "record_invoice_field": ["field", "value", "basis"],
    "convert_invoice_total": ["invoice_id", "to_currency"],
    "submit_reconciliation": ["invoice_id", "summary"],
}

# Words that, in text the model reads, would tell it what to assume.
STEERING = ["assum", "default", "omit", "missing", "infer", "guess", "usd", "dollar",
            "inr", "rupee", "₹", "$", "if the header", "not specified", "unspecified"]


def parameters(agent_tool) -> list[str]:
    spec = agent_tool.tool_spec["inputSchema"]["json"]
    return list(spec.get("properties", {}))


def model_visible_text(agent_tool) -> str:
    spec = agent_tool.tool_spec
    parts = [spec["name"], spec.get("description", "")]
    for prop in spec["inputSchema"]["json"].get("properties", {}).values():
        parts.append(prop.get("description", ""))
    return " ".join(parts).lower()


def test_every_tool_takes_exactly_the_parameters_it_is_known_to_take():
    assert {t.tool_name: parameters(t) for t in tools.TOOLS} == SIGNATURES


def test_no_tool_parameter_carries_a_source_currency():
    """The only currency any tool accepts is the one to convert INTO."""
    carrying = [(t.tool_name, p) for t in tools.TOOLS for p in parameters(t)
                if "currency" in p.lower() and p != "to_currency"]
    assert carrying == []


def test_only_the_recording_tool_can_write_the_invoice_currency():
    class State:
        def __init__(self):
            self.values, self.writes, self.reads = {"invoice.currency": None}, [], []

        def get(self, key=None):
            self.reads.append(key)
            return self.values.get(key)

        def set(self, key, value):
            self.writes.append(key)
            self.values[key] = value

    written_by = {}
    for agent_tool in tools.TOOLS:
        state = State()
        context = type("Context", (), {"agent": type("Agent", (), {"state": state})()})()
        function = agent_tool._tool_func
        arguments = {name: "INV-2291" if name == "invoice_id" else "currency" if name == "field" else "x"
                     for name in inspect.signature(function).parameters if name != "tool_context"}
        if "tool_context" in inspect.signature(function).parameters:
            arguments["tool_context"] = context
        function(**arguments)
        written_by[agent_tool.tool_name] = "invoice.currency" in state.writes
        if agent_tool.tool_name == "record_invoice_field":
            # The write that records the assumption must depend on nothing the
            # run had in memory, or the trace walks past it to the run's origin.
            assert state.reads == [], "the recording tool read state before writing"

    assert [name for name, wrote in written_by.items() if wrote] == ["record_invoice_field"]


def test_no_text_the_model_reads_suggests_what_to_assume():
    texts = {t.tool_name: model_visible_text(t) for t in tools.TOOLS}
    texts["system prompt"] = data.SYSTEM_PROMPT.lower()
    found = {name: [word for word in STEERING if word in text] for name, text in texts.items()}
    assert {name: words for name, words in found.items() if words} == {}


def test_the_invoice_itself_states_no_currency():
    assert "currency" not in data.HEADERS["INV-2291"]
    for item in data.LINE_ITEMS["INV-2291"]:
        assert set(item) == {"line", "description", "quantity", "amount"}
        assert isinstance(item["amount"], int)
    assert sum(item["amount"] for item in data.LINE_ITEMS["INV-2291"]) == 41000


def test_the_evidence_exists_for_an_agent_that_looks():
    """The model is free to get it right: the payment instructions name an Indian
    bank branch and an IFSC code, without ever stating a currency."""
    remittance = data.REMITTANCE["INV-2291"]
    assert remittance["ifsc"].startswith("HDFC")
    assert "currency" not in remittance


def test_the_conversion_error_names_the_remedy_and_not_the_answer():
    """The error says HOW to record a currency - which tool - and never WHICH one."""
    state = type("State", (), {"get": lambda self, key=None: None, "set": lambda self, k, v: None})()
    context = type("Context", (), {"agent": type("Agent", (), {"state": state})()})()
    error = tools.convert_invoice_total._tool_func(invoice_id="INV-2291", to_currency="USD",
                                                   tool_context=context)["error"].lower()
    assert "record_invoice_field" in error and '"currency"' in error
    remittance = [str(v).lower() for v in data.REMITTANCE["INV-2291"].values()]
    hints = STEERING + ["india", "pune", "bank", "remittance", "vendor", "country", "report"] + remittance
    assert [word for word in hints if word in error.replace("to_currency", "")] == []
