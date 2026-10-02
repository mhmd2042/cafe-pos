# Café POS — نظام نقاط البيع للمقهى

A standalone, offline-first desktop point-of-sale and accounting application for a
small-to-medium café. **Windows & Linux, Python 3 + PyQt6 + SQLite.**

**100% offline.** No cloud, no API server, no telemetry, no internet connection at any
point — including the reports, the backups and the receipt printing.

**Status: complete (v1.0.0).** All five phases built and verified — **314 automated
checks pass** plus the database self-test, across 14,500 lines of Python in 45 files.

---

## Quick start

```bash
cd cafe_pos
./run.sh                    # launch on the local display using the project venv
```

If `.venv` does not exist yet:

```bash
cd cafe_pos
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Other ways to run:

```bash
./run.sh --reset            # wipe the local database and rebuild it, then start
./run.sh --selftest         # headless database + auth check, no window
./run.sh --screenshot out.png   # render the login screen to a PNG and exit
```

> `run.sh` selects the Qt platform automatically (Wayland when the session provides
> it, otherwise X11/xcb). If you ever need to force one:
> `QT_QPA_PLATFORM=xcb ./run.sh`

### Default logins

| User | PIN | Role |
|---|---|---|
| `admin` | `1234` | المدير — full access |
| `cashier` | `1111` | الكاشير — selling only |

Both are flagged `must_change_pin`, so the app **forces a new PIN at first login**. The
PIN policy rejects repeated digits (`1111`) and common sequences (`1234`), so the
defaults cannot survive first use.

---

## What it does

### Cashier — selling
* **Unified single-flow ordering** — no table management; in-café and takeaway are one
  toggle. Orders are processed immediately.
* **Category rail + touch product grid** (a responsive flow layout; every tile ≥150×96 px)
  + **live cart** with +/− steppers and one-tap delete.
* **Modifiers popup** — size, milk type, sugar level, extra shots, add-ons, free-text
  notes. Required groups are pre-selected; multi-choice groups cap at their limit.
* **Tax-inclusive pricing throughout** — the price on the button is what the customer
  pays. Tax is *derived* for reporting only; there is no tax step at checkout.
* **Cash with a change calculator** (quick-tender buttons, live الباقي, refuses short
  payment) **or card**.
* **Thermal receipt printing** — ESC/POS. Arabic is rasterised to 1-bit at the head
  width because thermal printers' built-in fonts have no Arabic glyphs.
* **Shift-gated** — the checkout button is disabled until a shift is open.

### Shift management
* **Mandatory open-shift dialog** on login, asking for the opening cash float.
  Cancelling logs the user out rather than leaving an unusable till.
* **Close shift** — shows the expected drawer, takes the counted amount, computes the
  over/short, writes the **Z-Report**, runs the backup, then logs the user out.
* Over/short is labelled in words (`نقص 700 ر.ي` / `زيادة 300 ر.ي` / `مطابق`) — a bare
  signed number is ambiguous and a cashier needs to know instantly which it is.

### Admin — dashboard with five sections
* **Reports & analytics** — daily / weekly / monthly, per-day sales series, top-selling
  items, **peak hours** (24 zero-filled buckets), category split, cash vs card, and COGS
  from recipes with a gross-margin figure.
* **Menu & modifiers editor** — full CRUD for categories, products (price, cost, SKU,
  enable/disable), modifier groups and their options; a checklist of which groups each
  product offers; live margin display. Price changes are audited with before → after.
* **Inventory & recipes** — raw ingredients with units, reorder thresholds and low-stock
  alerts; recipe editor; **automatic deduction on every sale**; a complete stock ledger
  with receive / waste / stocktake actions.
* **Backup manager** — detected-drive picker, manual backup, history table, printer test.
* **Settings** — café identity, tax rate, printer configuration, system toggles, a
  database integrity check.

### Roles & permissions
Two roles only, expressed once in `models/user.py::CAPABILITIES`. Views call
`user.can("edit_prices")` rather than comparing role strings.

| Capability | Cashier | Admin |
|-----------|:-------:|:-----:|
| `create_order`, `take_payment`, `open_shift`, `close_shift` | ✅ | ✅ |
| `apply_discount`, `void_order` | — | ✅ |
| `edit_prices`, `manage_menu`, `manage_modifiers` | — | ✅ |
| `view_reports`, `view_profit`, `view_shift_audit` | — | ✅ |
| `manage_inventory`, `manage_backups`, `manage_users`, `manage_settings` | — | ✅ |

A cashier cannot see cost, margin or profit anywhere — `Product.margin_minor` exists but
is only ever rendered inside the admin pages.

---

## Data safety

* **Backups use SQLite's online backup API**, not a file copy — a snapshot taken
  mid-sale is consistent and includes WAL content. Each copy is verified with
  `PRAGMA quick_check` and deleted if it fails.
* **External drive, with a safe fallback:**

| Situation | Status | Behaviour |
|---|---|---|
| External target reachable | `ok` | Written to the drive **and** a secondary local copy kept |
| External drive unplugged | `fallback` | Local copy kept, gentle Arabic notice, sale unaffected |
| Target path invalid / permission denied | `fallback` | Same degradation — never raises |
| No target configured | `ok` | Local copy only, with an explanatory message |

* **Local copies are pruned** to `backup_keep_local` (default 30).
* **Everything is audited** — `audit_log` records logins, failed logins, logouts, PIN
  changes, shift open/close (with expected/counted/difference), every menu and price
  edit, every settings change, and every backup attempt; `backup_log` records status,
  size and target.
* **The stock ledger fully explains stock** — opening balances get a ledger row, so
  `theoretical_stock()` drift is always zero and any unexplained movement is visible.
* **Restore is deliberately manual.** `BackupController.restore_hint()` prints
  copy-the-file instructions. There is no one-tap restore, because overwriting a live
  till database from inside the app is how a day's sales get lost to a mis-tap.

---

## Repository layout

```
cafe_pos/
├── run.sh                     # launcher (uses .venv, picks the Qt platform)
├── main.py                    # entry point: GUI, --selftest, --screenshot, --reset
├── config.py                  # constants, money/tax helpers, theme palettes
├── requirements.txt
├── database/
│   ├── db_manager.py          # connections, transactions, backups, migrations
│   └── schema.sql             # 16 tables + idempotent seed data (schema v4)
├── models/                    # business logic — no Qt imports anywhere in this layer
│   ├── user.py                # User, CAPABILITIES, UserRepository
│   ├── product.py             # Category, Product, Modifier, ProductRepository
│   ├── order.py               # Cart, CartLine, OrderRepository (all money maths)
│   ├── shift.py               # Shift, ShiftTotals, ShiftRepository
│   └── inventory.py           # ingredients, recipes, deduction, stock ledger
├── controllers/               # glue — also Qt-free
│   ├── auth_controller.py     # login, session, permissions, audit
│   ├── pos_controller.py      # menu, cart, checkout, receipts, stock deduction
│   ├── shift_controller.py    # open/close workflow, Z-Report + backup orchestration
│   ├── backup_controller.py   # online backups, drive detection, fallback, retention
│   └── admin_controller.py    # menu CRUD, reporting, exports, settings
├── views/
│   ├── theme.py               # QSS token substitution + Arabic font selection
│   ├── widgets.py             # Toast, FlowLayout, QuantityStepper, Divider
│   ├── login_view.py          # PIN pad login + forced PIN change
│   ├── main_window.py         # shell, header, role routing, shift gate
│   ├── cashier/               # pos_main · modifier_dialog · cash_dialog · shift_dialog
│   └── admin/                 # dashboard · reports · menu_editor · inventory ·
│                              # backup_view · settings_view · charts
├── services/
│   ├── printer_service.py     # ESC/POS raster printing + file fallback
│   └── report_exporter.py     # Z-Reports (.txt/.csv) + sales exports
├── tests/                     # 4 test suites + 4 render scripts
├── assets/
│   ├── styles/                # cafe_dark.qss, cafe_light.qss
│   ├── icons/
│   └── database/cafe_pos.db   # the live database
├── backups/local/             # fallback copies (+ reports/ mirrored alongside)
└── logs/
    ├── cafe_pos.log
    ├── receipts/              # .txt + .png receipts when no printer is attached
    └── reports/               # Z-Reports and sales exports
