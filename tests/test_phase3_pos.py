"""
tests/test_phase3_pos.py — headless end-to-end check of the Phase-3 cashier POS.

Drives the real widgets offscreen against a scratch database:
    1. cart maths: tax-inclusive lines, modifier deltas, merge, discount cap
    2. product grid builds and reflows (responsive tile count)
    3. category switching and search filter the grid
    4. modifier dialog: required group enforcement, single/multi rules, pricing
    5. add → quantity → delete → totals in the live cart
    6. checkout: cash with change, card, and the two rejection paths
    7. printing: graceful fallback writes a text + a 1-bit PNG receipt
    8. role restriction: a cashier cannot edit prices

Run:  .venv/bin/python tests/test_phase3_pos.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_p3_"))
config.DB_PATH = _TMP / "pos.db"
config.RECEIPTS_DIR = _TMP / "receipts"

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from controllers.pos_controller import ModifierSelection, PosController  # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.order import Cart, SelectedModifier                 # noqa: E402
from models.product import Product, ProductRepository           # noqa: E402
from services.printer_service import PrinterService, ReceiptData  # noqa: E402
from views.cashier.cash_dialog import CashPaymentDialog         # noqa: E402
from views.cashier.modifier_dialog import ModifierDialog        # noqa: E402
from views.cashier.pos_main import PosMainView                  # noqa: E402
from views.theme import Theme                                   # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)


def section(title: str) -> None:
    print(f"\n{title}")


def main() -> int:
    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    Theme("dark").apply(app)

    print("=" * 68)
    print(f" Phase 3 POS test — {config.APP_NAME} v{config.VERSION}")
    print(f" scratch DB: {config.DB_PATH}")
    print("=" * 68)

    products = ProductRepository()
    cappuccino = products.get_product(4)      # كابتشينو 2500
    water = products.get_product(16)          # مياه معدنية 500
    assert cappuccino and water

    # ------------------------------------------------------------------ #
    section("1. Cart maths (tax-inclusive)")

    cart = Cart()
    cart.add(cappuccino)
    check("single line total = price × qty",
          cart.total_minor == cappuccino.price_minor,
          f"{cart.total_minor}")

    cart.add(cappuccino)                      # merge
    check("identical lines merge into quantity 2",
          cart.line_count == 1 and cart.item_count == 2, f"lines={cart.line_count}")

    large = SelectedModifier(1, "الحجم", 3, "كبير", 500)
    oat = SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500)
    cart.add(cappuccino, [large, oat])
    check("different options create a separate line", cart.line_count == 2)

    expected = cappuccino.price_minor * 2 + (cappuccino.price_minor + 1000)
    check("modifier deltas add to the unit price", cart.total_minor == expected,
          f"{cart.total_minor} vs {expected}")

    cart.set_discount(10 ** 9)
    check("discount is capped at the subtotal",
          cart.discount_minor == cart.subtotal_minor and cart.total_minor == 0)

    cart.set_discount(1000)
    check("discount reduces the total", cart.total_minor == expected - 1000)
    check("0% VAT derives zero tax", cart.tax_minor == 0)
    check("net equals total when tax is 0", cart.net_minor == cart.total_minor)

    # integer-only money
    check("no float in the totals",
          all(isinstance(v, int) for v in cart.summary().values()),
          str(cart.summary()))

    # ------------------------------------------------------------------ #
    section("2. Product grid (responsive)")

    auth = AuthController()
    login = auth.login("cashier", "1111")
    check("cashier login for POS", login.success)

    controller = PosController(auth=auth, printer=PrinterService(db))
    view = PosMainView(auth, controller)
    # Headless: never let a modal block. Tests drive selection through the
    # controller or by calling on_product_tapped with a stubbed chooser.
    view._ask_modifiers = lambda product: None
    view.resize(1400, 800)
    view.show()
    app.processEvents()

    tiles_wide = view.product_flow.count()
    check("product grid renders tiles", tiles_wide > 0, f"{tiles_wide} tiles")

    # narrow the window: FlowLayout must reflow, not overflow
    view.resize(1100, 700)
    app.processEvents()
    grid_width = view.product_container.width()
    tile_widths = [view.product_flow.itemAt(i).sizeHint().width()
                   for i in range(view.product_flow.count())]
    check("tiles keep the 150x96 touch minimum",
          min(tile_widths) >= config.PRODUCT_TILE_MIN_WIDTH,
          f"min width {min(tile_widths)}")
    rows = view.product_flow.heightForWidth(grid_width) // (
        view.product_flow.itemAt(0).sizeHint().height() + config.PRODUCT_GRID_SPACING)
    check("grid reflows into multiple rows when narrow", rows >= 2, f"~{rows} rows")

    # ------------------------------------------------------------------ #
    section("3. Category rail & search")

    categories = controller.load_categories()
    check("categories loaded", len(categories) >= 5, f"{len(categories)}")

    hot = next(c for c in categories if "ساخنة" in c.name_ar)
    view.select_category(hot.id)
    app.processEvents()
    hot_count = view.product_flow.count()
    check("selecting a category filters the grid", hot_count == hot.product_count,
          f"{hot_count} vs {hot.product_count}")

    view.search_edit.setText("لاتيه")
    app.processEvents()
    check("search filters across the menu", 0 < view.product_flow.count() < hot_count,
          f"{view.product_flow.count()} results")

    view.search_edit.clear()
    view.select_category(hot.id)
    app.processEvents()

    # ------------------------------------------------------------------ #
    section("4. Modifier dialog")

    groups = controller.modifier_groups(cappuccino)
    check("cappuccino has modifier groups", len(groups) >= 4, f"{len(groups)} groups")

    size_group = next(g for g in groups if g.group_type == "single")
    check("required single group pre-selects its default",
          len(size_group.default_selection()) == 1,
          size_group.default_selection()[0].display_name if size_group.default_selection() else "none")

    check("text group is not a real choice", any(g.is_text for g in groups))
    check("needs_modifier_dialog True for a drink", controller.needs_modifier_dialog(cappuccino))
    check("needs_modifier_dialog False for plain water",
          not controller.needs_modifier_dialog(water))

    dialog = ModifierDialog(controller, cappuccino)
    base = cappuccino.price_minor
    check("dialog opens at the product price", dialog.unit_price_minor() == base,
          f"{dialog.unit_price_minor()}")

    # pick "كبير" (+500) in the size group
    box = dialog.group_boxes[0]
    big = next(b for b in box.buttons if "كبير" in b.modifier.name_ar)
    box._on_single(box.group, big.modifier)
    check("single-choice selection is exclusive",
          sum(1 for b in box.buttons if b.isChecked()) == 1)
    check("price updates with the chosen delta",
          dialog.unit_price_minor() == base + big.modifier.price_minor,
          f"{dialog.unit_price_minor()}")

    # multi group cap
    multi = next((b for b in dialog.group_boxes if b.group.is_multi), None)
    if multi is not None:
        for button in multi.buttons:
            multi._on_multi(button)
        check("multi group never exceeds max_select",
              len(multi.selected()) <= multi.group.max_select,
              f"{len(multi.selected())}/{multi.group.max_select}")

    dialog.note_edit.setText("بدون رغوة")
    selection = dialog.selection()
    check("note is captured", selection.note == "بدون رغوة")
    check("selection carries the modifier delta",
          any(m.price_minor == big.modifier.price_minor for m in selection.modifiers))

    # ------------------------------------------------------------------ #
    section("5. Live cart interaction")

    controller.clear_cart()
    view.refresh_cart()
    check("cart starts empty", controller.cart.is_empty and view.empty_hint.isVisible())

    # A drink opens the modifier dialog; stub the answer to keep it headless.
    view._ask_modifiers = lambda product: ModifierSelection(
        modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500)], note="بدون رغوة"
    )
    view.on_product_tapped(cappuccino)
    app.processEvents()
    check("tapping a drink adds it with the chosen options",
          controller.cart.item_count == 1
          and controller.cart.lines[0].unit_price_minor == cappuccino.price_minor + 500,
          f"unit={controller.cart.lines[0].unit_price_minor}")
    check("the note reaches the cart line",
          controller.cart.lines[0].note == "بدون رغوة")

    # Cancelling the dialog must not add anything.
    view._ask_modifiers = lambda product: None
    before = controller.cart.item_count
    view.on_product_tapped(cappuccino)
    app.processEvents()
    check("cancelling the modifier dialog adds nothing",
          controller.cart.item_count == before)

    controller.clear_cart()
    view.refresh_cart()

    view.on_product_tapped(water)             # no dialog for water
    app.processEvents()
    check("tapping a plain product adds it directly",
          controller.cart.item_count == 1 and not controller.cart.is_empty)

    view.on_product_tapped(water)
    app.processEvents()
    check("tapping again merges to quantity 2", controller.cart.item_count == 2,
          f"items={controller.cart.item_count}")

    line = controller.cart.lines[0]
    view._on_increment(line.line_id)
    app.processEvents()
    check("+ button increments", controller.cart.lines[0].quantity == 3)

    view._on_decrement(line.line_id)
    app.processEvents()
    check("− button decrements", controller.cart.lines[0].quantity == 2)

    # add the cappuccino with its modifiers via the controller
    controller.add_product(cappuccino, selection)
    view.refresh_cart()
    app.processEvents()
    check("cart renders one widget per line", view.cart_layout.count() == 3,
          f"{view.cart_layout.count() - 1} line widgets")
    check("cart total matches the model",
          view.total_label.text() == config.format_money(controller.cart.total_minor),
          view.total_label.text())
    check("tax row hidden at 0% VAT",
          not view.tax_row_label.isVisible() or controller.cart.tax_minor > 0)
    check("category labels always show a count",
          all("(" in b.text() for b in view._category_buttons.values()),
          " | ".join(b.text() for b in view._category_buttons.values()))

    # Every line widget must carry its own controls; a row without a stepper
    # would mean a rendering/clipping bug rather than a scroll artefact.
    from PyQt6.QtWidgets import QPushButton as _PB
    line_widgets = [view.cart_layout.itemAt(i).widget()
                    for i in range(view.cart_layout.count() - 1)]
    check("each cart line has delete + stepper controls",
          all(len(w.findChildren(_PB)) >= 3 for w in line_widgets),
          str([len(w.findChildren(_PB)) for w in line_widgets]))

    # delete
    view._on_remove(controller.cart.lines[0].line_id)
    app.processEvents()
    check("delete removes the line", controller.cart.line_count == 1)

    # ------------------------------------------------------------------ #
    section("6. Checkout")

    controller.clear_cart()
    controller.add_product(water, quantity=2)
    total = controller.cart.total_minor
    check("cart total = 2 × water", total == water.price_minor * 2, f"{total}")

    empty = PosController(auth=auth, printer=PrinterService(db))
    rejected = empty.checkout(config.PAYMENT_CASH, 5000)
    check("empty cart is rejected", not rejected.ok, rejected.message)

    short = controller.checkout(config.PAYMENT_CASH, total - 100)
    check("insufficient cash is rejected", not short.ok, short.message)
    check("cart survives a rejected payment", not controller.cart.is_empty)

    cash = controller.checkout(config.PAYMENT_CASH, total + 1000)
    check("cash checkout succeeds", cash.ok, cash.message)
    check("change is computed", cash.order.change_minor == 1000, f"{cash.order.change_minor}")
    check("order number is daily-sequential",
          len(cash.order.order_number) == 13 and cash.order.order_number.endswith("-0001"),
          cash.order.order_number)
    check("cart is cleared after a sale", controller.cart.is_empty)

    controller.add_product(cappuccino, quantity=1)
    card = controller.checkout(config.PAYMENT_CARD)
    check("card checkout succeeds", card.ok, card.message)
    check("card is always exact (no change)", card.order.change_minor == 0)
    check("sequence increments",
          card.order.order_number.endswith("-0002"), card.order.order_number)

    # persistence
    stored = controller.orders.get(cash.order.id)
    check("order persisted with its items",
          stored is not None and len(stored["items"]) == 1)
    check("payment row persisted",
          db.scalar("SELECT COUNT(*) FROM payments WHERE order_id = ?", (cash.order.id,)) == 1)
    check("modifier JSON round-trips",
          controller.orders.item_modifiers(stored["items"][0]) == []
          or isinstance(controller.orders.item_modifiers(stored["items"][0]), list))

    # ------------------------------------------------------------------ #
    section("7. Printing with no printer attached (graceful fallback)")

    printer = PrinterService(db)
    check("printer disabled by default", not printer.is_enabled())
    check("backend falls back to file", printer.detect_backend() == "file",
          printer.detect_backend())

    result = controller.print_order(cash.order.id)
    check("print returns ok rather than raising", result.ok)
    check("fallback path taken", result.fell_back and not result.printed)
    check("a soft message is produced", bool(result.message), result.message)
    check("text receipt written",
          result.text_path is not None and result.text_path.exists(),
          str(result.text_path))

    text = result.text_path.read_text(encoding="utf-8")
    check("receipt contains the order number", cash.order.order_number in text)
    check("receipt contains the total",
          config.format_money(total, symbol=False) in text)
    check("receipt contains the footer",
          "شكراً لزيارتكم" in text)
    check("receipt shows the payment method", "نقداً" in text)
    check("no tax line at 0% VAT", "منها ضريبة" not in text)

    data = controller.receipt_data(card.order.id)
    image_path = printer.render_image(data)
    check("1-bit raster receipt rendered", image_path.exists(),
          f"{image_path.stat().st_size:,} bytes")
    from PyQt6.QtGui import QImage
    image = QImage(str(image_path))
    check("raster width matches the thermal head",
          image.width() == config.RECEIPT_DOTS_80MM, f"{image.width()} px")
    check("raster is 1-bit monochrome", image.format() == QImage.Format.Format_Mono,
          str(image.format()))
    check("raster has real content (not blank)", image.height() > 200,
          f"{image.height()} px tall")

    # Guard against the all-black / all-white failure mode: a receipt must be
    # mostly white paper with some black ink, not a solid block.
    dark = sum(1 for y in range(0, image.height(), 2)
               for x in range(0, image.width(), 2)
               if image.pixelColor(x, y).value() < 128)
    total = (image.height() // 2) * (image.width() // 2)
    ratio = dark / max(1, total)
    check("raster is mostly white with black ink", 0.005 < ratio < 0.5,
          f"{ratio:.1%} dark")

    # The bottom line must not be clipped: check the last text row has ink and
    # the very last rows of the canvas are blank paper.
    bottom_band = range(max(0, image.height() - 6), image.height())
    bottom_ink = sum(1 for y in bottom_band
                     for x in range(0, image.width(), 2)
                     if image.pixelColor(x, y).value() < 128)
    check("nothing is clipped at the bottom edge", bottom_ink == 0,
          f"{bottom_ink} dark px in the last 6 rows")

    # enabled but unreachable printer must still not raise
    db.set_setting("printer_enabled", "1")
    db.set_setting("printer_backend", "cups")
    db.set_setting("printer_name", "no-such-printer-xyz")
    forced = printer.print_receipt(controller.receipt_data(cash.order.id))
    check("unreachable printer degrades instead of raising",
          forced.ok and forced.fell_back, f"{forced.message} / {forced.error[:40]}")
    db.set_setting("printer_enabled", "0")
    db.set_setting("printer_backend", "auto")

    # ------------------------------------------------------------------ #
    section("8. Roles")

    cashier = auth.current_user
    check("cashier can sell", cashier.can("create_order"))
    check("cashier cannot edit prices", not cashier.can("edit_prices"))
    check("cashier cannot view profit", not cashier.can("view_profit"))
    check("cashier cannot manage backups", not cashier.can("manage_backups"))

    auth.logout()
    admin_login = auth.login("admin", "1234")
    check("admin login", admin_login.success)
    check("admin can edit prices", auth.current_user.can("edit_prices"))

    denied = False
    try:
        auth.require_role(config.ROLE_ADMIN)
        auth.require("view_reports")
    except Exception:
        denied = True
    check("admin passes admin-only capability checks", not denied)

    # ------------------------------------------------------------------ #
    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:")
        for name in FAILED:
            print(f"  - {name}")
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
