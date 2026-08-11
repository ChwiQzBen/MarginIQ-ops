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

    Returns    Return Date | Customer | Item | Quantity (units) |
               Price per Unit (KSh) (optional) | Reason | Condition |
               Disposition | Original Ref (optional) | Notes (optional)

               Price per Unit is optional. When filled in, it's converted
               to price_per_kg via the same Pack Size mapping as quantity,
               and the return's value is EXACT. When left blank, the
               return's value falls back to an estimate in
               customer_analytics.compute_return_metrics (that customer's
               own blended avg revenue/kg) -- see that function's
               docstring.

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
    "get_returns_sheet_rows", "get_returns_sheet_diagnostics",
    "clear_returns_sheet_caches", "sync_new_returns_from_sheet",
]

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")

RETURNS_TAB = "Returns"


class ReturnsRow(TypedDict):
    return_date: Optional[date]
    customer_name: str
    item_name: str           # raw SKU text from the Sheet
    quantity_units: float    # units/packs returned -- NOT kg
    price_per_unit: float    # 0.0 if left blank on the Sheet -- means "no price given"
    reason_code: str
    condition: str
    disposition: str
    original_ref: str
    notes: str


class DroppedReturnRow(TypedDict):
    row_summary: str
    reason: str


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
        price_per_unit = _parse_float(normalized.get("price per unit (ksh)"), default=0.0)

        if not customer_name or not item_name or not return_date or quantity_units <= 0:
            continue

        rows.append({
            "return_date": return_date,
            "customer_name": customer_name,
            "item_name": item_name,
            "quantity_units": quantity_units,
            "price_per_unit": price_per_unit,
            "reason_code": str(normalized.get("reason", "")).strip(),
            "condition": str(normalized.get("condition", "")).strip(),
            "disposition": str(normalized.get("disposition", "")).strip(),
            "original_ref": str(normalized.get("original ref", "")).strip(),
            "notes": str(normalized.get("notes", "")).strip(),
        })
    return rows


@st.cache_data(ttl=300, show_spinner=False)
def get_returns_sheet_diagnostics(sheet_id: str, worksheet_name: str = RETURNS_TAB) -> List[DroppedReturnRow]:
    """Companion to get_returns_sheet_rows -- re-reads the same (small) tab
    to report WHY rows were silently dropped there: missing customer/item/
    date, or non-positive quantity. get_returns_sheet_rows stays silent on
    these so its own return type is a clean List[ReturnsRow]; this exists
    purely to surface the reason in sync_new_returns_from_sheet's result,
    instead of a dropped row just vanishing with no trace."""
    from app.core.lpo_sheet_sync import _get_gspread_client
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    dropped: List[DroppedReturnRow] = []
    for i, record in enumerate(records, start=2):  # sheet row 2 = first data row
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        customer_name = str(normalized.get("customer", "")).strip()
        item_name = str(normalized.get("item", "")).strip()
        raw_date = normalized.get("return date")
        return_date = _parse_date(raw_date)
        quantity_units = _parse_float(normalized.get("quantity (units)"), default=0.0)

        if not customer_name or not item_name or not return_date or quantity_units <= 0:
            reasons = []
            if not customer_name:
                reasons.append("Customer blank")
            if not item_name:
                reasons.append("Item blank")
            if not return_date:
                reasons.append(f"Return Date '{raw_date}' not recognized")
            if quantity_units <= 0:
                reasons.append("Quantity (units) missing or zero")
            dropped.append({"row_summary": f"Sheet row {i}", "reason": "; ".join(reasons)})
    return dropped


def clear_returns_sheet_caches() -> None:
    """Call from a 'Refresh from Sheet' button. Deliberately does NOT
    clear get_item_master_map's cache -- that's lpo_sheet_sync's cache to
    own, shared across LPO and Returns sync; clearing it here would just
    mean two callers stepping on each other's TTL."""
    get_returns_sheet_rows.clear()
    get_returns_sheet_diagnostics.clear()


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
    dropped_rows = get_returns_sheet_diagnostics(sheet_id)
    item_master_map = get_item_master_map(sheet_id)
    existing_returns = get_returns(supabase_client=supabase_client)
    existing_keys = {
        (r["return_date"], r["customer_name"], r.get("sku_description"), r.get("quantity_units"))
        for r in existing_returns
    }
    customer_cache = build_customer_name_cache(supabase_client)

    created = 0
    skipped_existing = 0
    # Rows that never made it into sheet_rows (blank customer/item/date, or
    # non-positive quantity) surface here now instead of silently vanishing --
    # this was the actual cause of "No new returns to sync" appearing even
    # when the Sheet visibly had rows in it.
    skipped_invalid = len(dropped_rows)
    errors: List[str] = [f"{d['row_summary']}: {d['reason']} — skipped." for d in dropped_rows]

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
            # price_per_unit=0.0 means "left blank on the Sheet" -- keep it
            # as None rather than 0.0 so save_return/compute_return_metrics
            # can tell "no price given" apart from "given as zero".
            price_per_kg = (row["price_per_unit"] / pack_size_kg) if row["price_per_unit"] > 0 else None
            save_return(
                return_date=row["return_date"], customer_name=row["customer_name"],
                cheese_name=recipe, quantity_kg=quantity_kg,
                reason_code=row["reason_code"], condition=row["condition"],
                disposition=row["disposition"], notes=row["notes"],
                customer_id=customer_id, original_ref=row["original_ref"],
                sku_description=row["item_name"], quantity_units=row["quantity_units"],
                price_per_kg=price_per_kg,
                price_per_unit=row["price_per_unit"] if row["price_per_unit"] > 0 else None,
                supabase_client=supabase_client,
            )
            created += 1
            existing_keys.add(key)
        except Exception as e:
            errors.append(f"Return ({row['customer_name']}, {row['item_name']}): {e}")

    return {"created": created, "skipped_existing": skipped_existing,
             "skipped_invalid": skipped_invalid, "errors": errors}