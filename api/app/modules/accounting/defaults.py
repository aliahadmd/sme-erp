"""Portable ERP defaults shared by server and Workers runtimes."""

DEFAULT_COA: list[tuple[str, str, str]] = [
    # (code, name, type)
    ("1000", "Cash", "asset"),
    ("1010", "Bank", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Inventory", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2050", "Goods Received Not Invoiced", "liability"),
    ("2100", "Tax Payable", "liability"),
    ("3000", "Owner's Equity", "equity"),
    ("4000", "Sales Revenue", "income"),
    ("4100", "Sales Returns & Refunds", "expense"),
    ("4900", "Realized FX Gain", "income"),
    ("5000", "Cost of Goods Sold", "expense"),
    ("5100", "Purchases Expense", "expense"),
    ("5200", "Stock Correction", "expense"),
    ("5900", "Rounding", "expense"),
    ("6900", "Realized FX Loss", "expense"),
]


DEFAULT_MAPPING: dict[str, str] = {
    "ar": "1100",
    "ap": "2000",
    "sales_revenue": "4000",
    "tax_payable": "2100",
    "inventory": "1200",
    "cogs": "5000",
    "refunds": "4100",
    "purchases": "5100",
    "stock_correction": "5200",
    "grni": "2050",
    "fx_gain": "4900",
    "fx_loss": "6900",
    "cash": "1000",
    "bank": "1010",
}
