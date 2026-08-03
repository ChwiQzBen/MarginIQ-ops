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
                      Recipe (Cheese Type)
                      One row per real product. Recipe (Cheese Type) must
                      exactly match a recipe name in the Recipe Book
                      (case-sensitive) for cheese products; leave blank
                      for non-cheese products.

    Customers         Customer Name | Customer Code
                      One row per real customer.

    LPO Register      Customer | Item | Quantity (kg) | Delivery Date |
                      LPO Number | LPO Date | LPO Expiry Date | Price
                      per kg (optional) | Notes (optional)
                      Customer and Item are dropdowns sourced from the
                      Customers and Items tabs. LPO Expiry Date is left
                      blank for LPOs with none.

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

from app.core.cheese_data_access import save_lpo_line, get_lpo_lines, get_customers, save_customer

__all__ = [
    "get_lpo_register_sheet_rows", "get_item_recipe_map", "clear_lpo_sheet_caches",
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
    quantity_kg: float
    delivery_date: Optional[date]
    lpo_number: str
    lpo_date: Optional[date]
    lpo_expiry_date: Optional[date]
    price_per_kg: float
    notes: str


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
        quantity_kg = _parse_float(normalized.get("quantity (kg)"), default=0.0)

        if not customer_name or not item_name or not lpo_number or quantity_kg <= 0:
            continue

        rows.append({
            "customer_name": customer_name,
            "item_name": item_name,
            "quantity_kg": quantity_kg,
            "delivery_date": _parse_date(normalized.get("delivery date")),
            "lpo_number": lpo_number,
            "lpo_date": _parse_date(normalized.get("lpo date")),
            "lpo_expiry_date": _parse_date(normalized.get("lpo expiry date")),
            "price_per_kg": _parse_float(normalized.get("price per kg"), default=0.0),
            "notes": str(normalized.get("notes", "")).strip(),
        })
    return rows


@st.cache_data(ttl=300, show_spinner=False)
def get_item_recipe_map(sheet_id: str, worksheet_name: str = ITEMS_TAB) -> Dict[str, str]:
    """
    Maps each SKU's Item Name (lowercased) -> Recipe (Cheese Type) from
    the Items tab. SKUs with a blank Recipe cell are excluded from the
    map entirely -- those are non-cheese products, not tracked here.
    """
    client = _get_gspread_client()
    worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()

    mapping: Dict[str, str] = {}
    for record in records:
        normalized = {str(k).strip().lower(): v for k, v in record.items()}
        item_name = str(normalized.get("item name", "")).strip()
        recipe = str(normalized.get("recipe (cheese type)", "")).strip()
        if item_name and recipe:
            mapping[item_name.lower()] = recipe
    return mapping


def clear_lpo_sheet_caches() -> None:
    """Call from a 'Refresh from Sheet' button so the next read hits
    Google again instead of serving the 5-minute cache, for both the
    order lines and the item->recipe mapping."""
    get_lpo_register_sheet_rows.clear()
    get_item_recipe_map.clear()


def _resolve_customer_id(customer_name: str, existing_customers: List[Dict[str, Any]],
                          supabase_client=None) -> Optional[int]:
    match = next((c for c in existing_customers
                  if c["name"].strip().lower() == customer_name.strip().lower()), None)
    if match:
        return match["id"]
    return save_customer(name=customer_name, supabase_client=supabase_client)


def sync_new_lpo_lines_from_sheet(sheet_id: str, valid_cheese_names: List[str],
                                    supabase_client=None) -> Dict[str, Any]:
    """
    One-way, additive sync: every Sheet row not already represented in
    lpo_lines becomes a new record. Dedup key is (lpo_number, item_name)
    -- the real SKU, not the resolved recipe -- so two different pack
    sizes of the same cheese on one LPO (e.g. Feta 200g and Feta 1kg)
    are correctly treated as two distinct lines, not duplicates of each
    other. Once a (lpo_number, item_name) pair has been synced, later
    syncs skip it even if the Sheet row's quantity/dates/price changed
    afterwards -- the app never rewrites a record it already created.

    Rows whose Item has no Recipe mapping in the Items tab (non-cheese
    products) are skipped and reported, not silently dropped.

    Returns {"created": int, "skipped_existing": int, "skipped_invalid": int,
             "errors": [str, ...]}
    """
    sheet_rows = get_lpo_register_sheet_rows(sheet_id)
    item_recipe_map = get_item_recipe_map(sheet_id)
    existing_lines = get_lpo_lines(supabase_client=supabase_client)
    existing_keys = {(l["lpo_number"], l.get("sku_description")) for l in existing_lines}
    existing_customers = get_customers(supabase_client=supabase_client)

    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    errors: List[str] = []

    for row in sheet_rows:
        key = (row["lpo_number"], row["item_name"])
        if key in existing_keys:
            skipped_existing += 1
            continue

        recipe = item_recipe_map.get(row["item_name"].lower())
        if not recipe:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: '{row['item_name']}' has no Recipe "
                           f"mapping in the Items tab (non-cheese item, or mapping missing) — skipped.")
            continue
        if recipe not in valid_cheese_names:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: '{row['item_name']}' maps to Recipe "
                           f"'{recipe}', which doesn't match any recipe in the Recipe Book — skipped.")
            continue
        if not row["delivery_date"]:
            skipped_invalid += 1
            errors.append(f"LPO {row['lpo_number']}: no valid Delivery Date — skipped.")
            continue

        try:
            customer_id = _resolve_customer_id(row["customer_name"], existing_customers, supabase_client)
            save_lpo_line(
                lpo_number=row["lpo_number"], customer_name=row["customer_name"],
                delivery_date=row["delivery_date"], cheese_name=recipe,
                quantity_kg=row["quantity_kg"], price_per_kg=row["price_per_kg"],
                notes=row["notes"], supabase_client=supabase_client,
                customer_id=customer_id, lpo_date=row["lpo_date"],
                lpo_expiry_date=row["lpo_expiry_date"], sku_description=row["item_name"],
            )
            created += 1
            existing_keys.add(key)  # guard against duplicate rows within the same sheet pull
        except Exception as e:
            errors.append(f"LPO {row['lpo_number']}: {e}")

    return {"created": created, "skipped_existing": skipped_existing,
             "skipped_invalid": skipped_invalid, "errors": errors}