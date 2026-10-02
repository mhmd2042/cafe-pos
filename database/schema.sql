-- ========================================================================= --
--  Bunney POS — SQLite schema (Phase 1)
--  Offline-first, single-file database. Applied by database/db_manager.py.
--
--  CONVENTIONS
--    * All money columns end in `_minor` and hold INTEGER MINOR UNITS, i.e. the
--      smallest amount the currency expresses. For YER (no subunit in use) that
--      is a whole rial: 2,500 ر.ي -> 2500.  Never store money as REAL.
--    * Prices are TAX-INCLUSIVE; `tax_minor` on an order is the portion of
--      `total_minor` that is tax (reporting only, never added at checkout).
--      The configured rate is 0% (YER), so that column stays 0 unless the admin
--      sets a rate later — the code path is already correct for any rate.
--    * Timestamps are TEXT, local time, 'YYYY-MM-DD HH:MM:SS' (sortable).
--    * Booleans are INTEGER 0/1 with CHECK constraints.
--    * Seed rows use explicit ids + INSERT OR IGNORE so re-running is safe.
-- ========================================================================= --

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------------- --
-- Users / roles
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    full_name       TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('cashier', 'admin')),
    pin_hash        TEXT    NOT NULL,
    pin_salt        TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    must_change_pin INTEGER NOT NULL DEFAULT 0 CHECK (must_change_pin IN (0, 1)),
    last_login_at   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ------------------------------------------------------------------------- --
