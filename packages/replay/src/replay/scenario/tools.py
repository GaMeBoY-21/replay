"""The scenario agent's tools.

Tool names, signatures and docstrings are part of the model's prompt. Each is
written as it would be for a real accounts-payable integration, and none of them
says anything about which currency an invoice is in or what to do when a record
does not say. `tests/test_scenario_tools.py` holds that structurally.

What reaches `agent.state`, and so what the trace can walk:

    invoice.<field>         record_invoice_field    - what the agent concluded, and nothing it read
    invoice.<field>.basis   record_invoice_field    - why, in the agent's words
    fx.rate, report.total   convert_invoice_total   - reads invoice.currency
    report.submitted        submit_reconciliation   - reads report.total

The currency the conversion uses is whatever the agent recorded. No tool records
one on the agent's behalf.
"""

from __future__ import annotations

from strands import tool

from . import data


def _state(tool_context):
    return tool_context.agent.state


@tool
def list_invoices(vendor: str) -> dict:
    """List the open invoice IDs for a vendor."""
    return {"vendor": vendor, "invoices": list(data.INVOICES.get(vendor, []))}


@tool
def get_invoice_header(invoice_id: str) -> dict:
    """Fetch the header of an invoice."""
    if invoice_id not in data.HEADERS:
        return {"error": f"invoice {invoice_id} not found"}
    return dict(data.HEADERS[invoice_id])


@tool
def get_line_items(invoice_id: str) -> dict:
    """Fetch the line items of an invoice."""
    if invoice_id not in data.LINE_ITEMS:
        return {"error": f"invoice {invoice_id} not found"}
    return {"invoice_id": invoice_id, "line_items": [dict(item) for item in data.LINE_ITEMS[invoice_id]]}


@tool
def get_remittance_details(invoice_id: str) -> dict:
    """Fetch the payment instructions attached to an invoice."""
    if invoice_id not in data.REMITTANCE:
        return {"error": f"invoice {invoice_id} not found"}
    return dict(data.REMITTANCE[invoice_id])


@tool
def lookup_vendor(name: str) -> dict:
    """Look up a vendor's master record."""
    return {"error": f"vendor master record not found: {name}"}


@tool(context=True)
def record_invoice_field(field: str, value: str, basis: str, tool_context) -> dict:
    """Record a field on the reconciliation worksheet, with the basis for its value."""
    name = field.strip().lower().removeprefix("invoice.")
    _state(tool_context).set(f"invoice.{name}", value.strip())
    _state(tool_context).set(f"invoice.{name}.basis", basis)
    return {"recorded": f"invoice.{name}", "value": value.strip()}


@tool(context=True)
def convert_invoice_total(invoice_id: str, to_currency: str, tool_context) -> dict:
    """Convert an invoice's total into another currency, using the invoice currency on the worksheet."""
    if invoice_id not in data.LINE_ITEMS:
        return {"error": f"invoice {invoice_id} not found"}
    source = _state(tool_context).get("invoice.currency")
    if source is None:
        return {"error": "invoice.currency is not recorded on the worksheet; "
                         "record it with record_invoice_field, field \"currency\""}
    source, target = str(source).strip().upper(), to_currency.strip().upper()
    rate = data.FX_RATES.get((source, target))
    if rate is None:
        return {"error": f"no exchange rate from {source} to {target}"}
    total = sum(item["amount"] for item in data.LINE_ITEMS[invoice_id])
    converted = round(total * rate, 2)
    _state(tool_context).set("fx.rate", rate)
    _state(tool_context).set("report.total", {"amount": converted, "currency": target})
    return {"invoice_id": invoice_id, "from": source, "to": target, "rate": rate,
            "total": total, "converted": converted}


@tool(context=True)
def submit_reconciliation(invoice_id: str, summary: str, tool_context) -> dict:
    """Submit the completed reconciliation for an invoice."""
    total = _state(tool_context).get("report.total")
    if total is None:
        return {"error": "no converted total is recorded on the worksheet"}
    _state(tool_context).set("report.submitted", {"invoice_id": invoice_id, "total": total, "summary": summary})
    return {"submitted": True, "invoice_id": invoice_id, "total": total}


TOOLS = [
    list_invoices,
    get_invoice_header,
    get_line_items,
    get_remittance_details,
    lookup_vendor,
    record_invoice_field,
    convert_invoice_total,
    submit_reconciliation,
]
