"""
app/core/demand_utils.py
=========================
Pure helpers for turning raw Google Sheets check-out history into a clean
per-item daily demand series. Extracted from the inline logic in
all_items_ui.py's Demand Forecast section (All Items Analytics tab) so
JIT purchasing can build the same series without going through Streamlit
or re-deriving the column-detection / date-parsing heuristics.

No Streamlit, no DB calls.
"""
from typing import List, Optional
import pandas as pd

# Tier 1: stable per-SKU identifiers. Always prefer these for GROUPING and
# JOINING, since free-text description fields can vary slightly for the
# same physical item (a typo, an extra space, "3.5Kg" vs "3.5kg") -- using
# one of those as a group key would silently split one item into several.
ITEM_CODE_KEYWORDS = ['item_serial', 'item serial', 'sku', 'item_code', 'item code', 'serial']

# Tier 2: human-readable label. Used only when no code column exists, or
# explicitly requested for a display column (see supplier_utils.py, which
# pulls this alongside the code rather than instead of it).
ITEM_LABEL_KEYWORDS = ['item_description', 'item description', 'item_name', 'item name',
                        'product_name', 'product name', 'description', 'name']

# Combined, code-first: the default for "the item identifier column" when a
# caller just needs one consistent grouping key and doesn't care whether
# it's a code or a name -- e.g. compute_daily_demand_for_item, where
# grouping by a stable SKU matters more than what it's called on screen.
ITEM_NAME_KEYWORDS = ITEM_CODE_KEYWORDS + ITEM_LABEL_KEYWORDS + ['item', 'product']

# Values that show up in Google Sheets exports as formula errors or blanks,
# not real data -- filtered out anywhere item/supplier/etc. values are
# grouped or displayed, same spirit as the existing 'nan'/'' checks already
# used elsewhere in this codebase (e.g. all_items_ui.py's top-items filters).
_JUNK_VALUES = {'', 'nan', 'none', 'nat', '#n/a', '#ref!', '#value!', '#div/0!', '#null!', '#name?', 'n/a'}


def is_junk_value(value) -> bool:
    """True for blanks, NaN, and common spreadsheet formula-error strings
    (#N/A, #REF!, etc.) that shouldn't be treated as real category/item/
    supplier values."""
    return str(value).strip().lower() in _JUNK_VALUES


def parse_date_safe(date_str) -> Optional[pd.Timestamp]:
    """Best-effort date parse across the handful of formats that show up in
    the Google Sheets exports. Returns None (not NaT) for anything
    unparseable so callers can .dropna() cleanly either way."""
    if pd.isna(date_str) or date_str in ('', 'nan', 'None', 'NaT'):
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S'):
        parsed = pd.to_datetime(date_str, format=fmt, errors='coerce')
        if pd.notna(parsed):
            return parsed
    return pd.to_datetime(date_str, format='mixed', errors='coerce')


def detect_column(df: pd.DataFrame, keywords: List[str]) -> Optional[str]:
    """First column matching the highest-priority keyword in the list.
    Checks keywords in order -- every column is checked against keywords[0]
    before falling through to keywords[1], etc. -- so put the most specific
    keyword first when a looser one could also match an unwanted column
    (e.g. 'item_name' before the bare 'item', which would otherwise just as
    happily match ITEM_SERIAL or ITEM_CATEGORY). Same underlying heuristic
    already used throughout all_items_ui.py to find 'the item column' /
    'the date column' / 'the quantity column' across Google Sheets exports
    with inconsistent headers -- just keyword-priority-aware now."""
    for keyword in keywords:
        for col in df.columns:
            if keyword in col.lower():
                return col
    return None


def compute_daily_demand_for_item(check_out_df: pd.DataFrame, item_name: str,
                                   item_col: Optional[str] = None,
                                   qty_col: Optional[str] = None,
                                   date_col: Optional[str] = None) -> pd.DataFrame:
    """Date/Order_Quantity_kg series for one item, built from raw check-out
    history. Auto-detects item/quantity/date columns by name if not passed
    explicitly, so callers (JIT, forecasting) don't need to know the exact
    Google Sheet header names in advance. Returns an empty DataFrame with
    the right columns (never raises) if the item has no usable history --
    callers should treat an empty result as "not enough data", same as the
    len(daily_demand) >= 5 check already used in the Analytics tab.
    """
    empty = pd.DataFrame(columns=['Date', 'Order_Quantity_kg'])
    if check_out_df is None or check_out_df.empty:
        return empty

    item_col = item_col or detect_column(check_out_df, ITEM_NAME_KEYWORDS)
    qty_col = qty_col or detect_column(check_out_df, ['quantity', 'qty'])
    date_col = date_col or detect_column(check_out_df, ['date'])
    if not (item_col and qty_col and date_col):
        return empty

    item_history = check_out_df[check_out_df[item_col] == item_name].copy()
    if item_history.empty:
        return empty

    item_history['DATE'] = item_history[date_col].astype(str).str.strip().apply(parse_date_safe)
    item_history[qty_col] = pd.to_numeric(item_history[qty_col], errors='coerce')
    item_history = item_history.dropna(subset=['DATE', qty_col])
    if item_history.empty:
        return empty

    daily_demand = item_history.groupby('DATE')[qty_col].sum().reset_index()
    daily_demand.columns = ['Date', 'Order_Quantity_kg']
    return daily_demand.sort_values('Date').reset_index(drop=True)