-- Menu: categories, products
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar    TEXT    NOT NULL,
    name_en    TEXT    NOT NULL DEFAULT '',
    icon       TEXT    NOT NULL DEFAULT '',
    color      TEXT    NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_categories_sort ON categories(sort_order, id);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id     INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name_ar         TEXT    NOT NULL,
    name_en         TEXT    NOT NULL DEFAULT '',
    sku             TEXT    UNIQUE,
    price_minor     INTEGER NOT NULL CHECK (price_minor >= 0),  -- tax-inclusive
    cost_minor      INTEGER NOT NULL DEFAULT 0 CHECK (cost_minor >= 0),
    color           TEXT    NOT NULL DEFAULT '',
    image_path      TEXT    NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    track_inventory INTEGER NOT NULL DEFAULT 1 CHECK (track_inventory IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_products_active   ON products(is_active);

-- ------------------------------------------------------------------------- --
-- Modifiers (size / milk / sugar / extras / free-text notes)
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS modifier_groups (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar        TEXT    NOT NULL,
    name_en        TEXT    NOT NULL DEFAULT '',
    group_type     TEXT    NOT NULL DEFAULT 'single'
                           CHECK (group_type IN ('single', 'multi', 'text')),
    is_required    INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    min_select     INTEGER NOT NULL DEFAULT 0,
    max_select     INTEGER NOT NULL DEFAULT 1,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS modifiers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      INTEGER NOT NULL REFERENCES modifier_groups(id) ON DELETE CASCADE,
    name_ar       TEXT    NOT NULL,
    name_en       TEXT    NOT NULL DEFAULT '',
    price_minor   INTEGER NOT NULL DEFAULT 0,   -- delta added to item price
    is_default    INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    sort_order    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_modifiers_group ON modifiers(group_id, sort_order);

-- Which modifier groups a product offers (many-to-many).
CREATE TABLE IF NOT EXISTS product_modifier_groups (
    product_id INTEGER NOT NULL REFERENCES products(id)        ON DELETE CASCADE,
    group_id   INTEGER NOT NULL REFERENCES modifier_groups(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, group_id)
);

-- ------------------------------------------------------------------------- --
-- Shifts (cashier sessions)
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS shifts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status               TEXT    NOT NULL DEFAULT 'open'
                                 CHECK (status IN ('open', 'closed')),
    opening_float_minor  INTEGER NOT NULL DEFAULT 0 CHECK (opening_float_minor >= 0),
    expected_cash_minor  INTEGER NOT NULL DEFAULT 0,
    counted_cash_minor   INTEGER NOT NULL DEFAULT 0,
    difference_minor     INTEGER NOT NULL DEFAULT 0,
    total_sales_minor    INTEGER NOT NULL DEFAULT 0,
    cash_sales_minor     INTEGER NOT NULL DEFAULT 0,
    card_sales_minor     INTEGER NOT NULL DEFAULT 0,
    order_count          INTEGER NOT NULL DEFAULT 0,
    opened_at            TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed_at            TEXT,
    z_report_path        TEXT    NOT NULL DEFAULT '',
    notes                TEXT    NOT NULL DEFAULT ''
);
-- Only one shift may be open at a time (partial unique index).
CREATE UNIQUE INDEX IF NOT EXISTS idx_shifts_single_open
    ON shifts(status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_shifts_opened ON shifts(opened_at);

-- ------------------------------------------------------------------------- --
-- Orders
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number   TEXT    NOT NULL UNIQUE,        -- e.g. 20261002-0042
    shift_id       INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    user_id        INTEGER REFERENCES users(id)  ON DELETE SET NULL,
    order_type     TEXT    NOT NULL DEFAULT 'dine_in'
                           CHECK (order_type IN ('dine_in', 'takeaway')),
    status         TEXT    NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open', 'completed', 'voided')),
    subtotal_minor INTEGER NOT NULL DEFAULT 0,     -- sum of lines, tax-inclusive
    discount_minor INTEGER NOT NULL DEFAULT 0 CHECK (discount_minor >= 0),
    total_minor    INTEGER NOT NULL DEFAULT 0 CHECK (total_minor >= 0),
    tax_minor      INTEGER NOT NULL DEFAULT 0,     -- embedded tax (reporting)
    payment_method TEXT    CHECK (payment_method IN ('cash', 'card')),
    paid_minor     INTEGER NOT NULL DEFAULT 0,
    change_minor   INTEGER NOT NULL DEFAULT 0,
    note           TEXT    NOT NULL DEFAULT '',
    printed_at     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_created  ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_shift    ON orders(shift_id);
CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_payment  ON orders(payment_method, created_at);

CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id)   ON DELETE CASCADE,
    product_id    INTEGER REFERENCES products(id)          ON DELETE SET NULL,
    name_ar       TEXT    NOT NULL,                 -- snapshot at sale time
    name_en       TEXT    NOT NULL DEFAULT '',
    unit_price_minor INTEGER NOT NULL DEFAULT 0,    -- base + modifier deltas
    quantity      INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    line_total_minor INTEGER NOT NULL DEFAULT 0,
    modifiers_json TEXT   NOT NULL DEFAULT '[]',    -- [{"group":"Size",...}]
    note          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_order_items_order   ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);

-- Payment audit trail (supports future split payments).
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    method      TEXT    NOT NULL CHECK (method IN ('cash', 'card')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);

-- ------------------------------------------------------------------------- --
-- Inventory & recipes
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS inventory_items (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar            TEXT    NOT NULL,
    name_en            TEXT    NOT NULL DEFAULT '',
    unit               TEXT    NOT NULL CHECK (unit IN ('g', 'ml', 'piece')),
    current_qty        REAL    NOT NULL DEFAULT 0,
    min_qty            REAL    NOT NULL DEFAULT 0,
    cost_per_unit_minor REAL   NOT NULL DEFAULT 0,  -- minor units per unit (g/ml/piece)
    supplier           TEXT    NOT NULL DEFAULT '',
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inventory_active ON inventory_items(is_active);

CREATE TABLE IF NOT EXISTS recipes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id        INTEGER NOT NULL REFERENCES products(id)         ON DELETE CASCADE,
    inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id)  ON DELETE CASCADE,
    quantity          REAL    NOT NULL CHECK (quantity > 0),
    modifier_id       INTEGER REFERENCES modifiers(id) ON DELETE SET NULL, -- variant link (e.g. oat milk)
    UNIQUE (product_id, inventory_item_id, modifier_id)
);
CREATE INDEX IF NOT EXISTS idx_recipes_product ON recipes(product_id);

