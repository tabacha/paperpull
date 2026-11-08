"""What the ebay.de pages say, read the way this app reads them.

Every date, number, name and amount here is invented.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import ebayde_site as site
from paperpull_core.models import Purchase


@pytest.mark.parametrize("text,want", [
    ("12. Mai. 2026", "2026-05-12"),          # a card, with the point
    ("3. Nov 2026, 14:30", "2026-11-03"),     # the details page, without it
    ("1. Mär. 2025", "2025-03-01"),
    ("1. Mrz. 2025", "2025-03-01"),
    ("28. Dezember 2024", "2024-12-28"),
    ("7. Okt. 2017", "2017-10-07"),
    ("05.06.2026", "2026-06-05"),
    ("2026-01-02", "2026-01-02"),
])
def test_german_dates(text, want):
    assert site.parse_date(text) == want


def test_a_word_that_is_no_month_is_not_a_date():
    assert site.parse_date("Rücksendung bis 12. Kalenderwoche 2026") is None


@pytest.mark.parametrize("text,want", [
    ("EUR 1.234,56", "EUR 1.234,56"),
    ("Gesamtbetrag: EUR 9,99", "EUR 9,99"),
    ("12,50 €", "EUR 12,50"),
    ("€ 0,99", "EUR 0,99"),
    ("US $5.00", ""),
])
def test_amounts(text, want):
    assert site.parse_money(text) == want


@pytest.mark.parametrize("text,want", [
    ("Zugestellt", "Zugestellt"),
    ("Verschickt (ohne Sendungsverfolgung)", "Verschickt"),
    ("Storniert", "Storniert"),
    ("Bestellung abgebrochen", "Bestellung abgebrochen"),
])
def test_status(text, want):
    assert site.parse_status(text) == want


def test_canceled():
    assert site.is_canceled("Storniert") and site.is_canceled("Bestellung abgebrochen")
    assert not site.is_canceled("Zugestellt")


@pytest.mark.parametrize("label", [
    "Erneut kaufen", "Sofort-Kaufen", "Bieten", "Preisvorschlag senden", "In den Warenkorb",
    "Jetzt bezahlen", "Weiterverkaufen", "Verkaufen", "Bewertung abgeben",
    "Artikel zurückgeben", "Artikel zurueckgeben", "Rückgabe starten", "Verkäufer kontaktieren",
    "Verkaeufer kontaktieren", "Bestellung ausblenden", "Hilfe & Melden", "Abbrechen", "Löschen",
    "Loeschen", "Speichern", "Notiz hinzufügen", "Ausblenden rückgängig machen", "Buy it again",
])
def test_an_action_is_never_pressed(label):
    assert site.FORBIDDEN_CONTROL_RE.search(label), label
    assert not site.is_safe_control(label)


@pytest.mark.parametrize("label", [
    "Mehr anzeigen", "Weitere Bestellungen anzeigen", "Rechnung ansehen",
    "Einzelheiten zum Kauf ansehen", "Show more",
])
def test_a_document_control_may_be_named(label):
    assert site.is_safe_control(label), label


@pytest.mark.parametrize("name", [
    "2026-05-12 eBay Kauf Receipt.pdf", "2026-05-12 eBay Kreditkartenabrechnung Invoice.pdf",
    "Einzelheiten zum Kauf", "Rückgaben und Abbrüche", "Bestellinformationen",
    "Zahlungsinformationen", "Rechnungsdetails", "Kaufabwicklung",
])
def test_nouns_in_document_names_are_not_refused(name):
    assert not site.FORBIDDEN_CONTROL_RE.search(name), name


def test_the_daily_limit_by_its_words():
    assert site.DAILY_LIMIT_RE.search(
        "Sie haben die maximale Anzahl an Anfragen für heute erreicht.")
    assert site.DAILY_LIMIT_RE.search("You've exceeded the number of requests allowed in one day")
    assert not site.DAILY_LIMIT_RE.search("Bestellnummer 25-12345-67890, zugestellt am Tag danach")


def test_the_hosts():
    assert site.is_safe_url("https://order.ebay.de/ord/show?orderId=1")
    assert site.is_safe_url("https://www.ebay.de/mye/myebay/purchase")
    assert not site.is_safe_url("https://order.ebay.com/ord/show?orderId=1")
    assert not site.is_safe_url("https://notebay.de/")
    assert not site.is_safe_url("http://order.ebay.de/")


def test_the_year_filter_words():
    from datetime import date
    today = date(2026, 11, 8)
    assert site.year_filter_word(2026, today) == "CURRENT_YEAR", "THIS_YEAR lists nothing on ebay.de"
    assert site.year_filter_word(2017, today) == "NINE_YEARS_AGO"
    assert site.year_filter_word(2016, today) is None, "the menu stops nine years back"
    assert site.orders_url(None).endswith("?filter=year_filter:ALL_YEARS"), \
        "the bare address shows whatever filter was chosen last"


def test_invoice_address():
    url = site.invoice_url("17-12345-67890")
    assert url.startswith("https://order.ebay.de/ord/show/taxInfo?docType=taxInfo&orderId=17-12345-67890")
    assert site.is_safe_url(url)


BLOCK = """Bestellinformationen
Bestellt am\t3. Nov 2026, 14:30
Bestellnummer\t25-12345-67890
Gesamt\tEUR 1.020,00 (2 Artikel)
Verkauft von\terfundener_shop
Lieferinformationen
Zugestellt
Artikelinformationen
Erfundener Tisch aus Eiche
EUR 1.000,00
Preis pro Einheit EUR 1.000,00
Artikelnummer: 100000000001
Neu
Erfundene Schraube M4 verzinkt
EUR 20,00
Stückzahl: 10
Artikelnummer: 100000000002
Erneut kaufen
Andere Aktionen
Rechnung ansehen
Lieferadresse
Erika Mustermann
Erfundene Straße 1
Zahlungsinformationen
Visa-Kreditkarte endend auf 0000
Erika Mustermann
EUR 1.020,00
3. Nov um 14:30
EUR 1.020,00
"""


def test_the_address_and_the_payment_are_never_items():
    """The pilot read a card holder's name and a payment date as items,
    because the items did not end at "Andere Aktionen"."""
    names = [i.name for i in site.items_from_text(BLOCK)]
    assert names == ["Erfundener Tisch aus Eiche", "Erfundene Schraube M4 verzinkt"]
    assert not any("Mustermann" in n or "Visa" in n or "Nov um" in n for n in names)


def test_an_order_block_is_read():
    p = site.details_from_text(BLOCK, Purchase(order_number="25-12345-67890"))
    assert p.purchase_date == "2026-11-03"
    assert p.total == "EUR 1.020,00"
    assert p.store_info == "erfundener_shop"
    assert p.status == "Zugestellt"
    items = site.items_from_text(BLOCK)
    assert [i.name for i in items] == ["Erfundener Tisch aus Eiche", "Erfundene Schraube M4 verzinkt"]
    assert items[0].unit_price == "EUR 1.000,00" and items[0].line_total == "EUR 1.000,00"
    assert items[1].quantity == "10"


class _Page:
    """Just enough of a page for the sign-out and challenge checks."""

    def __init__(self, url, body, password=False):
        self.url, self._body, self._password = url, body, password

    def title(self):
        return ""

    def locator(self, sel):
        outer = self

        class L:
            def count(self):
                if "password" in sel:
                    return 1 if outer._password else 0
                return 0

            def inner_text(self, timeout=0):
                return outer._body
        return L()


def test_a_sign_in_page_is_a_sign_out_and_not_a_challenge():
    """The sign-in page carries a captcha of its own."""
    pg = _Page("https://signin.ebay.de/ws/eBayISAPI.dll?SignIn",
               "Bitte bestätigen Sie, dass Sie kein Roboter sind", password=True)
    assert site.looks_signed_out(pg)
    assert site.detect_security_challenge(pg) is None


def test_a_challenge_by_its_whole_sentence():
    pg = _Page("https://www.ebay.de/splashui/challenge",
               "Bitte bestätigen Sie, dass Sie kein Roboter sind.")
    assert site.detect_security_challenge(pg)


def test_a_bare_word_is_no_challenge():
    pg = _Page("https://www.ebay.de/mye/myebay/purchase",
               "Sicherheit beim Kaufen. Captcha. Käuferschutz. Mehr erfahren")
    assert site.detect_security_challenge(pg) is None
