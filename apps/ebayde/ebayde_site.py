"""ALL ebay.de selectors, URL patterns, and page behavior live here.

When eBay changes its German website, repair this file only.

This app started as a copy of apps/ebay (ebay.com) and keeps its way of
working: the purchase history walked one year at a time, the
order-details page printed as the receipt, nothing clicked. Everything a
page says is German here, and so is every pattern that reads it.

How ebay.de works, mapped against a signed-in account on 2026-11-08
(structure only, see the comments at each pattern):

* The purchase history is ``https://www.ebay.de/mye/myebay/purchase``
  ("Käufe | Mein eBay | eBay"). Unfiltered it shows this year and the two
  before it. Each card holds the status ("Zugestellt", "Verschickt",
  "Verschickt (ohne Sendungsverfolgung)"), ``Bestelldatum:`` with a date
  like ``12. Mai. 2026`` (the month carries a point even when it is not
  shortened), ``Gesamtbetrag:`` with ``EUR 1.234,56``, ``Bestellnummer:``
  with ``25-12345-67890``, ``Verkauft von:`` and the seller, the link
  "Einzelheiten zum Kauf ansehen" to ``order.ebay.de/ord/show?orderId=…&
  purchaseOrderId=…``, and on some cards "Rechnung ansehen", see below.
* The year menu ("Letzte 60 Tage", then one entry per year back nine
  years) has no links, its entries carry a data-url with the filter word:
  ``CURRENT_YEAR`` (not ebay.com's THIS_YEAR, which lists nothing here),
  ``LAST_YEAR`` … ``NINE_YEARS_AGO``, ``LAST_60_DAYS`` and ``ALL_YEARS``.
  The same word in ``?filter=year_filter:…`` on the page address works.
  ``ALL_YEARS`` is three years and at most 25 orders, and the bare address
  shows whichever filter was chosen last, so the run never relies on it.
* The order-details page ("Einzelheiten zum Kauf | eBay") is the receipt,
  inside ``div.main-regions``: Bestellinformationen (``Bestellt am`` with
  ``12. Nov 2026, 14:30``, here WITHOUT a point after the month,
  ``Bestellnummer``, ``Gesamt`` with ``EUR 99,99 (2 Artikel)``,
  ``Verkauft von``), Lieferinformationen, Angaben zur Sendungsverfolgung,
  Artikelinformationen (title, price, ``Preis pro Einheit EUR …``,
  ``Artikelnummer: …``), Andere Aktionen, then Lieferadresse and
  Zahlungsinformationen (``Gesamtbetrag``, ``Versand``, ``MwSt.``).
* ONE details page can hold SEVERAL orders. A cart bought from two sellers
  showed two Bestellinformationen blocks, two sellers and two order
  numbers, with one Lieferadresse and one Zahlungsinformationen below
  them. So a card is known by the Bestellnummer in its own text, and when
  its receipt is printed, every other order's block is hidden.
* Some cards have "Rechnung ansehen", ``order.ebay.de/ord/show/taxInfo?
  docType=taxInfo&orderId=…&page=true&skipSellerRedirect=false``. That is
  an HTML invoice ("Rechnung", "Rechnungsdetails", "MwSt.-Aufschlüsselung")
  issued by eBay itself, with a "Seite drucken" button that is not needed.
  It is printed to PDF the same way, into its own folder.
* eBay runs a Radware bot manager on these pages, which wraps fetch, and
  a fetch from www.ebay.de to order.ebay.de fails on CORS anyway. This app
  only ever navigates.

Not seen yet, and written as eBay's usual German phrasing until a run
shows the real sentence: the daily-limit page, "order not found", a
canceled order, a history with nothing in it, the sign-in page after an
idle sign-out. Diagnose writes each of them down when it meets them.

eBay also lists purchases in the Nachrichten area, which this app does not
touch. Nothing here clicks an action control. Capture is navigation plus
printToPDF, so ``FORBIDDEN_CONTROL_RE`` exists as a guard for the "Mehr
anzeigen" press, the diagnostics helpers and the repo-wide guard tests.

Site layer mapped against the live site: 2026-11-08
"""
from __future__ import annotations

import html as _html
import logging
import re
import time
from dataclasses import dataclass
from datetime import date as _date
from typing import List, Optional
from urllib.parse import parse_qs, quote, urlsplit

# Everything on its way into a diagnostic file goes through here.
from paperpull_core.redact import redact, set_private_words  # noqa: F401

from paperpull_core.models import ONLINE, Item, Purchase
from paperpull_core.urls import is_safe_url as _host_allows
from paperpull_core.dates import checked as _checked_date
from paperpull_core import page_check as _page_check
from storage import now_iso

log = logging.getLogger("ebayde_receipts.site")

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

BASE = "https://www.ebay.de"
ORDERS_URL = f"{BASE}/mye/myebay/purchase"
DETAILS_BASE = "https://order.ebay.de/ord/show"
INVOICE_BASE = "https://order.ebay.de/ord/show/taxInfo"
URLS = {
    "home": ORDERS_URL,
    "orders": ORDERS_URL,
    "account": f"{BASE}/mye/myebay/summary",
}

# signin.ebay.de is where an idle sign-out lands. The others are eBay's
# sign-in and verification paths on any of its sites.
LOGIN_URL_MARKERS = ["/signin", "/sign-in", "/login", "signin.ebay", "/ws/ebayisapi.dll?signin",
                     "/authenticate", "/mfa", "/verification"]

# The year filter's vocabulary, as ebay.de's own menu writes it into its
# data-url (read off the menu on 2026-11-08). Index 0 is this year. It is
# NOT ebay.com's: this year is CURRENT_YEAR (THIS_YEAR lists nothing), and
# it stops at NINE_YEARS_AGO.
YEAR_FILTER_WORDS = ["CURRENT_YEAR", "LAST_YEAR", "TWO_YEARS_AGO", "THREE_YEARS_AGO", "FOUR_YEARS_AGO",
                     "FIVE_YEARS_AGO", "SIX_YEARS_AGO", "SEVEN_YEARS_AGO", "EIGHT_YEARS_AGO",
                     "NINE_YEARS_AGO"]
# The menu's "all", which is this year and the two before it, at most one
# page of 25. Asked for by name, because the bare address shows whatever
# filter was chosen last, in this run or by hand.
ALL_YEARS_WORD = "ALL_YEARS"
# One page of the list holds this many orders. A year that fills it may
# hold more than the page shows, which the run says.
PAGE_SIZE = 25


def year_filter_word(year: int, today: Optional[_date] = None) -> Optional[str]:
    """The filter word for a calendar year, or None when it is further back
    than the vocabulary reaches."""
    back = (today or _date.today()).year - year
    if 0 <= back < len(YEAR_FILTER_WORDS):
        return YEAR_FILTER_WORDS[back]
    return None


def orders_url(year: Optional[int] = None) -> str:
    word = year_filter_word(year) if year else ALL_YEARS_WORD
    return f"{ORDERS_URL}?filter=year_filter:{word or ALL_YEARS_WORD}"


