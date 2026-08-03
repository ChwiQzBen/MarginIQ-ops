"""
app/core/cheese_data_access.py
================================
Dual-backend (Supabase primary / SQLite fallback) persistence for the
Cheese Production mode — mirrors the pattern already used for Dry Ice in
main.py (USE_SUPABASE flag, try Supabase first, fall back to SQLite, never
let a DB hiccup crash the UI).

This module owns ONLY persistence + rehydration. All business logic (the
newsvendor math, milk allocation, FEFO, the batch state machine) stays in
newsvendor_engine.py / production_tracking.py / production_plan.py — this
file just loads/saves those objects.

Usage from main.py:

    from app.core.cheese_data_access import (
        init_cheese_storage, load_recipe_book, save_recipe,
        save_milk_receipt, get_milk_receipts, get_milk_liters_for_date,
        load_batch_tracker, get_aging_room_capacity_kg,
        set_aging_room_capacity_kg, get_aging_room_used_kg,
    )
    from app.core.supabase_client import init_supabase
    supabase = init_supabase()          # you already have this
    init_cheese_storage(supabase)       # safe to call every run
    book = load_recipe_book(supabase)
    tracker = load_batch_tracker(supabase)   # a PersistentBatchTracker
"""

from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
import json
import sqlite3
import streamlit as st

from newsvendor_engine import AgingConfig
from production_tracking import (
    RecipeBook, CheeseRecipe, BOMLineItem, OperationStep,
    BatchTracker, ProductionBatch, AgingBatch, FinishedGoodBatch,
    QualityCheckpoint, BatchStatus,
)

CHEESE_SQLITE_FILE = "cheese_production.db"
DEFAULT_AGING_ROOM_CAPACITY_KG = 500.0


