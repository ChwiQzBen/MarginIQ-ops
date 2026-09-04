"""
app/core/stock_ledger.py
==========================
Shared "what is current stock, right now" computation: the last completed
Stock Take's counted quantity at a location, plus everything that's moved
since (Check-In, Check-Out, Transfers). Returns None for a location with
no completed Stock Take yet for that item.

Phase 4a complete: full historical Check-In and Check-Out data has been
imported into stock_checkins / stock_checkouts, so this module no longer
reads Google Sheets at all -- doing so now would double-count every
pre-import event (once from the sheet, once from the import). Check-In,
Check-Out, and Transfers are all app-only from here.

Two computation paths: get_current_stock()/get_total_current_stock() for
single-item lookups (fetches fresh each call); get_all_current_stock()
for computing many items at once (fetches each source ONCE, computes
every item from the same in-memory data) -- see load_inventory_data().
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Dict, List, Any
from collections import defaultdict
import logging
import pandas as pd
import streamlit as st

from app.core.checkin_records import get_checkins
from app.core.checkout_reconciliation import get_checkouts
from app.core.transfer_reconciliation import get_transfers
from app.core.locations import COMPANY_LOCATIONS

logger = logging.getLogger(__name__)


def sum_check_in_window(checkin_records: List[Dict], item_name: str, location: str, start, end) -> float:
    """Pure computation over an already-fetched list of stock_checkins records."""
    start_dt, end_dt = pd.to_datetime(start, errors='coerce'), pd.to_datetime(end, errors='coerce')
    total = 0.0
    for r in checkin_records:
        if r.get('item_name') != item_name or r.get('store') != location:
            continue
        d = pd.to_datetime(r.get('checkin_date'), errors='coerce')
        if pd.isna(d):
            continue
        if pd.notna(start_dt) and d < start_dt:
            continue
        if pd.notna(end_dt) and d > end_dt:
            continue
        total += float(r.get('quantity') or 0)
    return total


def _sum_check_out_from_records(checkout_records: List[Dict], item_name: str, location: str, start, end) -> float:
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
    return _sum_check_out_from_records(get_checkouts(supabase_client=supabase_client), item_name, location, start, end)


def _transfer_total_from_records(transfer_records: List[Dict], item_name: str, location: str, start, end, direction: str) -> float:
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
    return _transfer_total_from_records(get_transfers(supabase_client=supabase_client), item_name, location, start, end, direction)


def get_last_stock_take_anchor(item_name: str, location: str) -> Optional[Dict[str, Any]]:
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
    anchor = get_last_stock_take_anchor(item_name, location)
    if anchor is None:
        return None

    anchor_date = anchor['completed_at']
    today = datetime.now().strftime('%Y-%m-%d %H:%M')

    check_in_total = sum_check_in_window(get_checkins(supabase_client=supabase_client), item_name, location, anchor_date, today)
    check_out_total = sum_check_out_window(item_name, location, anchor_date, today, supabase_client=supabase_client)
    transfers_in = transfer_total(item_name, location, anchor_date, today, 'in', supabase_client=supabase_client)
    transfers_out = transfer_total(item_name, location, anchor_date, today, 'out', supabase_client=supabase_client)

    return anchor['counted_qty'] + check_in_total - check_out_total + transfers_in - transfers_out


def get_total_current_stock(item_name: str, supabase_client=None) -> Dict[str, Any]:
    per_location = {}
    missing = []
    for loc in COMPANY_LOCATIONS:
        val = get_current_stock(item_name, loc, supabase_client=supabase_client)
        if val is None:
            missing.append(loc)
        else:
            per_location[loc] = val
    return {'total': sum(per_location.values()), 'per_location': per_location, 'missing_locations': missing}


def get_all_current_stock(item_names: List[str], sheet_quantities: Optional[Dict[str, float]] = None,
                           supabase_client=None) -> Dict[str, Dict[str, Any]]:
    sheet_quantities = sheet_quantities or {}
    all_app_checkins = get_checkins(supabase_client=supabase_client)
    all_checkouts = get_checkouts(supabase_client=supabase_client)
    all_transfers = get_transfers(supabase_client=supabase_client)
    today = datetime.now().strftime('%Y-%m-%d %H:%M')

    checkins_by_item = defaultdict(list)
    for r in all_app_checkins:
        checkins_by_item[r.get('item_name')].append(r)
    checkouts_by_item = defaultdict(list)
    for r in all_checkouts:
        checkouts_by_item[r.get('item_name')].append(r)
    transfers_by_item = defaultdict(list)
    for t in all_transfers:
        transfers_by_item[t.get('item_name')].append(t)

    results = {}
    for item_name in item_names:
        item_checkins = checkins_by_item.get(item_name, [])
        item_checkouts = checkouts_by_item.get(item_name, [])
        item_transfers = transfers_by_item.get(item_name, [])
        per_location = {}
        missing = []
        for loc in COMPANY_LOCATIONS:
            anchor = get_last_stock_take_anchor(item_name, loc)
            if anchor is None:
                missing.append(loc)
                continue
            anchor_date = anchor['completed_at']
            check_in_total = sum_check_in_window(item_checkins, item_name, loc, anchor_date, today)
            check_out_total = _sum_check_out_from_records(item_checkouts, item_name, loc, anchor_date, today)
            transfers_in = _transfer_total_from_records(item_transfers, item_name, loc, anchor_date, today, 'in')
            transfers_out = _transfer_total_from_records(item_transfers, item_name, loc, anchor_date, today, 'out')
            per_location[loc] = anchor['counted_qty'] + check_in_total - check_out_total + transfers_in - transfers_out

        if per_location:
            results[item_name] = {'total': sum(per_location.values()), 'per_location': per_location,
                                   'missing_locations': missing, 'source': 'computed'}
        else:
            results[item_name] = {'total': sheet_quantities.get(item_name, 0), 'per_location': {},
                                   'missing_locations': missing, 'source': 'sheet_fallback'}
    return results