# An order number is "25-12345-67890". Orders from 2018 and before may
# carry the older "123456789012-1234567890123!1234" form.
ORDER_ID_RE = re.compile(r"\b(\d{2}-\d{5}-\d{5}|\d{5,}-\d{5,})\b")
ORDER_ID_TOKEN_RE = re.compile(r"^[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*(?:![0-9A-Za-z]+)?$")
ORDER_ID_IN_URL_RE = re.compile(r"[?&]orderId=([0-9A-Za-z!-]+)", re.I)


def order_details_url(order_id: str, purchase_order_id: str = "") -> str:
    url = f"{DETAILS_BASE}?orderId={quote(order_id, safe='-')}"
    if purchase_order_id:
        url += f"&purchaseOrderId={quote(purchase_order_id, safe='-')}"
    return url


def invoice_url(order_id: str) -> str:
    """The "Rechnung ansehen" address, built the way the card writes it."""
    return (f"{INVOICE_BASE}?docType=taxInfo&orderId={quote(order_id, safe='-')}"
            "&page=true&skipSellerRedirect=false")


def parse_order_link(href: str):
    """(ONLINE, orderId, purchaseOrderId) from an "Einzelheiten zum Kauf
    ansehen" link."""
    try:
        q = parse_qs(urlsplit(href or "").query)
    except ValueError:
        return None, None, None
    oid = (q.get("orderId") or [""])[0]
    if not oid or len(oid) > 60 or not ORDER_ID_TOKEN_RE.match(oid):
        return None, None, None
    return ONLINE, oid, (q.get("purchaseOrderId") or [""])[0]


# ---------------------------------------------------------------------------
# Guards. Nothing here is clicked but a "Mehr anzeigen", and the repo-wide
# tests hold every app to the same standard.
#
# Verbs are refused, nouns in document names are not: "Erneut kaufen" is a
# button, "Einzelheiten zum Kauf ansehen" is the receipt. Umlauts are
# matched both ways (ä|ae, ü|ue, ß|ss), and a German word is bounded on
# both sides so a stem does not fire inside a longer word. The English
# words stay, because eBay mixes them in.
# ---------------------------------------------------------------------------
_GERMAN_FORBIDDEN = (
    r"\b(?:erneut\s+|sofort[-\s]?|jetzt\s+)?kaufen\b|\bsofort[-\s]?kauf\b|"
    r"\bbieten\b|\bgebot(?:\s+abgeben)?\b|\bpreisvorschlag\b|"
    r"\bin\s+den\s+warenkorb\b|\bwarenkorb\b|\bbeobachten\b|"
    r"\b(?:jetzt\s+)?bezahlen\b|\bzahlung\s+(?:senden|abschlie(?:ss|ß)en)\b|"
    r"\bweiterverkaufen\b|\bverkaufen\b|\bartikel\s+einstellen\b|"
    r"\bbewertung\s+abgeben\b|\bbewerten\b|"
    r"\b(?:artikel\s+)?zur(?:ue|ü)ckgeben\b|\br(?:ue|ü)ckgabe\s+(?:starten|beantragen)\b|"
    r"\bmelden\b|\bhilfe\s*&\s*melden\b|\bverk(?:ae|ä)ufer\s+kontaktieren\b|"
    r"\bbestellung\s+(?:abbrechen|stornieren|ausblenden)\b|\babbrechen\b|\bstornieren\b|"
    r"\bausblenden\b|\bausblenden\s+r(?:ue|ü)ckg(?:ae|ä)ngig\b|"
    r"\bl(?:oe|ö)schen\b|\bentfernen\b|\bspeichern\b|\bnotiz\s+hinzuf(?:ue|ü)gen\b|"
    r"\babonnieren\b|\beinl(?:oe|ö)sen\b|\bjetzt\s+beantragen\b|\bjetzt\s+anmelden\b"
)
FORBIDDEN_CONTROL_RE = re.compile(
    r"(buy\s+(it\s+)?again|buy\s+now|add\s+to\s+(cart|watchlist)|checkout|place\s+(bid|order)|"
    r"\bbid\b|make\s+(an\s+)?offer|pay\s+now|\bpay\b|payment|resell|sell\s+(one|it)|"
    r"leave\s+feedback|return\s+(this\s+)?item|start\s+a\s+return|report\s+(item|seller|a\s+problem)|"
    r"contact\s+seller|cancel\s+(order|item)|hide\s+order|unhide|"
    r"delete|remove|subscribe|redeem|apply\s+now|\bsell\b|list\s+(an\s+)?item|"
    + _GERMAN_FORBIDDEN + r")", re.I)

try:
    from paperpull_core.controls import SETTINGS_CONTROL_RE as _SHARED_SETTINGS
    FORBIDDEN_CONTROL_RE = re.compile(
        "(?:%s)|(?:%s)" % (FORBIDDEN_CONTROL_RE.pattern, _SHARED_SETTINGS.pattern),
        re.I)
except Exception:  # the shared core is optional at import time
    pass

SAFE_DOC_CONTROL_RE = re.compile(
    r"(print|printer\s+friendly|view\s+(receipt|invoice|order\s+details)|order\s+details|"
    r"purchase\s+history|load\s+more|show\s+more|view\s+more|"
    r"\bdrucken\b|rechnung\s+ansehen|einzelheiten\s+zum\s+kauf|"
    r"\bmehr\s+(?:anzeigen|laden)\b|\bweitere\s+(?:anzeigen|laden|bestellungen|k(?:ae|ä)ufe)\b)", re.I)

# Whole prompts only. A bare product word ("Sicherheit", "Captcha") can be
# on any normal page, and the sign-in page carries a captcha field of its
# own, which is why detect_security_challenge asks about a sign-out first.
SECURITY_CHALLENGE_MARKERS = [
    # German
    "bitte bestätigen sie, dass sie kein roboter sind",
    "bestätigen sie, dass sie ein mensch sind",
    "bitte bestätigen sie, dass sie ein mensch sind",
    "geben sie die zeichen ein, die sie sehen",
    "geben sie die angezeigten zeichen ein",
    "geben sie den sicherheitscode ein",
    "geben sie den bestätigungscode ein",
    "bitte geben sie den code ein",
    "bitte bestätigen sie ihre identität",
    "bestätigen sie ihre identität",
    "wir müssen bestätigen, dass sie es sind",
    "zur sicherheit ihres kontos",
    "halten sie die taste gedrückt",
    "gedrückt halten",
    "der zugriff auf diese seite wurde verweigert",
    # English, which eBay's shared check pages fall back to
    "enter the characters you see", "type the characters you see",
    "are you a robot", "press & hold", "press and hold",
    "verify you are a human", "verify you are human",
    "checking your browser before accessing",
    "access to this page has been denied",
    "enter the verification code", "confirm it's you",
]

RATE_LIMIT_MARKERS = [
    "zu viele anfragen", "bitte versuchen sie es später erneut",
    "vorübergehend gesperrt",
    "too many requests", "try again later", "temporarily blocked",
    "http error 429", "request was throttled",
]