```

---

## Verifying it

```bash
cd cafe_pos
.venv/bin/python main.py --selftest                 # database + auth self-test
.venv/bin/python tests/test_phase2_ui.py            # login/auth UI flow
.venv/bin/python tests/test_phase3_pos.py           # POS, cart, checkout, printing
.venv/bin/python tests/test_phase4_shift_backup.py  # shifts, Z-Reports, backups
.venv/bin/python tests/test_phase5_admin.py         # admin, menu, inventory, reports
```

All suites run **headless** (`QT_QPA_PLATFORM=offscreen`) against a scratch database, so
they never touch live till data.

Render scripts produce PNGs you can look at:

```bash
.venv/bin/python tests/render_screens.py            # login screens
.venv/bin/python tests/render_pos.py                # POS, dialogs, sample receipt
.venv/bin/python tests/render_shift.py              # shift dialogs + a real Z-Report
.venv/bin/python tests/render_admin.py              # admin pages + printable report
```

Measured: cold start **~360–630 ms** (budget 3000 ms).

---

## Design decisions worth knowing

1. **Money is integer minor units, never float.** For YER (no subunit in use) the base
   unit is a whole rial, so `2,500 ر.ي` is stored as `2500` and shown with no decimals.
   Set `MINOR_UNITS_PER_MAJOR = 100` + `MONEY_DECIMALS = 2` for a subdivided currency
   (SAR, AED, USD).
2. **Tax-inclusive, tax derived.** `orders.total_minor` is what the customer pays;
   `orders.tax_minor` is the VAT portion *already inside it*. At the configured 0% rate
   the tax row is hidden rather than shown as a meaningless zero.
3. **Single till.** A partial unique index allows only one open shift at a time.
4. **Arabic-first RTL**, mirrored from the spec's LTR description: the category rail sits
   at the reading start (right) and the cart on the far left.
   `POS_LAYOUT_MIRROR_RTL = False` forces the literal LTR placement.
5. **No charting library.** The bar charts are hand-drawn with `QPainter` — matplotlib
   would add ~40 MB and needs explicit Arabic font registration.
6. **Reports export to HTML, not PDF.** reportlab would need an Arabic font embedded and
   registered; any browser prints the HTML to PDF offline with no extra dependency.
7. **Deduction never blocks a sale.** It runs after the order is committed, in its own
   transaction, and swallows its own errors — a stock-table problem must not cost the
   café a sale.
8. **Layer discipline.** `models/` and `controllers/` contain no Qt imports, which is why
   the whole money/auth/stock/reporting layer is testable without a display.

### RTL / Arabic Qt notes (learned the hard way)

* `QGridLayout` mirrors columns under `RightToLeft`, which renders a numeric pad as
  `3 2 1`. Keypads opt out with `setLayoutDirection(LeftToRight)`.
* Mixed Latin/digit runs get bidi-reordered — `admin / 1234` displays as `1234 / admin`.
  Wrap them in isolates: `\u2066…\u2069`.
* `⌫` (U+232B) and `✓` (U+2713) are absent from the Arabic font fallbacks and render as
  blank keys — use words (`حذف`, `دخول`).
* `QTableWidget.selectRow()` is a no-op in PyQt6 6.11; use `setCurrentCell(row, col)`.
* Painting on `QImage.Format_Mono` produces a solid black bitmap — paint on
  `Format_Grayscale8` and convert at the end.
* Size modal dialogs to the screen and assert it; a dialog taller than the display makes
  its confirm button unreachable.

---

## Dependencies

Phase 1 is stdlib-only. The application needs **PyQt6** and nothing else.

```
PyQt6>=6.6,<7.0
```

Optional, only when the corresponding hardware exists:

```
python-escpos  + pyusb / pyserial   # direct USB/serial ESC/POS printing
```

CUPS (`lp`) is used automatically on Linux if a printer is configured and
python-escpos is not installed. With no printer at all, receipts are written to
`logs/receipts/` as a `.txt` plus a `.png` of exactly what would have been printed.
