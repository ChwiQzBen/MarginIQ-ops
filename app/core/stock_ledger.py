"""
app/core/stock_ledger.py
==========================
Shared "what is current stock, right now" computation: the last completed
Stock Take's counted quantity at a location, plus everything that's moved
since (Check-In, Check-Out, Transfers). Returns None for a location with
no completed Stock Take yet for that item -- there's no honest baseline
to compute from, and treating that as zero would silently understate
stock rather than say so.

Two computation paths, same underlying math:
- get_current_stock() / get_total_current_stock(): single-item
  convenience, fetches fresh each call. Fine for one-off lookups (Stock
  Variance, a detail view).
- get_all_current_stock(): batch path for computing many items at once
  (load_inventory_data()). Fetches Check-In/Check-Out/Transfers ONCE,
  then computes every item from the same in-memory data -- calling the
  single-item path in a loop over a whole inventory would re-fetch all
  three sources from scratch per item, which is fine for one item and
  ruinous for hundreds.

Check-Out and Transfers read from the app's own tables. Check-In still
reads from Google Sheets until Phase 3's tables get their historical
import and full cutover.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Dict, List, Any
import logging
import pandas as pd
import streamlit as st

from app.core.google_sheet_reader import GoogleSheetReader
from app.core.demand_utils import detect_column, ITEM_LABEL_KEYWORDS
from app.core.checkout_reconciliation import get_checkouts
from app.core.transfer_reconciliation import get_transfers
from app.core.locations import COMPANY_LOCATIONS

logger = logging.getLogger(__name__)


def sum_check_in_window(check_in_df, loc_col, item_name: str, location: str, start, end) -> float:
    """Pure computation over an already-fetched Check-In dataframe."""
    if check_in_df is None or check_in_df.empty or not loc_col:
        return 0.0
    item_col = detect_column(check_in_df, ITEM_LABEL_KEYWORDS)
    date_col = next((c for c in check_in_df.columns if 'date' in c.lower()), None)
    qty_col = next((c for c in check_in_df.columns if 'quantity' in c.lower() or 'qty' in c.lower()), None)
    if not item_col or not date_col or not qty_col:
        return 0.0
    sub = check_in_df[
        (check_in_df[item_col] == item_name)
        & (check_in_df[loc_col].astype(str).str.strip() == location)
    ].copy()
    if sub.empty:
        return 0.0
    sub['_DATE'] = pd.to_datetime(sub[date_col], errors='coerce')
    start_dt, end_dt = pd.to_datetime(start, errors='coerce'), pd.to_datetime(end, errors='coerce')
    if pd.notna(start_dt):
        sub = sub[sub['_DATE'] >= start_dt]
    if pd.notna(end_dt):
        sub = sub[sub['_DATE'] <= end_dt]
    return pd.to_numeric(sub[qty_col], errors='coerce').fillna(0).sum()


def _fetch_check_in_df():
    """Isolated fetch, used by both the single-item and batch paths so
    the try/except lives in one place."""
    try:
        gsheet = GoogleSheetReader()
        return gsheet.get_check_in() if gsheet.authenticate() else pd.DataFrame()
    except Exception as e:
        logger.error(f"stock_ledger: could not reach Check-In source: {e}")
        return pd.DataFrame()


def _sum_check_out_from_records(checkout_records: List[Dict], item_name: str, location: str, start, end) -> float:
    """Pure computation over an already-fetched list of checkout dicts.
    Sums ALL check-outs regardless of reconciliation status -- whether a
    check-out has been paperwork-reconciled is an audit/trust question,
    not whether the stock physically left the shelf."""
    start_dt, end_dt = pd.to_datetime(start, errors='coerce'), pd.to_datetime(end, errors='coerce')
    total = 0.0
    for r in checkout_records:
        if r.get('item_name') != item_name or r.get('store') != location:
            continue
        d = pd.to_datetime(r.get('checkout_date'), errors='coerce')
        if pd.isna(d):
            continue
        if pd.notna(start_dt) and d < start_dt:
            continue
        if pd.notna(end_dt) and d > end_dt:
            continue
        total += float(r.get('quantity') or 0)
    return total


def sum_check_out_window(item_name: str, location: str, start, end, supabase_client=None) -> float:
    """Single-item convenience: fetches fresh, then delegates to the pure
    computation. For many items, fetch get_checkouts() once and call
    _sum_check_out_from_records directly -- see get_all_current_stock."""
    return _sum_check_out_from_records(get_checkouts(supabase_client=supabase_client), item_name, location, start, end)


def _transfer_total_from_records(transfer_records: List[Dict], item_name: str, location: str, start, end, direction: str) -> float:
    """Pure computation over an already-fetched list of transfer dicts."""
    start_dt, end_dt = pd.to_datetime(start, errors='coerce'), pd.to_datetime(end, errors='coerce')
    total = 0.0
    for t in transfer_records:
        if t.get('item_name') != item_name:
            continue
        if direction == 'out':
            if t.get('from_location') != location:
                continue
            d = pd.to_datetime(t.get('transfer_date'), errors='coerce')
            qty = float(t.get('quantity_issued') or 0)
        else:
            if t.get('to_location') != location or t.get('status') not in ('Received-Matched', 'Received-Mismatch'):
                continue
            d = pd.to_datetime(t.get('received_at'), errors='coerce')
            qty = float(t.get('quantity_received') or 0)
        if pd.isna(d):
            continue
        if pd.notna(start_dt) and d < start_dt:
            continue
        if pd.notna(end_dt) and d > end_dt:
            continue
        total += qty
    return total


def transfer_total(item_name: str, location: str, start, end, direction: str, supabase_client=None) -> float:
    """Single-item convenience wrapper -- see _transfer_total_from_records."""
    return _transfer_total_from_records(get_transfers(supabase_client=supabase_client), item_name, location, start, end, direction)


def get_last_stock_take_anchor(item_name: str, location: str) -> Optional[Dict[str, Any]]:
    """Most recent completed Stock Take at `location` that counted
    `item_name`. None if this location has never had one for this item.
    Pure in-memory lookup (st.session_state), no network cost."""
    completed = [
        c for c in st.session_state.get('stock_takes', {}).values()
        if c.get('status') == 'Completed' and c.get('warehouse') == location
        and item_name in c.get('items', {})
    ]
    if not completed:
        return None
    completed.sort(key=lambda c: c.get('completed', ''))
    latest = completed[-1]
    return {
        'counted_qty': latest['items'][item_name].get('counted_qty', 0),
        'completed_at': latest.get('completed', ''),
    }


def get_current_stock(item_name: str, location: str, supabase_client=None) -> Optional[float]:
    """Current stock for one item at one location. None if there's no
    completed Stock Take there yet for this item to anchor from."""
    anchor = get_last_stock_take_anchor(item_name, location)
    if anchor is None:
        return None

    anchor_date = anchor['completed_at']
    today = datetime.now().strftime('%Y-%m-%d %H:%M')

    check_in_df = _fetch_check_in_df()
    check_in_col = detect_column(check_in_df, ["location", "warehouse", "site", "store", "outlet"]) if not check_in_df.empty else None
    check_in_total = sum_check_in_window(check_in_df, check_in_col, item_name, location, anchor_date, today)
    check_out_total = sum_check_out_window(item_name, location, anchor_date, today, supabase_client=supabase_client)
    transfers_in = transfer_total(item_name, location, anchor_date, today, 'in', supabase_client=supabase_client)
    transfers_out = transfer_total(item_name, location, anchor_date, today, 'out', supabase_client=supabase_client)

    return anchor['counted_qty'] + check_in_total - check_out_total + transfers_in - transfers_out


def get_total_current_stock(item_name: str, supabase_client=None) -> Dict[str, Any]:
    """Sums get_current_stock() across every company location. Locations
    with no baseline yet are reported separately, not silently zeroed."""
    per_location = {}
    missing = []
    for loc in COMPANY_LOCATIONS:
        val = get_current_stock(item_name, loc, supabase_client=supabase_client)
        if val is None:
            missing.append(loc)
        else:
            per_location[loc] = val
    return {
        'total': sum(per_location.values()),
        'per_location': per_location,
        'missing_locations': missing,
    }


def get_all_current_stock(item_names: List[str], sheet_quantities: Optional[Dict[str, float]] = None,
                           supabase_client=None) -> Dict[str, Dict[str, Any]]:
    """Batch version: fetches Check-In, Check-Out, and Transfers ONCE for
    every item passed in, instead of once per item. If an item has no
    Stock Take anchor at ANY location yet, falls back to
    sheet_quantities[item_name] (tagged 'sheet_fallback') rather than
    reporting a false zero -- an item with no baseline isn't "confirmed
    empty," it's "not yet counted," and those two states must not look
    the same to anyone reading a Low Stock alert."""
    sheet_quantities = sheet_quantities or {}
    check_in_df = _fetch_check_in_df()
    check_in_col = detect_column(check_in_df, ["location", "warehouse", "site", "store", "outlet"]) if not check_in_df.empty else None
    all_checkouts = get_checkouts(supabase_client=supabase_client)
    all_transfers = get_transfers(supabase_client=supabase_client)
    today = datetime.now().strftime('%Y-%m-%d %H:%M')

    results = {}
    for item_name in item_names:
        per_location = {}
        missing = []
        for loc in COMPANY_LOCATIONS:
            anchor = get_last_stock_take_anchor(item_name, loc)
            if anchor is None:
                missing.append(loc)
                continue
            anchor_date = anchor['completed_at']
            check_in_total = sum_check_in_window(check_in_df, check_in_col, item_name, loc, anchor_date, today)
            check_out_total = _sum_check_out_from_records(all_checkouts, item_name, loc, anchor_date, today)
            transfers_in = _transfer_total_from_records(all_transfers, item_name, loc, anchor_date, today, 'in')
            transfers_out = _transfer_total_from_records(all_transfers, item_name, loc, anchor_date, today, 'out')
            per_location[loc] = anchor['counted_qty'] + check_in_total - check_out_total + transfers_in - transfers_out

        if per_location:
            results[item_name] = {'total': sum(per_location.values()), 'per_location': per_location,
                                   'missing_locations': missing, 'source': 'computed'}
        else:
            results[item_name] = {'total': sheet_quantities.get(item_name, 0), 'per_location': {},
                                   'missing_locations': missing, 'source': 'sheet_fallback'}
    return results
