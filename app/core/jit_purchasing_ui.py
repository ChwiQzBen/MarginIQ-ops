"""
app/core/jit_purchasing_ui.py
================================
🔄 JIT Purchasing tab -- reorder point / order quantity dashboard. All the
actual decision logic (reorder point, status badge, suggested order
quantity) lives in jit_purchasing.compute_jit_status(); this file is just
glue: load stock/demand from Google Sheets, load suppliers from BOTH a
local CSV and (if present) a Google Sheets SUPPLIERS tab, detect columns,
build Supplier objects per item, and render results.

Suppliers come from TWO sources that are merged together:
  1. app/data/suppliers.csv -- always editable locally, no sharing needed.
  2. The Google Sheets SUPPLIERS tab, via GoogleSheetReader.get_suppliers()
     -- for teammates who DO have edit rights on the shared sheet and would
     rather maintain supplier data there. Returns empty if that tab
     doesn't exist; nothing breaks either way.
Rows from both are normalized to the same column names before merging
(see _normalize_suppliers_df) so they combine cleanly even if the two
sources don't use identical headers. _pick_supplier() already handles
multiple candidate rows per item (PREFERRED flag, then shortest lead
time), so no separate conflict-resolution logic is needed here -- having
a row in two places just gives it two candidates to choose from.

Designed to work with EMPTY or PARTIAL data in either source -- items
without a supplier row anywhere show "No supplier data yet" rather than
being hidden, so the dashboard is useful before every item has been
filled in. Same "degrade gracefully with sparse data" principle used in
Customer Analytics' RFM/CLV/churn.

Does NOT yet include Purchase Order generation or Supplier Performance
tracking (Phase 3+ in the original JIT plan) -- those need PO state
persistence, which hasn't been designed yet. This is the Dashboard piece
only.

Expected schema, in either source (one row per item/supplier pair):
    ITEM_SERIAL,SUPPLIER_NAME,LEAD_TIME_DAYS,MIN_ORDER_QTY,UNIT_COST,RELIABILITY_SCORE,PREFERRED
ITEM_SERIAL must match the same codes used in STOCK_LISTING/CHECK_IN (e.g.
CHEM-001) -- use GoogleSheetReader.get_item_supplier_links_from_check_in()
(surfaced in the expander below) to see which item/supplier pairs to add.
"""
from typing import Optional
from datetime import datetime
import streamlit as st
import pandas as pd

from app.core.google_sheet_reader import GoogleSheetReader
from app.core.suppliers_data_access import load_suppliers_csv, SUPPLIERS_COLUMNS
from app.core.demand_utils import compute_daily_demand_for_item, detect_column, ITEM_NAME_KEYWORDS, is_junk_value
from app.core.jit_purchasing import Supplier, compute_jit_status, STATUS_SORT_ORDER

# Keyword detectors for each canonical suppliers column, reused to
# normalize whichever headers each source actually has (see
# _normalize_suppliers_df).
_SUPPLIER_COLUMN_DETECTORS = {
    'ITEM_SERIAL': ITEM_NAME_KEYWORDS,
    'SUPPLIER_NAME': ['supplier_name', 'supplier', 'vendor'],
    'LEAD_TIME_DAYS': ['lead_time', 'lead time'],
    'MIN_ORDER_QTY': ['min_order_qty', 'min order qty', 'moq'],
    'UNIT_COST': ['unit_cost', 'unit cost'],
    'RELIABILITY_SCORE': ['reliability'],
    'PREFERRED': ['preferred'],
}


def _normalize_suppliers_df(df: pd.DataFrame) -> pd.DataFrame:
    """Rename a suppliers DataFrame's columns to the canonical schema
    (SUPPLIERS_COLUMNS) via the same keyword detection used everywhere
    else in this file, so the CSV and a Google Sheets SUPPLIERS tab merge
    cleanly even if whoever maintains the sheet uses slightly different
    headers (e.g. 'Item Serial' vs 'ITEM_SERIAL'). Columns that don't
    match any detector are dropped; already-canonical input passes
    through unchanged.
    """
    if df.empty:
        return df

    rename_map = {}
    for canonical, keywords in _SUPPLIER_COLUMN_DETECTORS.items():
        found = detect_column(df, keywords)
        if found:
            rename_map[found] = canonical

    renamed = df.rename(columns=rename_map)
    keep_cols = [c for c in SUPPLIERS_COLUMNS if c in renamed.columns]
    return renamed[keep_cols]


