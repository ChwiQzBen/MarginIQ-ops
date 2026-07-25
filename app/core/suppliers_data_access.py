"""
app/core/suppliers_data_access.py
====================================
Reads SUPPLIERS reference data (lead time, MOQ, unit cost, reliability,
preferred flag) from a repo-committed CSV instead of a Google Sheets tab --
view-only access to the shared operational sheet means this data lives in
the codebase instead, where there's already full read/write.

No Streamlit, no network calls. Pure file I/O + pandas.
"""
from pathlib import Path
from typing import Optional
import pandas as pd

# Default location: app/data/suppliers.csv, relative to this file (app/core/),
# not the current working directory -- so this resolves the same whether
# Streamlit is launched from the repo root or anywhere else.
DEFAULT_SUPPLIERS_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "suppliers.csv"

SUPPLIERS_COLUMNS = [
    'ITEM_SERIAL', 'SUPPLIER_NAME', 'LEAD_TIME_DAYS', 'MIN_ORDER_QTY',
    'UNIT_COST', 'RELIABILITY_SCORE', 'PREFERRED',
]


def load_suppliers_csv(path: Optional[Path] = None) -> pd.DataFrame:
    """SUPPLIERS reference data from a local CSV. Returns an empty
    DataFrame with the expected columns (never raises) if the file doesn't
    exist yet -- same "not there yet is a normal state, not an error"
    contract used throughout this codebase (GoogleSheetReader.get_suppliers()
    had the same behavior for a missing tab, before this replaced it).
    """
    csv_path = Path(path) if path else DEFAULT_SUPPLIERS_CSV_PATH
    if not csv_path.exists():
        return pd.DataFrame(columns=SUPPLIERS_COLUMNS)

    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return pd.DataFrame(columns=SUPPLIERS_COLUMNS)

    return df.dropna(how='all')


if __name__ == "__main__":
    import tempfile
    import os

    print("Test 1: missing file returns empty with the right columns, no error")
    missing = load_suppliers_csv(Path("/tmp/does-not-exist-suppliers.csv"))
    assert missing.empty
    assert list(missing.columns) == SUPPLIERS_COLUMNS

    print("\nTest 2: real file loads correctly")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("ITEM_SERIAL,SUPPLIER_NAME,LEAD_TIME_DAYS,MIN_ORDER_QTY,UNIT_COST,RELIABILITY_SCORE,PREFERRED\n")
        f.write("CHEM-001,Abadare Chemical Ltd,7,5,430,0.95,TRUE\n")
        temp_path = f.name
    try:
        df = load_suppliers_csv(Path(temp_path))
        print(df)
        assert len(df) == 1
        assert df.iloc[0]['ITEM_SERIAL'] == 'CHEM-001'
        assert df.iloc[0]['LEAD_TIME_DAYS'] == 7
    finally:
        os.unlink(temp_path)

    print("\nTest 3: default path resolves under app/data/, not cwd-relative")
    print(f"  DEFAULT_SUPPLIERS_CSV_PATH = {DEFAULT_SUPPLIERS_CSV_PATH}")
    assert DEFAULT_SUPPLIERS_CSV_PATH.name == "suppliers.csv"
    assert DEFAULT_SUPPLIERS_CSV_PATH.parent.name == "data"

    print("\nAll suppliers_data_access checks passed.")