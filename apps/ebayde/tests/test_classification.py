"""Deterministic local classification tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # binds this provider's AppSpec
from paperpull_core import classification
from paperpull_core.classification import classify_items, load_rules
from paperpull_core.models import Item

RULES = load_rules()


def test_groceries_high_confidence():
    items = [
        Item(name="Good & Gather Whole Milk 1 Gallon", line_total="$3.99"),
        Item(name="Wonder Bread Classic White", line_total="$2.49"),
        Item(name="Large Eggs Grade A 12ct", line_total="$4.29"),
        Item(name="Tillamook Cheddar Cheese", line_total="$5.99"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == "Groceries"
    assert c.confidence == classification.HIGH


def test_single_significant_item():
    c = classify_items([Item(name="Shark Navigator Lift-Away Upright Vacuum")], RULES)
    assert c.summary == "Vacuum Cleaner"
    assert c.confidence == classification.HIGH


def test_primary_product_with_accessories():
    items = [
        Item(name='TCL 55" 4K Smart TV Roku TV', line_total="$299.99"),
        Item(name="HDMI Cable 6ft", line_total="$9.99"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == "Television"
    assert c.confidence == classification.HIGH


def test_childrens_clothing():
    items = [
        Item(name="Kids Boys' Shorts", line_total="$8.00"),
        Item(name="Toddler Girls' Sundress", line_total="$7.00"),
        Item(name="Youth Boys' Socks 6-Pack", line_total="$6.00"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == "Children's Clothing"


def test_majority_category_wins():
    # cleaning items carry most of the dollar value -> Cleaning Supplies
    items = [
        Item(name="Milk 2% Half Gallon", line_total="$3.00"),
        Item(name="Bananas 2lb", line_total="$1.50"),
        Item(name="Clorox Disinfecting Wipes", line_total="$4.00"),
        Item(name="Dawn Dish Soap", line_total="$3.50"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == "Cleaning Supplies"


def test_combined_categories():
    # groceries 45% / cleaning 40% / paper 15% -> combined summary
    items = [
        Item(name="Milk 2% Half Gallon", line_total="$3.00"),
        Item(name="Bananas 2lb", line_total="$1.50"),
        Item(name="Clorox Disinfecting Wipes", line_total="$4.00"),
        Item(name="Bounty Paper Towels 2pk", line_total="$1.50"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == "Groceries and Household"


def test_mixed_low_confidence():
    items = [
        Item(name="zzqx unknowable widget"),
        Item(name="mystery gadget deluxe"),
    ]
    c = classify_items(items, RULES)
    assert c.summary == classification.MIXED
    assert c.confidence == classification.LOW


def test_empty_items():
    c = classify_items([], RULES)
    assert c.summary == classification.MIXED
    assert c.confidence == classification.LOW


def test_weighting_by_price():
    # cheap grocery + expensive electronics -> electronics dominates by $
    items = [
        Item(name="Candy Bar", line_total="$1.50"),
        Item(name="Sony Wireless Headphones", line_total="$199.99"),
    ]
    c = classify_items(items, RULES)
    assert c.summary in ("Headphones", "Electronics")


def test_short_keyword_word_boundary():
    # "pen" must not match inside "opened"; name contains no category keyword
    c = classify_items([Item(name="Opened-box mystery unit")], RULES)
    assert c.summary == classification.MIXED


# ---------------------------------------------------------------------------
# The eBay site layer, against the shapes the live site showed 2026-09-21.
# Every fixture here is made up.
# ---------------------------------------------------------------------------
import ebayde_site as site
from paperpull_core.models import ONLINE, Purchase



# -- the ebay.de site layer, the parts the US copy tested --------------------

import ebayde_site as site  # noqa: E402
from paperpull_core.models import ONLINE  # noqa: E402


class _Page:
    url = "https://order.ebay.de/ord/show?orderId=1"

    def __init__(self, body):
        self._body = body

    def title(self):
        return "Einzelheiten zum Kauf | eBay"

    def locator(self, sel):
        body = self._body

        class L:
            def inner_text(self, timeout=0):
                return body

            def count(self):
                return 0
        return L()


def test_an_older_orders_id_is_an_item_and_transaction_pair():
    kind, oid, poid = site.parse_order_link(
        "https://order.ebay.de/ord/show?orderId=123456789012-1234567890123!1234&purchaseOrderId=12345")
    assert (kind, oid, poid) == (ONLINE, "123456789012-1234567890123!1234", "12345")
    assert site.parse_order_link("https://order.ebay.de/ord/show?orderId=../etc") == (None, None, None)
    assert site.parse_order_link("https://order.ebay.de/ord/show?orderId=") == (None, None, None)


def test_ebays_own_order_not_found_page_is_recognized():
    assert site.order_not_found(_Page("Bestellung nicht gefunden\nLeider ist ein Fehler aufgetreten."))
    assert not site.order_not_found(_Page("Bestellt am\t3. Nov 2026\nBestellnummer\t1"))
    assert not site.order_not_found(_Page(""))


def test_ebays_daily_limit_page_is_recognized_by_url():
    class _Limited(_Page):
        url = "https://pages.ebay.de/limitexceeded.html"
    assert site.hit_daily_limit(_Limited(""))
    assert not site.hit_daily_limit(_Page("Bestellt am\t3. Nov 2026\nBestellnummer\t1"))


def test_german_titles_are_classified():
    cls = classify_items([Item(name="Erfundene Bluetooth Kopfhörer kabellos", line_total="EUR 49,99")], RULES)
    assert cls.summary == "Electronics"


def test_the_scroll_waits_for_the_page_to_stop_growing():
    import inspect
    src = inspect.getsource(site.scroll_all_orders)
    assert "scrollHeight" in src and "_press_show_more(page)" in src
    assert site.scroll_all_orders.__defaults__[0] >= 20
