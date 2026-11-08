"""What makes the ebay.de app different - everything else is paperpull_core.

This file used to be a full copy of the storage logic every other app carried.
That logic now lives in `paperpull_core`; what remains here is eBay's own
facts: the folders it files into, how a document routes to one, its CSV
columns, and its config defaults.

To repair eBay's *page* behavior, edit `ebayde_site.py` instead.
"""
from __future__ import annotations

from pathlib import Path

from paperpull_core import storage as _core
from paperpull_core.spec import (AppSpec, CsvSpec, RECEIPT, Folder,
                                 INFRASTRUCTURE_FOLDERS)

ORDER_HISTORY_COLUMNS = [
    "Account Holder",
    "Purchase Date", "Purchase Type", "Order or Receipt Number", "Order Status",
    "Item Name", "Quantity", "Unit Price", "Line Item Total", "Order Total",
    "Fulfillment Method", "Return Status", "Purchase Summary", "PDF Filename",
    "Purchase Details URL", "Receipt URL", "Processing Status", "Notes",
]

RECEIPT_INDEX_COLUMNS = [
    "Account Holder",
    "Purchase Date", "Purchase Type", "Order or Receipt Number", "Order Total",
    "Purchase Summary", "PDF Filename", "PDF Full Path", "Document Type",
    "Receipt Status", "Receipt Count", "Classification Confidence", "Receipt URL",
    "PDF File Size", "PDF Page Count", "Downloaded At", "Verified At",
    "Processing Status", "Notes",
]

SPEC = AppSpec(
    provider="eBay DE",
    project_dir=Path(__file__).resolve().parent,
    kind=RECEIPT,
    folders=[
        Folder("online", "Online"),
        # eBay's own invoices ("Rechnung ansehen"), beside the receipts.
        Folder("invoices", "Rechnungen"),
        *INFRASTRUCTURE_FOLDERS,
    ],
    routes={
        "Online": "online",
        "invoices": "invoices",
    },
    default_route="online",
    csv_files=[
        CsvSpec("order_history_csv", "eBay DE Order History.csv", ORDER_HISTORY_COLUMNS),
        CsvSpec("receipt_index_csv", "eBay DE Receipt Index.csv", RECEIPT_INDEX_COLUMNS),
    ],
    config_defaults={
        "pilot_online": 5,
        "include_invoices": True,
    },
    base_url="https://www.ebay.de/",
    rules_filename="category_rules.json",
)

_core.bind(SPEC)

PROJECT_DIR = SPEC.project_dir

# The shared API, re-exported so the orchestrator's imports read as they always did.
from paperpull_core.storage import (  # noqa: E402  (must follow bind)
    CsvFile, JsonStore, Paths, atomic_write_json, atomic_write_text,
    backup_file, build_pdf_filename, ensure_owner, load_config, now_iso,
    sanitize_component, set_filename_owner, title_case, unique_path,
)
