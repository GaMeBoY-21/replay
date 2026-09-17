"""The invoice the demo agent reconciles. Built to docs/SCENARIO.md.

The header has no currency field. That is the whole scenario: the invoice is in
INR, nothing says so, and the agent fills the gap. The error is the model's
inference, not a value planted in the data.
"""

from __future__ import annotations

TASK = "Reconcile invoice INV-2291 from Meridian Supplies and report the total in USD."

SYSTEM_PROMPT = (
    "You are an accounts-payable reconciliation agent. Work only through the tools. "
    "Record what you learn about the invoice in agent state so later steps can use it, "
    "and report the reconciled total in USD."
)

INVOICES = {"Meridian": ["INV-2291"]}

# Deliberately no "currency" key. Some vendors' headers omit it.
HEADERS = {
    "INV-2291": {
        "invoice_id": "INV-2291",
        "vendor": "Meridian Supplies",
        "issued": "2026-08-28",
        "due": "2026-09-27",
        "po_number": "PO-5512",
    },
}

# Six items, amounts as bare numbers, summing to exactly 41000 - so the 83x
# error is clean on screen: $41,000.00 reported, $492.00 correct.
LINE_ITEMS = {
    "INV-2291": [
        {"sku": "MS-100", "description": "Steel mounting brackets", "amount": 12500},
        {"sku": "MS-204", "description": "Anodised rail sections", "amount": 9800},
        {"sku": "MS-310", "description": "Fastener assortment", "amount": 7200},
        {"sku": "MS-415", "description": "Cable trays", "amount": 5400},
        {"sku": "MS-520", "description": "Inspection and certification", "amount": 3600},
        {"sku": "MS-990", "description": "Freight", "amount": 2500},
    ],
}

FX_RATES = {("USD", "USD"): 1.0, ("INR", "USD"): 0.012}

PAYMENT_TERMS = {"INV-2291": "Net 30"}
