"""
app/core/supplier_utils.py
============================
Pure helpers for deriving item-supplier relationships from CHECK_IN history
already recorded in Google Sheets. CHECK_IN records which supplier
delivered which item and when, but NOT lead time (order date isn't
tracked -- only delivery date), so this gives a starting list of "who
supplies what" to seed the new SUPPLIERS tab with, not the lead
times/MOQs/costs themselves -- those still need to be filled in by hand,
since they're not observable from receipt records alone.

No Streamlit, no DB calls.
"""
from typing import Optional
import pandas as pd

from app.core.demand_utils import detect_column, parse_date_safe


def derive_item_supplier_links_from_check_in(check_in_df: pd.DataFrame,
                                              item_col: Optional[str] = None,
                                              supplier_col: Optional[str] = None,
                                              date_col: Optional[str] = None) -> pd.DataFrame:
    """Unique (Item, Supplier) pairs from CHECK_IN history, with delivery
    count and first/last delivery date -- a starting point for deciding
    which item/supplier rows to create in the new SUPPLIERS tab, not a
    substitute for it. Lead time, MOQ, unit cost, and reliability still
    need to be entered by hand; CHECK_IN alone has no order date to derive
    lead time from.

    Auto-detects columns by name if not passed explicitly, same heuristic
    as compute_daily_demand_for_item.
    """
    empty = pd.DataFrame(columns=['Item', 'Supplier', 'Delivery Count', 'First Seen', 'Last Seen'])
    if check_in_df is None or check_in_df.empty:
        return empty

    item_col = item_col or detect_column(check_in_df, ['item', 'product', 'name'])
    supplier_col = supplier_col or detect_column(check_in_df, ['supplier', 'vendor'])
    date_col = date_col or detect_column(check_in_df, ['date'])

    if not (item_col and supplier_col):
        return empty

    df = check_in_df.copy()
    df = df.dropna(subset=[item_col, supplier_col])
    df = df[(df[item_col].astype(str).str.strip() != '') & (df[supplier_col].astype(str).str.strip() != '')]
    if df.empty:
        return empty

    if date_col:
        df['_DATE'] = df[date_col].astype(str).str.strip().apply(parse_date_safe)

    rows = []
    for (item, supplier), group in df.groupby([item_col, supplier_col]):
        row = {
            'Item': item,
            'Supplier': supplier,
            'Delivery Count': len(group),
        }
        if date_col and group['_DATE'].notna().any():
            row['First Seen'] = group['_DATE'].min().date().isoformat()
            row['Last Seen'] = group['_DATE'].max().date().isoformat()
        else:
            row['First Seen'] = None
            row['Last Seen'] = None
        rows.append(row)

    result = pd.DataFrame(rows)
    return result.sort_values(['Item', 'Supplier']).reset_index(drop=True)


if __name__ == "__main__":
    print("Test 1: basic item-supplier derivation")
    check_in_df = pd.DataFrame([
        {'ITEM_NAME': 'Rennet', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-01'},
        {'ITEM_NAME': 'Rennet', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-06-01'},
        {'ITEM_NAME': 'Rennet', 'SUPPLIER': 'BioCurd Supplies', 'DATE': '2026-06-15'},
        {'ITEM_NAME': 'Salt', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-20'},
        {'ITEM_NAME': 'Salt', 'SUPPLIER': None, 'DATE': '2026-05-25'},  # missing supplier, excluded
    ])
    links = derive_item_supplier_links_from_check_in(check_in_df)
    print(links)
    assert len(links) == 3
    rennet_dairychem = links[(links['Item'] == 'Rennet') & (links['Supplier'] == 'DairyChem Ltd')].iloc[0]
    assert rennet_dairychem['Delivery Count'] == 2
    assert rennet_dairychem['First Seen'] == '2026-05-01'
    assert rennet_dairychem['Last Seen'] == '2026-06-01'

    print("\nTest 2: empty input returns empty with right columns")
    empty_result = derive_item_supplier_links_from_check_in(pd.DataFrame())
    assert empty_result.empty
    assert list(empty_result.columns) == ['Item', 'Supplier', 'Delivery Count', 'First Seen', 'Last Seen']

    print("\nTest 3: missing supplier column returns empty, not a KeyError")
    no_supplier_df = pd.DataFrame([{'ITEM_NAME': 'Rennet', 'DATE': '2026-05-01'}])
    assert derive_item_supplier_links_from_check_in(no_supplier_df).empty

    print("\nAll supplier_utils checks passed.")