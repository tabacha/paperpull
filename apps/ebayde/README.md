# eBay.de Receipts Downloader (local, supervised)

Downloads your **ebay.de** purchase history (Mein eBay > Käufe) and saves

- each order's **order-details page** ("Einzelheiten zum Kauf") as a PDF
  receipt, in `Online\`
- each **invoice** eBay offers on an order ("Rechnung ansehen"), as a PDF
  in `Rechnungen\`
- two CSV files, `eBay DE Order History.csv` (one row per item) and
  `eBay DE Receipt Index.csv` (one row per PDF)

This is the German sibling of [`ebay`](../ebay), which reads ebay.com.
Every text on ebay.de is German, the hosts are `www.ebay.de` and
`order.ebay.de`, dates read `12. Mai. 2026`, amounts `EUR 1.234,56`, so it
is an app of its own rather than a setting of the other one.

Everything runs **locally**. Nothing is sent to any external AI API or
third-party service. You sign in to eBay **manually**. The tool never
touches your credentials and never bypasses CAPTCHAs or OTP.

## How far back this goes

Käufe is filtered by year ("Letzte 60 Tage", then one entry per year). The
unfiltered list shows this year and the two before it. Discovery reads it,
then walks the year filter, ebay.de's own words (`CURRENT_YEAR`,
`LAST_YEAR` … `NINE_YEARS_AGO`, not ebay.com's `THIS_YEAR`), and stops
after two empty years in a row. The plain address shows whichever filter
was chosen last, so the app always names one.

To narrow what gets downloaded, set `default_start_date` in `config.json`
(for example `"2025-01-01"`) or pass `--start-date 2025-01-01` or
`--year 2026`.

## How it connects

`login.bat` / `login.command` opens your installed Edge or Chrome (or the
bundled Chromium) with debugging port **9304** and a profile folder of its
own. **You** sign in. The tool then connects to that browser and reads the
pages you are allowed to see. **Keep that window open while it runs**, and
do not use the tab the run works in.

Put `profile_dir` (the browser profile) and `output_dir` (your documents)
**outside** the repository in your `config.json`. The progress of a run is
kept in `output_dir`, so a new `output_dir` starts from the beginning.

## Workflow

| Step | Command | What it does |
|------|---------|--------------|
| 1 | `setup.bat` | Creates the environment |
| 2 | `login.bat` | Opens the browser. Sign in, open Käufe, **leave it open** |
| 3 | `paperpull ebayde diagnose` | Writes what the pages hold, structure only, into `Diagnostics\` |
| 4 | `paperpull ebayde pilot` | Newest five orders, then **stops** for your inspection |
| 5 | `paperpull ebayde all` | The whole purchase history (asks for `yes`) |
| any time | `paperpull ebayde resume` | Continue after an interruption, never redoes finished work |
| any time | `paperpull ebayde verify` | Re-validate every indexed PDF |
| any time | `paperpull ebayde review-names` | Fix low-confidence filenames |

## Receipts

The order-details page **is** the receipt. The tool opens
`order.ebay.de/ord/show?orderId=…`, waits for it to fill in, hides
everything but the details block (Bestellinformationen, Lieferinformationen,
Artikelinformationen, Lieferadresse, Zahlungsinformationen), scales it to
the page width and prints it with Chromium's `printToPDF`. Files are named
`YYYY-MM-DD eBay DE <Category> Receipt.pdf`.

**A cart from several sellers** can put several orders on one details
page. Every order is still its own receipt: the card is known by its
Bestellnummer, and when it is printed every other order's block on that
page is hidden, so each PDF shows its own order with the shared address and
payment.

## Invoices ("Rechnung ansehen")

**eBay asks for a fresh sign-in before it shows an invoice**, seconds after
an ordinary page, even when the rest of the site is signed in. The run
stops there and says so ("eBay asks for a fresh sign-in before it shows this
invoice"). Sign in on the page eBay shows in the browser, then `resume`.
After one fresh sign-in, a whole run's invoices (five, over some fifteen
minutes) came without another question on 2026-11-08.

Some orders carry an invoice that eBay issues itself (eBay GmbH, with a
VAT breakdown), at `order.ebay.de/ord/show/taxInfo`. It is an HTML page,
printed to PDF the same way, as `YYYY-MM-DD eBay DE <Category> Invoice.pdf`
in `Rechnungen\`. Set `"include_invoices": false` to skip them.

An invoice that is listed but does not show is said so plainly in the run,
and `resume` asks for it again, up to three times. If it keeps failing,
open "Rechnung ansehen" on ebay.de yourself: if eBay cannot show it either,
it is not the app.

## Sign-outs and session length

Measured on 2026-11-08: no idle sign-out in some forty minutes of runs at
the default 2 to 4.5 s between orders. Every run keeps measuring it: when it is
signed out, the log and the console say how many minutes into the run and
how many seconds after the last page it happened. Nothing is done to keep a
session alive (no reload, no token refresh, no input). The run stops at a
sign-out, and after you sign in again `resume` carries on.

## eBay's daily limit

eBay allows one account a few hundred order-details pages a day. The app
recognizes the limit page by its address (`limitexceeded`) and its words,
stops with its progress saved, and `resume` continues the next day.

## Not seen yet

The German wording of the daily-limit page, "order not found", a canceled
order and an empty history were not on the account this was mapped
against. They are matched by eBay's usual phrasing. When you meet one, run
`diagnose` and repair `ebayde_site.py`.

## When eBay changes its website

All selectors and URLs live in **`ebayde_site.py`** only.

## Safety

- Read-only on eBay. Never bids, buys, pays, returns, cancels, leaves a
  rating, contacts a seller, hides an order or changes a setting. Forbidden
  controls are refused by a regex that knows the German verbs ("Erneut
  kaufen", "Bieten", "Bezahlen", "Bewertung abgeben", "Verkäufer
  kontaktieren", "Bestellung ausblenden", …) as well as the English ones,
  and nothing is clicked but a "Mehr anzeigen" the list offers.
- Stops and hands control to you on CAPTCHA/OTP, sign-out or rate limiting.
- Sequential, with polite randomized delays.
- Progress written atomically after every order. Existing PDFs are never
  overwritten.

## Tests

```
..\..\gui\.venv\Scripts\python -m pytest tests
```
