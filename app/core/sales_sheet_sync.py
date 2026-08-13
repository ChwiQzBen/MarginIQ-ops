"""
app/core/sales_sheet_sync.py
==============================
Read-only sync of the "Daily Sales" tab in the same Google Sheet as LPO
Register — same Items/Customers tabs, same service-account auth, same
units-not-kg convention. Exists so walk-in/cash sales (no LPO) can be
entered in the Sheet instead of typed into the Commercial UI Sales form,
the same way LPO intake already moved out of the app.

Unlike lpo_sheet_sync.py, this module actually moves stock: each synced
row calls dispatch_and_record_sale(), which allocates real FEFO batches
and books revenue — not just writing a record. That's why it needs a
`tracker` argument the LPO sync doesn't.

Reuses _get_gspread_client() and get_item_master_map() from
lpo_sheet_sync.py rather than re-deriving Google auth or the Items-tab
mapping a second time — same spreadsheet, same source of truth for both.

Expected tab layout (same spreadsheet as LPO Register):

    Daily Sales    Customer | Item | Quantity (units) | Sale Date |
                   Price per unit (KSh) | Notes
                   Same Item dropdown as LPO Register — quantity and
                   price are per unit/pack, converted to kg via the
                   Items tab's Pack Size (kg), same as LPO Register.

Dedup: (sale_date, customer, resolved recipe name, quantity_kg, price_per_kg)
against existing cheese_sales_history rows, rounded to 2dp. This runs on
the KG-CONVERTED values, not the raw sheet units — cheese_sales_history
doesn't currently store the original SKU/unit quantity the way lpo_lines
does, so exact-SKU dedup (which LPO Register gets via item_name) isn't
available here yet. See module docstring note if this needs tightening.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, TypedDict, List, Dict, Any

import streamlit as st

from app.core.lpo_sheet_sync import _get_gspread_client, get_item_master_map, _parse_date, _parse_float
from app.core.cheese_data_access import (
    get_sales_history, build_customer_name_cache, find_or_create_customer_id,
)
from app.core.sales_service import dispatch_and_record_sale

__all__ = ["get_daily_sales_sheet_rows", "clear_sales_sheet_caches", "sync_new_sales_from_sheet"]

DAILY_SALES_TAB = "Daily Sales"


class DailySalesRow(TypedDict):
    customer_name: str
    item_name: str
    quantity_units: float
    sale_date: Optional[date]
    price_per_unit: float
    notes: str


@st.cache_data(ttl=300, show_spinner="Syncing daily sales from Google Sheet...")
def get_daily_sales_sheet_rows(sheet_id: str, worksheet_name: str = DAILY_SALES_TAB) -> List[DailySalesRow]:
    """Pull every row from the Daily Sales tab. Rows missing a customer,
    item, valid sale date, or a positive quantity are silently dropped
    here — sync_new_sales_from_sheet re-derives which rows fail
    validation and reports them back, same split as the LPO sync."""
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    rows: List[DailySalesRow] = []
    for record in records:
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        customer_name = str(normalized.get("customer", "")).strip()
        item_name = str(normalized.get("item", "")).strip()
        quantity_units = _parse_float(normalized.get("quantity (units)"), default=0.0)
        sale_date = _parse_date(normalized.get("sale date"))

        if not customer_name or not item_name or quantity_units <= 0 or not sale_date:
            continue

        rows.append({
            "customer_name": customer_name,
            "item_name": item_name,
            "quantity_units": quantity_units,
            "sale_date": sale_date,
            "price_per_unit": _parse_float(normalized.get("price per unit (ksh)"), default=0.0),
            "notes": str(normalized.get("notes", "")).strip(),
        })
    return rows


def clear_sales_sheet_caches() -> None:
    """Call from a 'Refresh from Sheet' button. Also clears the shared
    Items-tab mapping cache (from lpo_sheet_sync) since a stale Pack
    Size there would silently mis-convert a sale's kg the same way it
    would mis-convert an LPO line."""
    get_daily_sales_sheet_rows.clear()
    get_item_master_map.clear()


def sync_new_sales_from_sheet(sheet_id: str, tracker, valid_cheese_names: List[str],
                                supabase_client=None) -> Dict[str, Any]:
    """
    One-way, additive sync: every Sheet row not already matched against
    existing sales_history becomes a real dispatched sale — FEFO stock
    allocated, revenue booked — via dispatch_and_record_sale(), the same
    single path the manual Sales form uses. Never writes to
    cheese_sales_history directly.

    Returns {"created": int, "skipped_existing": int, "skipped_invalid": int,
             "shortfalls": [str, ...], "errors": [str, ...], "duplicate_warnings": [str, ...]}
    """
    sheet_rows = get_daily_sales_sheet_rows(sheet_id)
    item_master_map = get_item_master_map(sheet_id)
    existing_sales = get_sales_history(supabase_client=supabase_client)
    customer_cache = build_customer_name_cache(supabase_client)
    duplicate_warnings: List[str] = []

    existing_keys = {
        (
            str(s.get("date"))[:10],
            str(s.get("customer", "")).strip().lower(),
            s.get("cheese_name"),
            round(float(s.get("quantity_kg", 0)), 2),
            round(float(s.get("price_per_kg", 0)), 2),
        )
        for s in existing_sales
    }

    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    shortfalls: List[str] = []
    errors: List[str] = []

    for row in sheet_rows:
        item_info = item_master_map.get(row["item_name"].lower())
        if not item_info:
            skipped_invalid += 1
            errors.append(f"{row['sale_date']} — {row['customer_name']}: '{row['item_name']}' has no "
                           f"Recipe mapping in the Items tab — skipped.")
            continue

        recipe = item_info["recipe"]
        if recipe not in valid_cheese_names:
            skipped_invalid += 1
            errors.append(f"{row['sale_date']} — {row['customer_name']}: '{row['item_name']}' maps to "
                           f"Recipe '{recipe}', which doesn't match any recipe in the Recipe Book — skipped.")
            continue

        pack_size_kg = item_info["pack_size_kg"]
        if not pack_size_kg or pack_size_kg <= 0:
            skipped_invalid += 1
            errors.append(f"{row['sale_date']} — {row['customer_name']}: '{row['item_name']}' has no "
                           f"valid Pack Size (kg) set in the Items tab — skipped.")
            continue

        if row["price_per_unit"] <= 0:
            skipped_invalid += 1
            errors.append(f"{row['sale_date']} — {row['customer_name']}: '{row['item_name']}' has no "
                           f"Price per unit set — skipped (can't book revenue at KSh 0).")
            continue

        quantity_kg = row["quantity_units"] * pack_size_kg
        price_per_kg = row["price_per_unit"] / pack_size_kg

        key = (
            row["sale_date"].isoformat(),
            row["customer_name"].strip().lower(),
            recipe,
            round(quantity_kg, 2),
            round(price_per_kg, 2),
        )
        if key in existing_keys:
            skipped_existing += 1
            continue

        try:
            customer_id = find_or_create_customer_id(
                row["customer_name"], customer_cache, supabase_client,
                duplicate_warnings=duplicate_warnings,
            )
            result = dispatch_and_record_sale(
                tracker, recipe, quantity_kg, price_per_kg, row["sale_date"],
                row["customer_name"], row["notes"] or f"Synced from Daily Sales sheet ({row['item_name']})",
                supabase_client, customer_id=customer_id,
            )
            if result.shortfall_kg > 0:
                shortfalls.append(
                    f"{row['sale_date']} — {row['customer_name']} / {row['item_name']}: only "
                    f"{result.allocated_kg:.1f}kg of {quantity_kg:.1f}kg requested was in stock — "
                    f"recorded for what was actually dispatched."
                )
            created += 1
            existing_keys.add(key)  # guard against duplicate rows within the same sheet pull
        except Exception as e:
            errors.append(f"{row['sale_date']} — {row['customer_name']} / {row['item_name']}: {e}")

    return {"created": created, "skipped_existing": skipped_existing,
             "skipped_invalid": skipped_invalid, "shortfalls": shortfalls, "errors": errors,
             "duplicate_warnings": duplicate_warnings}