# ============================================================
# SQLITE SCHEMA (fallback / local dev — Supabase tables are created via
# cheese_schema.sql, run once in the Supabase SQL editor)
# ============================================================
def init_cheese_storage(supabase_client=None) -> None:
    """Safe to call on every app run. Only touches SQLite — Supabase
    tables must already exist (see cheese_schema.sql)."""
    conn = sqlite3.connect(CHEESE_SQLITE_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS cheese_recipes (
        name TEXT PRIMARY KEY, product_code TEXT, category TEXT,
        batch_size_kg REAL, milk_liters_per_batch REAL,
        shelf_life_days INTEGER, lead_time_days INTEGER,
        non_milk_ingredients TEXT, packaging TEXT, operations TEXT,
        aging_config TEXT, recipe_version TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS milk_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, liters REAL,
        cost_per_liter REAL, supplier TEXT, notes TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS cheese_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, cheese_name TEXT,
        quantity_kg REAL, price_per_kg REAL, revenue REAL, batch_lines TEXT,
        customer TEXT, notes TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS cheese_production_batches (
        batch_id TEXT PRIMARY KEY, cheese_name TEXT, recipe_version TEXT,
        quantity_kg REAL, milk_receipt_ids TEXT, operator TEXT,
        status TEXT, checkpoints TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS cheese_aging_batches (
        batch_id TEXT PRIMARY KEY, production_batch_id TEXT, cheese_name TEXT,
        aging_years REAL, start_date TEXT, scheduled_end_date TEXT,
        starting_quantity_kg REAL, status TEXT, checkpoints TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS cheese_finished_batches (
        batch_id TEXT PRIMARY KEY, production_batch_id TEXT,
        aging_batch_id TEXT, cheese_name TEXT, quantity_kg REAL,
        packaging_date TEXT, expiry_date TEXT, status TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS aging_room_config (
        room_name TEXT PRIMARY KEY, max_capacity_kg REAL, notes TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS lpo_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lpo_number TEXT, customer_name TEXT,
        date_received TEXT, delivery_date TEXT, cheese_name TEXT,
        quantity_kg REAL, quantity_delivered_kg REAL, price_per_kg REAL,
        status TEXT, notes TEXT, created_at TEXT,
        lpo_date TEXT, lpo_expiry_date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        contact_person TEXT, phone TEXT, email TEXT, address TEXT,
        credit_terms_days INTEGER DEFAULT 0, notes TEXT, created_at TEXT
    )""")
    # one-time migration: customer_id FK on cheese_sales / lpo_lines
    c.execute("PRAGMA table_info(cheese_sales)")
    if 'customer_id' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE cheese_sales ADD COLUMN customer_id INTEGER")
    c.execute("PRAGMA table_info(lpo_lines)")
    if 'customer_id' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE lpo_lines ADD COLUMN customer_id INTEGER")
    c.execute("PRAGMA table_info(lpo_lines)")
    if 'lpo_date' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE lpo_lines ADD COLUMN lpo_date TEXT")
    c.execute("PRAGMA table_info(lpo_lines)")
    if 'lpo_expiry_date' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE lpo_lines ADD COLUMN lpo_expiry_date TEXT")
    c.execute("""INSERT OR IGNORE INTO aging_room_config (room_name, max_capacity_kg, notes)
                 VALUES ('default', ?, 'Set this to your real aging room capacity in kg')""",
              (DEFAULT_AGING_ROOM_CAPACITY_KG,))
    conn.commit()
    conn.close()


def _sqlite():
    return sqlite3.connect(CHEESE_SQLITE_FILE)


# ============================================================
# SERIALIZATION HELPERS
# ============================================================
def _as_list(raw) -> list:
    """Supabase (JSONB) hands back a python list/dict already; SQLite
    hands back a JSON string. Normalize both to a python object."""
    if raw is None:
        return []
    if isinstance(raw, (list, dict)):
        return raw
    return json.loads(raw)


def _checkpoints_to_json(checkpoints: List[QualityCheckpoint]) -> str:
    return json.dumps([{
        "stage": c.stage, "required": c.required, "status": c.status,
        "checked_at": c.checked_at.isoformat() if c.checked_at else None,
        "notes": c.notes,
    } for c in checkpoints])


def _json_to_checkpoints(raw) -> List[QualityCheckpoint]:
    out = []
    for d in _as_list(raw):
        cp = QualityCheckpoint(stage=d["stage"], required=d.get("required", True))
        cp.status = d.get("status", "Pending")
        cp.notes = d.get("notes", "")
        checked_at = d.get("checked_at")
        cp.checked_at = datetime.fromisoformat(checked_at) if checked_at else None
        out.append(cp)
    return out


def _recipe_to_row(recipe: CheeseRecipe) -> Dict[str, Any]:
    return {
        "name": recipe.name,
        "product_code": recipe.product_code,
        "category": recipe.category,
        "batch_size_kg": recipe.batch_size_kg,
        "milk_liters_per_batch": recipe.milk_liters_per_batch,
        "shelf_life_days": recipe.shelf_life_days,
        "lead_time_days": recipe.lead_time_days,
        "non_milk_ingredients": json.dumps([asdict(i) for i in recipe.non_milk_ingredients]),
        "packaging": json.dumps([asdict(i) for i in recipe.packaging]),
        "operations": json.dumps([asdict(o) for o in recipe.operations]),
        "aging_config": json.dumps(asdict(recipe.aging)) if recipe.aging else None,
        "recipe_version": recipe.recipe_version,
    }


def _row_to_recipe(row: Dict[str, Any]) -> CheeseRecipe:
    aging = None
    if row.get("aging_config"):
        raw = row["aging_config"]
        aging_data = raw if isinstance(raw, dict) else json.loads(raw)
        aging = AgingConfig(**aging_data)
    non_milk = [BOMLineItem(**i) for i in _as_list(row.get("non_milk_ingredients"))]
    packaging = [BOMLineItem(**i) for i in _as_list(row.get("packaging"))]
    operations = [OperationStep(**o) for o in _as_list(row.get("operations"))]
    return CheeseRecipe(
        name=row["name"], product_code=row.get("product_code") or "",
        category=row.get("category") or "",
        batch_size_kg=row["batch_size_kg"], milk_liters_per_batch=row["milk_liters_per_batch"],
        shelf_life_days=row["shelf_life_days"], lead_time_days=row["lead_time_days"],
        non_milk_ingredients=non_milk, packaging=packaging, operations=operations,
        aging=aging, recipe_version=row.get("recipe_version") or "v1.0",
    )


# ============================================================
# RECIPES
# ============================================================
def save_recipe(recipe: CheeseRecipe, supabase_client=None) -> None:
    row = _recipe_to_row(recipe)
    if supabase_client:
        try:
            supabase_client.table("cheese_recipes").upsert(row).execute()
            return
        except Exception:
            pass  # fall through to SQLite
    conn = _sqlite()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO cheese_recipes
        (name, product_code, category, batch_size_kg, milk_liters_per_batch,
         shelf_life_days, lead_time_days, non_milk_ingredients, packaging,
         operations, aging_config, recipe_version)
        VALUES (:name, :product_code, :category, :batch_size_kg, :milk_liters_per_batch,
         :shelf_life_days, :lead_time_days, :non_milk_ingredients, :packaging,
         :operations, :aging_config, :recipe_version)""", row)
    conn.commit()
    conn.close()


def delete_recipe(name: str, supabase_client=None) -> None:
    if supabase_client:
        try:
            supabase_client.table("cheese_recipes").delete().eq("name", name).execute()
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("DELETE FROM cheese_recipes WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def load_recipe_book(supabase_client=None) -> RecipeBook:
    book = RecipeBook()
    rows = None
    if supabase_client:
        try:
            rows = supabase_client.table("cheese_recipes").select("*").execute().data
        except Exception:
            rows = None
    if rows is None:
        conn = _sqlite()
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM cheese_recipes").fetchall()]
        conn.close()
    for row in rows:
        try:
            book.add(_row_to_recipe(row))
        except Exception:
            continue  # skip a corrupted row rather than crashing the whole app
    return book


# ============================================================
# MILK RECEIPTS  (the explicitly-called-out "milk receipt persistence")
# ============================================================
def save_milk_receipt(receipt_date: date, liters: float, cost_per_liter: float,
                       supplier: str = "", notes: str = "",
                       supabase_client=None) -> Optional[int]:
    date_str = receipt_date.isoformat() if hasattr(receipt_date, "isoformat") else str(receipt_date)
    row = {"date": date_str, "liters": liters, "cost_per_liter": cost_per_liter,
           "supplier": supplier, "notes": notes}
    if supabase_client:
        try:
            result = supabase_client.table("milk_receipts").insert(row).execute()
            get_weighted_milk_cost_for_date.clear()
            return result.data[0]["id"] if result.data else None
        except Exception:
            pass
    conn = _sqlite()
    c = conn.cursor()
    c.execute("""INSERT INTO milk_receipts (date, liters, cost_per_liter, supplier, notes, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (date_str, liters, cost_per_liter, supplier, notes, datetime.now().isoformat()))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    get_weighted_milk_cost_for_date.clear()
    return new_id


def get_milk_receipts(start_date: Optional[date] = None, end_date: Optional[date] = None,
                       supabase_client=None) -> List[Dict[str, Any]]:
    if supabase_client:
        try:
            query = supabase_client.table("milk_receipts").select("*").order("date", desc=True)
            if start_date:
                query = query.gte("date", start_date.isoformat())
            if end_date:
                query = query.lte("date", end_date.isoformat())
            return query.execute().data
        except Exception:
            pass
    conn = _sqlite()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM milk_receipts WHERE 1=1"
    params = []
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY date DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_milk_liters_for_date(target_date: date, supabase_client=None) -> float:
    """Total milk received on a given day — the natural default for
    'milk_available_l' in a no-storage daily allocation model."""
    receipts = get_milk_receipts(start_date=target_date, end_date=target_date,
                                  supabase_client=supabase_client)
    return sum(r["liters"] for r in receipts)


# ============================================================
# CHEESE SALES  (feeds CheeseDemandForecaster's daily_sales_kg input)
# ============================================================
def save_cheese_sale(sale_date: date, cheese_name: str, quantity_kg: float,
                      price_per_kg: float, batch_lines: List[Dict[str, Any]],
                      customer: str = "", notes: str = "",
                      supabase_client=None, customer_id: Optional[int] = None) -> Optional[int]:
    """Persists one sale EVENT, including which batch(es) it was fulfilled
    from (batch_lines = [{"batch_id": ..., "quantity_kg": ...}, ...] — the
    same shape as FEFOAllocationResult.lines), so a sale stays traceable
    back to a specific ProductionBatch via BatchTracker.trace(). This does
    NOT decrement stock itself — call FEFOInventory.allocate(commit=True)
    for that, then pass its .lines here. Keeping them separate matches this
    module's stated scope: persistence only, business logic lives elsewhere.

    customer_id links this sale to the customers table for Customer
    Analytics — optional and appended at the end of the signature (not
    inserted between existing params) so old positional call sites don't
    silently shift their other arguments."""
    date_str = sale_date.isoformat() if hasattr(sale_date, "isoformat") else str(sale_date)
    revenue = quantity_kg * price_per_kg
    row = {
        "date": date_str, "cheese_name": cheese_name, "quantity_kg": quantity_kg,
        "price_per_kg": price_per_kg, "revenue": revenue,
        "batch_lines": json.dumps(batch_lines), "customer": customer,
        "customer_id": customer_id, "notes": notes,
    }
    if supabase_client:
        try:
            result = supabase_client.table("cheese_sales").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception:
            pass
    conn = _sqlite()
    c = conn.cursor()
    c.execute("""INSERT INTO cheese_sales
        (date, cheese_name, quantity_kg, price_per_kg, revenue, batch_lines, customer, customer_id, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (date_str, cheese_name, quantity_kg, price_per_kg, revenue,
               json.dumps(batch_lines), customer, customer_id, notes, datetime.now().isoformat()))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_sales_history(cheese_name: Optional[str] = None, start_date: Optional[date] = None,
                       end_date: Optional[date] = None, supabase_client=None) -> List[Dict[str, Any]]:
    """Raw sale rows, most recent first. Omit cheese_name for all cheeses
    (used by the Sales tab's recent-activity table)."""
    if supabase_client:
        try:
            query = supabase_client.table("cheese_sales").select("*").order("date", desc=True)
            if cheese_name:
                query = query.eq("cheese_name", cheese_name)
            if start_date:
                query = query.gte("date", start_date.isoformat())
            if end_date:
                query = query.lte("date", end_date.isoformat())
            return query.execute().data
        except Exception:
            pass
    conn = _sqlite()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM cheese_sales WHERE 1=1"
    params = []
    if cheese_name:
        sql += " AND cheese_name = ?"
        params.append(cheese_name)
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY date DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_daily_sales_kg(cheese_name: str, days: int = 90, supabase_client=None) -> List[float]:
    """Chronologically-ordered (oldest -> newest) total kg sold per day over
    the last `days` days, zero-filled on no-sale days — exactly the
    daily_sales_kg shape CheeseDemandForecaster.forecast() expects. Zero-
    filling matters: a slow day should pull the mean down, not disappear
    from the series entirely."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = get_sales_history(cheese_name=cheese_name, start_date=start, end_date=end,
                              supabase_client=supabase_client)
    totals_by_day: Dict[str, float] = {}
    for r in rows:
        totals_by_day[r["date"]] = totals_by_day.get(r["date"], 0.0) + float(r["quantity_kg"])
    return [totals_by_day.get((start + timedelta(days=i)).isoformat(), 0.0) for i in range(days)]


# ============================================================
# BATCH TRACKER PERSISTENCE
# ============================================================
def _save_production_batch_row(batch: ProductionBatch, supabase_client=None) -> None:
    row = {
        "batch_id": batch.batch_id, "cheese_name": batch.cheese_name,
        "recipe_version": batch.recipe_version, "quantity_kg": batch.quantity_kg,
        "milk_receipt_ids": json.dumps(batch.milk_receipt_ids),
        "operator": batch.operator, "status": batch.status.value,
        "checkpoints": _checkpoints_to_json(batch.checkpoints),
        "created_at": batch.created_at.isoformat(),
    }
    if supabase_client:
        try:
            supabase_client.table("cheese_production_batches").upsert(row).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("""INSERT OR REPLACE INTO cheese_production_batches
        (batch_id, cheese_name, recipe_version, quantity_kg, milk_receipt_ids,
         operator, status, checkpoints, created_at)
        VALUES (:batch_id, :cheese_name, :recipe_version, :quantity_kg, :milk_receipt_ids,
         :operator, :status, :checkpoints, :created_at)""", row)
    conn.commit()
    conn.close()


def _save_aging_batch_row(batch: AgingBatch, supabase_client=None) -> None:
    row = {
        "batch_id": batch.batch_id, "production_batch_id": batch.production_batch_id,
        "cheese_name": batch.cheese_name, "aging_years": batch.aging_years,
        "start_date": batch.start_date.isoformat(),
        "scheduled_end_date": batch.scheduled_end_date.isoformat(),
        "starting_quantity_kg": batch.starting_quantity_kg,
        "status": batch.status.value,
        "checkpoints": _checkpoints_to_json(batch.checkpoints),
    }
    if supabase_client:
        try:
            supabase_client.table("cheese_aging_batches").upsert(row).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("""INSERT OR REPLACE INTO cheese_aging_batches
        (batch_id, production_batch_id, cheese_name, aging_years, start_date,
         scheduled_end_date, starting_quantity_kg, status, checkpoints)
        VALUES (:batch_id, :production_batch_id, :cheese_name, :aging_years, :start_date,
         :scheduled_end_date, :starting_quantity_kg, :status, :checkpoints)""", row)
    conn.commit()
    conn.close()


def _save_finished_batch_row(batch: FinishedGoodBatch, supabase_client=None) -> None:
    row = {
        "batch_id": batch.batch_id, "production_batch_id": batch.production_batch_id,
        "aging_batch_id": batch.aging_batch_id, "cheese_name": batch.cheese_name,
        "quantity_kg": batch.quantity_kg,
        "packaging_date": batch.packaging_date.isoformat(),
        "expiry_date": batch.expiry_date.isoformat(),
        "status": batch.status.value,
    }
    if supabase_client:
        try:
            supabase_client.table("cheese_finished_batches").upsert(row).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("""INSERT OR REPLACE INTO cheese_finished_batches
        (batch_id, production_batch_id, aging_batch_id, cheese_name, quantity_kg,
         packaging_date, expiry_date, status)
        VALUES (:batch_id, :production_batch_id, :aging_batch_id, :cheese_name, :quantity_kg,
         :packaging_date, :expiry_date, :status)""", row)
    conn.commit()
    conn.close()


class PersistentBatchTracker(BatchTracker):
    """Drop-in replacement for BatchTracker that persists every mutation.
    Everything else about production_tracking.py's API is unchanged —
    the newsvendor/production-plan code doesn't need to know this exists."""

    def __init__(self, supabase_client=None):
        super().__init__()
        self._supabase_client = supabase_client

    def start_production(self, *args, **kwargs) -> ProductionBatch:
        batch = super().start_production(*args, **kwargs)
        _save_production_batch_row(batch, self._supabase_client)
        return batch

    def record_production_checkpoint(self, *args, **kwargs) -> ProductionBatch:
        batch = super().record_production_checkpoint(*args, **kwargs)
        _save_production_batch_row(batch, self._supabase_client)
        return batch

    def start_aging(self, *args, **kwargs) -> AgingBatch:
        batch = super().start_aging(*args, **kwargs)
        # QC status on the production batch flips to AGING — persist both
        pb = self.production_batches[batch.production_batch_id]
        _save_production_batch_row(pb, self._supabase_client)
        _save_aging_batch_row(batch, self._supabase_client)
        return batch

    def release_from_aging(self, *args, **kwargs) -> FinishedGoodBatch:
        fg = super().release_from_aging(*args, **kwargs)
        ab = self.aging_batches[fg.aging_batch_id]
        _save_aging_batch_row(ab, self._supabase_client)
        _save_finished_batch_row(fg, self._supabase_client)
        return fg

    def release_fresh_to_finished(self, *args, **kwargs) -> FinishedGoodBatch:
        fg = super().release_fresh_to_finished(*args, **kwargs)
        pb = self.production_batches[fg.production_batch_id]
        _save_production_batch_row(pb, self._supabase_client)
        _save_finished_batch_row(fg, self._supabase_client)
        return fg


def load_batch_tracker(supabase_client=None) -> PersistentBatchTracker:
    """Rehydrates a PersistentBatchTracker from whichever backend has data,
    so batches survive an app restart / redeploy instead of living only in
    st.session_state for the current browser session."""
    tracker = PersistentBatchTracker(supabase_client)

    pb_rows = fg_rows = ab_rows = None
    if supabase_client:
        try:
            pb_rows = supabase_client.table("cheese_production_batches").select("*").execute().data
            ab_rows = supabase_client.table("cheese_aging_batches").select("*").execute().data
            fg_rows = supabase_client.table("cheese_finished_batches").select("*").execute().data
        except Exception:
            pb_rows = ab_rows = fg_rows = None

    if pb_rows is None:
        conn = _sqlite()
        conn.row_factory = sqlite3.Row
        pb_rows = [dict(r) for r in conn.execute("SELECT * FROM cheese_production_batches").fetchall()]
        ab_rows = [dict(r) for r in conn.execute("SELECT * FROM cheese_aging_batches").fetchall()]
        fg_rows = [dict(r) for r in conn.execute("SELECT * FROM cheese_finished_batches").fetchall()]
        conn.close()

    for row in pb_rows:
        tracker.production_batches[row["batch_id"]] = ProductionBatch(
            batch_id=row["batch_id"], cheese_name=row["cheese_name"],
            recipe_version=row.get("recipe_version") or "v1.0",
            quantity_kg=row["quantity_kg"],
            milk_receipt_ids=_as_list(row.get("milk_receipt_ids")),
            operator=row.get("operator") or "", status=BatchStatus(row["status"]),
            checkpoints=_json_to_checkpoints(row.get("checkpoints")),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    for row in ab_rows:
        tracker.aging_batches[row["batch_id"]] = AgingBatch(
            batch_id=row["batch_id"], production_batch_id=row["production_batch_id"],
            cheese_name=row["cheese_name"], aging_years=row["aging_years"],
            start_date=datetime.fromisoformat(row["start_date"]),
            scheduled_end_date=datetime.fromisoformat(row["scheduled_end_date"]),
            starting_quantity_kg=row["starting_quantity_kg"], status=BatchStatus(row["status"]),
            checkpoints=_json_to_checkpoints(row.get("checkpoints")),
        )
    for row in fg_rows:
        tracker.finished_batches[row["batch_id"]] = FinishedGoodBatch(
            batch_id=row["batch_id"], production_batch_id=row["production_batch_id"],
            aging_batch_id=row.get("aging_batch_id"), cheese_name=row["cheese_name"],
            quantity_kg=row["quantity_kg"],
            packaging_date=datetime.fromisoformat(row["packaging_date"]),
            expiry_date=datetime.fromisoformat(row["expiry_date"]), status=BatchStatus(row["status"]),
        )
    return tracker


# ============================================================
# AGING ROOM CAPACITY  (the explicitly-called-out capacity constraint)
# ============================================================
def get_aging_room_capacity_kg(room_name: str = "default", supabase_client=None) -> float:
    if supabase_client:
        try:
            result = supabase_client.table("aging_room_config").select("*").eq("room_name", room_name).execute()
            if result.data:
                return float(result.data[0]["max_capacity_kg"])
        except Exception:
            pass
    conn = _sqlite()
    row = conn.execute("SELECT max_capacity_kg FROM aging_room_config WHERE room_name = ?",
                        (room_name,)).fetchone()
    conn.close()
    return float(row[0]) if row else DEFAULT_AGING_ROOM_CAPACITY_KG


def set_aging_room_capacity_kg(max_capacity_kg: float, room_name: str = "default",
                                 notes: str = "", supabase_client=None) -> None:
    row = {"room_name": room_name, "max_capacity_kg": max_capacity_kg, "notes": notes}
    if supabase_client:
        try:
            supabase_client.table("aging_room_config").upsert(row).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("INSERT OR REPLACE INTO aging_room_config (room_name, max_capacity_kg, notes) VALUES (?, ?, ?)",
                 (room_name, max_capacity_kg, notes))
    conn.commit()
    conn.close()


def get_aging_room_used_kg(tracker: BatchTracker) -> float:
    """Kg currently occupying the aging room — everything with status
    AGING. (Finished/failed/recalled batches have left the room.)"""
    return sum(
        b.starting_quantity_kg for b in tracker.aging_batches.values()
        if b.status == BatchStatus.AGING
    )


def check_aging_room_capacity(tracker: BatchTracker, additional_kg: float,
                                room_name: str = "default", supabase_client=None):
    """Returns (ok: bool, used_kg: float, capacity_kg: float, remaining_kg: float).
    Use this BEFORE calling tracker.start_aging() to decide whether to warn,
    block, or cap the quantity going into aging."""
    capacity = get_aging_room_capacity_kg(room_name, supabase_client)
    used = get_aging_room_used_kg(tracker)
    remaining = capacity - used
    ok = additional_kg <= remaining
    return ok, used, capacity, remaining

@st.cache_data(ttl=60)
def get_weighted_milk_cost_for_date(target_date: date) -> float:
    """Weighted-average cost/liter across today's receipts. Returns 0.0
    if nothing's been received yet — caller should fall back to a default.
    Cached 60s: this was hitting Supabase on every BCPOS rerun."""
    # Initialize Supabase INSIDE the function to avoid hashing issues
    from app.core.supabase_client import init_supabase
    supabase_client = init_supabase()
    
    receipts = get_milk_receipts(start_date=target_date, supabase_client=supabase_client)
    todays = [r for r in receipts if r["date"] == target_date.isoformat()]
    total_liters = sum(r["liters"] for r in todays)
    if total_liters <= 0:
        return 0.0
    total_cost = sum(r["liters"] * r["cost_per_liter"] for r in todays)
    return total_cost / total_liters

# ============================================================
# LPO REGISTER  (confirmed customer demand — floors production_plan.py's
# newsvendor quantity instead of being blended into the forecast mean)
# ============================================================
def save_lpo_line(lpo_number: str, customer_name: str, delivery_date: date,
                   cheese_name: str, quantity_kg: float, price_per_kg: float = 0.0,
                   date_received: Optional[date] = None, notes: str = "",
                   supabase_client=None, customer_id: Optional[int] = None,
                   lpo_date: Optional[date] = None,
                   lpo_expiry_date: Optional[date] = None) -> Optional[int]:
    date_received = date_received or date.today()
    row = {
        "lpo_number": lpo_number, "customer_name": customer_name, "customer_id": customer_id,
        "date_received": date_received.isoformat(), "delivery_date": delivery_date.isoformat(),
        "cheese_name": cheese_name, "quantity_kg": quantity_kg,
        "quantity_delivered_kg": None, "price_per_kg": price_per_kg,
        "status": "Pending", "notes": notes,
        "lpo_date": lpo_date.isoformat() if lpo_date else None,
        "lpo_expiry_date": lpo_expiry_date.isoformat() if lpo_expiry_date else None,
    }
    if supabase_client:
        try:
            result = supabase_client.table("lpo_lines").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception:
            pass
    conn = _sqlite()
    c = conn.cursor()
    c.execute("""INSERT INTO lpo_lines
        (lpo_number, customer_name, customer_id, date_received, delivery_date, cheese_name,
         quantity_kg, quantity_delivered_kg, price_per_kg, status, notes, created_at,
         lpo_date, lpo_expiry_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'Pending', ?, ?, ?, ?)""",
              (lpo_number, customer_name, customer_id, date_received.isoformat(), delivery_date.isoformat(),
               cheese_name, quantity_kg, price_per_kg, notes, datetime.now().isoformat(),
               row["lpo_date"], row["lpo_expiry_date"]))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_lpo_lines(delivery_date: Optional[date] = None, status: Optional[str] = None,
                   supabase_client=None) -> List[Dict[str, Any]]:
    if supabase_client:
        try:
            query = supabase_client.table("lpo_lines").select("*").order("delivery_date")
            if delivery_date:
                query = query.eq("delivery_date", delivery_date.isoformat())
            if status:
                query = query.eq("status", status)
            return query.execute().data
        except Exception:
            pass
    conn = _sqlite()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM lpo_lines WHERE 1=1"
    params = []
    if delivery_date:
        sql += " AND delivery_date = ?"
        params.append(delivery_date.isoformat())
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY delivery_date"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_confirmed_demand_for_date(target_date: date, supabase_client=None) -> Dict[str, float]:
    """Sum of REMAINING (undelivered) open LPO quantity per cheese for a
    delivery date — this is what production_plan.py's build_plan(
    confirmed_demand=...) floors production against. Excludes Cancelled
    and fully-Delivered lines. A Partially Delivered line contributes only
    what's still owed (quantity_kg - quantity_delivered_kg), not the
    original quantity_kg — otherwise the floor keeps demanding cheese
    that's already left the building."""
    lines = get_lpo_lines(delivery_date=target_date, supabase_client=supabase_client)
    confirmed: Dict[str, float] = {}
    for line in lines:
        if line["status"] in ("Cancelled", "Delivered"):
            continue
        delivered_kg = float(line.get("quantity_delivered_kg") or 0.0)
        remaining_kg = float(line["quantity_kg"]) - delivered_kg
        if remaining_kg <= 0:
            continue
        confirmed[line["cheese_name"]] = confirmed.get(line["cheese_name"], 0.0) + remaining_kg
    return confirmed


def record_lpo_delivery(lpo_line_id: int, quantity_delivered_kg: float,
                         supabase_client=None) -> None:
    """Call this when a delivery against an LPO line happens. Sets status
    to 'Delivered' if fully met, else 'Partially Delivered' — the shortfall
    is what should feed a lost-sales metric in Customer Analytics later."""
    rows = None
    if supabase_client:
        try:
            rows = supabase_client.table("lpo_lines").select("*").eq("id", lpo_line_id).execute().data
        except Exception:
            rows = None
    if rows is None:
        conn = _sqlite()
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM lpo_lines WHERE id = ?", (lpo_line_id,)).fetchone()
        conn.close()
        rows = [dict(r)] if r else []

    if not rows:
        raise ValueError(f"LPO line {lpo_line_id} not found")

    requested_kg = float(rows[0]["quantity_kg"])
    status = "Delivered" if quantity_delivered_kg >= requested_kg - 1e-6 else "Partially Delivered"

    if supabase_client:
        try:
            supabase_client.table("lpo_lines").update({
                "quantity_delivered_kg": quantity_delivered_kg, "status": status,
            }).eq("id", lpo_line_id).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("UPDATE lpo_lines SET quantity_delivered_kg = ?, status = ? WHERE id = ?",
                 (quantity_delivered_kg, status, lpo_line_id))
    conn.commit()
    conn.close()

def cancel_lpo_line(lpo_line_id: int, supabase_client=None) -> None:
    """Cancel an LPO line - sets status to 'Cancelled'."""
    if supabase_client:
        try:
            supabase_client.table("lpo_lines").update({
                "status": "Cancelled"
            }).eq("id", lpo_line_id).execute()
            return
        except Exception:
            pass
    
    conn = _sqlite()
    conn.execute("UPDATE lpo_lines SET status = 'Cancelled' WHERE id = ?", (lpo_line_id,))
    conn.commit()
    conn.close()

# ============================================================
# CUSTOMERS  (registry only for now — Sales/LPO customer fields stay
# freetext until a reconciliation pass links existing history to this
# table; see commercial_ui.py's module docstring)
# ============================================================
def save_customer(name: str, contact_person: str = "", phone: str = "", email: str = "",
                   address: str = "", credit_terms_days: int = 0, notes: str = "",
                   customer_id: Optional[int] = None, supabase_client=None) -> Optional[int]:
    row = {
        "name": name, "contact_person": contact_person, "phone": phone, "email": email,
        "address": address, "credit_terms_days": credit_terms_days, "notes": notes,
    }
    if supabase_client:
        try:
            if customer_id is not None:
                supabase_client.table("customers").update(row).eq("id", customer_id).execute()
                return customer_id
            row["created_at"] = datetime.now().isoformat()
            result = supabase_client.table("customers").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception:
            pass
    conn = _sqlite()
    c = conn.cursor()
    if customer_id is not None:
        row["id"] = customer_id
        c.execute("""UPDATE customers SET name=:name, contact_person=:contact_person, phone=:phone,
            email=:email, address=:address, credit_terms_days=:credit_terms_days, notes=:notes
            WHERE id=:id""", row)
        new_id = customer_id
    else:
        row["created_at"] = datetime.now().isoformat()
        c.execute("""INSERT INTO customers (name, contact_person, phone, email, address,
            credit_terms_days, notes, created_at)
            VALUES (:name, :contact_person, :phone, :email, :address, :credit_terms_days, :notes, :created_at)""", row)
        new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_customers(supabase_client=None) -> List[Dict[str, Any]]:
    if supabase_client:
        try:
            return supabase_client.table("customers").select("*").order("name").execute().data
        except Exception:
            pass
    conn = _sqlite()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY name").fetchall()]
    conn.close()
    return rows


def delete_customer(customer_id: int, supabase_client=None) -> None:
    if supabase_client:
        try:
            supabase_client.table("customers").delete().eq("id", customer_id).execute()
            return
        except Exception:
            pass
    conn = _sqlite()
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    
def reconcile_customers_from_history(supabase_client=None) -> Dict[str, int]:
    """One-time (safe to re-run) backfill: matches freetext customer names in
    cheese_sales.customer and lpo_lines.customer_name to a customers row —
    case-insensitive, trimmed — creating a new customer record for any name
    that doesn't already exist, then sets customer_id on the historical row.
    Returns {'customers_created': N, 'sales_linked': N, 'lpo_linked': N}.
    Call this once before trusting any customer-level analytics; safe to
    call again later if new unlinked freetext names show up."""
    existing = get_customers(supabase_client)
    by_normalized = {c["name"].strip().lower(): c["id"] for c in existing}
    customers_created_before = len(by_normalized)

    def _get_or_create(raw_name: str) -> Optional[int]:
        name = (raw_name or "").strip()
        if not name:
            return None
        key = name.lower()
        if key in by_normalized:
            return by_normalized[key]
        new_id = save_customer(name=name, supabase_client=supabase_client)
        by_normalized[key] = new_id
        return new_id

    sales_linked = 0
    if supabase_client:
        try:
            unlinked = supabase_client.table("cheese_sales").select("id, customer") \
                .is_("customer_id", "null").execute().data
        except Exception:
            unlinked = []
    else:
        conn = _sqlite()
        conn.row_factory = sqlite3.Row
        unlinked = [dict(r) for r in conn.execute(
            "SELECT id, customer FROM cheese_sales WHERE customer_id IS NULL").fetchall()]
        conn.close()

    for row in unlinked:
        cid = _get_or_create(row.get("customer"))
        if cid is None:
            continue
        if supabase_client:
            try:
                supabase_client.table("cheese_sales").update({"customer_id": cid}).eq("id", row["id"]).execute()
            except Exception:
                continue
        else:
            conn = _sqlite()
            conn.execute("UPDATE cheese_sales SET customer_id = ? WHERE id = ?", (cid, row["id"]))
            conn.commit()
            conn.close()
        sales_linked += 1

    lpo_linked = 0
    if supabase_client:
        try:
            unlinked_lpo = supabase_client.table("lpo_lines").select("id, customer_name") \
                .is_("customer_id", "null").execute().data
        except Exception:
            unlinked_lpo = []
    else:
        conn = _sqlite()
        conn.row_factory = sqlite3.Row
        unlinked_lpo = [dict(r) for r in conn.execute(
            "SELECT id, customer_name FROM lpo_lines WHERE customer_id IS NULL").fetchall()]
        conn.close()

    for row in unlinked_lpo:
        cid = _get_or_create(row.get("customer_name"))
        if cid is None:
            continue
        if supabase_client:
            try:
                supabase_client.table("lpo_lines").update({"customer_id": cid}).eq("id", row["id"]).execute()
            except Exception:
                continue
        else:
            conn = _sqlite()
            conn.execute("UPDATE lpo_lines SET customer_id = ? WHERE id = ?", (cid, row["id"]))
            conn.commit()
            conn.close()
        lpo_linked += 1

    return {
        "customers_created": len(by_normalized) - customers_created_before,
        "sales_linked": sales_linked,
        "lpo_linked": lpo_linked,
    }    