# eBay caps how many order-details pages one account may open in a day.
# On ebay.com past the cap every details URL lands on
# pages.ebay.com/limitexceeded.html. The German sentence has not been
# seen, so the URL is the sure sign and the words are eBay's usual ones.
DAILY_LIMIT_URL_MARKER = "limitexceeded"
DAILY_LIMIT_RE = re.compile(
    r"exceeded\s+the\s+number\s+of\s+requests\s+allowed\s+in\s+one\s+day|daily\s+limit\s+exceeded|"
    r"\bmaximale?\s+anzahl\s+(?:an|der|von)\s+(?:zul(?:ae|ä)ssigen\s+)?(?:anfragen|aufrufen)\b.{0,60}"
    r"\b(?:tag|heute|24\s+stunden)\b|"
    r"\banfragen\s+(?:pro|f(?:ue|ü)r\s+einen|an\s+einem)\s+tag\s+(?:ue|ü)berschritten\b|"
    r"\btageslimit\s+(?:erreicht|(?:ue|ü)berschritten)\b", re.I | re.S)


def hit_daily_limit(page) -> bool:
    """True when eBay has answered with its daily request limit page."""
    try:
        if DAILY_LIMIT_URL_MARKER in str(page.url or "").lower():
            return True
        title = page.title() or ""
        body = page.locator("body").inner_text(timeout=5000) or ""
    except Exception:
        return False
    if not isinstance(title, str) or not isinstance(body, str):
        return False
    return bool(DAILY_LIMIT_RE.search(title + "\n" + body[:3000]))


# Every selector names the details path only, never the host, so a link
# eBay writes relative or on another of its hosts still counts.
DETAILS_LINK = "a[href*='/ord/show?'], a[href*='/ord/show#'], a[href$='/ord/show']"
INVOICE_LINK = "a[href*='/ord/show/taxInfo']"

FALLBACK = {
    "order_link": DETAILS_LINK,
    "order_card": DETAILS_LINK,
    "page_ready": DETAILS_LINK + ", main, [class*='m-ph-card']",
    "invoice_link": INVOICE_LINK,
    "item_row": "[class*='m-item-card'], [class*='item-card']",
    "item_title": ".main-regions a[href*='/itm/'], .main-regions h3.clipped",
    "next_page": "",  # a year filter and a scroll, no pager
    "print_page_body": "body",
}

# ---------------------------------------------------------------------------
# Dates, amounts, status
# ---------------------------------------------------------------------------

# The month as ebay.de writes it: "Mai." and "Nov." on a card, "Nov" on the
# details page, and the long names in case a page spells them out. "Mär"
# is also written "Mrz" and "Maer".
_DE_MONTHS = {
    "jan": 1, "januar": 1, "jän": 1, "jänner": 1,
    "feb": 2, "februar": 2,
    "mär": 3, "mrz": 3, "maer": 3, "märz": 3, "maerz": 3,
    "apr": 4, "april": 4,
    "mai": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dez": 12, "dezember": 12,
}
_DE_MONTH_WORD = r"([A-Za-zÄÖÜäöü]{3,9})"
DATE_PATTERNS = [
    # 12. Mai. 2026 / 12. Nov 2026 / 3. März 2026 / 12.Nov.2026
    (re.compile(r"\b(\d{1,2})\.\s*" + _DE_MONTH_WORD + r"\.?\s*(\d{4})\b"), "dMY"),
    # 12.05.2026
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), "dmy_dot"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)"), "iso"),
]


def _parse_date_from_page(text: str) -> Optional[str]:
    if not text:
        return None
    for pattern, kind in DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if kind == "dMY":
                    month = _DE_MONTHS.get(m.group(2).lower())
                    if not month:
                        continue
                    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
                if kind == "dmy_dot":
                    return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
                if kind == "iso":
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            except ValueError:
                continue
    return None


def parse_date(text):
    """The date this provider's page is showing, as YYYY-MM-DD.

    Refuses a result that names a day which does not exist, because a
    reference number can be shaped like a date."""
    return _checked_date(_parse_date_from_page(text), None)


# "EUR 1.234,56", "1.234,56 EUR", "€ 9,99" and "9,99 €". The amount is
# kept as the page writes it, "EUR 1.234,56", so the saved PDF can be
# checked against it as printed.
_DE_AMOUNT = r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
MONEY_RE = re.compile(r"(?:EUR|€)\s*" + _DE_AMOUNT + r"|" + _DE_AMOUNT + r"\s*(?:EUR|€)")
_MONEY_LINE_RE = re.compile(r"^(?:(?:EUR|€)\s*" + _DE_AMOUNT + r"|" + _DE_AMOUNT + r"\s*(?:EUR|€))$")


def parse_money(text: str) -> str:
    m = MONEY_RE.search(text or "")
    if not m:
        return ""
    return f"EUR {m.group(1) or m.group(2)}"


# The status words, longest first, so "Verschickt (ohne Sendungsverfolgung)"
# reads as Verschickt and "Rückgabe gestartet" is not taken for anything
# shorter.
STATUS_WORDS_RE = re.compile(
    r"\b(zugestellt|verschickt|versandt|unterwegs|in\s+zustellung|"
    r"storniert|abgebrochen|bestellung\s+abgebrochen|zur(?:ue|ü)ckgegeben|erstattet|"
    r"r(?:ue|ü)ckerstattung\s+(?:veranlasst|erfolgt)|r(?:ue|ü)ckgabe\s+(?:gestartet|beantragt)|"
    r"in\s+bearbeitung|bezahlt|nicht\s+bezahlt|zahlung\s+fehlgeschlagen|"
    r"warten\s+auf\s+versand)\b", re.I)

CANCELED_RE = re.compile(
    r"\b(?:storniert|abgebrochen)\b|bestellung\s+wurde\s+(?:storniert|abgebrochen)|"
    r"diese\s+bestellung\s+wurde\s+(?:storniert|abgebrochen)|cancell?ed", re.I)


def parse_status(text: str) -> str:
    m = STATUS_WORDS_RE.search(text or "")
    if not m:
        return ""
    word = re.sub(r"\s+", " ", m.group(1))
    return word[:1].upper() + word[1:].lower()


def is_canceled(status: str) -> bool:
    return bool(CANCELED_RE.search(status or ""))


# ---------------------------------------------------------------------------
# Session / safety
# ---------------------------------------------------------------------------

def looks_signed_out(page) -> bool:
    url = (page.url or "").lower()
    if any(m in url for m in LOGIN_URL_MARKERS):
        return True
    try:
        if page.locator("input[type='password']").count() > 0:
            return True
    except Exception:
        pass
    return False


def detect_security_challenge(page) -> Optional[str]:
    """Names the CAPTCHA, passcode or throttling prompt on screen, or None.

    Never on a signed-out page, since the sign-in page carries a captcha
    field of its own and a sign-out is what is really going on. Visible
    text only, and only when the purchase list is not there, since a
    page's scripts can carry every one of these strings on a normal day."""
    if looks_signed_out(page):
        return None
    try:
        if page.locator(FALLBACK["order_link"]).count() > 0:
            return None
    except Exception:
        pass
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = ""
    hay = title + "\n" + body[:2000]
    for m in SECURITY_CHALLENGE_MARKERS:
        if m in hay:
            return f"Security challenge detected: '{m}'"
    for m in RATE_LIMIT_MARKERS:
        if m in hay:
            return f"Possible rate limiting detected: '{m}'"
    return None


def is_safe_control(name: str) -> bool:
    name = (name or "").strip()
    if not name or FORBIDDEN_CONTROL_RE.search(name):
        return False
    return bool(SAFE_DOC_CONTROL_RE.search(name))


