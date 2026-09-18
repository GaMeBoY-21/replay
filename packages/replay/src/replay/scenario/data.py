"""The records behind the scenario's tools.

Nothing here states the invoice's currency. The header has no currency field and
the line items are bare numbers. The only evidence of the currency is in the
remittance details - the paying bank and its branch - which an agent finds only
if it looks. The total, 41000, is a plausible invoice total in rupees and in
dollars alike, so neither reading is absurd on its face.
"""

from __future__ import annotations

TASK = "Reconcile invoice INV-2291 from Meridian Supplies and report the total in USD."

SYSTEM_PROMPT = "You are an accounts-payable assistant. Use the available tools to complete the task."

INVOICES = {"Meridian Supplies": ["INV-2291"]}

HEADERS = {
    "INV-2291": {
        "invoice_id": "INV-2291",
        "vendor": "Meridian Supplies",
        "issued": "2026-08-28",
        "due": "2026-09-27",
        "po_number": "PO-5512",
        "terms": "Net 30",
    },
}

LINE_ITEMS = {
    "INV-2291": [
        {"line": 1, "description": "Steel mounting brackets", "quantity": 250, "amount": 26000},
        {"line": 2, "description": "Anodised rail sections", "quantity": 70, "amount": 12500},
        {"line": 3, "description": "Freight", "quantity": 1, "amount": 2500},
    ],
}

REMITTANCE = {
    "INV-2291": {
        "beneficiary": "Meridian Supplies",
        "bank": "HDFC Bank",
        "branch": "Baner, Pune",
        "ifsc": "HDFC0004172",
        "account": "50200011223344",
        "reference": "INV-2291",
    },
}

# Several pairs, so the table itself does not single out the invoice's currency.
FX_RATES = {
    ("USD", "USD"): 1.0,
    ("INR", "USD"): 0.012,
    ("EUR", "USD"): 1.08,
    ("GBP", "USD"): 1.27,
    ("AUD", "USD"): 0.66,
    ("CAD", "USD"): 0.73,
    ("SGD", "USD"): 0.74,
    ("JPY", "USD"): 0.0068,
}