CREATE TABLE IF NOT EXISTS stock_movements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    change_qty        REAL    NOT NULL,             -- negative = deduction
    reason            TEXT    NOT NULL
                              CHECK (reason IN ('sale','purchase','waste','adjustment','return')),
    order_id          INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    user_id           INTEGER REFERENCES users(id)  ON DELETE SET NULL,
    note              TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_stock_item ON stock_movements(inventory_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_order ON stock_movements(order_id);

-- ------------------------------------------------------------------------- --
-- Backups, settings, audit
-- ------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS backup_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL CHECK (kind IN ('auto', 'manual')),
    target_path TEXT   NOT NULL DEFAULT '',
    is_external INTEGER NOT NULL DEFAULT 0 CHECK (is_external IN (0, 1)),
    status     TEXT    NOT NULL DEFAULT 'ok'
                       CHECK (status IN ('ok', 'failed', 'fallback')),
    size_bytes INTEGER NOT NULL DEFAULT 0,
    message    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_backup_created ON backup_log(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT    NOT NULL,
    entity     TEXT    NOT NULL DEFAULT '',
    entity_id  INTEGER,
    details    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action, created_at);

-- ========================================================================= --
-- SEED DEFAULTS  (idempotent — explicit ids + INSERT OR IGNORE)
-- ========================================================================= --

-- Settings ------------------------------------------------------------------
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('cafe_name',                 'مقهى الرصيف'),
    ('cafe_phone',                ''),
    ('cafe_address',              ''),
    ('tax_rate',                  '0.00'),
    ('tax_inclusive',             '1'),
    ('tax_number',                ''),
    ('currency_code',             'YER'),
    ('currency_symbol',           'ر.ي'),
    ('language',                  'ar'),
    ('theme',                     'dark'),
    ('receipt_footer',            'شكراً لزيارتكم ☕'),
    ('receipt_width_mm',          '80'),
    ('printer_enabled',           '0'),
    ('printer_name',              ''),
    ('backup_dir',                ''),
    ('backup_auto_on_shift_close','1'),
    ('backup_keep_local',         '30'),
    ('require_open_shift',        '1'),
    ('low_stock_alerts',          '1'),
    ('order_number_prefix',       ''),
    ('last_backup_at',            ''),
    ('schema_version',            '1');

-- Categories ----------------------------------------------------------------
INSERT OR IGNORE INTO categories (id, name_ar, name_en, icon, color, sort_order) VALUES
    (1, 'مشروبات ساخنة',  'Hot Drinks',  'hot',   '#C98F4B', 1),
    (2, 'مشروبات باردة',  'Cold Drinks', 'cold',  '#6C93A8', 2),
    (3, 'حلويات',         'Desserts',    'cake',  '#B8768C', 3),
    (4, 'وجبات خفيفة',    'Snacks',      'snack', '#8FA05C', 4),
    (5, 'إضافات',         'Extras',      'plus',  '#9C8A78', 5);

-- Products (prices are TAX-INCLUSIVE, in minor units) ------------------------
INSERT OR IGNORE INTO products (id, category_id, name_ar, name_en, sku, price_minor, cost_minor, sort_order) VALUES
    -- Hot drinks
    (1,  1, 'إسبريسو',            'Espresso',          'HOT-001', 1200,  400, 1),
    (2,  1, 'دوبل إسبريسو',       'Double Espresso',   'HOT-002', 1600,  600, 2),
    (3,  1, 'أمريكانو',           'Americano',         'HOT-003', 1600,  500, 3),
    (4,  1, 'كابتشينو',           'Cappuccino',        'HOT-004', 2500,  600, 4),
    (5,  1, 'لاتيه',              'Latte',             'HOT-005', 2600,  660, 5),
    (6,  1, 'فلات وايت',          'Flat White',        'HOT-006', 2600,  640, 6),
    (7,  1, 'قهوة عربية',         'Arabic Coffee',     'HOT-007', 1500,  290, 7),
    (8,  1, 'شاي أحمر',           'Black Tea',         'HOT-008', 1000,  115, 8),
    (9,  1, 'شاي أخضر',           'Green Tea',         'HOT-009', 1200,  140, 9),
    (10, 1, 'هوت شوكليت',         'Hot Chocolate',     'HOT-010', 2800,  690, 10),
    -- Cold drinks
    (11, 2, 'آيس أمريكانو',       'Iced Americano',    'CLD-001', 2200,  500, 1),
    (12, 2, 'آيس لاتيه',          'Iced Latte',        'CLD-002', 2900,  700, 2),
    (13, 2, 'كراميل ماكياتو بارد', 'Iced Caramel Macchiato', 'CLD-003', 3200,  850, 3),
    (14, 2, 'موهيتو',             'Mojito',            'CLD-004', 2700,  700, 4),
    (15, 2, 'عصير برتقال',        'Orange Juice',      'CLD-005', 2500,  900, 5),
    (16, 2, 'مياه معدنية',        'Mineral Water',     'CLD-006',  500,  150, 6),
    -- Desserts
    (17, 3, 'تشيز كيك',           'Cheesecake',        'DES-001', 3200,  950, 1),
    (18, 3, 'كرواسون شوكولاتة',   'Chocolate Croissant','DES-002',2000,  600, 2),
    (19, 3, 'كوكيز',              'Cookie',            'DES-003', 1000,  300, 3),
    (20, 3, 'براوني',             'Brownie',           'DES-004', 2600,  750, 4),
    (21, 3, 'كيك التمر',          'Date Cake',         'DES-005', 2200,  650, 5),
    -- Snacks
    (22, 4, 'ساندويتش حلوم',      'Halloumi Sandwich', 'SNK-001', 3800, 1400, 1),
    (23, 4, 'توست أفوكادو',       'Avocado Toast',     'SNK-002', 4200, 1600, 2),
    (24, 4, 'ميني بيتزا',         'Mini Pizza',        'SNK-003', 3900, 1500, 3),
    (25, 4, 'بطاطس مقلية',        'French Fries',      'SNK-004', 2200,  700, 4);

-- Modifier groups -----------------------------------------------------------
INSERT OR IGNORE INTO modifier_groups (id, name_ar, name_en, group_type, is_required, min_select, max_select, sort_order) VALUES
    (1, 'الحجم',        'Size',       'single', 1, 1, 1, 1),
    (2, 'نوع الحليب',   'Milk',       'single', 0, 0, 1, 2),
    (3, 'مستوى السكر',  'Sugar',      'single', 0, 0, 1, 3),
    (4, 'عدد الشوتات',  'Shots',      'single', 0, 0, 1, 4),
    (5, 'إضافات',       'Extras',     'multi',  0, 0, 4, 5),
    (6, 'ملاحظات',      'Notes',      'text',   0, 0, 0, 6);

INSERT OR IGNORE INTO modifiers (id, group_id, name_ar, name_en, price_minor, is_default, sort_order) VALUES
    -- Size (group 1)
    (1,  1, 'صغير',            'Small',         -300, 0, 1),
    (2,  1, 'وسط',             'Medium',           0, 1, 2),
    (3,  1, 'كبير',            'Large',          500, 0, 3),
    -- Milk (group 2)
    (10, 2, 'حليب كامل الدسم', 'Full Fat',         0, 1, 1),
    (11, 2, 'قليل الدسم',      'Skimmed',          0, 0, 2),
    (12, 2, 'حليب شوفان',      'Oat Milk',       500, 0, 3),
    (13, 2, 'حليب لوز',        'Almond Milk',    500, 0, 4),
    (14, 2, 'بدون لاكتوز',     'Lactose Free',   300, 0, 5),
    (15, 2, 'بدون حليب',       'No Milk',          0, 0, 6),
    -- Sugar (group 3)
    (20, 3, 'بدون سكر',        'No Sugar',         0, 0, 1),
    (21, 3, 'سكر خفيف',        'Light Sugar',      0, 0, 2),
    (22, 3, 'سكر وسط',         'Medium Sugar',     0, 1, 3),
    (23, 3, 'سكر زيادة',       'Extra Sugar',      0, 0, 4),
    -- Shots (group 4)
    (30, 4, 'شوت واحد',        '1 Shot',           0, 1, 1),
    (31, 4, 'شوتين',           '2 Shots',        700, 0, 2),
    (32, 4, 'ثلاث شوتات',      '3 Shots',       1400, 0, 3),
    -- Extras (group 5)
    (40, 5, 'كريمة مخفوقة',    'Whipped Cream',  500, 0, 1),
    (41, 5, 'شوت فانيلا',      'Vanilla Shot',   500, 0, 2),
    (42, 5, 'سيرب كراميل',     'Caramel Syrup',  500, 0, 3),
    (43, 5, 'قرفة',            'Cinnamon',         0, 0, 4);

-- Attach groups to products -------------------------------------------------
-- Deliberately per-product rather than per-category: attaching "milk type" and
-- "sugar level" to mineral water or orange juice is wrong, and it also forces a
-- needless modifier modal on the cashier for drinks that have no real choice.
--   size   : all drinks
--   milk   : coffee-based drinks and tea
--   sugar  : coffee, tea and chocolate
--   shots  : espresso-based drinks
--   extras : drinks and desserts
--   notes  : everything (free text, never triggers a modal on its own)
INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 1, 1 FROM products p WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13);

INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 2, 2 FROM products p WHERE p.id IN (1,2,3,4,5,6,10,11,12,13);

INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 3, 3 FROM products p WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13);

INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 4, 4 FROM products p WHERE p.id IN (1,2,3,4,5,6,11,12,13);

INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 5, 5 FROM products p
 WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13,17,18,19,20,21);

INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order)
SELECT p.id, 6, 6 FROM products p WHERE p.id BETWEEN 1 AND 25;

-- Inventory items -----------------------------------------------------------
-- current_qty is the opening balance. The matching 'adjustment' movement row is
-- inserted at the end of this file so that the stock ledger fully explains the
-- on-hand quantity from day one (otherwise the audit trail has a hole from the
-- very first sale and "theoretical vs actual" can never reconcile).
INSERT OR IGNORE INTO inventory_items (id, name_ar, name_en, unit, current_qty, min_qty, cost_per_unit_minor, supplier) VALUES
    (1, 'حبوب قهوة',          'Coffee Beans',      'g',     5000, 1000,  15, 'محمصة محلية'),
    (2, 'حليب كامل الدسم',    'Full Fat Milk',     'ml',   20000, 4000, 1.2, 'مورد ألبان'),
    (3, 'حليب شوفان',         'Oat Milk',          'ml',    4000, 1000,   4, 'مورد ألبان'),
    (4, 'حليب لوز',           'Almond Milk',       'ml',    4000, 1000,   5, 'مورد ألبان'),
    (5, 'سكر',                'Sugar',             'g',    10000, 2000, 0.6, 'مورد عام'),
    (6, 'مسحوق شوكولاتة',     'Cocoa Powder',      'g',     2000,  500,  12, 'مورد عام'),
    (7, 'كريمة مخفوقة',       'Whipped Cream',     'ml',    3000,  800, 3.5, 'مورد ألبان'),
    (8, 'أكواب 8 أونصة',      'Cups 8oz',          'piece',  500,  100,  90, 'مورد تغليف'),
    (9, 'أكواب 12 أونصة',     'Cups 12oz',         'piece',  500,  100, 110, 'مورد تغليف'),
    (10,'أكواب 16 أونصة',     'Cups 16oz',         'piece',  500,  100, 130, 'مورد تغليف'),
    (11,'أغطية أكواب',        'Cup Lids',          'piece',  800,  150,  40, 'مورد تغليف'),
    (12,'تمر',                'Dates',             'g',     3000,  500,  10, 'مورد محلي'),
    (13,'شاي',                'Tea Leaves',        'g',     2000,  400,   8, 'مورد عام');

