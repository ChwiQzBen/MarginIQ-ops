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

Groups by the item's stable code/serial (see ITEM_CODE_KEYWORDS in
demand_utils.py), not a free-text description -- CHECK_IN sheets in the
wild have both (e.g. ITEM_SERIAL + ITEM_DESCRIPTION), and the description
can vary slightly between entries for the same physical item. Grouping by
that would silently split one item into several rows. A readable label is
still attached to each row (from the sheet's own description column, no
join required) purely for human review of the output.

No Streamlit, no DB calls.
"""
from typing import Optional
import pandas as pd

from app.core.demand_utils import detect_column, parse_date_safe, is_junk_value, ITEM_NAME_KEYWORDS, ITEM_LABEL_KEYWORDS

OUTPUT_COLUMNS = ['Item Code', 'Item Description', 'Supplier', 'Delivery Count', 'First Seen', 'Last Seen']


def derive_item_supplier_links_from_check_in(check_in_df: pd.DataFrame,
                                              item_col: Optional[str] = None,
                                              supplier_col: Optional[str] = None,
                                              date_col: Optional[str] = None,
                                              label_col: Optional[str] = None) -> pd.DataFrame:
    """Unique (Item Code, Supplier) pairs from CHECK_IN history, with a
    readable Item Description, delivery count, and first/last delivery
    date -- a starting point for deciding which item/supplier rows to
    create in the new SUPPLIERS tab, not a substitute for it. Lead time,
    MOQ, unit cost, and reliability still need to be entered by hand;
    CHECK_IN alone has no order date to derive lead time from.

    Auto-detects columns by name if not passed explicitly. item_col uses
    the code-first ITEM_NAME_KEYWORDS priority (see demand_utils.py) so
    grouping is stable even when a text description column is also
    present; label_col separately looks for a description/name column to
    attach for readability, without changing what's grouped on.
    """
    empty = pd.DataFrame(columns=OUTPUT_COLUMNS)
    if check_in_df is None or check_in_df.empty:
        return empty

    item_col = item_col or detect_column(check_in_df, ITEM_NAME_KEYWORDS)
    supplier_col = supplier_col or detect_column(check_in_df, ['supplier', 'vendor'])
    date_col = date_col or detect_column(check_in_df, ['date'])
    label_col = label_col or detect_column(check_in_df, ITEM_LABEL_KEYWORDS)
    if label_col == item_col:
        label_col = None  # sheet has no separate description column -- don't label with the code itself

    if not (item_col and supplier_col):
        return empty

    df = check_in_df.copy()
    df = df.dropna(subset=[item_col, supplier_col])
    df = df[~df[item_col].apply(is_junk_value) & ~df[supplier_col].apply(is_junk_value)]
    if df.empty:
        return empty

    if date_col:
        df['_DATE'] = df[date_col].astype(str).str.strip().apply(parse_date_safe)

    rows = []
    for (item, supplier), group in df.groupby([item_col, supplier_col]):
        row = {
            'Item Code': item,
            'Supplier': supplier,
            'Delivery Count': len(group),
        }

        label_value = None
        if label_col:
            clean_labels = group[label_col].dropna()
            clean_labels = clean_labels[~clean_labels.apply(is_junk_value)]
            if not clean_labels.empty:
                # most common variant, in case the description differs
                # slightly across entries for the same item code
                label_value = clean_labels.mode().iloc[0]
        row['Item Description'] = label_value

        if date_col and group['_DATE'].notna().any():
            row['First Seen'] = group['_DATE'].min().date().isoformat()
            row['Last Seen'] = group['_DATE'].max().date().isoformat()
        else:
            row['First Seen'] = None
            row['Last Seen'] = None
        rows.append(row)

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return result.sort_values(['Item Code', 'Supplier']).reset_index(drop=True)


if __name__ == "__main__":
    print("Test 1: basic item-supplier derivation, code + label present")
    check_in_df = pd.DataFrame([
        {'ITEM_SERIAL': 'CHEM-101', 'ITEM_DESCRIPTION': 'Bio-Clean', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-01'},
        {'ITEM_SERIAL': 'CHEM-101', 'ITEM_DESCRIPTION': 'Bio-Clean', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-06-01'},
        {'ITEM_SERIAL': 'CHEM-101', 'ITEM_DESCRIPTION': 'Bio-Clean', 'SUPPLIER': 'BioCurd Supplies', 'DATE': '2026-06-15'},
        {'ITEM_SERIAL': 'PCKG-117', 'ITEM_DESCRIPTION': 'Limuru Reserve cheddar', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-20'},
        {'ITEM_SERIAL': 'PCKG-117', 'ITEM_DESCRIPTION': 'Limuru Reserve cheddar', 'SUPPLIER': None, 'DATE': '2026-05-25'},  # missing supplier, excluded
    ])
    links = derive_item_supplier_links_from_check_in(check_in_df)
    print(links)
    assert len(links) == 3
    chem_dairychem = links[(links['Item Code'] == 'CHEM-101') & (links['Supplier'] == 'DairyChem Ltd')].iloc[0]
    assert chem_dairychem['Delivery Count'] == 2
    assert chem_dairychem['Item Description'] == 'Bio-Clean'
    assert chem_dairychem['First Seen'] == '2026-05-01'
    assert chem_dairychem['Last Seen'] == '2026-06-01'

    print("\nTest 2: empty input returns empty with right columns")
    empty_result = derive_item_supplier_links_from_check_in(pd.DataFrame())
    assert empty_result.empty
    assert list(empty_result.columns) == OUTPUT_COLUMNS

    print("\nTest 3: missing supplier column returns empty, not a KeyError")
    no_supplier_df = pd.DataFrame([{'ITEM_SERIAL': 'CHEM-101', 'DATE': '2026-05-01'}])
    assert derive_item_supplier_links_from_check_in(no_supplier_df).empty

    print("\nTest 4: #N/A junk rows dropped, grouping uses the code not the description")
    messy_df = pd.DataFrame([
        {'ITEM_SERIAL': 'CHEM-001', 'ITEM_DESCRIPTION': 'Potassium Permanganate (500g)', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-01'},
        {'ITEM_SERIAL': 'CHEM-001', 'ITEM_DESCRIPTION': 'Potassium Permanganate 500g', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-06-01'},  # slightly different text, same code
        {'ITEM_SERIAL': '#N/A', 'ITEM_DESCRIPTION': '#N/A', 'SUPPLIER': 'Farm to Feed', 'DATE': '2024-06-12'},
    ])
    messy_links = derive_item_supplier_links_from_check_in(messy_df)
    print(messy_links)
    assert len(messy_links) == 1, "the #N/A row must be dropped, not treated as a real item"
    assert messy_links.iloc[0]['Item Code'] == 'CHEM-001', "must group by ITEM_SERIAL, not the description text"
    assert messy_links.iloc[0]['Delivery Count'] == 2, "both description variants must be counted under one code"

    print("\nTest 5: no description column at all -- Item Description comes back None, not an error")
    code_only_df = pd.DataFrame([
        {'ITEM_SERIAL': 'CHEM-001', 'SUPPLIER': 'DairyChem Ltd', 'DATE': '2026-05-01'},
    ])
    code_only_links = derive_item_supplier_links_from_check_in(code_only_df)
    assert len(code_only_links) == 1
    assert code_only_links.iloc[0]['Item Description'] is None

    print("\nAll supplier_utils checks passed.")