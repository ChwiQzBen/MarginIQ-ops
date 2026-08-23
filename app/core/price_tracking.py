"""
app/core/price_tracking.py
===========================
Tracks item price changes over time despite the app having no write
access to the Google Sheet itself -- there's no way to store history
*in* the sheet, so this polls the price the app already reads on every
inventory load and logs a change the moment it differs from what was
last seen, into Supabase.

This is a diffing mechanism, not a live watcher: it only sees a change
the next time an inventory load runs. A price changed and changed back
between two loads would never be seen -- a real limitation, not a bug.
"""
from datetime import datetime
from typing import Any
import logging

logger = logging.getLogger(__name__)

_CURRENT_PRICES_TABLE = "current_prices"
_PRICE_HISTORY_TABLE = "price_history"


def record_price_changes(stock_df, supabase_client: Any = None) -> int:
    """Returns the number of price changes detected this run. Does
    nothing (and returns 0) without a Supabase client or the expected
    columns -- no history without persistence."""
    if supabase_client is None or stock_df is None or stock_df.empty:
        return 0
    if 'ITEM_NAME' not in stock_df.columns or 'UNIT PRICE' not in stock_df.columns:
        return 0

    try:
        existing = supabase_client.table(_CURRENT_PRICES_TABLE).select("*").execute()
        current_prices = {row["item_name"]: row["price"] for row in existing.data}
    except Exception as e:
        logger.error(f"Could not load current_prices for diffing: {e}")
        return 0

    changes = 0
    for _, row in stock_df.iterrows():
        item_name = row.get('ITEM_NAME')
        price = row.get('UNIT PRICE')
        if not item_name or price is None or str(price).strip() == '':
            continue
        try:
            price = float(price)
        except (ValueError, TypeError):
            continue

        old_price = current_prices.get(item_name)
        if old_price is None:
            try:
                supabase_client.table(_CURRENT_PRICES_TABLE).upsert({
                    "item_name": item_name, "price": price,
                    "updated_at": datetime.now().isoformat(),
                }).execute()
            except Exception as e:
                logger.error(f"Could not seed current_prices for {item_name}: {e}")
            continue

        if abs(float(old_price) - price) > 0.001:
            try:
                supabase_client.table(_PRICE_HISTORY_TABLE).insert({
                    "item_name": item_name, "old_price": old_price,
                    "new_price": price, "changed_at": datetime.now().isoformat(),
                }).execute()
                supabase_client.table(_CURRENT_PRICES_TABLE).upsert({
                    "item_name": item_name, "price": price,
                    "updated_at": datetime.now().isoformat(),
                }).execute()
                changes += 1
            except Exception as e:
                logger.error(f"Could not log price change for {item_name}: {e}")

    return changes


def get_price_history(item_name: str = None, supabase_client: Any = None, limit: int = 100):
    if supabase_client is None:
        return []
    try:
        query = supabase_client.table(_PRICE_HISTORY_TABLE).select("*").order("changed_at", desc=True).limit(limit)
        if item_name:
            query = query.eq("item_name", item_name)
        return query.execute().data
    except Exception as e:
        logger.error(f"Could not fetch price_history: {e}")
        return []