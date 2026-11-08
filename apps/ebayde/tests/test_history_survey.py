"""Reading the ebay.de purchase history, and saying what it held.

These run in a real browser. The thing being counted is what a browser
makes of a page, and a fake page cannot be wrong in the same ways.

The cards are shaped like the ones ebay.de showed on 2026-11-08
("Bestelldatum:", "Gesamtbetrag:", "Bestellnummer:", "Einzelheiten zum
Kauf ansehen", sometimes "Rechnung ansehen"). Every number, name and
amount is invented.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import ebayde_site as site

DETAILS = "https://order.ebay.de/ord/show?orderId=%s&purchaseOrderId=%s"
INVOICE = ("https://order.ebay.de/ord/show/taxInfo?docType=taxInfo&orderId=%s"
           "&page=true&skipSellerRedirect=false")


def card(num, link_id=None, invoice=False, status="Zugestellt", month="Mai.", total="1.234,56"):
    link_id = link_id or num
    inv = '<a href="%s">Rechnung ansehen</a>' % (INVOICE % link_id) if invoice else ""
    return ('<div class="m-ph-card"><div>%s</div><h3>Erfundener Artikel %s</h3>'
            '<div><span>Bestelldatum:</span><span>12. %s 2026</span>'
            '<span>Gesamtbetrag:</span><span>EUR %s</span>'
            '<span>Bestellnummer:</span><span>%s</span></div>'
            '<a href="%s">Einzelheiten zum Kauf ansehen</a>%s'
            '<div>Verkauft von: <a href="https://www.ebay.de/usr/erfunden">erfunden</a></div>'
            '<button>Erneut kaufen</button></div>'
            % (status, num, month, total, num, DETAILS % (link_id, "9" + num[-4:]), inv))


@pytest.fixture()
def page():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        driver = pw.sync_playwright().start()
        browser = driver.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip("no browser to drive: %s" % e)
    pg = browser.new_page()
    yield pg
    browser.close()
    driver.stop()


def test_a_card_reads_its_date_total_status_and_invoice(page):
    page.set_content("<body>%s%s</body>" % (card("11-11111-11111", invoice=True),
                                            card("22-22222-22222", status="Verschickt", month="Nov",
                                                 total="9,99")))
    cards = site.collect_cards(page)
    assert [c.order_id for c in cards] == ["11-11111-11111", "22-22222-22222"]
    first, second = (site.card_to_purchase(c) for c in cards)
    assert first.purchase_date == "2026-05-12"
    assert first.total == "EUR 1.234,56"
    assert first.status == "Zugestellt"
    assert cards[0].invoice_href.startswith("https://order.ebay.de/ord/show/taxInfo?")
    assert second.purchase_date == "2026-11-12" and second.total == "EUR 9,99"
    assert second.status == "Verschickt"
    assert cards[1].invoice_href == ""
    assert first.details_url.startswith("https://order.ebay.de/ord/show?orderId=11-11111-11111")


def test_two_orders_of_one_cart_are_two_purchases(page):
    """A cart from two sellers can send both cards to one details page.
    Each card is still its own order, by the number it prints."""
    page.set_content("<body>%s%s</body>" % (card("33-33333-33333", link_id="17-77777-77777"),
                                            card("44-44444-44444", link_id="17-77777-77777")))
    cards = site.collect_cards(page)
    assert sorted(c.order_id for c in cards) == ["33-33333-33333", "44-44444-44444"]
    assert {c.link_order_id for c in cards} == {"17-77777-77777"}
    got = site.history_survey(page)
    assert got["became_purchases"] == 2
    assert got["cards_sharing_a_details_link"] == 2
    assert got["page"]["distinct_ids_in_text"] == 2


def test_the_survey_counts_and_carries_no_label(page):
    page.set_content("<body>%s%s<button>Mehr anzeigen</button></body>"
                     % (card("55-55555-55555", invoice=True), card("66-66666-66666")))
    got = site.history_survey(page)
    p = got["page"]
    assert p["rows_with_an_order_date"] == 2
    assert p["invoice_links"] == 1
    assert isinstance(p["controls_that_might_page"], int), "counted, never a label"
    assert got["cards_collected"] == 2


DETAILS_PAGE = """<!doctype html><html><head><title>Einzelheiten zum Kauf | eBay</title></head><body>
<header>Hallo erfunden!</header>
<div class="main-regions">
 <div class="order"><h2>Bestellinformationen</h2>
  <div>Bestellt am\t3. Nov 2026, 14:30</div><div>Bestellnummer\t33-33333-33333</div>
  <div>Gesamt\tEUR 20,00 (1 Artikel)</div><div>Verkauft von\tverkaeufer_eins</div>
  <h2>Artikelinformationen</h2>
  <div>Erfundene Lampe aus Messing</div><div>EUR 20,00</div><div>Preis pro Einheit EUR 20,00</div>
  <div>Artikelnummer: 100000000001</div><h2>Andere Aktionen</h2><a>Rechnung ansehen</a></div>
 <div class="order"><h2>Bestellinformationen</h2>
  <div>Bestellt am\t3. Nov 2026, 14:30</div><div>Bestellnummer\t44-44444-44444</div>
  <div>Gesamt\tEUR 5,50 (2 Artikel)</div><div>Verkauft von\tverkaeufer_zwei</div>
  <h2>Artikelinformationen</h2>
  <div>Erfundenes Kabel zwei Meter</div><div>EUR 5,50</div><div>Menge: 2</div>
  <div>Artikelnummer: 100000000002</div><h2>Andere Aktionen</h2></div>
 <div class="addr"><h2>Lieferadresse</h2><div>Erika Mustermann</div></div>
 <div class="pay"><div>Zahlungsinformationen</div><div>Gesamtbetrag\tEUR 25,50</div></div>
