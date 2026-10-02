"""
database/db_manager.py — SQLite connection manager for Café POS.

Responsibilities
    * One process-wide singleton (DatabaseManager.get_instance()).
    * ONE connection PER THREAD (sqlite3 connections are not shareable across
      threads). The GUI thread and any backup/report worker each get their own.
    * Safe transactions: BEGIN IMMEDIATE / COMMIT / ROLLBACK, with SAVEPOINTs
      for nesting, plus busy-timeout + WAL so a background backup or report can
      read while the cashier writes.
    * Automatic table initialisation from database/schema.sql and versioned
      migrations via PRAGMA user_version.
    * Hot, crash-safe backups through the sqlite3 online backup API.

No network access, no ORM, no external services.

Usage
    from database.db_manager import get_db
    db = get_db()
    db.execute("UPDATE products SET price_minor=? WHERE id=?", (1500, 5))
    with db.transaction():
        db.execute(...)
        db.execute(...)
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import config
from models.user import UserRepository

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class DatabaseError(RuntimeError):
    """Base error for database layer problems."""


class SchemaError(DatabaseError):
    """schema.sql is missing, unreadable, or failed to apply."""


class MigrationError(DatabaseError):
    """A versioned migration failed."""


# --------------------------------------------------------------------------- #
# Migrations: version -> list of SQL statements.
# Each entry upgrades the DB FROM (version-1) TO version. Applied in order by
# _run_migrations() only when PRAGMA user_version is behind config.SCHEMA_VERSION.
# Never edit a released migration — add a new numbered entry instead.
# --------------------------------------------------------------------------- #
MIGRATIONS: dict[int, list[str]] = {
    2: [
        # Phase 2: force a PIN change on first admin/cashier login.
        "ALTER TABLE users ADD COLUMN must_change_pin INTEGER NOT NULL DEFAULT 0 "
        "CHECK (must_change_pin IN (0, 1))",
    ],
    3: [
        # Phase 3 fix: the v1 seed attached size/milk/sugar to every cold drink
        # by category, which gave mineral water a milk-type choice and forced a
        # pointless modifier modal on the cashier. Rebuild the mapping explicitly
        # per product. Safe to run repeatedly (delete-then-insert).
        "DELETE FROM product_modifier_groups",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 1, 1 FROM products p WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13)",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 2, 2 FROM products p WHERE p.id IN (1,2,3,4,5,6,10,11,12,13)",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 3, 3 FROM products p WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13)",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 4, 4 FROM products p WHERE p.id IN (1,2,3,4,5,6,11,12,13)",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 5, 5 FROM products p "
        "WHERE p.id IN (1,2,3,4,5,6,7,8,9,10,11,12,13,17,18,19,20,21)",
        "INSERT OR IGNORE INTO product_modifier_groups (product_id, group_id, sort_order) "
        "SELECT p.id, 6, 6 FROM products p WHERE p.id BETWEEN 1 AND 25",
    ],
    4: [
        # Phase 5: (a) milk must be conditional on the milk-type choice, not a
        # flat base row — otherwise choosing oat milk deducts both cow's and
        # oat milk; (b) opening stock needs a ledger row so the audit trail
        # explains current quantities from the very first day.
        "DELETE FROM recipes",
        "INSERT OR IGNORE INTO recipes (product_id, inventory_item_id, quantity) VALUES "
        "(1,1,18),(1,8,1),(1,11,1),(2,1,18),(2,8,1),(2,11,1),(3,1,18),(3,9,1),(3,11,1),"
        "(4,1,18),(4,9,1),(4,11,1),(5,1,18),(5,9,1),(5,11,1),(6,1,18),(6,9,1),(6,11,1),"
        "(7,12,20),(7,8,1),(8,13,3),(8,8,1),(8,11,1),(10,6,25),(10,9,1),(10,11,1),"
        "(11,1,18),(11,10,1),(11,11,1),(12,1,18),(12,10,1),(12,11,1),"
        "(13,1,18),(13,10,1),(13,11,1)",
        "INSERT OR IGNORE INTO recipes (product_id, inventory_item_id, quantity, modifier_id) VALUES "
        "(4,2,200,10),(4,2,200,11),(4,3,200,12),(4,4,200,13),(4,2,200,14),"
        "(5,2,200,10),(5,2,200,11),(5,3,200,12),(5,4,200,13),(5,2,200,14),"
        "(6,2,160,10),(6,2,160,11),(6,3,160,12),(6,4,160,13),(6,2,160,14),"
        "(10,2,200,10),(10,2,200,11),(10,3,200,12),(10,4,200,13),(10,2,200,14),"
        "(12,2,200,10),(12,2,200,11),(12,3,200,12),(12,4,200,13),(12,2,200,14),"
        "(13,2,200,10),(13,2,200,11),(13,3,200,12),(13,4,200,13),(13,2,200,14),"
        "(3,2,30,10),(3,3,30,12),(3,4,30,13),(11,2,30,10),(11,3,30,12),(11,4,30,13)",
        # Opening balance, only for ingredients that have no movement history at
        # all. After the first real sale this row is skipped, so re-running the
        # migration never doubles the stock.
        "INSERT INTO stock_movements (inventory_item_id, change_qty, reason, note) "
        "SELECT i.id, i.current_qty, 'adjustment', 'رصيد افتتاحي' FROM inventory_items i "
        "WHERE NOT EXISTS (SELECT 1 FROM stock_movements m WHERE m.inventory_item_id = i.id)",
    ],
}


class DatabaseManager:
    """Thread-aware SQLite manager. Use get_db() rather than constructing."""

    _instance: "DatabaseManager | None" = None
    _instance_lock = threading.Lock()

    # -- construction ------------------------------------------------------ #

    def __init__(self, db_path: Path | str | None = None, schema_path: Path | str | None = None):
        self.db_path = Path(db_path or config.DB_PATH)
        self.schema_path = Path(schema_path or config.SCHEMA_PATH)
        self._local = threading.local()
        self._all_connections: set[sqlite3.Connection] = set()
        self._registry_lock = threading.Lock()
        self._initialized = False
        self._init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Process-wide singleton."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- connections ------------------------------------------------------- #

    @property
    def connection(self) -> sqlite3.Connection:
        """The calling thread's connection, created on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    def _new_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,      # autocommit; transactions are explicit
                check_same_thread=True,    # one connection == one thread, enforced
            )
        except sqlite3.Error as exc:  # pragma: no cover - filesystem failure
            raise DatabaseError(f"تعذّر فتح قاعدة البيانات: {exc}") from exc

        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute(f"PRAGMA journal_mode = {config.SQLITE_JOURNAL_MODE}")
        cur.execute(f"PRAGMA synchronous = {config.SQLITE_SYNCHRONOUS}")
        cur.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
        cur.execute(f"PRAGMA cache_size = {config.SQLITE_CACHE_SIZE_KB}")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.close()

        # The database holds PIN hashes and the café's full sales history. sqlite3
        # creates it with the process umask (commonly 0644 = world-readable), so
        # tighten it — and the WAL/SHM sidecars, which contain the same pages.
        config.secure_sqlite_sidecars(self.db_path)

        with self._registry_lock:
            self._all_connections.add(conn)
        return conn

    def close_thread_connection(self) -> None:
        """Close this thread's connection (call before a worker thread exits)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                with self._registry_lock:
                    self._all_connections.discard(conn)
                self._local.conn = None

    def close_all(self) -> None:
        """Close every connection opened by this manager (app shutdown)."""
        with self._registry_lock:
            conns = list(self._all_connections)
            self._all_connections.clear()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
        self._local.conn = None
        self._initialized = False

    # -- transactions ------------------------------------------------------ #

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """
        Explicit transaction. Nested use inside the same thread degrades to a
        SAVEPOINT so partial rollback works without "cannot start a transaction
        within a transaction" errors.

        `immediate=True` takes the write lock up front: on a busy till this
        prevents the upgrade deadlock that plain BEGIN can hit.
        """
        conn = self.connection
        depth = getattr(self._local, "tx_depth", 0)
        try:
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            else:
                conn.execute(f"SAVEPOINT sp_{depth}")
            self._local.tx_depth = depth + 1
            yield conn
        except Exception:
            if depth == 0:
                conn.execute("ROLLBACK")
            else:
                conn.execute(f"ROLLBACK TO sp_{depth}")
                conn.execute(f"RELEASE sp_{depth}")
            raise
        else:
            if depth == 0:
                conn.execute("COMMIT")
            else:
                conn.execute(f"RELEASE sp_{depth}")
        finally:
            self._local.tx_depth = depth

    # -- query helpers ----------------------------------------------------- #

    def execute(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
        """Run a write/DDL statement (auto-committed unless inside transaction())."""
        try:
            return self.connection.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(f"فشل تنفيذ الاستعلام: {exc}") from exc

    def executemany(self, sql: str, seq_params: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        try:
            return self.connection.executemany(sql, seq_params)
        except sqlite3.Error as exc:
            raise DatabaseError(f"فشل تنفيذ الاستعلامات: {exc}") from exc

    def insert(self, sql: str, params: Sequence[Any] | dict = ()) -> int:
        """Execute an INSERT and return lastrowid."""
        return int(self.execute(sql, params).lastrowid or 0)

    def query_all(self, sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
        cur = self.connection.execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def query_one(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
        cur = self.connection.execute(sql, params)
        try:
            return cur.fetchone()
        finally:
            cur.close()

    def query_value(self, sql: str, params: Sequence[Any] | dict = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        return row[0] if row is not None else default

    def scalar(self, sql: str, params: Sequence[Any] | dict = (), default: int = 0) -> int:
        value = self.query_value(sql, params, default)
        return int(value or 0)

    # -- settings helpers (used by nearly every other module) -------------- #

    def get_setting(self, key: str, default: str = "") -> str:
        try:
            value = self.query_value("SELECT value FROM settings WHERE key = ?", (key,))
        except DatabaseError:
            return default
        return default if value is None else str(value)

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, "" if value is None else str(value)),
        )

    def get_all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.query_all("SELECT key, value FROM settings")}

    def get_setting_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get_setting(key, "1" if default else "0").strip().lower()
        return raw in ("1", "true", "yes", "on", "نعم")

    # -- initialisation & migrations --------------------------------------- #

    def initialize(self, force: bool = False) -> "DatabaseManager":
        """
        Create tables from schema.sql if needed, apply migrations, seed defaults.
        Safe to call from any thread and repeatedly; the first call does the work.
        """
        if self._initialized and not force:
            return self

        with self._init_lock:
            if self._initialized and not force:
                return self

            config.ensure_directories()
            version = int(self.query_value("PRAGMA user_version", default=0) or 0)

            if force or version == 0 or not self._schema_present():
                self._apply_schema()
                version = config.SCHEMA_VERSION

            self._run_migrations(version)
            self._seed_users()

            self.execute("PRAGMA optimize")
            self._initialized = True
            logger.info("قاعدة البيانات جاهزة: %s (schema v%s)", self.db_path, config.SCHEMA_VERSION)
            return self

    def _schema_present(self) -> bool:
        return bool(
            self.query_value(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            )
        )

    def _apply_schema(self) -> None:
        if not self.schema_path.exists():
            raise SchemaError(f"ملف المخطط غير موجود: {self.schema_path}")
        sql = self.schema_path.read_text(encoding="utf-8")
        try:
            # executescript() manages its own COMMIT; never call it inside a
            # transaction block.
            self.connection.executescript(sql)
            self.execute(f"PRAGMA user_version = {int(config.SCHEMA_VERSION)}")
        except sqlite3.Error as exc:
            raise SchemaError(f"فشل تطبيق المخطط: {exc}") from exc
        logger.info("تم إنشاء الجداول من %s", self.schema_path.name)

    def _run_migrations(self, current_version: int) -> None:
        target = config.SCHEMA_VERSION
        if current_version >= target:
            return
        for version in range(current_version + 1, target + 1):
            statements = MIGRATIONS.get(version)
            if not statements:
                continue
            try:
                with self.transaction():
                    for stmt in statements:
                        self.execute(stmt)
                    self.execute(f"PRAGMA user_version = {version}")
            except Exception as exc:
                raise MigrationError(f"فشل التحديث إلى الإصدار {version}: {exc}") from exc
            logger.info("تم تطبيق تحديث قاعدة البيانات v%s", version)

    # -- default users (needs Python hashing, so not in schema.sql) -------- #

    def _seed_users(self) -> None:
        """
        Create the first admin + cashier when the users table is empty.
        Delegated to UserRepository so the hashing policy lives in one place.
        Both accounts are flagged must_change_pin: the login view forces a new
        PIN before the till can be used.
        """
        # Pass self explicitly: UserRepository() would call get_db(), which
        # re-enters initialize() and deadlocks on the non-reentrant init lock.
        UserRepository(self).ensure_default_users()

    # -- health & maintenance ---------------------------------------------- #

    def integrity_check(self, quick: bool = False) -> bool:
        sql = "PRAGMA quick_check" if quick else "PRAGMA integrity_check"
        return str(self.query_value(sql, default="")) .lower() == "ok"

    def vacuum(self) -> None:
        self.execute("VACUUM")

    def table_names(self) -> list[str]:
        rows = self.query_all(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def db_size_bytes(self) -> int:
        try:
            return self.db_path.stat().st_size
        except OSError:
            return 0

    # -- backup (used by controllers/backup_controller.py in Phase 4) ------ #

    def backup_to(self, destination: Path | str, *, verify: bool = True) -> Path:
        """
        Hot, consistent copy of the live database to `destination`.

        Uses sqlite3's online backup API (not a file copy) so a backup taken
        mid-transaction is still a valid database, and WAL content is included.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()

        target_conn = sqlite3.connect(str(destination))
        try:
            with self.transaction(immediate=False):
                self.connection.backup(target_conn)
        finally:
            target_conn.close()

        if verify:
            check_conn = sqlite3.connect(str(destination))
            try:
                result = check_conn.execute("PRAGMA quick_check").fetchone()
            finally:
                check_conn.close()
            if not result or str(result[0]).lower() != "ok":
                destination.unlink(missing_ok=True)
                raise DatabaseError("النسخة الاحتياطية تالفة — تم حذفها")

        # A backup is a full copy of the sales database; keep it owner-only too.
        config.secure_file(destination)
        return destination

    def checkpoint(self) -> None:
        """Fold the WAL back into the main DB file (nice before copying)."""
        self.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Re-assert permissions: a checkpoint rewrites the sidecars.
        config.secure_sqlite_sidecars(self.db_path)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DatabaseManager db={self.db_path} schema_v{config.SCHEMA_VERSION}>"


# --------------------------------------------------------------------------- #
# Module-level accessor
# --------------------------------------------------------------------------- #
_db_manager: DatabaseManager | None = None


def get_db(*, initialize: bool = True) -> DatabaseManager:
    """Return the singleton manager, initialising tables on first call."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager.get_instance()
    if initialize:
        _db_manager.initialize()
    return _db_manager


def reset_singleton() -> None:
    """Testing helper: drop the process-wide instance and its connections."""
    global _db_manager
    if _db_manager is not None:
        _db_manager.close_all()
    DatabaseManager._instance = None
    _db_manager = None


def now_stamp() -> str:
    """Local 'YYYY-MM-DD HH:MM:SS' timestamp, matching schema defaults."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