-- Recipes (per single serving; medium-size baseline) ------------------------
INSERT OR IGNORE INTO recipes (product_id, inventory_item_id, quantity) VALUES
    (1,  1, 18),   (1,  8, 1),    (1,  11, 1),     -- Espresso: 18g beans, 8oz cup, lid
    (2,  1, 18),   (2,  8, 1),    (2,  11, 1),     -- Double Espresso
    (3,  1, 18),   (3,  9, 1),    (3,  11, 1),     -- Americano
    (4,  1, 18),   (4,  9, 1),    (4, 11, 1),      -- Cappuccino (milk comes from the milk group)
    (5,  1, 18),   (5,  9, 1),    (5, 11, 1),      -- Latte
    (6,  1, 18),   (6,  9, 1),    (6, 11, 1),      -- Flat White
    (7,  12, 20),  (7,  8, 1),                     -- Arabic Coffee
    (8,  13, 3),   (8,  8, 1),    (8,  11, 1),     -- Black Tea
    (10, 6, 25),   (10, 9, 1),    (10, 11, 1),     -- Hot Chocolate
    (11, 1, 18),   (11, 10, 1),   (11, 11, 1),     -- Iced Americano
    (12, 1, 18),   (12, 10, 1),   (12, 11, 1),     -- Iced Latte
    (13, 1, 18),   (13, 10, 1),   (13, 11, 1);     -- Iced Caramel Macchiato