if __name__ == "__main__":
    print("Test 1: detect_column")
    df = pd.DataFrame(columns=['ITEM_NAME', 'QUANTITY_OUT', 'CHECKOUT_DATE'])
    assert detect_column(df, ['item', 'product', 'name']) == 'ITEM_NAME'
    assert detect_column(df, ['quantity', 'qty']) == 'QUANTITY_OUT'
    assert detect_column(df, ['date']) == 'CHECKOUT_DATE'
    assert detect_column(df, ['nonexistent']) is None

    print("\nTest 2: compute_daily_demand_for_item, normal case")
    check_out_df = pd.DataFrame([
        {'ITEM_NAME': 'Mozzarella', 'QUANTITY_OUT': 5, 'CHECKOUT_DATE': '2026-07-01'},
        {'ITEM_NAME': 'Mozzarella', 'QUANTITY_OUT': 3, 'CHECKOUT_DATE': '2026-07-01'},
        {'ITEM_NAME': 'Mozzarella', 'QUANTITY_OUT': 4, 'CHECKOUT_DATE': '2026-07-02'},
        {'ITEM_NAME': 'Gouda', 'QUANTITY_OUT': 10, 'CHECKOUT_DATE': '2026-07-01'},
    ])
    demand = compute_daily_demand_for_item(check_out_df, 'Mozzarella')
    print(demand)
    assert len(demand) == 2
    assert demand.iloc[0]['Order_Quantity_kg'] == 8  # 5+3 on 2026-07-01
    assert demand.iloc[1]['Order_Quantity_kg'] == 4

    print("\nTest 3: item with no history returns empty, not an error")
    empty_result = compute_daily_demand_for_item(check_out_df, 'Halloumi')
    assert empty_result.empty
    assert list(empty_result.columns) == ['Date', 'Order_Quantity_kg']

    print("\nTest 4: missing columns returns empty, not a KeyError")
    bad_df = pd.DataFrame([{'foo': 1, 'bar': 2}])
    assert compute_daily_demand_for_item(bad_df, 'anything').empty

    print("\nTest 5: detect_column prefers the stable code column over a text label")
    trap_df = pd.DataFrame(columns=['ITEM_SERIAL', 'ITEM_NAME', 'QUANTITY_OUT', 'CHECKOUT_DATE'])
    assert detect_column(trap_df, ITEM_NAME_KEYWORDS) == 'ITEM_SERIAL', \
        f"got {detect_column(trap_df, ITEM_NAME_KEYWORDS)} -- grouping key should be the code, not the label"

    print("\nTest 5b: falls back to a label column when no code column exists")
    label_only_df = pd.DataFrame(columns=['ITEM_DESCRIPTION', 'QUANTITY', 'Date'])
    assert detect_column(label_only_df, ITEM_NAME_KEYWORDS) == 'ITEM_DESCRIPTION'

    print("\nTest 5c: real CHECK_IN column set (ITEM_SERIAL + ITEM_DESCRIPTION, no ITEM_NAME)")
    real_check_in_cols = pd.DataFrame(columns=[
        'Date', 'ITEM_SERIAL', 'ITEM_DESCRIPTION', 'QUANTITY', 'UoM',
        'UNIT_PRICE Excl Vat', 'Total Value (EXCL TAX)', 'SUPPLIER', 'ITEM_CATEGORY',
    ])
    assert detect_column(real_check_in_cols, ITEM_NAME_KEYWORDS) == 'ITEM_SERIAL'
    assert detect_column(real_check_in_cols, ITEM_LABEL_KEYWORDS) == 'ITEM_DESCRIPTION'

    print("\nTest 6: is_junk_value catches spreadsheet formula errors")
    assert is_junk_value('#N/A')
    assert is_junk_value('#REF!')
    assert is_junk_value('')
    assert is_junk_value(None)
    assert not is_junk_value('Mozzarella')

    print("\nAll demand_utils checks passed.")