# ---------------------------------------------------------------------------
# Purchase history
# ---------------------------------------------------------------------------

# What the history's own scripts and this module both call an order:
# the "Bestellnummer:" line on a card, the orderId in a details link.
_NUM_JS = r"/bestellnummer\s*:?\s*([0-9A-Za-z][0-9A-Za-z!-]{4,})/i"

_ORDER_LINK_COUNT_JS = "() => document.querySelectorAll(%r).length" % DETAILS_LINK


def _order_link_count(page) -> int:
    try:
        return int(page.evaluate(_ORDER_LINK_COUNT_JS) or 0)
    except Exception:
        return 0


_SHOW_MORE_RE = re.compile(
    r"^\s*(?:(?:show|see|load)\s+(?:more|older)(?:\s+orders|\s+purchases)?|"
    r"(?:mehr|weitere)(?:\s+(?:bestellungen|k(?:ae|ä)ufe))?\s+(?:anzeigen|laden))\s*$", re.I)


def _press_show_more(page) -> bool:
    """A "Mehr anzeigen" the list offers instead of loading on scroll."""
    try:
        loc = page.get_by_role("button", name=_SHOW_MORE_RE).or_(page.get_by_role("link", name=_SHOW_MORE_RE))
        if loc.count() and loc.first.is_visible():
            label = (loc.first.inner_text(timeout=800) or "").strip()
            if is_safe_control(label):
                loc.first.click(timeout=5000)
                return True
    except Exception:
        pass
    return False


_ORDER_COUNT_JS = r"""
() => { const s = new Set();
  const rx = new RegExp(%s.source, 'gi');
  const t = document.body.innerText || ''; let m;
  while ((m = rx.exec(t))) s.add(m[1]);
  if (!s.size) for (const a of document.querySelectorAll(%r)) {
    const k = (a.getAttribute('href')||'').match(/[?&]orderId=([0-9A-Za-z!-]+)/); if (k) s.add(k[1]); }
  return s.size; }
""" % (_NUM_JS, DETAILS_LINK)


def _order_count(page) -> int:
    """Orders on the page, by the order number each card prints, and by
    link only when no card prints one."""
    try:
        return int(page.evaluate(_ORDER_COUNT_JS))
    except Exception:
        return _order_link_count(page)


def scroll_all_orders(page, max_rounds: int = 25, delay_ms: Optional[int] = None,
                      stable_rounds: int = 3) -> int:
    """Scroll until the list stops growing, in orders and in page height,
    pressing any "Mehr anzeigen" it offers. Returns the order count."""
    delay_ms = SCROLL_DELAY_MS if delay_ms is None else delay_ms
    last = _order_count(page)
    last_height = 0
    stable = 0
    for _ in range(max_rounds):
        try:
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(delay_ms)
            page.keyboard.press("End")
            page.wait_for_timeout(delay_ms)
            if _press_show_more(page):
                page.wait_for_timeout(delay_ms)
        except Exception:
            break
        now = _order_count(page)
        try:
            height = int(page.evaluate("() => document.body.scrollHeight") or 0)
        except Exception:
            height = last_height
        if now == last and height == last_height:
            stable += 1
            if stable >= stable_rounds:
                break
        else:
            stable = 0
            last = now
            last_height = height
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    return last


ORDERS_WAIT_MS = 20000
SETTLE_MS = 2500
CHALLENGE_WAIT_MS = 15000
SCROLL_DELAY_MS = 2000

# eBay's words for a history with nothing in it. Not seen on a real
# account, so this is the usual phrasing. A page that says something else
# and shows no orders is taken as one that did not load.
EMPTY_RE = re.compile(
    r"\b(?:sie\s+haben\s+)?(?:noch\s+)?keine\s+(?:k(?:ae|ä)ufe|bestellungen)\b|"
    r"\bwir\s+konnten\s+keine\s+(?:k(?:ae|ä)ufe|bestellungen)\s+finden\b|"
    r"\b(?:no|haven.t\s+(?:bought|purchased|made)\s+any|don.t\s+have\s+any)\s+(?:recent\s+)?"
    r"(?:orders|purchases)\b", re.I)