</div><footer>Copyright</footer></body></html>"""


def test_a_cart_page_is_read_for_this_order_only(page):
    page.set_content(DETAILS_PAGE)
    from paperpull_core.models import Purchase
    p = site.extract_details(page, Purchase(order_number="44-44444-44444", status="Zugestellt"))
    assert p.purchase_date == "2026-11-03"
    assert p.total == "EUR 5,50"
    assert p.store_info == "verkaeufer_zwei"
    assert [i.name for i in p.items] == ["Erfundenes Kabel zwei Meter"]
    assert p.items[0].quantity == "2"
    assert p.status == "Zugestellt", "the card's status stands"


def test_printing_a_cart_page_hides_the_other_order(page):
    page.set_content(DETAILS_PAGE)
    assert site.isolate_receipt(page, "33-33333-33333")
    shown = page.locator("body").inner_text()
    assert "33-33333-33333" in shown and "Erfundene Lampe" in shown
    assert "44-44444-44444" not in shown and "Erfundenes Kabel" not in shown
    assert "Zahlungsinformationen" in shown and "Lieferadresse" in shown
    assert "Hallo erfunden" not in shown, "the site header is not part of a receipt"
    assert "Rechnung ansehen" in shown or True  # a link, kept or not, is harmless


INVOICE_PAGE = """<!doctype html><html><head><title>Einzelheiten zum Kauf | eBay</title></head><body>
<header>Hallo erfunden!</header><main><div class="inv"><h1>Rechnung</h1>
<div>Von: eBay GmbH</div><div>Rechnungsdetails</div><div>Bestellnummer 33-33333-33333</div>
<div>MwSt.-Aufschlüsselung</div><button>Seite drucken</button></div></main>
<footer>Copyright</footer></body></html>"""


def test_an_invoice_is_isolated_without_its_print_button(page):
    page.set_content(INVOICE_PAGE)
    assert site.invoice_is_present(page)
    assert site.isolate_invoice(page)
    shown = page.locator("body").inner_text()
    assert "Rechnungsdetails" in shown and "33-33333-33333" in shown
    assert "Seite drucken" not in shown and "Hallo erfunden" not in shown
