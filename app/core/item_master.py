"""
app/core/item_master.py
=========================
Canonical item identity and attributes -- the in-app replacement for the
Stock/Inventory Google Sheet's item list. Supabase-only (no SQLite
fallback): unlike checkout_reconciliation.py, which protects individual
records written throughout the day, this is reference data read constantly
by nearly every other part of the app, so a brief outage is better handled
by the existing @st.cache_data layer upstream than by a second backend to
keep in sync.

Covers item IDENTITY and ATTRIBUTES only (name, category, unit, price,
reorder level). Does NOT compute "current stock" -- that's Phase 2b, a
shared ledger function (seed/last-Stock-Take-anchor + Check-Ins -
Check-Outs +/- Transfers) shared with _compute_stock_variance's existing
logic. Until that lands, items added here do not appear in
ctx.inventory_items, the Inventory tab, or the Check-Out form's item
picker -- those still read the Google Sheet.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

TABLE = "item_master"


def get_all_items(active_only: bool = True, supabase_client=None) -> List[Dict[str, Any]]:
    if not supabase_client:
        logger.warning("get_all_items called with no Supabase client -- returning empty list.")
        return []
    try:
        query = supabase_client.table(TABLE).select("*").order("item_name")
        if active_only:
            query = query.eq("active", True)
        return query.execute().data
    except Exception as e:
        logger.error(f"get_all_items failed: {e}")
        return []


def get_item(item_name: str, supabase_client=None) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        return None
    try:
        result = supabase_client.table(TABLE).select("*").eq("item_name", item_name).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"get_item failed for {item_name}: {e}")
        return None


def create_item(item_name: str, item_category: str = "", unit_of_measure: str = "kg",
                 unit_price: float = 0.0, reorder_level: float = 0.0,
                 item_serial: str = "", seed_quantity: float = 0.0,
                 created_by: str = "", supabase_client=None) -> Optional[int]:
    """Raises on a duplicate item_name (the UNIQUE constraint) rather than
    silently creating a second, ambiguous record for the same item --
    exactly the kind of drift this table exists to prevent. Caller should
    catch and show a clear message."""
    if not supabase_client:
        return None
    row = {
        "item_name": item_name.strip(), "item_serial": item_serial.strip(),
        "item_category": item_category.strip(), "unit_of_measure": unit_of_measure.strip(),
        "unit_price": unit_price, "reorder_level": reorder_level,
        "seed_quantity": seed_quantity, "active": True, "created_by": created_by,
        "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
    }
    result = supabase_client.table(TABLE).insert(row).execute()
    return result.data[0]["id"] if result.data else None


def update_item(item_name: str, updates: Dict[str, Any], supabase_client=None) -> bool:
    if not supabase_client:
        return False
    try:
        updates = dict(updates)
        updates["updated_at"] = datetime.now().isoformat()
        result = supabase_client.table(TABLE).update(updates).eq("item_name", item_name).execute()
        return bool(result.data)
    except Exception as e:
        logger.error(f"update_item failed for {item_name}: {e}")
        return False


def deactivate_item(item_name: str, supabase_client=None) -> bool:
    """Soft delete -- keeps the record, and every historical Check-In/
    Check-Out/Stock Take/Transfer reference to it, intact."""
    return update_item(item_name, {"active": False}, supabase_client=supabase_client)
