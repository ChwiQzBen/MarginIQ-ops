"""
Read-only sync of the LPO Register Google Sheet.

The Sheet is the single source of truth for LPO intake -- customer, item
ordered (a real SKU, e.g. "Browns Feta 200g"), quantity, delivery date,
LPO number, LPO date, and LPO expiry date are all entered there directly,
never in the app.

The Items tab carries a "Recipe (Cheese Type)" column mapping each SKU to
the bulk cheese type your Recipe Book actually produces (e.g. all Feta
pack sizes map to "Feta") -- production tracking / FEFO only understands
recipe-level names, not individual SKUs, so that mapping is how a Sheet
row turns into a valid `lpo_lines` record. Rows whose SKU has no Recipe
filled in are treated as non-cheese items and are skipped: they're
outside what this BCPOS module tracks.

Already-synced rows are left alone: editing a row in the Sheet after
it's been synced will NOT update the existing app record (by design --
see `sync_new_lpo_lines_from_sheet`'s docstring for the exact dedup rule).

Expected spreadsheet layout: ONE Google Sheet, tabs:

    Items            Item Name | Item Code | Unit of Measure |
                      Recipe (Cheese Type) | Pack Size (kg)
                      One row per real product. Recipe (Cheese Type) must
                      exactly match a recipe name in the Recipe Book
                      (case-sensitive) for cheese products; leave blank
                      for non-cheese products. Pack Size (kg) is the kg
                      weight of ONE unit of that item -- required for any
                      item that has a Recipe set, since LPO Register is
                      filled in units/packs, not kg.

    Customers         Customer Name | Customer Code
                      One row per real customer.

    LPO Register      Customer | Item | Quantity (units) | Delivery Date |
                      LPO Number | LPO Date | LPO Expiry Date | Price
                      per unit (optional) | Notes (optional)
                      Quantity and Price are per unit/pack -- what's
                      actually written on a customer's LPO (e.g. "50
                      packs of Feta 200g") -- not kg. The sync converts
                      to kg using each item's Pack Size for production
                      tracking and revenue reporting. Customer and Item
                      are dropdowns sourced from the Customers and Items
                      tabs. LPO Expiry Date is left blank for LPOs with
                      none.

Setup (one-time, outside this file):
1. Google Cloud Console -> create a service account -> enable the
   Google Sheets API (and Drive API, read-only) for that project.
2. Create a JSON key for the service account, download it.
3. Share the spreadsheet with the service account's email address
   (found in the JSON as "client_email"), Viewer access is enough.
4. In Streamlit secrets (.streamlit/secrets.toml locally, or the
   Secrets panel on Streamlit Community Cloud), add:

    LPO_SHEET_URL = "https://docs.google.com/spreadsheets/d/.../edit"

    [lpo_credentials]
    type = "service_account"
    ... (paste straight from the downloaded JSON key)

Requirements: add to requirements.txt
    gspread>=6.0.0
    google-auth>=2.0.0
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, TypedDict, List, Dict, Any

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

from app.core.cheese_data_access import (
    save_lpo_line, get_lpo_lines, build_customer_name_cache, find_or_create_customer_id,
)

__all__ = [
    "get_lpo_register_sheet_rows", "get_item_master_map", "clear_lpo_sheet_caches",
    "sync_new_lpo_lines_from_sheet", "extract_sheet_id",
]

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")

LPO_REGISTER_TAB = "LPO Register"
ITEMS_TAB = "Items"


class LpoRegisterRow(TypedDict):
    customer_name: str
    item_name: str          # raw SKU text from the Sheet, e.g. "Browns Feta 200g"
    quantity_units: float   # units/packs ordered -- NOT kg
    delivery_date: Optional[date]
    lpo_number: str
    lpo_date: Optional[date]
    lpo_expiry_date: Optional[date]
    price_per_unit: float   # price for one unit/pack -- NOT per kg
    notes: str


class ItemMasterInfo(TypedDict):
    recipe: str
    pack_size_kg: Optional[float]


@st.cache_resource(show_spinner=False)
def _get_gspread_client() -> gspread.Client:
    creds_dict = dict(st.secrets["lpo_credentials"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=_SCOPES)
    return gspread.authorize(creds)


def extract_sheet_id(url_or_id: str) -> str:
    """Accepts either a bare Sheet id or a full Sheet URL
    (https://docs.google.com/spreadsheets/d/<id>/edit) and returns the id."""
    if "/d/" in url_or_id:
        return url_or_id.split("/d/")[1].split("/")[0]
    return url_or_id.strip()


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


@st.cache_data(ttl=300, show_spinner="Syncing LPO register from Google Sheet...")
def get_lpo_register_sheet_rows(sheet_id: str, worksheet_name: str = LPO_REGISTER_TAB) -> List[LpoRegisterRow]:
    """
    Pull every order line from the LPO Register tab. Rows missing a
    customer, item, LPO number, or a positive quantity are silently
    dropped here -- `sync_new_lpo_lines_from_sheet` re-derives which
    rows fail validation and reports them back to the caller instead.
    """
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    rows: List[LpoRegisterRow] = []
    for record in records:
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        customer_name = str(normalized.get("customer", "")).strip()
        item_name = str(normalized.get("item", "")).strip()
        lpo_number = str(normalized.get("lpo number", "")).strip()
        quantity_units = _parse_float(normalized.get("quantity (units)"), default=0.0)

        if not customer_name or not item_name or not lpo_number or quantity_units <= 0:
            continue

        rows.append({
            "customer_name": customer_name,
            "item_name": item_name,
            "quantity_units": quantity_units,
            "delivery_date": _parse_date(normalized.get("delivery date")),
            "lpo_number": lpo_number,
            "lpo_date": _parse_date(normalized.get("lpo date")),
            "lpo_expiry_date": _parse_date(normalized.get("lpo expiry date")),
            "price_per_unit": _parse_float(normalized.get("price per unit"), default=0.0),
            "notes": str(normalized.get("notes", "")).strip(),
        })
    return rows


@st.cache_data(ttl=300, show_spinner=False)
def get_item_master_map(sheet_id: str, worksheet_name: str = ITEMS_TAB) -> Dict[str, ItemMasterInfo]:
    """
    Maps each SKU's Item Name (lowercased) -> {recipe, pack_size_kg} from
    the Items tab. SKUs with a blank Recipe cell are excluded entirely --
    those are non-cheese products, not tracked here. pack_size_kg is
    None when the Items tab cell is blank/unparseable (a cheese item
    with no Recipe won't reach this far, but one WITH a Recipe and no
    Pack Size is a real data gap the sync needs to report, not guess at).
    """
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    mapping: Dict[str, ItemMasterInfo] = {}
    for record in records:
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        item_name = str(normalized.get("item name", "")).strip()
        recipe = str(normalized.get("recipe (cheese type)", "")).strip()
        if not item_name or not recipe:
            continue
        pack_size_raw = normalized.get("pack size (kg)")
        try:
            pack_size_kg = float(str(pack_size_raw).strip()) if pack_size_raw not in (None, "") else None
        except (ValueError, TypeError):
            pack_size_kg = None
        mapping[item_name.lower()] = {"recipe": recipe, "pack_size_kg": pack_size_kg}
    return mapping


def clear_lpo_sheet_caches() -> None:
    """Call from a 'Refresh from Sheet' button so the next read hits
    Google again instead of serving the 5-minute cache, for both the
    order lines and the item master mapping."""
    get_lpo_register_sheet_rows.clear()
    get_item_master_map.clear()


def sync_new_lpo_lines_from_sheet(sheet_id: str, valid_cheese_names: List[str],
                                    supabase_client=None) -> Dict[str, Any]:
    """
    One-way, additive sync: every Sheet row not already represented in
    lpo_lines becomes a new record. Dedup key is (lpo_number, item_name)
    -- the real SKU, not the resolved recipe -- so two different pack
    sizes of the same cheese on one LPO (e.g. Feta 200g and Feta 1kg)
    are correctly treated as two distinct lines, not duplicates of each
    other. Once a (lpo_number, item_name) pair has been synced, later
    syncs skip it even if the Sheet row changed afterwards -- the app
    never rewrites a record it already created.

    The Sheet is filled in units/packs (what's actually on a customer's
    LPO), not kg. Each item's Pack Size (kg), from the Items tab, is
    used to convert: quantity_kg = quantity_units * pack_size_kg, and
    price_per_kg = price_per_unit / pack_size_kg (for revenue reporting,
    which is still kg-based downstream). Both the original units and the
    computed kg are saved, so nothing about "what was actually ordered"
    gets lost in the conversion.

    Rows whose Item has no Recipe mapping, or has a Recipe but no usable
    Pack Size, are skipped and reported rather than silently dropped.

    Returns {"created": int, "skipped_existing": int, "skipped_invalid": int,
             "errors": [str, ...]}
    """
    sheet_rows = get_lpo_register_sheet_rows(sheet_id)
    item_master_map = get_item_master_map(sheet_id)
    existing_lines = get_lpo_lines(supabase_client=supabase_client)
    existing_keys = {(l["lpo_number"], l.get("sku_description")) for l in existing_lines}
    customer_cache = build_customer_name_cache(supabase_client)
    duplicate_warnings: List[str] = []

    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    errors: List[str] = []

    for row in sheet_rows:
        key = (row["lpo_number"], row["item_name"])
        if key in existing_keys:
            skipped_existing += 1
            continue

        item_info = item_master_map.get(row["item_name"].lower())
        if not item_info:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: '{row['item_name']}' has no Recipe "
                           f"mapping in the Items tab (non-cheese item, or mapping missing) — skipped.")
            continue

        recipe = item_info["recipe"]
        if recipe not in valid_cheese_names:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: '{row['item_name']}' maps to Recipe "
                           f"'{recipe}', which doesn't match any recipe in the Recipe Book — skipped.")
            continue

        pack_size_kg = item_info["pack_size_kg"]
        if not pack_size_kg or pack_size_kg <= 0:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: '{row['item_name']}' has no valid "
                           f"Pack Size (kg) set in the Items tab — skipped.")
            continue

        if not row["delivery_date"]:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: no valid Delivery Date — skipped.")
            continue

        try:
            customer_id = find_or_create_customer_id(
                row["customer_name"], customer_cache, supabase_client,
                duplicate_warnings=duplicate_warnings,
            )
            quantity_kg = row["quantity_units"] * pack_size_kg
            price_per_kg = (row["price_per_unit"] / pack_size_kg) if row["price_per_unit"] else 0.0
            save_lpo_line(
                lpo_number=row["lpo_number"], customer_name=row["customer_name"],
                delivery_date=row["delivery_date"], cheese_name=recipe,
                quantity_kg=quantity_kg, price_per_kg=price_per_kg,
                notes=row["notes"], supabase_client=supabase_client,
                customer_id=customer_id, lpo_date=row["lpo_date"],
                lpo_expiry_date=row["lpo_expiry_date"], sku_description=row["item_name"],
                quantity_units=row["quantity_units"], price_per_unit=row["price_per_unit"],
            )
            created += 1
            existing_keys.add(key)  # guard against duplicate rows within the same sheet pull
        except Exception as e:
            errors.append(f"LPO {row['lpo_number']}: {e}")

    return {"created": created, "skipped_existing": skipped_existing,
             "skipped_invalid": skipped_invalid, "errors": errors,
             "duplicate_warnings": duplicate_warnings}