@st.cache_data(ttl=300, show_spinner=False)
def _load_jit_data():
    gsheet = GoogleSheetReader()
    if not gsheet.authenticate():
        return pd.DataFrame(), pd.DataFrame()
    stock_df = gsheet.get_stock_with_pricing()
    check_out_df = gsheet.get_check_out()
    return stock_df, check_out_df


@st.cache_data(ttl=300, show_spinner=False)
def _load_suppliers():
    """Suppliers from both sources, merged. Neither source being present
    is a normal state (empty DataFrame, not an error) -- see module
    docstring."""
    csv_df = _normalize_suppliers_df(load_suppliers_csv())

    gsheet = GoogleSheetReader()
    sheet_df = pd.DataFrame()
    if gsheet.authenticate():
        sheet_df = _normalize_suppliers_df(gsheet.get_suppliers())

    if csv_df.empty and sheet_df.empty:
        return pd.DataFrame(columns=SUPPLIERS_COLUMNS)
    if csv_df.empty:
        return sheet_df
    if sheet_df.empty:
        return csv_df
    return pd.concat([csv_df, sheet_df], ignore_index=True)


@st.cache_data(ttl=300, show_spinner=False)
def _load_supplier_links():
    gsheet = GoogleSheetReader()
    if not gsheet.authenticate():
        return pd.DataFrame()
    return gsheet.get_item_supplier_links_from_check_in()


def _pick_supplier(item_suppliers: pd.DataFrame, name_col: str, lead_time_col: Optional[str],
                    moq_col: Optional[str], cost_col: Optional[str],
                    reliability_col: Optional[str], preferred_col: Optional[str]) -> Optional[Supplier]:
    """One Supplier per item. Prefers a row flagged PREFERRED; among the
    rest, prefers the shortest lead time (less safety stock needed) if
    lead time is available. Returns None if there's no usable lead time at
    all -- a reorder point can't be computed without one, so a supplier
    row with every other field filled in but no lead time is still
    treated as "not enough to act on" rather than silently defaulting.
    """
    if item_suppliers.empty or not lead_time_col:
        return None

    rows = item_suppliers
    if preferred_col and preferred_col in rows.columns:
        flagged = rows[rows[preferred_col].astype(str).str.strip().str.lower().isin(['true', 'yes', '1', 'y'])]
        if not flagged.empty:
            rows = flagged

    rows = rows.sort_values(lead_time_col, na_position='last')
    row = rows.iloc[0]

    try:
        lead_time = int(float(row[lead_time_col])) if pd.notna(row[lead_time_col]) else None
    except (ValueError, TypeError):
        lead_time = None
    if not lead_time or lead_time <= 0:
        return None

    def _num(col: Optional[str], default: float = 0.0) -> float:
        if col and col in row.index and pd.notna(row[col]):
            try:
                return float(row[col])
            except (ValueError, TypeError):
                return default
        return default

    return Supplier(
        name=str(row[name_col]),
        lead_time_days=lead_time,
        min_order_qty=_num(moq_col),
        unit_cost=_num(cost_col),
        reliability_score=_num(reliability_col, default=1.0),
        preferred=True,
    )