def goto_orders(page, year: Optional[int] = None) -> bool:
    """Open the purchase history, for one calendar year when given, and
    scroll it until the list settles. True when it holds orders, or says
    it has none."""
    page.goto(orders_url(year), wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector(FALLBACK["order_link"], timeout=ORDERS_WAIT_MS)
    except Exception:
        pass
    page.wait_for_timeout(SETTLE_MS)
    scroll_all_orders(page)
    return orders_listed(page)


def history_state(page) -> str:
    """"empty" when the page says there are no purchases, else ""."""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""
    return "empty" if EMPTY_RE.search(text or "") else ""


def orders_listed(page) -> bool:
    if _order_count(page) > 0:
        return True
    return history_state(page) == "empty"


def challenge_after_a_moment(page, wait_ms: Optional[int] = None) -> Optional[str]:
    wait_ms = CHALLENGE_WAIT_MS if wait_ms is None else wait_ms
    deadline = time.monotonic() + wait_ms / 1000.0
    while True:
        found = detect_security_challenge(page)
        if found or orders_listed(page) or time.monotonic() >= deadline:
            return found
        try:
            page.wait_for_timeout(500)
        except Exception:
            return found


@dataclass
class RawCard:
    href: str
    text: str
    order_id: str
    purchase_order_id: str = ""
    kind: str = ONLINE
    title: str = ""
    seller: str = ""
    invoice_href: str = ""
    link_order_id: str = ""


# A card is the largest element around one "Bestellnummer:" that still
# holds no second one. The number is the card's identity, because a cart
# bought from several sellers may send every card to one details page.
# The details link and the "Rechnung ansehen" link are read from inside it.
_COLLECT_CARDS_JS = r"""
() => {
  const NUM = %s;
  const LINK = %r;
  const INV = %r;
  const numsIn = (el) => { const s = new Set(); const rx = new RegExp(NUM.source, 'gi');
    const t = el.innerText || ''; let m; while ((m = rx.exec(t))) s.add(m[1]); return s; };
  const named = [];
  for (const el of document.querySelectorAll('div,li,section,article,tr,span,p')) {
    const t = (el.innerText || '');
    if (t.length > 4000 || !NUM.test(t)) continue;
    named.push(el);
  }
  const innermost = named.filter(el => !named.some(o => o !== el && el.contains(o)));
  const out = [], seen = new Set();
  for (const start of innermost) {
    const m = (start.innerText || '').match(NUM);
    if (!m || seen.has(m[1])) continue;
    const id = m[1];
    seen.add(id);
    let best = start, el = start.parentElement;
    while (el && el !== document.body) {
      if (numsIn(el).size > 1) break;
      best = el;
      el = el.parentElement;
    }
    const a = best.querySelector(LINK);
    const inv = best.querySelector(INV);
    const h3 = best.querySelector('h3');
    const seller = best.querySelector("a[href*='/usr/']");
    out.push({id: id, href: a ? (a.getAttribute('href') || '') : '',
              invoice: inv ? (inv.getAttribute('href') || '') : '',
              text: (best.innerText || '').trim(),
              title: h3 ? h3.innerText.trim() : '',
              seller: seller ? seller.innerText.trim().split('\n')[0] : ''});
  }
  // No card printed a number at all: fall back to the links, one per id.
  if (!out.length) {
    const idOf = (l) => { const k = (l.getAttribute('href') || '').match(/[?&]orderId=([0-9A-Za-z!-]+)/); return k ? k[1] : null; };
    const idsIn = (el) => { const s = new Set(); for (const l of el.querySelectorAll(LINK)) { const i = idOf(l); if (i) s.add(i); } return s; };
    for (const a of document.querySelectorAll(LINK)) {
      const id = idOf(a);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      let best = a, el = a.parentElement;
      while (el && el !== document.body) { if (idsIn(el).size > 1) break; best = el; el = el.parentElement; }
      const inv = best.querySelector(INV);
      const h3 = best.querySelector('h3');
      const seller = best.querySelector("a[href*='/usr/']");
      out.push({id: id, href: a.getAttribute('href') || '', invoice: inv ? (inv.getAttribute('href') || '') : '',
                text: (best.innerText || '').trim(), title: h3 ? h3.innerText.trim() : '',
                seller: seller ? seller.innerText.trim().split('\n')[0] : ''});
    }
  }
  return out;
}
""" % (_NUM_JS, DETAILS_LINK, INVOICE_LINK)


def _absolute(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://order.ebay.de" + href
    return href


def collect_cards(page, purchase_type: str = "") -> List[RawCard]:
    """Every order card rendered on the history page, one per order
    number."""
    try:
        raw = page.evaluate(_COLLECT_CARDS_JS) or []
    except Exception as e:
        log.warning("Card collection failed: %s", e)
        raw = []
    cards: List[RawCard] = []
    for r in raw:
        oid = str(r.get("id") or "").strip()
        if not oid or len(oid) > 60 or not ORDER_ID_TOKEN_RE.match(oid):
            continue
        href = _absolute(r.get("href") or "")
        _, link_oid, poid = parse_order_link(href)
        if link_oid and is_safe_url(href):
            details = order_details_url(link_oid, poid or "")
        else:
            link_oid, poid = "", ""
            details = order_details_url(oid)
        inv = _absolute(r.get("invoice") or "")
        if inv and not is_safe_url(inv):
            inv = ""
        cards.append(RawCard(href=details, text=r.get("text") or "",
                             order_id=oid, purchase_order_id=poid or "", kind=ONLINE,
                             title=(r.get("title") or "").strip(),
                             seller=(r.get("seller") or "").strip(),
                             invoice_href=inv, link_order_id=link_oid or ""))
    if purchase_type and purchase_type != ONLINE:
        return []
    return cards


_CARD_DATE_RE = re.compile(r"bestelldatum\s*:?\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]{3,9}\.?\s*\d{4})", re.I)
_CARD_TOTAL_RE = re.compile(r"(?:gesamtbetrag|bestellsumme|gesamt)\s*:?\s*((?:EUR|€)\s*" + _DE_AMOUNT
                            + r"|" + _DE_AMOUNT + r"\s*(?:EUR|€))", re.I)


def card_to_purchase(card: RawCard, purchase_type: str = "",
                     base_url: str = BASE) -> Optional[Purchase]:
    """A Purchase from a card. "Bestelldatum: 12. Mai. 2026 Gesamtbetrag:
    EUR 99,99 Bestellnummer: 25-…" carries the date and the total, the
    line above them the status."""
    if not card.order_id:
        return None
    text = card.text or ""
    date = ""
    m = _CARD_DATE_RE.search(text)
    if m:
        date = parse_date(m.group(1)) or ""
    if not date:
        date = parse_date(text) or ""
    m = _CARD_TOTAL_RE.search(text)
    total = parse_money(m.group(1)) if m else parse_money(text)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    status = ""
    for ln in lines[:4]:
        status = parse_status(ln)
        if status:
            break
    status = status or parse_status(text)
    p = Purchase(
        purchase_type=ONLINE,
        purchase_date=date,
        order_number=card.order_id,
        total=total,
        status=status,
        store_info=card.seller or "eBay",
        details_url=card.href,
        receipt_url=card.href,
        items=[Item(name=card.title[:300], quantity="1")] if card.title else [],
        discovered_at=now_iso(),
    )
    return p


# ---------------------------------------------------------------------------
# What the purchase history page actually holds (Diagnose)
# ---------------------------------------------------------------------------

_HISTORY_SURVEY_JS = r"""
() => {
  const LINK = %r;
  const INV = %r;
  const NUM = %s;
  const anchors = document.querySelectorAll(LINK);
  const ids = new Set();
  for (const a of anchors) {
    const m = (a.getAttribute('href') || '').match(/[?&]orderId=([0-9A-Za-z!-]+)/);
    if (m) ids.add(m[1]);
  }
  const DATE = /bestelldatum\s*:?\s*\d{1,2}\.\s*[A-Za-zÄÖÜäöü]{3,9}\.?\s*\d{4}/i;
  const hits = [];
  for (const el of document.querySelectorAll('div,li,section,article,tr')) {
    const t = (el.innerText || '');
    if (t.length > 4000 || !DATE.test(t)) continue;
    hits.push(el);
  }
  const rowsEls = hits.filter(el => !hits.some(other => other !== el && el.contains(other)));
  const textIds = new Set();
  for (const el of rowsEls) {
    const m = (el.innerText || '').match(NUM);
    if (m) textIds.add(m[1]);
  }
  const dated = rowsEls.slice(0, 40).map(el => ({
    has_link: !!el.querySelector(LINK),
    has_invoice: !!el.querySelector(INV),
    names_an_order: NUM.test(el.innerText || ''),
    text_len: (el.innerText || '').length,
  }));
  // Controls that might turn a page, by their words. Counted only, so no
  // label (which can carry an item title) leaves the page.
  let paging = 0;
  for (const b of document.querySelectorAll('button,a,[role=button],[role=link]')) {
    const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
    if (t.length < 40 && /mehr|weiter|n(?:ä|ae)chste|seite\s*\d|more|next|older/i.test(t)) paging++;
  }
  return {
    anchors_to_an_order: anchors.length,
    distinct_ids_in_links: ids.size,
    distinct_ids_in_text: textIds.size,
    invoice_links: document.querySelectorAll(INV).length,
    elements_carrying_a_date_including_wrappers: hits.length,
    rows_with_an_order_date: dated.length,
    rows: dated.slice(0, 20),
    controls_that_might_page: paging,
    body_scroll_height: document.body.scrollHeight,
  };
}
""" % (DETAILS_LINK, INVOICE_LINK, _NUM_JS)


def history_survey(page) -> dict:
    """What the history page holds, and what this app makes of it.

    Counts and shapes only. The one piece of text that comes back is an
    order number, which is what the filenames already carry."""
    out = {"url": redact(page.url or "")}
    try:
        out["page"] = page.evaluate(_HISTORY_SURVEY_JS)
    except Exception as e:
        out["page"] = {"error": str(e)[:120]}
    cards = collect_cards(page)
    out["cards_collected"] = len(cards)
    became, no_date, no_id, shared_link = [], 0, 0, 0
    links = {}
    for c in cards:
        if c.link_order_id:
            links[c.link_order_id] = links.get(c.link_order_id, 0) + 1
    for c in cards:
        p = card_to_purchase(c)
        if p is None:
            no_id += 1
            continue
        if not p.purchase_date:
            no_date += 1
        if c.link_order_id and links.get(c.link_order_id, 0) > 1:
            shared_link += 1
        became.append({"id": p.order_number, "date": p.purchase_date or "no date",
                       "total": "yes" if p.total else "no",
                       "status": p.status or "none",
                       "has_title": bool(p.items),
                       "invoice": "yes" if c.invoice_href else "no",
                       "link_id_is_its_number": c.link_order_id == c.order_id,
                       "details": "built" if p.details_url else "none"})
    out["became_purchases"] = len(became)
    out["dropped_for_no_order_id"] = no_id
    out["purchases_with_no_date"] = no_date
    out["cards_sharing_a_details_link"] = shared_link
    out["purchases"] = became[:25]
    return out


# ---------------------------------------------------------------------------
# Details page = the receipt
# ---------------------------------------------------------------------------

HYDRATED_RE = re.compile(r"bestellnummer", re.I)
_PLACED_RE = re.compile(r"bestellt\s+am", re.I)


def on_details_page(page) -> bool:
    try:
        parts = urlsplit(page.url or "")
    except ValueError:
        return False
    return is_safe_url(page.url or "") and parts.path.rstrip("/") == "/ord/show"


def wait_for_hydration(page, timeout_ms: int = 45000) -> bool:
    """The details page renders its shell first and fills in the order a
    moment later. "Bestellnummer" and "Bestellt am" are the markers."""
    try:
        page.wait_for_function(
            "() => /Bestellnummer/i.test(document.body.innerText) && /Bestellt am/i.test(document.body.innerText)",
            timeout=timeout_ms)
        page.wait_for_timeout(800)
        return True
    except Exception:
        log.warning("Order-details page did not hydrate within %dms", timeout_ms)
        return False


def goto_details(page, purchase: Purchase) -> None:
    """Open the order-details page and wait for it to fill in."""
    url = purchase.details_url if is_safe_url(purchase.details_url or "") else ""
    if not url or "orderId=" not in url:
        url = order_details_url(purchase.order_number)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_for_hydration(page)


# The text of THIS order's block on a details page that may hold several:
# the largest element around its "Bestellnummer <nr>" that names no other
# order number. The whole main region when the number is not found.
_ORDER_BLOCK_JS = r"""
(oid) => {
  const NUM = %s;
  const root = document.querySelector('.main-regions') || document.querySelector('main') || document.body;
  const numsIn = (el) => { const s = new Set(); const rx = new RegExp(NUM.source, 'gi');
    const t = el.innerText || ''; let m; while ((m = rx.exec(t))) s.add(m[1]); return s; };
  const all = numsIn(root);
  if (all.size <= 1 || !all.has(oid)) return {text: root.innerText || '', orders: all.size, found: all.has(oid)};
  let start = null;
  for (const el of root.querySelectorAll('*')) {
    const s = numsIn(el);
    if (s.size === 1 && s.has(oid)) { if (!start || start.contains(el)) start = el; }
  }
  if (!start) return {text: root.innerText || '', orders: all.size, found: false};
  let best = start, el = start.parentElement;
  while (el && el !== root) {
    const s = numsIn(el);
    if (s.size > 1) break;
    best = el;
    el = el.parentElement;
  }
  return {text: best.innerText || '', orders: all.size, found: true};
}
""" % _NUM_JS


def order_block(page, order_number: str) -> dict:
    """{"text", "orders", "found"} for this order on the details page."""
    try:
        out = page.evaluate(_ORDER_BLOCK_JS, order_number) or {}
    except Exception as e:
        log.warning("Could not read the order block: %s", e)
        out = {}
    if not out.get("text"):
        try:
            out["text"] = page.locator("body").inner_text(timeout=8000)
        except Exception:
            out["text"] = ""
    return out


_SHOWN_NUMBER_RE = re.compile(r"bestellnummer\s*[\t:]?\s*([0-9A-Za-z][0-9A-Za-z!-]{4,})", re.I)


def link_order_id(details_url: str) -> str:
    """The orderId the details address is keyed on, which for an older
    order or a cart is not the number the order prints."""
    m = ORDER_ID_IN_URL_RE.search(details_url or "")
    return m.group(1) if m else ""


def not_this_purchase(page, order_number: str, link_id: str = "") -> str:
    """"" when the page in front of the app is this purchase's own page,
    else why not, in paperpull_core.page_check's fixed words.

    Its address has to carry this purchase's orderId (the link's, or the
    number itself), and among the Bestellnummer values the page prints
    this purchase's has to be one. Only one of them, not the only one,
    because one details page can hold a whole cart of orders. The order
    list is at an address of its own, and a sign-in page names the
    purchase only in its return address. The invoice page prints no
    number that has been seen, so a page that prints none stands on its
    address and is answered NAMES_NONE for the caller to weigh."""
    key = link_id or order_number
    if not (on_details_page(page) or on_invoice_page(page)):
        return _page_check.NOT_ITS_ADDRESS
    if not _page_check.address_names(page.url, key, query="orderId"):
        return _page_check.NOT_ITS_ADDRESS
    shown = {_page_check.plain(n) for n in _SHOWN_NUMBER_RE.findall(_page_check.page_text(page))}
    shown.discard("")
    if not shown:
        return _page_check.NAMES_NONE
    if _page_check.plain(order_number) not in shown:
        return _page_check.NAMES_ANOTHER
    return ""


# Lines that are labels, not titles.
_NON_ITEM_NAME_RE = re.compile(
    r"^(?:bestell(?:informationen|nummer|t\s+am)|gesamt(?:betrag)?\b|verkauft\s+von|"
    r"lieferinformationen|angaben\s+zur\s+sendungsverfolgung|sendungsverfolgung|nummer\b|"
    r"artikelinformationen|preis\s+pro\s+einheit|artikelnummer|menge|st(?:ü|ue)ckzahl|anzahl|"
    r"erneut\s+kaufen|weitere\s+aktionen|andere\s+aktionen|rechnung\s+ansehen|"
    r"verk(?:ä|ae)ufer\s+kontaktieren|lieferadresse|zahlungsinformationen|versand\b|mwst|"
    r"mehr\s+erfahren|ebay\s+top-service|zugestellt|verschickt|bezahlt|storniert|abgebrochen|"
    r"schritt\s+abgeschlossen|keine\s+r(?:ü|ue)ckgabe|r(?:ü|ue)ckgabe|\d+\s+artikel$|-)|"
    r"^(?:neu|gebraucht|neuwertig|generalüberholt|wie\s+neu|sehr\s+gut|gut|akzeptabel|"
    r"als\s+ersatzteil|used|new)\.?$", re.I)


def _clean_item_name(name: str) -> str:
    name = _html.unescape(re.sub(r"\s+", " ", name or "")).strip()
    if len(name) < 5 or _MONEY_LINE_RE.match(name) or _NON_ITEM_NAME_RE.search(name):
        return ""
    return name


def items_from_text(body: str) -> List[Item]:
    """Line items from an order block's text. Each title is a line of its
    own, followed by its price, "Preis pro Einheit EUR …" and an
    "Artikelnummer: …"."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    items: List[Item] = []
    inside = not any(re.match(r"^artikelinformationen$", ln, re.I) for ln in lines)
    current: Optional[Item] = None
    for i, ln in enumerate(lines):
        if re.match(r"^artikelinformationen$", ln, re.I):
            inside = True
            continue
        if inside and re.match(r"^(andere\s+aktionen|lieferadresse|zahlungsinformationen)$", ln, re.I):
            # The items end here. A later "Artikelinformationen" (the next
            # order of a cart) opens them again, nothing else does, so the
            # address and the payment (a card holder's name, a payment
            # date) are never read as items.
            inside = False
            current = None
            continue
        if not inside:
            continue
        qm = re.match(r"^(?:menge|st(?:ü|ue)ckzahl|anzahl)\s*:?\s*(\d{1,3})\b", ln, re.I)
        if qm and current is not None:
            current.quantity = qm.group(1)
            continue
        um = re.match(r"^preis\s+pro\s+einheit\s*:?\s*(.+)$", ln, re.I)
        if um and current is not None:
            current.unit_price = parse_money(um.group(1))
            continue
        if _MONEY_LINE_RE.match(ln) and current is not None:
            if not current.line_total:
                current.line_total = parse_money(ln)
            continue
        # A title is the line right before an item's price.
        priced = any(_MONEY_LINE_RE.match(nxt) for nxt in lines[i + 1:i + 3])
        name = _clean_item_name(ln) if priced else ""
        if name and (current is None or name.lower() != (current.name or "").lower()):
            current = Item(name=name[:300], quantity="1")
            items.append(current)
    return items


def extract_items(page, order_number: str = "") -> List[Item]:
    text = order_block(page, order_number)["text"] if order_number else ""
    if not text:
        try:
            text = page.locator("body").inner_text(timeout=8000)
        except Exception:
            text = ""
    items = items_from_text(text)
    if items:
        return items
    seen = set()
    try:
        for el in page.locator(FALLBACK["item_title"]).all():
            name = _clean_item_name(el.inner_text(timeout=1200) or "")
            if name and name.lower() not in seen:
                seen.add(name.lower())
                items.append(Item(name=name[:300], quantity="1"))
    except Exception:
        pass
    return items


_PLACED_DATE_RE = re.compile(r"bestellt\s+am\s*[\t:]?\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]{3,9}\.?\s*\d{4})", re.I)
_BLOCK_TOTAL_RE = re.compile(r"^gesamt\s*[\t:]?\s*(.+)$", re.I | re.M)
_SELLER_RE = re.compile(r"verkauft\s+von\s*[\t:]?\s*(\S+)", re.I)


def details_from_text(text: str, purchase: Purchase) -> Purchase:
    """Read one order's block:

        Bestellinformationen
        Bestellt am     12. Nov 2026, 14:30
        Bestellnummer   25-12345-67890
        Gesamt          EUR 99,99 (2 Artikel)
        Verkauft von    seller
    """
    m = _PLACED_DATE_RE.search(text)
    date = parse_date(m.group(1)) if m else None
    if date:
        purchase.purchase_date = date
    mm = _BLOCK_TOTAL_RE.search(text)
    total = parse_money(mm.group(1)) if mm else ""
    if total:
        purchase.total = total
    sm = _SELLER_RE.search(text)
    if sm:
        purchase.store_info = sm.group(1)
    # The card's status is the order's own. The details page has a
    # timeline ("Bezahlt", then "Zugestellt"), so it only fills one in.
    if not purchase.status:
        steps = [m.group(1) for m in STATUS_WORDS_RE.finditer(text or "")]
        if steps:
            purchase.status = parse_status(steps[-1])
    if re.search(r"bestellung\s+wurde\s+(?:storniert|abgebrochen)|"
                r"diese\s+bestellung\s+wurde\s+(?:storniert|abgebrochen)", text, re.I):
        purchase.status = "Storniert"
    return purchase


def extract_details(page, purchase: Purchase) -> Purchase:
    block = order_block(page, purchase.order_number)
    text = block.get("text") or ""
    details_from_text(text, purchase)
    items = items_from_text(text) or extract_items(page)
    if items:
        purchase.items = items
    purchase.receipt_url = page.url
    return purchase


def scroll_full_page(page, rounds: int = 2, delay_ms: int = 400) -> None:
    try:
        for _ in range(rounds):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(delay_ms)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass


def receipt_is_present(page) -> bool:
    """True when the current page is a filled-in order-details receipt."""
    try:
        body = page.locator("body").inner_text(timeout=8000)
    except Exception:
        return False
    return bool(body and HYDRATED_RE.search(body) and _PLACED_RE.search(body))


# The details block is .main-regions, or the smallest element holding
# both "Bestellinformationen" and "Zahlungsinformationen". Everything not
# on its ancestor path is hidden, and inside it every OTHER order's block,
# so a cart of two sellers prints as two receipts that each show their own
# order, the shared address and the shared payment. A live DOM display
# change only, discarded on the next navigation.
_ISOLATE_RECEIPT_JS = r"""
(oid) => {
  const NUM = %s;
  const smallest = (test) => {
    let best = null, bestLen = Infinity;
    for (const el of document.querySelectorAll('div,section,main,article')) {
      const t = el.innerText || '';
      if (t.length < bestLen && test(t)) { best = el; bestLen = t.length; }
    }
    return best;
  };
  let n = document.querySelector('.main-regions');
  if (!n) n = smallest(t => /Bestellinformationen/i.test(t) && /Zahlungsinformationen/i.test(t));
  if (!n) n = smallest(t => /Bestellnummer/i.test(t) && /Bestellt am/i.test(t));
  if (!n) return false;
  let el = n;
  while (el && el.parentElement && el !== document.body) {
    for (const s of Array.from(el.parentElement.children)) {
      if (s !== el) s.style.display = 'none';
    }
    el = el.parentElement;
  }
  const numsIn = (e) => { const s = new Set(); const rx = new RegExp(NUM.source, 'gi');
    const t = e.innerText || ''; let m; while ((m = rx.exec(t))) s.add(m[1]); return s; };
  let hidden = 0;
  if (oid && numsIn(n).size > 1) {
    // Each order's block: the largest element under n naming one number.
    const blocks = [];
    for (const e of n.querySelectorAll('*')) {
      const s = numsIn(e);
      if (s.size !== 1) continue;
      const p = e.parentElement;
      if (p && p !== n && numsIn(p).size === 1) continue;
      blocks.push([e, [...s][0]]);
    }
    for (const [e, num] of blocks) {
      if (num !== oid) { e.style.display = 'none'; hidden++; }
    }
  }
  const st = document.createElement('style');
  st.textContent = '.shipment-info, .shipment-containers, .shipment-container, .shipment-card, ' +
    '.shipment-card-row, .shipment-card-content, .item-container ' +
    '{ height: auto !important; max-height: none !important; overflow: visible !important; }';
  document.head.appendChild(st);
  for (const x of n.querySelectorAll('button')) x.style.display = 'none';
  const w = Math.max(n.scrollWidth, n.getBoundingClientRect().width);
  const zoom = Math.min(1, Math.max(0.45, 736 / (w + 16)));
  document.body.style.zoom = String(zoom);
  return true;
}
""" % _NUM_JS


NOT_FOUND_RE = re.compile(
    r"bestellung\s+(?:wurde\s+)?nicht\s+gefunden|bestellung\s+konnte\s+nicht\s+gefunden\s+werden|"
    r"fehler\s+beim\s+abrufen\s+(?:ihrer|der)\s+bestellung|"
    r"order\s+not\s+found|error\s+retrieving\s+your\s+order", re.I)


def order_not_found(page) -> bool:
    """True when eBay answers the details URL with its own "not found"
    page. The purchase history still lists the order, so its card is the
    record."""
    try:
        body = page.locator("body").inner_text(timeout=8000)
    except Exception:
        return False
    return bool(body and NOT_FOUND_RE.search(body) and not _PLACED_RE.search(body))


# The card for one order number on the purchase history page, everything
# else hidden, the same way the details block is isolated.
_ISOLATE_CARD_JS = r"""
(oid) => {
  const NUM = %s;
  const numsIn = (el) => { const s = new Set(); const rx = new RegExp(NUM.source, 'gi');
    const t = el.innerText || ''; let m; while ((m = rx.exec(t))) s.add(m[1]); return s; };
  let start = null;
  for (const el of document.querySelectorAll('div,li,section,article,tr,span,p')) {
    const s = numsIn(el);
    if (s.size === 1 && s.has(oid)) { if (!start || start.contains(el)) start = el; }
  }
  if (!start) return false;
  let n = start, el = start.parentElement;
  while (el && el !== document.body) {
    if (numsIn(el).size > 1) break;
    n = el;
    el = el.parentElement;
  }
  el = n;
  while (el && el.parentElement && el !== document.body) {
    for (const s of Array.from(el.parentElement.children)) {
      if (s !== el) s.style.display = 'none';
    }
    el = el.parentElement;
  }
  for (const x of n.querySelectorAll('button')) x.style.display = 'none';
  const w = Math.max(n.scrollWidth, n.getBoundingClientRect().width);
  const zoom = Math.min(1, Math.max(0.45, 736 / (w + 16)));
  document.body.style.zoom = String(zoom);
  window.scrollTo(0, 0);
  return true;
}
""" % _NUM_JS


def show_order_card(page, purchase: Purchase) -> bool:
    """Open the purchase history for the order's year and leave only this
    order's card on the page, for printing when the details page is gone."""
    year = None
    m = re.match(r"(\d{4})-", purchase.purchase_date or "")
    if m:
        year = int(m.group(1))
    try:
        goto_orders(page, year)
        ok = bool(page.evaluate(_ISOLATE_CARD_JS, purchase.order_number))
    except Exception as e:
        log.warning("Could not show the history card for %s: %s", purchase.order_number, e)
        return False
    if ok:
        page.wait_for_timeout(300)
    else:
        log.warning("No history card for %s in %s", purchase.order_number, year or "the history")
    return ok


def isolate_receipt(page, order_number: str = "") -> bool:
    """Strip the site chrome so printToPDF renders only this order's
    receipt, and scale the fixed-width layout to the page."""
    try:
        ok = bool(page.evaluate(_ISOLATE_RECEIPT_JS, order_number or ""))
    except Exception as e:
        log.warning("Receipt isolation failed: %s", e)
        return False
    if ok:
        page.wait_for_timeout(300)
    else:
        log.warning("Could not find the order details block to isolate")
    return ok


def open_receipt_section(page) -> bool:
    """Nothing to expand on eBay, the receipt is the page."""
    if not receipt_is_present(page):
        wait_for_hydration(page, timeout_ms=15000)
    return receipt_is_present(page)


# ---------------------------------------------------------------------------
# The invoice ("Rechnung ansehen")
# ---------------------------------------------------------------------------

INVOICE_READY_RE = re.compile(r"rechnungsdetails|mwst\.?-aufschl(?:ü|ue)sselung", re.I)


def on_invoice_page(page) -> bool:
    try:
        parts = urlsplit(page.url or "")
    except ValueError:
        return False
    return is_safe_url(page.url or "") and parts.path.rstrip("/") == "/ord/show/taxInfo"


def goto_invoice(page, url: str, timeout_ms: int = 30000) -> bool:
    """Open the invoice and wait for it to fill in. False when the address
    is not eBay's, when eBay sends it somewhere else (a seller's own
    invoice, skipSellerRedirect), or when it never fills in."""
    if not is_safe_url(url or ""):
        return False
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if not on_invoice_page(page):
        log.info("The invoice address went elsewhere: %s", redact(page.url or ""))
        return False
    try:
        page.wait_for_function(
            "() => /Rechnungsdetails|MwSt\\.?-Aufschl(ü|ue)sselung/i.test(document.body.innerText)",
            timeout=timeout_ms)
        page.wait_for_timeout(800)
    except Exception:
        log.warning("The invoice did not fill in within %dms", timeout_ms)
    return invoice_is_present(page)


def invoice_is_present(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=8000)
    except Exception:
        return False
    return bool(body and INVOICE_READY_RE.search(body))


_ISOLATE_INVOICE_JS = r"""
() => {
  const smallest = (test) => {
    let best = null, bestLen = Infinity;
    for (const el of document.querySelectorAll('div,section,main,article')) {
      const t = el.innerText || '';
      if (t.length < bestLen && test(t)) { best = el; bestLen = t.length; }
    }
    return best;
  };
  let n = smallest(t => /Rechnung/.test(t) && /Rechnungsdetails/i.test(t) && /MwSt/i.test(t));
  if (!n) n = smallest(t => /Rechnungsdetails/i.test(t));
  if (!n) n = document.querySelector('.main-regions') || document.querySelector('main');
  if (!n) return false;
  let el = n;
  while (el && el.parentElement && el !== document.body) {
    for (const s of Array.from(el.parentElement.children)) {
      if (s !== el) s.style.display = 'none';
    }
    el = el.parentElement;
  }
  for (const x of n.querySelectorAll('button')) x.style.display = 'none';
  const w = Math.max(n.scrollWidth, n.getBoundingClientRect().width);
  const zoom = Math.min(1, Math.max(0.45, 736 / (w + 16)));
  document.body.style.zoom = String(zoom);
  window.scrollTo(0, 0);
  return true;
}
"""


def isolate_invoice(page) -> bool:
    try:
        ok = bool(page.evaluate(_ISOLATE_INVOICE_JS))
    except Exception as e:
        log.warning("Invoice isolation failed: %s", e)
        return False
    if ok:
        page.wait_for_timeout(300)
    return ok


# --- receipt-access hooks eBay does not need -------------------------------

def find_print_receipt_controls(page) -> list:
    """Capture is navigation plus printToPDF, "Seite drucken" is not needed."""
    return []


def find_invoice_controls(page) -> list:
    """The invoice is opened by its address, nothing is pressed."""
    return []


def find_printing_frame(page, wait_ms: int = 2000):
    return None


def find_receipt_iframe(page):
    return None


# ---------------------------------------------------------------------------
# Host allowlist. Parsed, never a string prefix.
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = {"ebay.de"}


def is_safe_url(url: str) -> bool:
    """True only for an https URL on one of this provider's own hosts."""
    return _host_allows(url, ALLOWED_HOSTS)
