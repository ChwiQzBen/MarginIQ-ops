"""
app/core/data_integrity.py
============================
Detects when an item silently vanishes from the Google Sheet between one
load and the next -- flagged for manual review, not proof of anything.
Uses app.core.inventory_cache's existing snapshot of the previous
successful load as the baseline to diff against.

A row can disappear for entirely innocent reasons (deliberate cleanup, a
rename, a filter change upstream) -- this only tells you *that* something
is gone and what it looked like the last time it was seen, never why.
"""
from datetime import datetime
from typing import Any, List, Dict
import logging

from app.core.inventory_cache import load_snapshot

logger = logging.getLogger(__name__)

_ALERTS_TABLE = "deleted_item_alerts"


def detect_and_log_disappearances(new_stock_df, id_column: str = "ITEM_NAME",
                                    supabase_client: Any = None) -> int:
    """Call this BEFORE save_snapshot('full_inventory', ...) overwrites
    the baseline for the same load -- calling it after means comparing
    the new load against itself, and nothing will ever show up missing.
    Returns the number of newly-detected disappearances logged."""
    if not supabase_client or new_stock_df is None or new_stock_df.empty:
        return 0
    if id_column not in new_stock_df.columns:
        return 0

    previous = load_snapshot("full_inventory", supabase_client=supabase_client)
    if previous is None:
        return 0

    frames, _, cached_at = previous
    old_stock_df = frames.get("stock")
    if old_stock_df is None or old_stock_df.empty or id_column not in old_stock_df.columns:
        return 0

    old_ids = set(old_stock_df[id_column].dropna().astype(str))
    new_ids = set(new_stock_df[id_column].dropna().astype(str))
    missing = old_ids - new_ids
    if not missing:
        return 0

    # Don't re-log the same disappearance on every subsequent load --
    # only new ones not already sitting unacknowledged.
    try:
        existing = supabase_client.table(_ALERTS_TABLE).select("item_name").is_("acknowledged_at", "null").execute()
        already_logged = {row["item_name"] for row in existing.data}
    except Exception as e:
        logger.error(f"Could not check existing deleted_item_alerts: {e}")
        already_logged = set()

    missing_rows = old_stock_df[old_stock_df[id_column].astype(str).isin(missing)]
    logged = 0
    for _, row in missing_rows.iterrows():
        item_name = str(row[id_column])
        if item_name in already_logged:
            continue
        try:
            supabase_client.table(_ALERTS_TABLE).insert({
                "item_name": item_name,
                "last_seen_quantity": row.get("QUANTITY"),
                "last_seen_price": row.get("UNIT PRICE"),
                "last_seen_at": cached_at.isoformat(),
                "detected_at": datetime.now().isoformat(),
            }).execute()
            logged += 1
        except Exception as e:
            logger.error(f"Could not log deleted_item_alert for {item_name}: {e}")

    return logged


def get_open_alerts(supabase_client: Any = None) -> List[Dict]:
    if not supabase_client:
        return []
    try:
        result = (supabase_client.table(_ALERTS_TABLE).select("*")
                  .is_("acknowledged_at", "null").order("detected_at", desc=True).execute())
        return result.data
    except Exception as e:
        logger.error(f"Could not fetch deleted_item_alerts: {e}")
        return []


def acknowledge_alert(alert_id: int, acknowledged_by: str, notes: str = "", supabase_client: Any = None) -> bool:
    if not supabase_client:
        return False
    try:
        supabase_client.table(_ALERTS_TABLE).update({
            "acknowledged_by": acknowledged_by,
            "acknowledged_at": datetime.now().isoformat(),
            "notes": notes,
        }).eq("id", alert_id).execute()
        return True
    except Exception as e:
        logger.error(f"Could not acknowledge deleted_item_alert {alert_id}: {e}")
        return False