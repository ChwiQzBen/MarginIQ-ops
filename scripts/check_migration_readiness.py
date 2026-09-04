"""
scripts/check_migration_readiness.py
======================================
One-time diagnostic before Phase 4a's historical import: compares
Check-In and Check-Out item names against the canonical item names on
the Stock sheet (ITEM_NAME) to see how many rows would match cleanly on
import versus need reconciliation first.

Run this BEFORE building the actual import script. If match rates are
high, the import is straightforward. If not, the unmatched list below
tells you exactly what needs fixing in the sheet -- or what needs a
manual old-name -> canonical-name mapping -- before anything gets
written to stock_checkins / stock_checkouts.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _migration_db import init_supabase_admin
from app.core.google_sheet_reader import GoogleSheetReader
from app.core.demand_utils import detect_column, ITEM_LABEL_KEYWORDS


def check_sheet(name: str, df, canonical_names: set):
    if df.empty:
        print(f"{name}: sheet is empty, nothing to check.")
        return

    item_col = detect_column(df, ITEM_LABEL_KEYWORDS)
    if not item_col:
        print(f"{name}: could not find an item column.")
        return

    names = df[item_col].dropna().astype(str).str.strip()
    distinct_names = sorted(names.unique())
    matched = [n for n in distinct_names if n in canonical_names]
    unmatched = [n for n in distinct_names if n not in canonical_names]

    total_rows = len(df)
    matched_rows = int(names.isin(canonical_names).sum())
    unmatched_rows = total_rows - matched_rows

    print(f"\n=== {name} (item column: '{item_col}') ===")
    print(f"Distinct item names: {len(distinct_names)}  (matched: {len(matched)}, unmatched: {len(unmatched)})")
    print(f"Rows: {total_rows} total  |  matched: {matched_rows} ({matched_rows/total_rows*100:.1f}%)  |  unmatched: {unmatched_rows} ({unmatched_rows/total_rows*100:.1f}%)")

    if unmatched:
        print("\nUnmatched names (fix in the sheet, or map before importing):")
        for n in unmatched:
            count = int((names == n).sum())
            print(f"  '{n}' -- {count} row(s)")


def main():
    gsheet = GoogleSheetReader()
    if not gsheet.authenticate():
        print("Could not connect to Google Sheets.")
        return

    stock_df = gsheet.get_stock_with_pricing()
    if stock_df.empty or 'ITEM_NAME' not in stock_df.columns:
        print("Stock sheet is empty or has no ITEM_NAME column -- can't build the canonical list.")
        return
    canonical_names = set(stock_df['ITEM_NAME'].dropna().astype(str).str.strip())

    # Also include item_master -- items added there directly (bulk
    # scripts, the Add/Edit Item form) are just as valid a match now that
    # load_inventory_data() merges both sources. Uses the elevated
    # connection like every other standalone script here -- the app's
    # own init_supabase() depends on a live Streamlit session that
    # doesn't exist when run bare, and fails silently rather than erroring.
    try:
        from app.core.item_master import get_all_items
        supabase_client = init_supabase_admin()
        canonical_names |= {i['item_name'] for i in get_all_items(active_only=False, supabase_client=supabase_client)}
    except Exception as e:
        print(f"(Could not load item_master for cross-check: {e})")

    print(f"Canonical item names (Stock sheet + item_master): {len(canonical_names)}")

    check_sheet("Check-In", gsheet.get_check_in(), canonical_names)
    check_sheet("Check-Out", gsheet.get_check_out(), canonical_names)


if __name__ == "__main__":
    main()
