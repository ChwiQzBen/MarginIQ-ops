"""
scripts/import_historical_movements.py
=========================================
Phase 4a: one-time bulk import of full Check-In and Check-Out history
from Google Sheets into stock_checkins / stock_checkouts. Run AFTER
add_missing_items.py and after check_migration_readiness.py shows a
clean match rate.

Uses the service role connection (_migration_db.py) -- this writes
thousands of rows outside any user's session, not appropriate for the
app's own RLS-constrained connection.

Usage:
    python scripts/import_historical_movements.py --dry-run   # preview only
    python scripts/import_historical_movements.py --reset     # clear any prior import, then import live
    python scripts/import_historical_movements.py             # import live (no cleanup first)
"""
import sys
import os
import re
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from datetime import datetime
from _migration_db import init_supabase_admin
from app.core.google_sheet_reader import GoogleSheetReader
from app.core.demand_utils import detect_column, ITEM_LABEL_KEYWORDS, is_junk_value

BATCH_SIZE = 500
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3
IMPORTED_BY = "phase4-import"


def _parse_qty(raw):
    """Same tolerant numeric extraction as the Check-Out Oversight fix --
    '2kgs' parses as 2.0. None for genuinely non-numeric or junk values."""
    if pd.isna(raw) or str(raw).strip() == '' or is_junk_value(raw):
        return None
    match = re.match(r'^\s*(-?\d+\.?\d*)', str(raw))
    return float(match.group(1)) if match else None


def _col(df, name):
    return name if name in df.columns else None


def _val(row, col):
    """Blank for a missing column, a blank cell, OR a spreadsheet junk
    value (#N/A, #REF!, etc.) -- these should never land in the database
    as literal junk text."""
    if not col:
        return ''
    v = row.get(col, '')
    if pd.isna(v) or is_junk_value(v):
        return ''
    return str(v).strip()


def _is_data_error(exc) -> bool:
    """True for a genuine constraint/data violation -- retrying won't
    help, the row itself needs fixing. Supabase/PostgREST errors come
    back with a 'code' key; network-level timeouts/resets don't."""
    return "'code':" in str(exc)


def reset_imported_data(supabase_client, dry_run):
    """Deletes previously-imported rows (created_by=IMPORTED_BY) from
    both tables, so a re-run after a bug fix starts clean instead of
    risking duplicates from a prior partial run. Only ever touches rows
    carrying this script's own marker -- never anything entered through
    the app."""
    for table in ("stock_checkins", "stock_checkouts"):
        if dry_run:
            existing = supabase_client.table(table).select("id", count="exact").eq("created_by", IMPORTED_BY).execute()
            print(f"{table}: DRY RUN -- would delete {existing.count} previously-imported row(s)")
        else:
            result = supabase_client.table(table).delete().eq("created_by", IMPORTED_BY).execute()
            print(f"{table}: deleted {len(result.data)} previously-imported row(s)")


def _batched_insert(supabase_client, table, rows, label, dry_run):
    if dry_run:
        print(f"{label}: DRY RUN -- would insert {len(rows)} rows. First 3:")
        for r in rows[:3]:
            print(f"  {r}")
        return len(rows), 0

    inserted, failed = 0, 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                supabase_client.table(table).insert(chunk).execute()
                inserted += len(chunk)
                print(f"{label}: inserted rows {i+1}-{i+len(chunk)} of {len(rows)}")
                break
            except Exception as e:
                if _is_data_error(e) or attempt == MAX_RETRIES:
                    failed += len(chunk)
                    print(f"{label}: FAILED batch {i+1}-{i+len(chunk)} (attempt {attempt}/{MAX_RETRIES}) -- {e}")
                    break
                print(f"{label}: batch {i+1}-{i+len(chunk)} failed (attempt {attempt}/{MAX_RETRIES}, retrying in {RETRY_DELAY_SECONDS}s) -- {e}")
                time.sleep(RETRY_DELAY_SECONDS)
    return inserted, failed


