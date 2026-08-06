"""
app/core/checkout_reconciliation.py
=====================================
Security control for All Items Mode: a check-out only counts as CONFIRMED
once it's been reconciled against a physical dispatch slip / gate pass
number. Every check-out is recorded as 'Pending' first — nothing reads
it as confirmed usage until an authorized user reconciles it.

Dual-backend (Supabase primary / SQLite fallback), mirroring the pattern
in cheese_data_access.py and dry_ice_data_access.py.

Status lifecycle: Pending -> Reconciled (verified against slip)
                            -> Blocked (could not be verified; excluded
                               from confirmed-usage totals until corrected)
"""
from __future__ import annotations
from datetime import datetime, date
from typing import Optional, List, Dict, Any
import logging
import sqlite3
import streamlit as st

logger = logging.getLogger(__name__)

CHECKOUT_SQLITE_FILE = "checkout_reconciliation.db"


def init_checkout_reconciliation_storage(supabase_client=None) -> None:
    """Safe to call on every app run. Only touches SQLite — Supabase table
    must already exist (see stock_checkouts schema below, run once in the
    Supabase SQL editor):

    CREATE TABLE stock_checkouts (
        id BIGSERIAL PRIMARY KEY,
        checkout_date TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL,
        unit TEXT,
        requested_by TEXT,
        destination TEXT,
        dispatch_slip_number TEXT NOT NULL,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'Pending',
        reconciled_by TEXT,
        reconciled_at TEXT,
        reconciliation_notes TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL
    );
    """
    conn = sqlite3.connect(CHECKOUT_SQLITE_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS stock_checkouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checkout_date TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL,
        unit TEXT,
        requested_by TEXT,
        destination TEXT,
        dispatch_slip_number TEXT NOT NULL,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'Pending',
        reconciled_by TEXT,
        reconciled_at TEXT,
        reconciliation_notes TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()


def _sqlite():
    return sqlite3.connect(CHECKOUT_SQLITE_FILE)


def record_checkout(checkout_date: date, item_name: str, quantity: float,
                     dispatch_slip_number: str, unit: str = "",
                     requested_by: str = "", destination: str = "",
                     notes: str = "", created_by: str = "",
                     supabase_client=None) -> Optional[int]:
    """Always created with status='Pending' — a check-out is NOT confirmed
    until reconcile_checkout() is called against it. dispatch_slip_number
    is required (this is the whole point of the control) but validation
    of non-blank is left to the caller/UI so this stays pure persistence."""
    row = {
        "checkout_date": checkout_date.isoformat(), "item_name": item_name,
        "quantity": quantity, "unit": unit, "requested_by": requested_by,
        "destination": destination, "dispatch_slip_number": dispatch_slip_number,
        "notes": notes, "status": "Pending", "created_by": created_by,
        "created_at": datetime.now().isoformat(),
    }
    if supabase_client:
        try:
            result = supabase_client.table("stock_checkouts").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception as e:
            logger.error(f"Supabase record_checkout failed, falling back to SQLite: {e}")
    conn = _sqlite()
    c = conn.cursor()
    c.execute("""INSERT INTO stock_checkouts
        (checkout_date, item_name, quantity, unit, requested_by, destination,
         dispatch_slip_number, notes, status, created_by, created_at)
        VALUES (:checkout_date, :item_name, :quantity, :unit, :requested_by, :destination,
         :dispatch_slip_number, :notes, :status, :created_by, :created_at)""", row)
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_checkouts(status: Optional[str] = None, start_date: Optional[date] = None,
                   end_date: Optional[date] = None, supabase_client=None) -> List[Dict[str, Any]]:
    if supabase_client:
        try:
            query = supabase_client.table("stock_checkouts").select("*").order("checkout_date", desc=True)
            if status:
                query = query.eq("status", status)
            if start_date:
                query = query.gte("checkout_date", start_date.isoformat())
            if end_date:
                query = query.lte("checkout_date", end_date.isoformat())
            return query.execute().data
        except Exception as e:
            logger.error(f"Supabase get_checkouts failed, falling back to SQLite: {e}")
    conn = _sqlite()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM stock_checkouts WHERE 1=1"
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if start_date:
        sql += " AND checkout_date >= ?"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND checkout_date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY checkout_date DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def reconcile_checkout(checkout_id: int, reconciled_by: str, verified: bool,
                        reconciliation_notes: str = "", supabase_client=None) -> None:
    """verified=True  -> status becomes 'Reconciled' (slip number matched
                          the physical dispatch slip, counts as confirmed).
    verified=False -> status becomes 'Blocked' (could not be verified;
                          excluded from confirmed-usage totals until
                          corrected — this is the actual security gate)."""
    new_status = "Reconciled" if verified else "Blocked"
    update = {
        "status": new_status, "reconciled_by": reconciled_by,
        "reconciled_at": datetime.now().isoformat(),
        "reconciliation_notes": reconciliation_notes,
    }
    if supabase_client:
        try:
            supabase_client.table("stock_checkouts").update(update).eq("id", checkout_id).execute()
            return
        except Exception as e:
            logger.error(f"Supabase reconcile_checkout failed, falling back to SQLite: {e}")
    conn = _sqlite()
    conn.execute("""UPDATE stock_checkouts SET status = ?, reconciled_by = ?,
        reconciled_at = ?, reconciliation_notes = ? WHERE id = ?""",
        (new_status, reconciled_by, update["reconciled_at"], reconciliation_notes, checkout_id))
    conn.commit()
    conn.close()


def get_confirmed_checkout_total(item_name: Optional[str] = None,
                                  start_date: Optional[date] = None,
                                  end_date: Optional[date] = None,
                                  supabase_client=None) -> float:
    """Sum of quantity across RECONCILED check-outs only — Pending and
    Blocked are excluded. This is the number any downstream report should
    use as 'confirmed usage,' once wired in — not the raw record count."""
    rows = get_checkouts(status="Reconciled", start_date=start_date,
                          end_date=end_date, supabase_client=supabase_client)
    if item_name:
        rows = [r for r in rows if r["item_name"] == item_name]
    return sum(float(r["quantity"]) for r in rows)