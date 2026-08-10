"""
app/core/returns_sheet_sync.py
================================
Read-only sync of the Returns tab in the same LPO/Sales Google Sheet.

Market returns are entered ONLY in the Sheet — customer, item (raw SKU),
quantity (units/packs), reason, condition, and disposition. This module
does a one-way additive sync into `cheese_returns`, same pattern as
`lpo_sheet_sync.py` and `sales_sheet_sync.py`. Returns are logging-only:
no disposition here restocks FEFO inventory, so this file never touches
`FEFOInventory` or `BatchTracker` — it's a pure record of loss for
reporting and churn-risk signal, nothing more.

Reuses `get_item_master_map()` from lpo_sheet_sync.py — same Items tab,
same Recipe (Cheese Type) / Pack Size (kg) mapping used to convert units
entered on the Sheet into kg. No separate item lookup here.

Expected tab layout, added to the same spreadsheet as LPO Register / Sales:

    Returns    Return Date | Customer | Item | Quantity (units) | Reason |
               Condition | Disposition | Original Ref (optional) |
               Notes (optional)

               Customer and Item are dropdowns sourced from the same
               Customers / Items tabs as LPO Register. Quantity is in
               units/packs, not kg — converted via the Item's Pack Size,
               same as LPO intake. Reason: spoiled / damaged_transit /
               wrong_product / near_expiry / customer_dissatisfaction.
               Disposition: dispose / discount_sale / donate / write_off
               — a reporting tag only; nothing in the app acts on it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, TypedDict, List, Dict, Any

import streamlit as st

from app.core.cheese_data_access import (
    save_return, get_returns, build_customer_name_cache, find_or_create_customer_id,
)
from app.core.lpo_sheet_sync import get_item_master_map

__all__ = [
    "get_returns_sheet_rows", "clear_returns_sheet_caches", "sync_new_returns_from_sheet",
]

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")

RETURNS_TAB = "Returns"


class ReturnsRow(TypedDict):
    return_date: Optional[date]
    customer_name: str
    item_name: str           # raw SKU text from the Sheet
    quantity_units: float    # units/packs returned -- NOT kg
    reason_code: str
    condition: str
    disposition: str
    original_ref: str
    notes: str


def _parse_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


@st.cache_data(ttl=300, show_spinner="Syncing returns from Google Sheet...")
def get_returns_sheet_rows(sheet_id: str, worksheet_name: str = RETURNS_TAB) -> List[ReturnsRow]:
    """Pull every row from the Returns tab. Rows missing a customer, item,
    valid date, or a positive quantity are silently dropped here --
    `sync_new_returns_from_sheet` re-derives which rows fail validation
    for the rest (item/recipe mapping) and reports them back."""
    from app.core.lpo_sheet_sync import _get_gspread_client  # shared authed client
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    rows: List[ReturnsRow] = []
    for record in records:
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        customer_name = str(normalized.get("customer", "")).strip()
        item_name = str(normalized.get("item", "")).strip()
        return_date = _parse_date(normalized.get("return date"))
        quantity_units = _parse_float(normalized.get("quantity (units)"), default=0.0)

        if not customer_name or not item_name or not return_date or quantity_units <= 0:
            continue

        rows.append({
            "return_date": return_date,
            "customer_name": customer_name,
            "item_name": item_name,
            "quantity_units": quantity_units,
            "reason_code": str(normalized.get("reason", "")).strip(),
            "condition": str(normalized.get("condition", "")).strip(),
            "disposition": str(normalized.get("disposition", "")).strip(),
            "original_ref": str(normalized.get("original ref", "")).strip(),
            "notes": str(normalized.get("notes", "")).strip(),
        })
    return rows


def clear_returns_sheet_caches() -> None:
    """Call from a 'Refresh from Sheet' button. Deliberately does NOT
    clear get_item_master_map's cache -- that's lpo_sheet_sync's cache to
    own, shared across LPO and Returns sync; clearing it here would just
    mean two callers stepping on each other's TTL."""
    get_returns_sheet_rows.clear()


def sync_new_returns_from_sheet(sheet_id: str, valid_cheese_names: List[str],
                                  supabase_client=None) -> Dict[str, Any]:
    """
    One-way, additive sync, same shape as sync_new_lpo_lines_from_sheet.
    Dedup key is (return_date, customer_name, item_name, quantity_units)
    -- there's no natural unique ID like an LPO number for a return, so
    the composite of what's actually on the Sheet row stands in for one.
    A genuine duplicate return (same customer, item, date, AND quantity)
    is rare enough in practice that this is an acceptable dedup key; if
    it ever collides, add a row-UUID column to the Sheet.

    Returns {"created": int, "skipped_existing": int, "skipped_invalid": int,
             "errors": [str, ...]}
    """
    sheet_rows = get_returns_sheet_rows(sheet_id)
    item_master_map = get_item_master_map(sheet_id)
    existing_returns = get_returns(supabase_client=supabase_client)
    existing_keys = {
        (r["return_date"], r["customer_name"], r.get("sku_description"), r.get("quantity_units"))
        for r in existing_returns
    }
    customer_cache = build_customer_name_cache(supabase_client)

    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    errors: List[str] = []

    for row in sheet_rows:
        key = (row["return_date"].isoformat(), row["customer_name"], row["item_name"], row["quantity_units"])
        if key in existing_keys:
            skipped_existing += 1
            continue

        item_info = item_master_map.get(row["item_name"].lower())
        if not item_info:
            skipped_invalid += 1
            errors.append(f"Return ({row['customer_name']}, {row['item_name']}): no Recipe "
                           f"mapping in the Items tab — skipped.")
            continue

        recipe = item_info["recipe"]
        if recipe not in valid_cheese_names:
            skipped_invalid += 1
            errors.append(f"Return ({row['customer_name']}, {row['item_name']}): maps to Recipe "
                           f"'{recipe}', which doesn't match any recipe in the Recipe Book — skipped.")
            continue

        pack_size_kg = item_info["pack_size_kg"]
        if not pack_size_kg or pack_size_kg <= 0:
            skipped_invalid += 1
            errors.append(f"Return ({row['customer_name']}, {row['item_name']}): no valid "
                           f"Pack Size (kg) in the Items tab — skipped.")
            continue

        try:
            customer_id = find_or_create_customer_id(row["customer_name"], customer_cache, supabase_client)
            quantity_kg = row["quantity_units"] * pack_size_kg
            save_return(
                return_date=row["return_date"], customer_name=row["customer_name"],
                cheese_name=recipe, quantity_kg=quantity_kg,
                reason_code=row["reason_code"], condition=row["condition"],
                disposition=row["disposition"], notes=row["notes"],
                customer_id=customer_id, original_ref=row["original_ref"],
                sku_description=row["item_name"], quantity_units=row["quantity_units"],
                supabase_client=supabase_client,
            )
            created += 1
            existing_keys.add(key)
        except Exception as e:
            errors.append(f"Return ({row['customer_name']}, {row['item_name']}): {e}")

    return {"created": created, "skipped_existing": skipped_existing,
             "skipped_invalid": skipped_invalid, "errors": errors}