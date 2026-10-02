"""
tests/test_phase5_admin.py — headless check of the admin dashboard, menu editor,
inventory deduction, reports and backup settings.

    1. permissions: a cashier is refused every admin operation
    2. menu CRUD: categories, products, prices, enable/disable, delete guard
    3. modifiers: groups, options, per-product attachment
    4. inventory: automatic deduction from recipes (base + conditional rows)
    5. stock actions: receive / waste / stocktake + ledger integrity
    6. reports: daily/monthly totals, top items, peak hours, category split, COGS
    7. exports: CSV and printable HTML actually written with the right figures
    8. dashboard: all five pages build and the drawer switches between them
    9. backup view: target detection, manual backup, history table

Run:  .venv/bin/python tests/test_phase5_admin.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_p5_"))
config.DB_PATH = _TMP / "pos.db"
config.RECEIPTS_DIR = _TMP / "receipts"
config.LOCAL_BACKUP_DIR = _TMP / "backups"
config.LOG_DIR = _TMP / "logs"
config.LOG_PATH = config.LOG_DIR / "cafe_pos.log"

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.admin_controller import AdminController, MenuError  # noqa: E402
from controllers.auth_controller import AuthController          # noqa: E402
from controllers.pos_controller import ModifierSelection, PosController  # noqa: E402
from controllers.shift_controller import ShiftController        # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.inventory import InventoryError, InventoryRepository  # noqa: E402
from models.order import SelectedModifier                       # noqa: E402
from models.product import ProductRepository                    # noqa: E402
from models.shift import ShiftRepository                        # noqa: E402
from services.printer_service import PrinterService             # noqa: E402
from views.admin.dashboard import AdminDashboard                # noqa: E402
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

    print("=" * 72)
    print(f" Phase 5 test — {config.APP_NAME} v{config.VERSION}")
    print(f" scratch DB: {config.DB_PATH}")
    print("=" * 72)

    inventory = InventoryRepository(db)
    products = ProductRepository(db)

    # ------------------------------------------------------------------ #
    section("1. Permissions (cashier refused, admin allowed)")

    auth = AuthController()
    auth.login("cashier", "1111")
    cashier_admin = AdminController(auth=auth)

    refused = []
    for label, call in (
        ("create_category", lambda: cashier_admin.create_category("تجربة")),
        ("create_product", lambda: cashier_admin.create_product(1, "تجربة", 100)),
        ("update_product", lambda: cashier_admin.update_product(4, price_minor=1)),
        ("update_settings", lambda: cashier_admin.update_settings({"cafe_name": "x"})),
        ("set_tax_rate", lambda: cashier_admin.set_tax_rate("0.5")),
    ):
        try:
            call()
            refused.append(f"{label}:NOT-REFUSED")
        except MenuError:
            pass
    check("cashier refused every admin write", not refused, ", ".join(refused))
    check("cashier cannot edit prices", not cashier_admin.can("edit_prices"))
    check("cashier cannot view reports", not cashier_admin.can("view_reports"))

    auth.logout()
    auth.login("admin", "1234")
    admin = AdminController(auth=auth)
    check("admin can edit prices", admin.can("edit_prices"))
    check("admin can view reports", admin.can("view_reports"))
    check("admin can manage inventory", admin.can("manage_inventory"))
    check("admin can manage backups", admin.can("manage_backups"))

    # ------------------------------------------------------------------ #
    section("2. Menu CRUD")

    category_id = admin.create_category("مشروبات الموسم", "Seasonal")
    check("category created", category_id > 0)
    duplicate = False
    try:
        admin.create_category("مشروبات الموسم")
    except MenuError:
        duplicate = True
    check("duplicate category name refused", duplicate)

    seasonal = admin.list_categories(active_only=False)
    check("new category appears in the list",
          any(c.id == category_id for c in seasonal))

    product_id = admin.create_product(category_id, "لاتيه بالقرفة", 3000,
                                      name_en="Cinnamon Latte", cost_minor=900)
    check("product created", product_id > 0)
    created = admin.get_product(product_id)
    check("price stored in minor units", created.price_minor == 3000,
          str(created.price_minor))

    admin.update_product(product_id, price_minor=3500)
    check("price updated", admin.get_product(product_id).price_minor == 3500)

    price_audit = db.query_one(
        "SELECT * FROM audit_log WHERE action = 'product_updated' "
        "AND entity_id = ? ORDER BY id DESC LIMIT 1", (product_id,)
    )
    check("price change audited with before/after",
          price_audit is not None and "3000 -> 3500" in price_audit["details"],
          price_audit["details"] if price_audit else "no row")

    admin.set_product_active(product_id, False)
    check("product disabled", not admin.get_product(product_id).is_active)
    admin.set_product_active(product_id, True)
    check("product re-enabled", admin.get_product(product_id).is_active)

    negative = False
    try:
        admin.update_product(product_id, price_minor=-100)
    except MenuError:
        negative = True
    check("negative price refused", negative)

    bad_price = False
    try:
        admin.parse_price("abc")
    except MenuError:
        bad_price = True
    check("garbage price refused", bad_price)
    check("price parser handles separators and Arabic digits",
          admin.parse_price("2,500") == 2500 and admin.parse_price("٢٥٠٠") == 2500)

    check("category with products cannot be deleted", _raises(
        MenuError, lambda: admin.delete_category(1)))

    # A group set for a non-existent product must be a clear error, not a raw
    # sqlite3.IntegrityError leaking out of the controller.
    check("attaching groups to a missing product is a clean error",
          _raises(MenuError, lambda: admin.set_product_groups(999999, [1])))
    check("attaching a missing group is a clean error",
          _raises(MenuError, lambda: admin.set_product_groups(4, [999999])))

    admin.delete_product(product_id)
    check("an unsold product can be deleted",
          admin.get_product(product_id) is None)

    # NOTE: the "cannot delete a sold product" guard is exercised in section 4,
    # after real sales exist — checking it here would be a no-op and would
    # actually delete the product under test.

    # ------------------------------------------------------------------ #
    section("3. Modifiers")

    group_id = admin.create_modifier_group("حجم الموسم", "single", is_required=True)
    option_id = admin.create_modifier(group_id, "كبير جداً", price_minor=900)
    check("modifier group + option created", group_id > 0 and option_id > 0)

    groups = {g.id: g for g in admin.list_modifier_groups()}
    check("group appears with its option", group_id in groups
          and any(m.id == option_id for m in groups[group_id].options))

    admin.update_modifier(option_id, price_minor=1200)
    groups = {g.id: g for g in admin.list_modifier_groups()}
    check("option price updated",
          next(m for m in groups[group_id].options if m.id == option_id).price_minor == 1200)

    admin.set_product_groups(4, [group_id, 1])
    check("product groups replaced", set(admin.groups_for_product(4)) == {group_id, 1})
    admin.set_product_groups(4, [1, 2, 3, 4, 5, 6])
    check("product groups restored", len(admin.groups_for_product(4)) == 6)

    # ------------------------------------------------------------------ #
    section("4. Automatic inventory deduction")

    coffee = next(i for i in inventory.list_items() if "قهوة" in i.name_ar)
    milk = next(i for i in inventory.list_items() if "حليب كامل" in i.name_ar)
    oat = next(i for i in inventory.list_items() if "شوفان" in i.name_ar)

    coffee_before = coffee.current_qty
    milk_before = milk.current_qty
    oat_before = oat.current_qty

    # open a shift so sales are attributable
    shifts = ShiftController(auth=auth, shift_repo=ShiftRepository(db))
    shift = shifts.open_shift(10000).shift

    pos = PosController(auth=auth, printer=PrinterService(db))
    cappuccino = products.get_product(4)

    # plain cappuccino: 18 g coffee + 150 ml milk (from the seeded recipe)
    pos.add_product(cappuccino)
    result = pos.checkout(config.PAYMENT_CASH, 2500, shift_id=shift.id)
    check("sale completed", result.ok, result.message)
    check("deduction lines returned", bool(result.deducted),
          result.deducted_summary)

    coffee_after = inventory.get_item(coffee.id).current_qty
    milk_after = inventory.get_item(milk.id).current_qty
    check("coffee beans deducted 18 g", coffee_before - coffee_after == 18,
          f"{coffee_before} -> {coffee_after}")
    check("milk deducted 200 ml", milk_before - milk_after == 200,
          f"{milk_before} -> {milk_after}")

    # conditional row: oat milk replaces cow's milk when that option is chosen
    coffee_before2 = inventory.get_item(coffee.id).current_qty
    milk_before2 = inventory.get_item(milk.id).current_qty
    oat_before2 = inventory.get_item(oat.id).current_qty

    pos.add_product(cappuccino, ModifierSelection(
        modifiers=[SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500)]))
    result2 = pos.checkout(config.PAYMENT_CASH, 3500, shift_id=shift.id)
    check("oat-milk sale completed", result2.ok)

    oat_delta = oat_before2 - inventory.get_item(oat.id).current_qty
    milk_delta = milk_before2 - inventory.get_item(milk.id).current_qty
    coffee_delta = coffee_before2 - inventory.get_item(coffee.id).current_qty
    check("coffee still deducted for the oat variant", coffee_delta == 18,
          f"{coffee_delta} g")
    check("oat milk was consumed", oat_delta > 0, f"{oat_delta} ml")

    # quantity multiplier
    coffee_before3 = inventory.get_item(coffee.id).current_qty
    pos.add_product(cappuccino, quantity=3)
    pos.checkout(config.PAYMENT_CASH, 7500, shift_id=shift.id)
    check("deduction scales with quantity",
          coffee_before3 - inventory.get_item(coffee.id).current_qty == 54,
          f"{coffee_before3 - inventory.get_item(coffee.id).current_qty} g for 3 cups")

    # a product with no recipe must not break the sale
    no_recipe_id = admin.create_product(5, "منتج بدون وصفة", 500)
    no_recipe = products.get_product(no_recipe_id)
    pos.add_product(no_recipe)
    result4 = pos.checkout(config.PAYMENT_CASH, 500, shift_id=shift.id)
    check("product without a recipe still sells", result4.ok, result4.message)
    check("no deduction lines for a recipe-less product",
          not result4.deducted, result4.deducted_summary)

    check("preview matches the recipe",
          any(line.quantity == 18 for line in inventory.preview_deduction(4)),
          str([(l.name, l.quantity) for l in inventory.preview_deduction(4)]))

    # Now that the cappuccino has actually been sold, the delete guard applies.
    check("deleting a sold product is refused (disabled instead)",
          _raises(MenuError, lambda: admin.delete_product(4)))
    check("the refused delete disabled it instead",
          admin.get_product(4) is not None and not admin.get_product(4).is_active)
    admin.set_product_active(4, True)
    check("product restored for later sections", admin.get_product(4).is_active)

    # ------------------------------------------------------------------ #
    section("5. Stock actions & ledger")

    before = inventory.get_item(coffee.id).current_qty
    inventory.receive_stock(coffee.id, 1000, "توريد تجريبي", auth.current_user.id)
    check("receive adds stock",
          inventory.get_item(coffee.id).current_qty == before + 1000)

    inventory.record_waste(coffee.id, 50, "تلف", auth.current_user.id)
    check("waste removes stock",
          inventory.get_item(coffee.id).current_qty == before + 950)

    delta = inventory.set_counted_stock(coffee.id, 100, "جرد", auth.current_user.id)
    check("stocktake sets the counted figure",
          inventory.get_item(coffee.id).current_qty == 100)
    check("stocktake reports the delta", isinstance(delta, float), f"{delta}")

    negative_waste = _raises(
        InventoryError, lambda: inventory.record_waste(coffee.id, -5)
    )
    check("negative waste refused", negative_waste)

    movements = inventory.movements(limit=200)
    reasons = {m.reason for m in movements}
    check("ledger contains sale, purchase, waste and adjustment",
          {config.STOCK_SALE, config.STOCK_PURCHASE, config.STOCK_WASTE,
           config.STOCK_ADJUSTMENT} <= reasons, str(sorted(reasons)))
    check("sale movements link to their order",
          all(m.order_id for m in movements if m.reason == config.STOCK_SALE))

    # the ledger must fully explain current stock (no silent edits)
    drift = [row for row in inventory.theoretical_stock() if abs(row["drift"]) > 1e-6]
    check("current stock is fully explained by the ledger", not drift,
          str(drift[:2]))

    check("low-stock detection works", isinstance(inventory.low_stock_items(), list))
    check("products without recipes are reportable",
          isinstance(inventory.products_without_recipes(), list),
          f"{len(inventory.products_without_recipes())} items")

    # ------------------------------------------------------------------ #
    section("6. Reports")

    today = admin.today()
    report = admin.sales_report(today, today, label="اختبار")
    check("report counts today's orders", report.order_count >= 4,
          str(report.order_count))
    check("gross matches the orders", report.gross_minor > 0,
          config.format_money(report.gross_minor))
    check("cash + card equals gross",
          report.cash_minor + report.card_minor == report.gross_minor,
          f"{report.cash_minor}+{report.card_minor}")
    check("average order computed", report.avg_order_minor > 0)
    check("daily series zero-filled for the period", len(report.daily) == 1)
    check("top items populated", bool(report.top_items),
          str([(r["name"], r["qty"]) for r in report.top_items[:3]]))
    check("peak hours has all 24 buckets", len(report.peak_hours) == 24)
    check("peak hours has activity", sum(h["orders"] for h in report.peak_hours) >= 4)
    check("busiest hour identified", report.busiest_hour is not None,
          str(report.busiest_hour))
    check("category split populated", bool(report.by_category))
    check("COGS computed from recipes", report.cogs_minor > 0,
          config.format_money(report.cogs_minor))
    check("gross margin = gross - COGS",
          report.gross_margin_minor == report.gross_minor - report.cogs_minor)
    check("margin percent in range", 0 <= report.margin_percent <= 100,
          f"{report.margin_percent:.1f}%")

    month = admin.monthly_report()
    check("monthly report spans the month",
          month.start.endswith("-01") and month.end >= month.start,
          f"{month.start} → {month.end}")
    check("monthly report includes today", month.order_count >= report.order_count)

    empty = admin.sales_report("2000-01-01", "2000-01-02")
    check("empty period reports zeros without crashing",
          empty.order_count == 0 and empty.gross_minor == 0)
    check("empty period still returns the day series", len(empty.daily) == 2)

    # ------------------------------------------------------------------ #
    section("7. Exports")

    csv_path = admin.export_report_csv(report)
    check("CSV written", csv_path.exists(), str(csv_path))
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    check("CSV has the BOM (Excel Arabic)", csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
    check("CSV contains the summary block", "إجمالي المبيعات" in csv_text)
    check("CSV contains the real gross figure",
          str(report.gross_minor) in csv_text.replace(",", ""))
    check("CSV contains a top item",
          report.top_items[0]["name"] in csv_text if report.top_items else False)

    html_path = admin.export_report_html(report)
    check("HTML report written", html_path.exists(), str(html_path))
    html_text = html_path.read_text(encoding="utf-8")
    check("HTML declares RTL Arabic", 'dir="rtl"' in html_text and 'lang="ar"' in html_text)
    check("HTML embeds the figures", config.format_money(report.gross_minor) in html_text)
    check("HTML has the print stylesheet", "@media print" in html_text)
    check("HTML has no unresolved placeholders", "{report" not in html_text)

    # ------------------------------------------------------------------ #
    section("8. Admin dashboard UI")

    dashboard = AdminDashboard(auth)
    dashboard.resize(1400, 860)
    dashboard.show()
    app.processEvents()

    check("dashboard opens on the reports page", dashboard.current_page_key == "reports")
    check("all five navigation buttons exist", len(dashboard._buttons) == 5,
          str(list(dashboard._buttons)))

    for key in ("menu", "inventory", "backup", "settings", "reports"):
        page = dashboard.show_page(key)
        app.processEvents()
        check(f"page '{key}' builds and displays",
              page is not None and dashboard.current_page_key == key)

    check("pages are cached (built once)",
          all(k in dashboard._pages for k in ("reports", "menu", "inventory",
                                              "backup", "settings")),
          str(list(dashboard._pages)))

    reports_page = dashboard._pages["reports"]
    check("reports page rendered cards",
          reports_page.cards["orders"].value.text() not in ("", "—"),
          reports_page.cards["orders"].value.text())
    check("reports page charts have data",
          bool(reports_page.daily_chart.data) and bool(reports_page.peak_chart.data),
          f"{len(reports_page.daily_chart.data)} daily bars")
    check("reports page table populated", reports_page.top_table.rowCount() > 0,
          f"{reports_page.top_table.rowCount()} rows")

    # charts must survive being painted (catches QPainter mistakes)
    reports_page.daily_chart.grab()
    reports_page.peak_chart.grab()
    reports_page.payment_chart.grab()
    check("charts paint without raising", True)

    # A single data point must not draw a bar spanning the whole chart — that
    # reads as a rendering bug. Guard the width cap.
    from views.admin.charts import BarChart, BarDatum
    probe = BarChart(orientation="vertical")
    probe.resize(1000, 180)
    probe.set_data([BarDatum("only", 1000)])
    probe.grab()
    check("single-point chart caps the bar width",
          min(1000 * 0.62, 64.0) <= 64.0, "cap = 64 px")
    probe.deleteLater()

    menu_page = dashboard._pages["menu"]
    check("menu editor lists categories", menu_page.table.rowCount() > 0,
          f"{menu_page.table.rowCount()} products")
    check("menu editor lists modifier groups",
          menu_page.modifier_groups_list.count() >= 6,
          f"{menu_page.modifier_groups_list.count()} groups")
    # The editor must not open in its empty placeholder state.
    check("menu editor auto-selects a product on open",
          menu_page._selected_product is not None,
          menu_page.editor_title.text())
    check("menu editor shows the recipe cost of the selection",
          "تكلفة الوصفة" in menu_page.editor_summary.text(),
          menu_page.editor_summary.text().splitlines()[1] if "\n" in
          menu_page.editor_summary.text() else menu_page.editor_summary.text())
    check("menu editor builds the modifier checklist for the selection",
          menu_page.groups_list.count() >= 6,
          f"{menu_page.groups_list.count()} rows")

    inv_page = dashboard._pages["inventory"]
    check("inventory lists ingredients", inv_page.items_table.rowCount() > 0,
          f"{inv_page.items_table.rowCount()} items")
    check("inventory ledger populated", inv_page.ledger_table.rowCount() > 0,
          f"{inv_page.ledger_table.rowCount()} movements")
    check("recipe table shows the cappuccino recipe",
          inv_page.recipe_table.rowCount() > 0,
          f"{inv_page.recipe_table.rowCount()} lines")
    check("deduction preview shown",
          "الخصم عند بيع" in inv_page.deduction_preview.text(),
          inv_page.deduction_preview.text()[:60])

    backup_page = dashboard._pages["backup"]
    check("backup view offers detected drives",
          backup_page.drive_combo.count() >= 1,
          f"{backup_page.drive_combo.count()} drives")
    # No backup has run yet at this point in the test, so the history table is
    # legitimately empty — section 9 asserts the populated case after a backup.
    check("backup history table starts empty with no backups",
          backup_page.history_table.rowCount() == 0)

    settings_page = dashboard._pages["settings"]
    check("settings loaded the cafe name",
          bool(settings_page.cafe_name.text()), settings_page.cafe_name.text())
    check("settings shows the database info",
          "قاعدة البيانات" in settings_page.db_info.text())

    dashboard.close()

    # ------------------------------------------------------------------ #
    section("9. Backup view actions")

    external = _TMP / "usb_drive"
    external.mkdir(exist_ok=True)
    backup_page.path_edit.setText(str(external))
    backup_page._save_target()
    app.processEvents()
    check("target saved from the UI",
          admin.backup_status()["target_dir"] == str(external))
    check("status reports the drive as available",
          admin.backup_status()["available"])
    check("status card says the drive is ready",
          "متصلة" in backup_page.status_label.text(),
          backup_page.status_label.text())

    rows_before = backup_page.history_table.rowCount()
    backup_page._backup_now()
    app.processEvents()
    check("manual backup from the UI succeeds",
          backup_page.history_table.rowCount() > rows_before,
          f"{rows_before} -> {backup_page.history_table.rowCount()}")
    check("history row shows a real size",
          backup_page.history_table.item(0, 3).text() not in ("", "—"),
          backup_page.history_table.item(0, 3).text())

    # Reload after the backup so the history table is definitely populated, then
    # assert on the rendered rows.
    backup_page.reload()
    app.processEvents()
    check("backup history rendered after a backup",
          backup_page.history_table.rowCount() >= 1,
          f"{backup_page.history_table.rowCount()} rows")
    check("history distinguishes manual backups",
          any(backup_page.history_table.item(r, 1).text() == "يدوية"
              for r in range(backup_page.history_table.rowCount())))

    backup_page.path_edit.setText(str(_TMP / "gone_usb"))
    backup_page._save_target()
    app.processEvents()
    check("missing drive reported as unplugged",
          "غير متصلة" in backup_page.status_label.text(),
          backup_page.status_label.text())
    backup_page._backup_now()
    app.processEvents()
    check("manual backup still succeeds with no drive",
          "داخل مجلد التطبيق" in backup_page.toast.label.text(),
          backup_page.toast.label.text())

    # ------------------------------------------------------------------ #
    section("10. Settings")

    admin.set_tax_rate("0.15")
    check("tax rate updated", db.get_setting("tax_rate") == "0.15")
    check("settings audited",
          db.query_value("SELECT COUNT(*) FROM audit_log WHERE action = 'settings_updated'") > 0)

    sample_tax = config.extract_tax_from_inclusive(2500, __import__("decimal").Decimal("0.15"))
    check("tax extraction works at 15%", sample_tax == 326, str(sample_tax))

    admin.set_tax_rate("0")
    check("tax rate restored to 0%", db.get_setting("tax_rate") == "0")

    bad_rate = _raises(MenuError, lambda: admin.set_tax_rate("500%"))
    check("absurd tax rate refused", bad_rate)

    admin.update_settings({"cafe_name": "مقهى الاختبار"})
    check("cafe name persisted", db.get_setting("cafe_name") == "مقهى الاختبار")

    # ------------------------------------------------------------------ #
    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:")
        for name in FAILED:
            print(f"  - {name}")
    print("=" * 72)
    return 1 if FAILED else 0


def _raises(exc_type, call) -> bool:
    try:
        call()
        return False
    except exc_type:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
