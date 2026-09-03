"""
app/core/checkin_records.py
=============================
In-app Check-In records -- the entry-side replacement for the Check-In
Google Sheet. Unlike checkout_reconciliation.py, no reconciliation
workflow here yet: Check-In actually has a real verification document
(INVOICE_LPO), captured but not currently gated on -- add a
pending/confirmed flow later if you want that field to do more than
record itself. Supabase-only, same reasoning as item_master.py.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

TABLE = "stock_checkins"


def record_checkin(checkin_date: date, item_name: str, quantity: float,
                    unit: str = "", item_category: str = "", unit_price: float = 0.0,
                    supplier: str = "", store: str = "", batch_no: str = "",
                    temperature: str = "", coa: str = "", invoice_lpo: str = "",
                    received_by: str = "", confirmed_by: str = "", notes: str = "",
                    created_by: str = "", supabase_client=None) -> Optional[int]:
    if not supabase_client:
        return None
    row = {
        "checkin_date": checkin_date.isoformat(), "item_name": item_name,
        "item_category": item_category, "quantity": quantity, "unit": unit,
        "unit_price": unit_price, "supplier": supplier, "store": store,
        "batch_no": batch_no, "temperature": temperature, "coa": coa,
        "invoice_lpo": invoice_lpo, "received_by": received_by,
        "confirmed_by": confirmed_by, "notes": notes, "created_by": created_by,
        "created_at": datetime.now().isoformat(),
    }
    result = supabase_client.table(TABLE).insert(row).execute()
    return result.data[0]["id"] if result.data else None


def get_checkins(start_date: Optional[date] = None, end_date: Optional[date] = None,
                  supabase_client=None) -> List[Dict[str, Any]]:
    if not supabase_client:
        return []
    try:
        query = supabase_client.table(TABLE).select("*").order("checkin_date", desc=True)
        if start_date:
            query = query.gte("checkin_date", start_date.isoformat())
        if end_date:
            query = query.lte("checkin_date", end_date.isoformat())
        return query.execute().data
    except Exception as e:
        logger.error(f"get_checkins failed: {e}")
        return []

def delete_checkin(checkin_id: int, supabase_client=None) -> bool:
    """Hard delete -- no soft-delete/audit column on this table, so the
    deletion itself is logged server-side (who, which id) even though the
    row itself is gone afterward."""
    if not supabase_client:
        return False
    try:
        result = supabase_client.table(TABLE).delete().eq("id", checkin_id).execute()
        return bool(result.data)
    except Exception as e:
        logger.error(f"delete_checkin failed for id={checkin_id}: {e}")
        return False