-- Milk is CONDITIONAL on the "نوع الحليب" group (id 2), so the choice made in
-- the modifier dialog decides which milk is deducted instead of deducting both.
-- Modifier ids: 10 full fat (default), 11 skimmed, 12 oat, 13 almond,
--                14 lactose-free, 15 no milk.
INSERT OR IGNORE INTO recipes (product_id, inventory_item_id, quantity, modifier_id) VALUES
    -- Cappuccino / Latte / Flat White / Hot Chocolate: 200 ml
    (4, 2, 200, 10), (4, 2, 200, 11), (4, 3, 200, 12), (4, 4, 200, 13), (4, 2, 200, 14),
    (5, 2, 200, 10), (5, 2, 200, 11), (5, 3, 200, 12), (5, 4, 200, 13), (5, 2, 200, 14),
    (6, 2, 160, 10), (6, 2, 160, 11), (6, 3, 160, 12), (6, 4, 160, 13), (6, 2, 160, 14),
    (10, 2, 200, 10), (10, 2, 200, 11), (10, 3, 200, 12), (10, 4, 200, 13), (10, 2, 200, 14),
    -- Iced drinks: 200 ml
    (12, 2, 200, 10), (12, 2, 200, 11), (12, 3, 200, 12), (12, 4, 200, 13), (12, 2, 200, 14),
    (13, 2, 200, 10), (13, 2, 200, 11), (13, 3, 200, 12), (13, 4, 200, 13), (13, 2, 200, 14),
    -- Black / iced Americano with milk on the side: a splash
    (3, 2, 30, 10), (3, 3, 30, 12), (3, 4, 30, 13),
    (11, 2, 30, 10), (11, 3, 30, 12), (11, 4, 30, 13);

-- Opening stock ledger -------------------------------------------------------
-- One 'adjustment' row per ingredient carrying its opening balance, so
-- current_qty is fully explained by stock_movements. Guarded with NOT EXISTS so
-- re-applying the seed never doubles the stock.
INSERT INTO stock_movements (inventory_item_id, change_qty, reason, note)
SELECT i.id, i.current_qty, 'adjustment', 'رصيد افتتاحي'
  FROM inventory_items i
 WHERE NOT EXISTS (
       SELECT 1 FROM stock_movements m WHERE m.inventory_item_id = i.id
 );
