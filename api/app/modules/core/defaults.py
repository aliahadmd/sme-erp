"""Portable ERP defaults shared by server and Workers runtimes."""

DEFAULT_SETTINGS: dict[str, dict] = {
    "company.info": {"legal_name": "", "tax_id": "", "email": "", "phone": ""},
    "numbering.prefixes": {
        "contact": "C",
        "quotation": "QT",
        "sales_order": "SO",
        "purchase_order": "PO",
        "receipt": "RCV",
        "delivery": "DLV",
        "adjustment": "ADJ",
        "ar_invoice": "INV",
        "ap_invoice": "BILL",
        "ar_credit": "CRN",
        "ap_credit": "SCN",
        "customer_payment": "PAY",
        "supplier_payment": "SPAY",
        "journal_entry": "JE",
    },
    "invoicing.defaults": {"payment_terms_days": 30, "default_sale_tax": None},
    "inventory.guard": {"allow_negative": False},
}
