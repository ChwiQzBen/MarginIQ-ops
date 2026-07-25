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
    """First column whose lowercased name contains any of the given
    keywords -- same heuristic already used throughout all_items_ui.py to
    find 'the item column' / 'the date column' / 'the quantity column'
    across Google Sheets exports with inconsistent headers."""
    for col in df.columns:
        col_lower = col.lower()
        if any(k in col_lower for k in keywords):
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

    item_col = item_col or detect_column(check_out_df, ['item', 'product', 'name'])
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

    print("\nAll demand_utils checks passed.")