def import_check_ins(supabase_client, check_in_df, dry_run):
    if check_in_df.empty:
        print("Check-In sheet is empty, nothing to import.")
        return

    item_col = detect_column(check_in_df, ITEM_LABEL_KEYWORDS)
    date_col = _col(check_in_df, 'Date')
    qty_col = _col(check_in_df, 'QUANTITY')
    unit_col = _col(check_in_df, 'UoM')
    price_col = _col(check_in_df, 'UNIT_PRICE Excl Vat')
    supplier_col = _col(check_in_df, 'SUPPLIER')
    category_col = _col(check_in_df, 'ITEM_CATEGORY')
    batch_col = _col(check_in_df, 'BATCH NO.')
    temp_col = _col(check_in_df, 'TEMP')
    coa_col = _col(check_in_df, 'COA')
    invoice_col = _col(check_in_df, 'INVOICE/LPO')
    store_col = _col(check_in_df, 'STORE')
    received_col = _col(check_in_df, 'RECEIVED BY')
    confirmed_col = _col(check_in_df, 'CONFIRMED BY')

    if not item_col or not date_col or not qty_col:
        print(f"Check-In: missing a required column (item={item_col}, date={date_col}, qty={qty_col}), aborting.")
        return

    rows, skipped = [], 0
    for _, row in check_in_df.iterrows():
        item_name = _val(row, item_col)
        if not item_name or item_name.lower() == 'nan':
            skipped += 1
            continue
        checkin_date = pd.to_datetime(row.get(date_col), errors='coerce')
        if pd.isna(checkin_date):
            skipped += 1
            continue
        qty = _parse_qty(row.get(qty_col))
        if qty is None:
            skipped += 1
            continue

        rows.append({
            "checkin_date": checkin_date.strftime('%Y-%m-%d'),
            "item_name": item_name,
            "item_category": _val(row, category_col),
            "quantity": qty,
            "unit": _val(row, unit_col),
            "unit_price": _parse_qty(row.get(price_col)) or 0.0,
            "supplier": _val(row, supplier_col),
            "store": _val(row, store_col),
            "batch_no": _val(row, batch_col),
            "temperature": _val(row, temp_col),
            "coa": _val(row, coa_col),
            "invoice_lpo": _val(row, invoice_col),
            "received_by": _val(row, received_col),
            "confirmed_by": _val(row, confirmed_col),
            "notes": "",
            "created_by": IMPORTED_BY,
            "created_at": datetime.now().isoformat(),
        })

    print(f"Check-In: {len(rows)} rows ready, {skipped} skipped (blank item/date/quantity)")
    inserted, failed = _batched_insert(supabase_client, "stock_checkins", rows, "Check-In", dry_run)
    print(f"Check-In: {inserted} inserted, {failed} failed\n")


def import_check_outs(supabase_client, check_out_df, dry_run):
    if check_out_df.empty:
        print("Check-Out sheet is empty, nothing to import.")
        return

    item_col = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)
    date_col = _col(check_out_df, 'DATE')
    qty_col = _col(check_out_df, 'QUANTITY')
    unit_col = _col(check_out_df, 'UNIT_OF_MEASURE')
    category_col = _col(check_out_df, 'ITEM_CATEGORY')
    department_col = _col(check_out_df, 'DEPARTMENT')
    issued_to_col = _col(check_out_df, 'ISSUED_TO')
    batch_col = _col(check_out_df, 'BATCH NO.')
    reference_col = _col(check_out_df, 'REFERENCE')
    store_col = _col(check_out_df, 'STORE')

    if not item_col or not date_col or not qty_col:
        print(f"Check-Out: missing a required column (item={item_col}, date={date_col}, qty={qty_col}), aborting.")
        return

    rows, skipped = [], 0
    for _, row in check_out_df.iterrows():
        item_name = _val(row, item_col)
        if not item_name or item_name.lower() == 'nan' or item_name == item_col:
            skipped += 1  # blank, junk, or the stray header-row leak
            continue
        checkout_date = pd.to_datetime(row.get(date_col), errors='coerce')
        if pd.isna(checkout_date):
            skipped += 1
            continue
        qty = _parse_qty(row.get(qty_col))
        if qty is None:
            skipped += 1
            continue

        rows.append({
            "checkout_date": checkout_date.strftime('%Y-%m-%d'),
            "item_name": item_name,
            "item_category": _val(row, category_col),
            "quantity": qty,
            "unit": _val(row, unit_col),
            "store": _val(row, store_col),
            "requested_by": _val(row, issued_to_col),
            "destination": _val(row, department_col),
            "batch_no": _val(row, batch_col),
            "dispatch_slip_number": _val(row, reference_col),
            "notes": "",
            "status": "Pending",
            "created_by": IMPORTED_BY,
            "created_at": datetime.now().isoformat(),
        })

    print(f"Check-Out: {len(rows)} rows ready, {skipped} skipped (blank item/date/quantity)")
    inserted, failed = _batched_insert(supabase_client, "stock_checkouts", rows, "Check-Out", dry_run)
    print(f"Check-Out: {inserted} inserted, {failed} failed\n")


def main():
    dry_run = "--dry-run" in sys.argv
    reset = "--reset" in sys.argv
    print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'LIVE — will write to Supabase'}"
          f"{' + RESET (clearing prior import first)' if reset else ''}\n")

    supabase_client = init_supabase_admin()

    if reset:
        print("=== Reset ===")
        reset_imported_data(supabase_client, dry_run)
        print()

    gsheet = GoogleSheetReader()
    if not gsheet.authenticate():
        print("Could not connect to Google Sheets.")
        return

    print("=== Check-In ===")
    import_check_ins(supabase_client, gsheet.get_check_in(), dry_run)

    print("=== Check-Out ===")
    import_check_outs(supabase_client, gsheet.get_check_out(), dry_run)

    print("Done." if not dry_run else "Dry run complete -- nothing was written. Re-run without --dry-run to commit.")


if __name__ == "__main__":
    main()