def render_jit_purchasing_tab(constants=None) -> None:
    st.markdown("## 🔄 JIT Purchasing")
    st.caption(
        "Reorder points and suggested order quantities from real demand history and "
        "lead times. Items without a supplier row yet show up as 'No supplier data' "
        "instead of being hidden -- add a row to app/data/suppliers.csv, or to the "
        "Google Sheets SUPPLIERS tab if you have edit rights there, and they join "
        "the rest automatically on next refresh."
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Refresh", use_container_width=True, key="jit_refresh"):
            st.cache_data.clear()
    with col2:
        st.caption(f"Stock/demand: Google Sheets | Suppliers: suppliers.csv + Sheets SUPPLIERS tab | "
                   f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    with st.spinner("Loading stock and demand data..."):
        stock_df, check_out_df = _load_jit_data()
    suppliers_df = _load_suppliers()

    if stock_df.empty:
        st.info("No stock data available. Check your Google Sheets connection.")
        return

    with st.expander("⚙️ JIT Settings", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            service_level = st.select_slider(
                "Target Service Level", options=[0.80, 0.85, 0.90, 0.95, 0.975, 0.98, 0.99],
                value=0.95, format_func=lambda x: f"{x * 100:.1f}%", key="jit_service_level",
            )
        with c2:
            order_cost = st.number_input(
                "Ordering Cost (KSh)", min_value=0.0,
                value=float(constants.ALL_ITEMS_ORDER_COST) if constants else 500.0,
                step=100.0, key="jit_order_cost",
            )
        holding_rate = st.number_input(
            "Holding Rate (%)", min_value=0.1,
            value=float(constants.ALL_ITEMS_HOLDING_RATE * 100) if constants else 20.0,
            step=0.5, key="jit_holding_rate",
        ) / 100
        st.caption(
            "Applied the same way to every item for now -- ABC-class-specific "
            "service levels (95%+ for A items, lower for C) are a planned "
            "refinement once ABC classification is pulled out of the Inventory "
            "tab into a reusable function (it's currently inline there)."
        )

    if suppliers_df.empty:
        st.warning(
            "⚠️ No supplier data found yet, in either app/data/suppliers.csv or the "
            "Google Sheets SUPPLIERS tab. Every item below will show demand stats, "
            "but none can be flagged 'Order Now' vs 'OK' without a lead time. See "
            "the 'Item/Supplier Links' expander below for a starting list of which "
            "item/supplier rows to add."
        )

    item_code_col = detect_column(stock_df, ITEM_NAME_KEYWORDS)
    item_label_col = 'ITEM_NAME' if 'ITEM_NAME' in stock_df.columns else None
    qty_col = 'QUANTITY' if 'QUANTITY' in stock_df.columns else detect_column(stock_df, ['quantity'])
    unit_price_col = 'UNIT PRICE' if 'UNIT PRICE' in stock_df.columns else None

    if not item_code_col or not qty_col:
        st.error("Could not find item/quantity columns in stock data.")
        return

    supplier_item_col = detect_column(suppliers_df, ITEM_NAME_KEYWORDS) if not suppliers_df.empty else None
    supplier_name_col = detect_column(suppliers_df, ['supplier_name', 'supplier', 'vendor']) if not suppliers_df.empty else None
    lead_time_col = detect_column(suppliers_df, ['lead_time', 'lead time']) if not suppliers_df.empty else None
    moq_col = detect_column(suppliers_df, ['min_order_qty', 'min order qty', 'moq']) if not suppliers_df.empty else None
    cost_col = detect_column(suppliers_df, ['unit_cost', 'unit cost']) if not suppliers_df.empty else None
    reliability_col = detect_column(suppliers_df, ['reliability']) if not suppliers_df.empty else None
    preferred_col = detect_column(suppliers_df, ['preferred']) if not suppliers_df.empty else None

    check_out_item_col = detect_column(check_out_df, ITEM_NAME_KEYWORDS) if not check_out_df.empty else None
    check_out_qty_col = detect_column(check_out_df, ['quantity', 'qty']) if not check_out_df.empty else None
    check_out_date_col = detect_column(check_out_df, ['date']) if not check_out_df.empty else None

    statuses = []
    for _, item_row in stock_df.iterrows():
        item_code = item_row[item_code_col]
        if is_junk_value(item_code):
            continue

        item_label = item_row[item_label_col] if item_label_col and pd.notna(item_row.get(item_label_col)) else str(item_code)
        try:
            current_stock = float(item_row[qty_col])
        except (ValueError, TypeError):
            continue

        daily_demand_df = pd.DataFrame()
        if check_out_item_col and check_out_qty_col and check_out_date_col:
            daily_demand_df = compute_daily_demand_for_item(
                check_out_df, item_code,
                item_col=check_out_item_col, qty_col=check_out_qty_col, date_col=check_out_date_col,
            )

        supplier = None
        if supplier_item_col and supplier_name_col and lead_time_col:
            item_suppliers = suppliers_df[suppliers_df[supplier_item_col] == item_code]
            supplier = _pick_supplier(item_suppliers, supplier_name_col, lead_time_col,
                                       moq_col, cost_col, reliability_col, preferred_col)

        unit_price_fallback = 0.0
        if unit_price_col and pd.notna(item_row.get(unit_price_col)):
            try:
                unit_price_fallback = float(item_row[unit_price_col])
            except (ValueError, TypeError):
                pass

        status = compute_jit_status(
            item_code=str(item_code), item_label=str(item_label), current_stock=current_stock,
            daily_demand_kg=daily_demand_df['Order_Quantity_kg'].tolist() if not daily_demand_df.empty else [],
            supplier=supplier, order_cost=order_cost, holding_rate=holding_rate,
            unit_price_fallback=unit_price_fallback, service_level=service_level,
        )
        statuses.append(status)

    if not statuses:
        st.info("No items to display.")
        return

    result_df = pd.DataFrame([{
        'Item Code': s.item_code,
        'Item': s.item_label,
        'Current Stock': round(s.current_stock, 1),
        'Avg Daily Demand': round(s.avg_daily_demand, 2) if s.avg_daily_demand is not None else None,
        'Reorder Point': round(s.reorder_point, 1) if s.reorder_point is not None else None,
        'Suggested Order Qty': round(s.suggested_order_qty, 1) if s.suggested_order_qty is not None else None,
        'Supplier': s.supplier_name,
        'Lead Time (days)': s.lead_time_days,
        'Status': s.status,
    } for s in statuses])

    result_df['_sort'] = result_df['Status'].map(STATUS_SORT_ORDER).fillna(len(STATUS_SORT_ORDER))
    result_df = result_df.sort_values('_sort').drop(columns='_sort').reset_index(drop=True)

    c1, c2, c3, c4 = st.columns(4)
    counts = result_df['Status'].value_counts()
    with c1:
        st.metric("🔴 Order Now", int(counts.get("🔴 Order Now", 0)))
    with c2:
        st.metric("🟡 Order Soon", int(counts.get("🟡 Order Soon", 0)))
    with c3:
        st.metric("⚫ No Supplier Data", int(counts.get("⚫ No supplier data yet", 0)))
    with c4:
        st.metric("⚪ Not Enough History", int(counts.get("⚪ Not enough demand history", 0)))

    st.divider()

    status_filter = st.multiselect(
        "Filter by status", options=list(STATUS_SORT_ORDER.keys()),
        default=["🔴 Order Now", "🟡 Order Soon"], key="jit_status_filter",
    )
    display_df = result_df[result_df['Status'].isin(status_filter)] if status_filter else result_df
    st.dataframe(display_df, use_container_width=True, hide_index=True, height=450)

    if not display_df.empty:
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Download JIT Status", csv,
            file_name=f"jit_status_{datetime.now().strftime('%Y%m%d')}.csv", mime='text/csv',
            key="jit_status_download",
        )

    with st.expander("🔗 Item/Supplier Links from Check-In History (seed data for suppliers.csv)", expanded=False):
        st.caption(
            "Who has actually delivered what, per CHECK_IN records -- not lead time "
            "(CHECK_IN has no order date). Use this to see which item/supplier rows "
            "still need lead time, MOQ, cost, and reliability filled in."
        )
        links = _load_supplier_links()
        if not links.empty:
            st.dataframe(links, use_container_width=True, hide_index=True, height=300)
            csv_links = links.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Download Item/Supplier Links", csv_links,
                file_name=f"item_supplier_links_{datetime.now().strftime('%Y%m%d')}.csv", mime='text/csv',
                key="jit_links_download",
            )
        else:
            st.caption("No item/supplier links found in CHECK_IN history.")