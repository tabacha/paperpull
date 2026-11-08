"""A saved receipt names its order, and eBay's name alone is not that.

eBay's receipt is its order-details page, opened at an address carrying
the order's number, and printed. The check it then passes used to pass
any page that said eBay. Here the app's own _finish_pdf is handed a
receipt, which it keeps, and an eBay page naming no order, which it puts
aside. Measured on real saved receipts before this was enabled, every
one printed its order number. Every number, item and amount is invented.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import ebayde_receipts as app_mod
from paperpull_core.models import ONLINE, Item, Purchase
from paperpull_core.testkit import file_a_receipt, receipt_app


def order():
    return Purchase(purchase_type=ONLINE, order_number="12-34567-89012",
                    purchase_date="2026-06-03", total="EUR 18,45",
                    items=[Item(name="Erfundener Kameragurt Vintage")])


RECEIPT = ["Einzelheiten zum Kauf", "Bestellinformationen", "Bestellt am 3. Jun 2026, 14:30",
           "Bestellnummer 12-34567-89012", "Verkauft von beispiel_verkaeufer",
           "Erfundener Kameragurt Vintage", "Artikelnummer: 100000000001",
           "Zahlungsinformationen", "Gesamtbetrag EUR 18,45"]

EBAY_PAGE = ["eBay", "Hallo! Einloggen oder neu anmelden", "Mein eBay  Käufe",
             "Käufe", "Copyright 1995-2026 eBay Inc. Alle Rechte vorbehalten."]


def test_a_receipt_is_kept(tmp_path):
    filed = file_a_receipt(receipt_app(app_mod, tmp_path), order(), RECEIPT)
    assert filed.kept and filed.record["downloaded_ok"] is True


def test_a_page_naming_only_ebay_is_put_aside(tmp_path):
    filed = file_a_receipt(receipt_app(app_mod, tmp_path), order(), EBAY_PAGE)
    assert not filed.kept and not filed.record.get("downloaded_ok")
    assert filed.path.parent.name == "Manual Review" and filed.path.exists()
