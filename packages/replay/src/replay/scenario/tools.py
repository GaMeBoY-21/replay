"""The demo agent's tools, built to SCENARIO.md's memory-key table exactly.

    Key                   Written at    Read at
    invoice.currency      3             4, 7, 9, 12, 33
    invoice.line_items    4             5-10
    invoice.subtotal      11            12, 13
    fx.rate               12            13
    report.total          13            33, 39

Every edge the trace walks exists because of this table, so the reads and writes
below are the table, and nothing else touches state. In Strands only a tool can
read or write `agent.state`, so every row the table gives a memory access is a
tool step, including the ones SCENARIO.md's run table labels `model`.

Tools are invoked only by the model. The SDK's direct-call path builds tool-use
ids with `random.randint`, which no recording can reproduce.
"""

from __future__ import annotations

from strands import tool

from . import data


def _state(tool_context):
    return tool_context.agent.state


# ---------------------------------------------------------------- steps 2-4


@tool
def list_invoices(vendor: str) -> list:
    """List the open invoices for a vendor."""
    return list(data.INVOICES.get(vendor, []))


@tool(context=True)
def get_invoice_header(invoice_id: str, currency: str, tool_context) -> dict:
    """Fetch an invoice's header, and record the currency the invoice is denominated in.

    Some vendors omit the currency from the header; in that case the `currency`
    you pass is what gets recorded.
    """
    header = dict(data.HEADERS[invoice_id])
    # Step 3. Reads nothing: the invoice id comes from the model's own context,
    # and so does the currency, because this header has none. The model passes
    # the currency it was asked to REPORT in - the poison.
    _state(tool_context).set("invoice.currency", header.get("currency", currency))
    return header


@tool(context=True)
def get_line_items(invoice_id: str, tool_context) -> list:
    """Fetch an invoice's line items and record them."""
    currency = _state(tool_context).get("invoice.currency")
    items = [dict(item, unit=currency) for item in data.LINE_ITEMS[invoice_id]]
    _state(tool_context).set("invoice.line_items", items)
    return [dict(item) for item in data.LINE_ITEMS[invoice_id]]


# ---------------------------------------------------------------- steps 5-10


@tool(context=True)
def format_line_items(start: int, end: int, tool_context) -> list:
    """Format a range of the recorded line items for the report."""
    items = _state(tool_context).get("invoice.line_items")
    return [f"{item['sku']} {item['description']}: {item['amount']}" for item in items[start:end]]


@tool(context=True)
def label_amounts(start: int, end: int, tool_context) -> list:
    """Label a range of the recorded line items with the invoice currency."""
    items = _state(tool_context).get("invoice.line_items")
    currency = _state(tool_context).get("invoice.currency")
    symbol = "$" if currency == "USD" else f"{currency} "
    return [f"{item['description']}: {symbol}{item['amount']:,}" for item in items[start:end]]


# ---------------------------------------------------------------- steps 11-13


@tool(context=True)
def sum_line_items(tool_context) -> dict:
    """Sum the recorded line items and record the subtotal."""
    items = _state(tool_context).get("invoice.line_items")
    subtotal = sum(item["amount"] for item in items)
    _state(tool_context).set("invoice.subtotal", subtotal)
    return {"subtotal": subtotal}


@tool(context=True)
def fx_convert(to: str, tool_context) -> dict:
    """Convert the recorded subtotal from the invoice currency, and record the rate used."""
    source = _state(tool_context).get("invoice.currency")
    subtotal = _state(tool_context).get("invoice.subtotal")
    rate = data.FX_RATES[(source, to)]
    _state(tool_context).set("fx.rate", rate)
    return {"rate": rate, "converted": round(subtotal * rate, 2)}


@tool(context=True)
def record_total(tool_context) -> dict:
    """Record the converted total, from the recorded rate and subtotal."""
    rate = _state(tool_context).get("fx.rate")
    subtotal = _state(tool_context).get("invoice.subtotal")
    if rate is None:
        # In a fork that replaced fx_convert's result, the real tool never ran,
        # so no rate was recorded. Say so rather than invent one.
        return {"error": "no exchange rate has been recorded"}
    total = round(subtotal * rate, 2)
    _state(tool_context).set("report.total", total)
    return {"total": total}


@tool(context=True)
def set_invoice_currency(currency: str, tool_context) -> dict:
    """Correct the recorded invoice currency."""
    _state(tool_context).set("invoice.currency", currency)
    return {"currency": currency}


# ---------------------------------------------------------------- steps 15-24


@tool
def get_payment_terms(invoice_id: str) -> str:
    """Look up an invoice's payment terms."""
    return data.PAYMENT_TERMS[invoice_id]


@tool
def vendor_lookup(name: str) -> dict:
    """Look up a vendor's master record."""
    # Fails identically every time. The loop breaker keys on the call's shape,
    # so the agent's retries must be byte-identical to be caught.
    return {"error": f"404: no vendor record for {name!r}"}


# ---------------------------------------------------------------- steps 25-39


@tool
def draft_report_section(section: str) -> str:
    """Draft one section of the reconciliation report."""
    return f"{section} section drafted"


@tool(context=True)
def check_total(tool_context) -> dict:
    """Sanity-check the recorded total against the invoice currency."""
    # Step 33: the agent checking its own work, and passing, because the check
    # reads the same poisoned key the error came from.
    currency = _state(tool_context).get("invoice.currency")
    total = _state(tool_context).get("report.total")
    return {"plausible": True, "checked": f"{total:,.2f} {currency}"}


@tool
def format_report(style: str) -> str:
    """Apply a formatting style to the report."""
    return f"report formatted as {style}"


@tool(context=True)
def compose_report(tool_context) -> str:
    """Compose the final report line from the recorded total."""
    total = _state(tool_context).get("report.total")
    return f"Total: ${total:,.2f}"


TOOLS = [
    list_invoices,
    get_invoice_header,
    get_line_items,
    format_line_items,
    label_amounts,
    sum_line_items,
    fx_convert,
    record_total,
    set_invoice_currency,
    get_payment_terms,
    vendor_lookup,
    draft_report_section,
    check_total,
    format_report,
    compose_report,
]
