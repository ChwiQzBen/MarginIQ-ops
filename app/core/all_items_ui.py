"""
app/core/all_items_ui.py
========================
All Items Mode's tabs, extracted from main.py following the same shape as
dry_ice_ui.py and cheese_production_ui.py: a single render_all_items_mode()
entry point, permission-filtered tabs, small _render_x_tab() functions per tab.

Takes has_permission as a callable (not the Permission enum) for the same
reason the other two modules do — avoids a circular import, since main.py
is what imports THIS module.

STATUS: All five tabs are fully ported. 📦 Inventory, 📊 Stock Movements
(including its nested "📋 Stock Take" sub-tab), 📈 All Items Analytics,
🖼️ Visual Inventory, and 🤖 Advanced Analytics.

Stock Take's ~19 supporting functions (create_stock_count, enter_count,
complete_and_adjust, new_count_form, active_counts_interface, etc.) now
live in app/core/stock_take.py. Visual Inventory's 14 display/calc helpers
(ai_powered_recommendations, inventory_status_dashboard,
visual_inventory_grid, inventory_heatmap, get_replenishment_recommendations,
etc.) plus the shared get_sample_inventory_data() fallback now live in
app/core/visual_inventory.py. Both were pure functions operating only on
their own args and st.session_state, so they lifted out cleanly with no
circular-import issues — same story as the forecasting extraction
(app/core/forecasting.py) that unblocked 📈 All Items Analytics.
"""
import sys
import os


def _bootstrap_repo_root_on_syspath() -> None:
    """all_items_ui.py previously had no path bootstrap at all, unlike
    commercial_ui.py and cheese_production_ui.py which both insert the
    repo root here -- it silently relied on main.py having already done
    this before importing this module. Fine in production (main.py
    always runs first), but it meant this module couldn't be imported
    standalone -- confirmed by AppTest failing on "No module named
    'core'" when testing this file directly.

    Walks upward from this file's location looking for the directory
    that contains core/error_handling.py -- NOT just any folder named
    "core", since this file's own folder (app/core/) unfortunately
    shares that exact name with the different, top-level core/ package
    it needs to find. An earlier version of this check matched the
    file's own containing folder by mistake for exactly that reason;
    checking for a specific known file inside the real target avoids
    the collision."""
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):  # generous bound, just avoids an infinite loop on a bad layout
        if os.path.isfile(os.path.join(current, "core", "error_handling.py")):
            if current not in sys.path:
                sys.path.insert(0, current)
            return
        parent = os.path.dirname(current)
        if parent == current:  # reached filesystem root without finding it
            break
        current = parent
    raise RuntimeError(
        "Could not locate a 'core/error_handling.py' above app/core/all_items_ui.py -- "
        "check that the repo layout matches what this bootstrap expects."
    )


_bootstrap_repo_root_on_syspath()

from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from datetime import datetime
import re
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from app.core.price_tracking import record_price_changes
from app.core.data_integrity import detect_and_log_disappearances, get_open_alerts, acknowledge_alert
from app.core.performance import LazyLoader, compress_dataframe
from core.error_handling import logger
from app.core.google_sheet_reader import GoogleSheetReader
from app.core.advanced_analytics import create_advanced_analytics_tab
from app.core.forecasting import create_ensemble_forecast
from app.core.eoq import calculate_eoq_costs
from app.core.demand_utils import (
    compute_daily_demand_for_item, detect_column, is_junk_value,
    build_code_to_label_map, ITEM_NAME_KEYWORDS, ITEM_LABEL_KEYWORDS, LOCATION_KEYWORDS,
)
from app.core.item_master import get_all_items, create_item, update_item, deactivate_item
from app.core.checkin_records import record_checkin, get_checkins
from app.core.stock_ledger import sum_check_in_window, sum_check_out_window, transfer_total
from app.core.visual_inventory import (
    ai_powered_recommendations,
    inventory_status_dashboard,
    inventory_stats_summary,
    inventory_filters,
    visual_inventory_grid,
    inventory_heatmap,
    get_replenishment_recommendations,
    get_replenishment_summary,
    show_replenishment_summary_cards,
    show_replenishment_suggestions,
)
from app.core.stock_take import stock_take_interface
from app.core.jit_purchasing_ui import render_jit_purchasing_tab
from app.core.rbac import ALL_ITEMS_TAB_REQUIREMENTS, Permission
from app.core.checkout_reconciliation import (
    init_checkout_reconciliation_storage, record_checkout, get_checkouts, reconcile_checkout,
)
from app.core.transfer_reconciliation import (
    init_transfer_storage, record_transfer_batch, get_transfers, get_goods_received_notes,
    get_pending_transfer_lines, create_grn, resolve_grn_mismatch,
)
from app.core.inventory_cache import save_snapshot, load_snapshot

def _style_cells(df: pd.DataFrame, style_fn, subset):
    """Pandas 3.0 compatibility shim: Styler.applymap was removed in
    pandas 3.0 in favor of Styler.map. Tries .map first, falls back to
    .applymap for older pinned environments.

    NOTE: per project memory, theme.py already has an equivalent
    _style_map() shim used elsewhere for this same migration — if
    that's exported, point this at it instead to avoid a second copy.
    Added locally here since this file doesn't currently import
    theme.py's styling helpers."""
    styler = df.style
    if hasattr(styler, "map"):
        return styler.map(style_fn, subset=subset)
    return styler.applymap(style_fn, subset=subset)


@dataclass
class AllItemsContext:
    """📦 Inventory and 📊 Stock Movements are both self-contained today —
    Stock Movements' inventory_items field is only consumed once the nested
    Stock Take sub-tab is ported. df/stock_df/analytics power
    🤖 Advanced Analytics. kpis powers 🖼️ Visual Inventory's AI
    recommendations. inventory_tracker is accepted for forward-compat with
    inventory_status_dashboard's signature but is currently unused inside
    that function."""
    inventory_items: dict = None
    df: pd.DataFrame = None
    stock_df: pd.DataFrame = None
    analytics: Any = None
    constants: Any = None
    kpis: Any = None
    inventory_tracker: Any = None
    supabase_client: Any = None


def render_all_items_mode(ctx: AllItemsContext,
                           has_permission: Optional[Callable[[str], bool]] = None) -> None:
    def _allowed(permission: str) -> bool:
        return has_permission(permission) if has_permission else True

    visible = [name for name, perm in ALL_ITEMS_TAB_REQUIREMENTS.items() if _allowed(perm)]
    if not visible:
        st.warning("This section isn't available for your current role. If you feel this is a mistake, please contact your administrator.")
        return

    
    if "all_items_active_tab" not in st.session_state or st.session_state.all_items_active_tab not in visible:
        st.session_state.all_items_active_tab = visible[0]

    active_tab = st.radio(
        "Section", visible, horizontal=True,
        key="all_items_active_tab", label_visibility="collapsed",
    )
    st.markdown("---")

    if active_tab == "📦 Inventory":
        _render_inventory_tab(ctx)
    elif active_tab == "📊 Stock Movements":
        _render_stock_movements_tab(ctx)
    elif active_tab == "📈 All Items Analytics":
        _render_analytics_tab(ctx)
    elif active_tab == "🖼️ Visual Inventory":
        _render_visual_inventory_tab(ctx)
    elif active_tab == "🤖 Advanced Analytics":
        _render_advanced_analytics_tab(ctx)
    elif active_tab == "🔄 JIT Purchasing":
        render_jit_purchasing_tab(ctx.constants, has_permission=has_permission)
    elif active_tab == "⚖️ Reconciliation & Oversight":
        _render_checkout_reconciliation_tab(ctx, has_permission=has_permission)
    elif active_tab == "🔁 Transfers":
        _render_transfer_reconciliation_tab(ctx)
    elif active_tab == "⤴️⤵️ Check In / Check Out":
        _render_checkin_checkout_tab(ctx)
    elif active_tab == "📊 Stock Variance":
        _render_variance_tab(ctx)


# ============================================================
# FULLY PORTED — 📦 Inventory
# ============================================================
def _render_inventory_tab(ctx: AllItemsContext) -> None:
    st.markdown("## 📦 Company Inventory")

    # --- HIDE SEARCH ONLY IN THIS TAB ---
    st.markdown("""
    <style>
    .stDataFrame [data-testid="stDataFrameSearch"] {
        display: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # Refresh button
    col1, col2 = st.columns([1, 4])

    with col1:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()

    with col2:
        st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    st.divider()

    with st.expander("➕ Add / Edit Item (new item master — not yet live elsewhere)", expanded=False):
        st.caption(
            "Adds to the new in-app item list. Until Check-In/Check-Out and this "
            "tab's display are switched over, items added here won't show up "
            "anywhere else yet -- safe to test, not yet the production way to add items."
        )
        existing_items = get_all_items(active_only=True, supabase_client=ctx.supabase_client)
        existing_names = [i['item_name'] for i in existing_items]

        edit_mode = st.checkbox("Editing an existing item", key="item_master_edit_mode")
        if edit_mode and existing_names:
            selected_name = st.selectbox("Item to edit", existing_names, key="item_master_edit_select")
            current = next((i for i in existing_items if i['item_name'] == selected_name), {})
        else:
            selected_name = None
            current = {}

        with st.form("item_master_form", clear_on_submit=not edit_mode):
            im_name = st.text_input("Item Name", value=current.get('item_name', ''), disabled=edit_mode)
            im_serial = st.text_input("Item Serial (optional)", value=current.get('item_serial', ''))
            im_category = st.text_input("Category", value=current.get('item_category', ''))
            im_unit = st.text_input("Unit of Measure", value=current.get('unit_of_measure', 'kg'))
            im_price = st.number_input("Unit Price (KSh)", min_value=0.0, value=float(current.get('unit_price', 0.0)), step=1.0)
            im_reorder = st.number_input("Reorder Level", min_value=0.0, value=float(current.get('reorder_level', 0.0)), step=1.0)
            st.caption(
                "Starting stock isn't set here — since stock is tracked per location, "
                "run an initial Stock Take at each location to establish its baseline."
            )
            im_created_by = st.text_input("Your Name")

            submit_label = "💾 Update Item" if edit_mode else "➕ Add Item"
            if st.form_submit_button(submit_label, type="primary"):
                if not im_name.strip():
                    st.error("Item name is required.")
                elif not im_created_by.strip():
                    st.error("Enter your name.")
                elif edit_mode:
                    ok = update_item(
                        selected_name,
                        {
                            "item_serial": im_serial.strip(), "item_category": im_category.strip(),
                            "unit_of_measure": im_unit.strip(), "unit_price": im_price,
                            "reorder_level": im_reorder,
                        },
                        supabase_client=ctx.supabase_client,
                    )
                    if ok:
                        st.success(f"✅ {selected_name} updated.")
                        st.rerun()
                    else:
                        st.error("Could not update -- check the logs.")
                else:
                    try:
                        create_item(
                            item_name=im_name, item_serial=im_serial, item_category=im_category,
                            unit_of_measure=im_unit, unit_price=im_price, reorder_level=im_reorder,
                            created_by=im_created_by.strip(),
                            supabase_client=ctx.supabase_client,
                        )
                        st.success(f"✅ {im_name} added.")
                        st.rerun()
                    except Exception:
                        st.error(f"Could not add '{im_name}' -- it may already exist.")

        if edit_mode and selected_name:
            if st.button(f"🚫 Deactivate {selected_name}", key="item_master_deactivate_btn"):
                if deactivate_item(selected_name, supabase_client=ctx.supabase_client):
                    st.success(f"✅ {selected_name} deactivated.")
                    st.rerun()
                else:
                    st.error("Could not deactivate -- check the logs.")

    @st.cache_data(ttl=300)
    def load_full_inventory_details():
        """Renamed from load_inventory_data() — that name collided with the
        module-level load_inventory_data() used for the KPI dashboard in
        main(). Same name, different return shape; this scoping only
        mattered when this tab lived inline in main.py — kept the rename
        here anyway for continuity with the original diff history.

        Three-layer fallback if the live data source can't be reached:
        1. st.session_state — covers a hiccup within this running session.
        2. inventory_cache (Supabase) — covers a cold start after a
           container restart/redeploy, when session_state is empty too.
        Only if BOTH are empty (truly first-ever run with no source
        available) does this show the hard error below."""
        try:
            gsheet = GoogleSheetReader()

            if gsheet.authenticate():
                stock = gsheet.get_stock_with_pricing()
                current = gsheet.get_current_stock()
                low = gsheet.get_low_stock_items()

                category_count = (
                    stock['ITEM_CATEGORY'].nunique()
                    if 'ITEM_CATEGORY' in stock.columns
                    else 0
                )

                if not stock.empty:
                    disappeared = detect_and_log_disappearances(
                        stock, supabase_client=ctx.supabase_client,
                    )
                    if disappeared:
                        st.session_state['_recent_disappearances'] = disappeared

                    st.session_state['_ai_inventory_cache'] = (
                        stock, current, low, category_count, datetime.now()
                    )
                    save_snapshot(
                        "full_inventory",
                        {"stock": stock, "current": current, "low": low},
                        scalars={"category_count": category_count},
                        supabase_client=ctx.supabase_client,
                    )
                    price_changes = record_price_changes(stock, ctx.supabase_client)
                    if price_changes:
                        st.session_state['_recent_price_changes'] = price_changes

                return stock, current, low, category_count

        except Exception as e:
            logger.error(f"Inventory loading error: {e}")

        cached = st.session_state.get('_ai_inventory_cache')
        if cached:
            stock, current, low, category_count, cached_at = cached
            st.warning(f"⚠️ Could not refresh inventory data just now — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')}.")
            return stock, current, low, category_count

        snapshot = load_snapshot("full_inventory", supabase_client=ctx.supabase_client)
        if snapshot:
            frames, scalars, cached_at = snapshot
            st.warning(f"⚠️ Could not reach the inventory data source — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')} "
                       f"(from before the app last restarted).")
            return frames["stock"], frames["current"], frames["low"], scalars.get("category_count", 0)

        st.error("Could not load inventory data, and no previous data is available to fall back on.")
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            0
        )

    with st.spinner("📊Loading inventory data..."):
        tab_stock_df, tab_current_df, tab_low_df, tab_category_count = load_full_inventory_details()

    if st.session_state.get('_recent_price_changes'):
        st.info(f"💰 {st.session_state['_recent_price_changes']} price change(s) detected on this load.")
        del st.session_state['_recent_price_changes']

    # Everything below stays INSIDE this tab
    if not tab_stock_df.empty:

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("📦 Total Items", len(tab_stock_df))

        with col2:
            st.metric("📂 Categories", tab_category_count)

        with col3:
            st.metric(
                "📊 Current Stock",
                len(tab_current_df) if not tab_current_df.empty else 0
            )

        with col4:
            low_count = len(tab_low_df) if not tab_low_df.empty else 0
            st.metric(
                "⚠️ Low Stock",
                low_count,
                delta=f"-{low_count}" if low_count > 0 else None
            )

        # Price stats
        if 'UNIT PRICE' in tab_stock_df.columns:
            price_count = tab_stock_df['UNIT PRICE'].notna().sum()

            if price_count > 0:
                st.caption(
                    f"💰 Prices available for "
                    f"{price_count} out of {len(tab_stock_df)} items"
                )

        # --- ABC ANALYSIS (Operationalized) ---
        if 'UNIT PRICE' in tab_stock_df.columns and 'QUANTITY' in tab_stock_df.columns:
            st.divider()
            st.markdown("### 📊 ABC Analysis (Pareto Analysis)")

            # Create a copy for ABC analysis
            abc_df = tab_stock_df.copy()

            # Convert to numeric, coercing errors to NaN
            abc_df['QUANTITY'] = pd.to_numeric(abc_df['QUANTITY'], errors='coerce')
            abc_df['UNIT PRICE'] = pd.to_numeric(abc_df['UNIT PRICE'], errors='coerce')

            # Drop rows with missing values
            abc_df = abc_df.dropna(subset=['QUANTITY', 'UNIT PRICE'])

            if not abc_df.empty and len(abc_df) > 0:
                # Calculate annual value
                abc_df['ANNUAL_VALUE'] = abc_df['QUANTITY'] * abc_df['UNIT PRICE']

                # Sort by value and calculate cumulative percentage
                abc_df = abc_df.sort_values('ANNUAL_VALUE', ascending=False)
                total_value = abc_df['ANNUAL_VALUE'].sum()

                if total_value > 0:
                    abc_df['CUM_PERCENT'] = abc_df['ANNUAL_VALUE'].cumsum() / total_value

                    # Classify items
                    def get_abc_class(row):
                        if row['CUM_PERCENT'] <= 0.70:
                            return '🔴 A (70% value)'
                        elif row['CUM_PERCENT'] <= 0.90:
                            return '🟡 B (20% value)'
                        else:
                            return '🟢 C (10% value)'

                    abc_df['ABC_CLASS'] = abc_df.apply(get_abc_class, axis=1)

                    # ============================================================
                    # 🎯 OPERATIONALIZE ABC ANALYSIS - inFlow Style
                    # ============================================================

                    # Define cycle counting frequencies based on ABC class
                    cycle_frequencies = {
                        '🔴 A (70% value)': {
                            'frequency': 'Monthly',
                            'days': 30,
                            'priority': 'High',
                            'color': '#dc3545',
                            'count_per_year': 12
                        },
                        '🟡 B (20% value)': {
                            'frequency': 'Quarterly',
                            'days': 90,
                            'priority': 'Medium',
                            'color': '#ffc107',
                            'count_per_year': 4
                        },
                        '🟢 C (10% value)': {
                            'frequency': 'Annually',
                            'days': 365,
                            'priority': 'Low',
                            'color': '#28a745',
                            'count_per_year': 1
                        }
                    }

                    # Create cycle counting schedule
                    cycle_schedule = []
                    for _, row in abc_df.iterrows():
                        class_label = row['ABC_CLASS']
                        freq_info = cycle_frequencies.get(class_label, cycle_frequencies['🟢 C (10% value)'])

                        cycle_schedule.append({
                            'Item': row['ITEM_NAME'],
                            'Category': row.get('ITEM_CATEGORY', 'Uncategorized'),
                            'ABC Class': class_label,
                            'Annual Value': row['ANNUAL_VALUE'],
                            'Count Frequency': freq_info['frequency'],
                            'Priority': freq_info['priority'],
                            'Counts per Year': freq_info['count_per_year']
                        })

                    cycle_df = pd.DataFrame(cycle_schedule)

                    # Display cycle counting schedule summary
                    st.markdown("#### 🔄 Cycle Counting Recommendations")

                    col1, col2, col3, col4 = st.columns(4)

                    # Count items by class
                    a_count = len(abc_df[abc_df['ABC_CLASS'] == '🔴 A (70% value)'])
                    b_count = len(abc_df[abc_df['ABC_CLASS'] == '🟡 B (20% value)'])
                    c_count = len(abc_df[abc_df['ABC_CLASS'] == '🟢 C (10% value)'])

                    with col1:
                        st.metric(
                            "🔴 A Items (Monthly)",
                            a_count,
                            delta=f"{a_count * 12} counts/year"
                        )
                    with col2:
                        st.metric(
                            "🟡 B Items (Quarterly)",
                            b_count,
                            delta=f"{b_count * 4} counts/year"
                        )
                    with col3:
                        st.metric(
                            "🟢 C Items (Annually)",
                            c_count,
                            delta=f"{c_count} counts/year"
                        )
                    with col4:
                        total_counts = (a_count * 12) + (b_count * 4) + c_count
                        st.metric(
                            "📊 Total Counts/Year",
                            total_counts,
                            delta=f"~{total_counts // 12} counts/month"
                        )

                    # Show detailed cycle counting schedule with styling
                    with st.expander("📋 View Cycle Counting Schedule", expanded=False):
                        # Style by priority
                        def style_priority(val):
                            if 'High' in val:
                                return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
                            elif 'Medium' in val:
                                return 'background-color: #fff3cd; color: #856404;'
                            else:
                                return 'background-color: #d4edda; color: #155724;'

                        styled_cycle_df = _style_cells(cycle_df, style_priority, subset=['Priority'])

                        st.dataframe(
                            styled_cycle_df,
                            use_container_width=True,
                            height=300,
                            hide_index=True
                        )

                    # ============================================================
                    # 🎯 REPLENISHMENT STRATEGY BY ABC CLASS
                    # ============================================================
                    st.markdown("#### 📦 Replenishment Strategy by ABC Class")

                    # Create replenishment strategies
                    replenishment_data = []
                    for _, row in abc_df.iterrows():
                        class_label = row['ABC_CLASS']
                        stock = row.get('QUANTITY', 0)

                        # Get reorder level (if available, otherwise use 50% of stock)
                        if 'REORDER LEVEL' in row and pd.notna(row['REORDER LEVEL']):
                            reorder = row['REORDER LEVEL']
                        else:
                            reorder = stock * 0.5

                        # Set safety stock multiplier based on ABC class
                        if '🔴 A' in class_label:
                            safety_multiplier = 0.3
                            order_frequency = 'Monthly'
                        elif '🟡 B' in class_label:
                            safety_multiplier = 0.2
                            order_frequency = 'Quarterly'
                        else:
                            safety_multiplier = 0.1
                            order_frequency = 'As Needed'

                        safety_stock_val = reorder * (1 + safety_multiplier)

                        # Check if below safety stock
                        below_safety = stock < safety_stock_val

                        replenishment_data.append({
                            'Item': row['ITEM_NAME'],
                            'ABC Class': class_label,
                            'Current Stock': stock,
                            'Reorder Point': round(reorder, 0),
                            'Safety Stock': round(safety_stock_val, 0),
                            'Order Frequency': order_frequency,
                            'Annual Value': row['ANNUAL_VALUE'],
                            'Below Safety': '⚠️ Yes' if below_safety else '✅ No'
                        })

                    replenishment_df = pd.DataFrame(replenishment_data)

                    # Display replenishment summary
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        # Count items needing immediate attention
                        urgent_items = len(replenishment_df[replenishment_df['Below Safety'] == '⚠️ Yes'])
                        st.metric(
                            "⚠️ Below Safety Stock",
                            urgent_items,
                            delta=f"-{urgent_items}" if urgent_items > 0 else None
                        )
                    with col2:
                        # Total value of A items
                        a_value = replenishment_df[replenishment_df['ABC Class'] == '🔴 A (70% value)']['Annual Value'].sum()
                        st.metric(
                            "💰 A Items Value",
                            f"KSh {a_value:,.0f}",
                            f"{a_value/total_value*100:.0f}% of total"
                        )
                    with col3:
                        # Most critical items
                        critical = replenishment_df[
                            (replenishment_df['ABC Class'] == '🔴 A (70% value)') &
                            (replenishment_df['Below Safety'] == '⚠️ Yes')
                        ]
                        st.metric(
                            "🎯 Critical A Items",
                            len(critical),
                            delta="⚠️ Needs Reorder"
                        )

                    # Show detailed replenishment recommendations
                    with st.expander("📋 View Replenishment Recommendations by ABC Class", expanded=False):
                        # Color code by ABC class
                        def style_abc_class(val):
                            if '🔴 A' in val:
                                return 'background-color: #f8d7da; font-weight: bold;'
                            elif '🟡 B' in val:
                                return 'background-color: #fff3cd;'
                            else:
                                return 'background-color: #d4edda;'

                        styled_replenishment = _style_cells(replenishment_df, style_abc_class, subset=['ABC Class'])

                        st.dataframe(
                            styled_replenishment,
                            use_container_width=True,
                            height=300,
                            hide_index=True
                        )

                    # ============================================================
                    # 🎯 DAILY / WEEKLY ACTION PLAN
                    # ============================================================
                    st.markdown("#### 📋 Action Plan by ABC Class")

                    # Create action plan cards
                    action_plan = {
                        '🔴 A (70% value)': {
                            'icon': '🔴',
                            'actions': [
                                '📊 Count weekly (or more frequently)',
                                '📦 Maintain higher safety stock',
                                '🔄 Reorder more frequently',
                                '👀 Monitor daily',
                                '📈 Review weekly performance'
                            ]
                        },
                        '🟡 B (20% value)': {
                            'icon': '🟡',
                            'actions': [
                                '📊 Count monthly',
                                '📦 Maintain moderate safety stock',
                                '🔄 Reorder as needed',
                                '👀 Monitor weekly',
                                '📈 Review monthly'
                            ]
                        },
                        '🟢 C (10% value)': {
                            'icon': '🟢',
                            'actions': [
                                '📊 Count quarterly',
                                '📦 Maintain basic safety stock',
                                '🔄 Reorder on demand',
                                '👀 Monitor monthly',
                                '📈 Review quarterly'
                            ]
                        }
                    }

                    # Display action plan as cards
                    cols = st.columns(3)
                    for idx, (class_name, plan) in enumerate(action_plan.items()):
                        with cols[idx]:
                            count = len(abc_df[abc_df['ABC_CLASS'] == class_name])
                            st.markdown(f"""
                            <div style="
                                border: 2px solid {cycle_frequencies[class_name]['color']};
                                border-radius: 12px;
                                padding: 15px;
                                margin-bottom: 10px;
                                background: rgba(255,255,255,0.05);
                                min-height: 200px;
                            ">
                                <div style="font-size: 16px; font-weight: 700; color: {cycle_frequencies[class_name]['color']};">
                                    {plan['icon']} {class_name}
                                    <span style="font-size: 12px; color: #888;">({count} items)</span>
                                </div>
                                <div style="font-size: 13px; color: #666; margin-top: 8px;">
                                    <strong>Count:</strong> {cycle_frequencies[class_name]['frequency']}<br>
                                    <strong>Priority:</strong> {cycle_frequencies[class_name]['priority']}
                                </div>
                                <div style="margin-top: 8px;">
                                    <ul style="padding-left: 20px; margin: 0;">
                                        {''.join([f'<li style="font-size: 12px; color: #555;">{action}</li>' for action in plan['actions']])}
                                    </ul>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                    # ============================================================
                    # 🎯 ABC CLASS BREAKDOWN - Original (Keep this)
                    # ============================================================
                    st.markdown("---")
                    st.markdown("#### 📊 ABC Class Breakdown")

                    # Show ABC breakdown
                    abc_counts = abc_df['ABC_CLASS'].value_counts()

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("🔴 A Items (70% Value)", abc_counts.get('🔴 A (70% value)', 0))
                    with col2:
                        st.metric("🟡 B Items (20% Value)", abc_counts.get('🟡 B (20% value)', 0))
                    with col3:
                        st.metric("🟢 C Items (10% Value)", abc_counts.get('🟢 C (10% value)', 0))
                    with col4:
                        st.metric("💰 Total Inventory Value", f"KSh {total_value:,.2f}")

                    # Show ABC summary table
                    abc_summary = abc_df.groupby('ABC_CLASS').agg({
                        'ITEM_NAME': 'count',
                        'ANNUAL_VALUE': 'sum'
                    }).reset_index()
                    abc_summary.columns = ['Class', 'Item Count', 'Total Value']
                    st.dataframe(abc_summary, use_container_width=True, hide_index=True)

                    # Show top A items with download
                    with st.expander("View Top A Items (70% Value)", expanded=False):
                        top_a = abc_df[abc_df['ABC_CLASS'] == '🔴 A (70% value)'].head(20)
                        st.dataframe(
                            top_a[['ITEM_NAME', 'ITEM_CATEGORY', 'QUANTITY', 'UNIT PRICE', 'ANNUAL_VALUE']],
                            use_container_width=True,
                            hide_index=True
                        )

                        # Add export button for A items
                        if not top_a.empty:
                            csv_a = top_a[['ITEM_NAME', 'ITEM_CATEGORY', 'QUANTITY', 'UNIT PRICE', 'ANNUAL_VALUE']].to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="📥 Download A Items List",
                                data=csv_a,
                                file_name=f"a_items_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime='text/csv'
                            )
                else:
                    st.info("Total inventory value is zero. Cannot perform ABC analysis.")
            else:
                st.info("No valid items with both quantity and price data found for ABC analysis.")

        # --- CATEGORY-LEVEL INVENTORY SUMMARY (WRAPPED IN EXPANDER) ---
        if 'ITEM_CATEGORY' in tab_stock_df.columns and 'QUANTITY' in tab_stock_df.columns and 'UNIT PRICE' in tab_stock_df.columns:
            st.divider()

            # Wrap Category-Level Inventory Summary in expander - collapsed by default
            with st.expander("📊 Category-Level Inventory Summary", expanded=False):
                # Convert to numeric
                cat_df = tab_stock_df.copy()
                cat_df['QUANTITY'] = pd.to_numeric(cat_df['QUANTITY'], errors='coerce')
                cat_df['UNIT PRICE'] = pd.to_numeric(cat_df['UNIT PRICE'], errors='coerce')

                # Drop rows with missing values
                cat_df = cat_df.dropna(subset=['QUANTITY', 'UNIT PRICE'])

                if not cat_df.empty:
                    # Calculate annual value first
                    cat_df['ANNUAL_VALUE'] = cat_df['QUANTITY'] * cat_df['UNIT PRICE']

                    # Group by category with all aggregations at once
                    category_summary = cat_df.groupby('ITEM_CATEGORY').agg({
                        'ITEM_NAME': 'count',
                        'QUANTITY': 'sum',
                        'UNIT PRICE': 'mean',
                        'ANNUAL_VALUE': 'sum'
                    }).reset_index()

                    # Rename columns
                    category_summary.columns = ['Category', 'Items', 'Total Quantity', 'Avg Unit Price', 'Total Value']

                    # Format currency columns (keep as numbers for chart, format for display)
                    display_summary = category_summary.copy()
                    display_summary['Avg Unit Price'] = display_summary['Avg Unit Price'].apply(lambda x: f"KSh {x:,.2f}")
                    display_summary['Total Value'] = display_summary['Total Value'].apply(lambda x: f"KSh {x:,.2f}")

                    # Show summary table
                    st.dataframe(display_summary, use_container_width=True, hide_index=True)

                    # Check if category_summary exists and has data
                    if 'category_summary' in locals() and not category_summary.empty:
                        fig_category = px.bar(
                            category_summary,
                            x='Category',
                            y='Total Value',
                            title='Inventory Value by Category',
                            color='Category',
                            height=400,
                            labels={'Total Value': 'Total Value (KSh)'}
                        )
                        fig_category.update_layout(showlegend=False)
                        st.plotly_chart(fig_category, use_container_width=True)
                    else:
                        st.info("No category data available to display")

                    # Show total value
                    total_inventory_value = cat_df['ANNUAL_VALUE'].sum()
                    st.metric("💰 Total Inventory Value Across All Categories", f"KSh {total_inventory_value:,.2f}")
                else:
                    st.info("No valid data available for category summary.")

        # Search + Filter — wrapped in a form so typing/selecting no longer
        # triggers a full-app rerun per keystroke. Before this, every
        # character typed here re-ran the entire app: sidebar, auth, RBAC,
        # and — worst of all — the unguarded ABC Analysis / Replenishment
        # Strategy blocks above (row-by-row .iterrows() + Styler.applymap),
        # which is the "hangs while interacting" symptom.
        with st.form("inventory_search_form"):
            col1, col2 = st.columns(2)

            with col1:
                search = st.text_input(
                    "Search Items",
                    placeholder="Type item name..."
                )

            with col2:
                if 'ITEM_CATEGORY' in tab_stock_df.columns:
                    categories = (
                        ['All'] +
                        sorted(
                            tab_stock_df['ITEM_CATEGORY']
                            .dropna()
                            .unique()
                            .tolist()
                        )
                    )

                    category_filter = st.selectbox(
                        "📂 Category",
                        categories
                    )
                else:
                    category_filter = "All"

            st.form_submit_button("Apply Filters")

        # Apply filters
        filtered_df = tab_stock_df.copy()

        if search:
            mask = False

            for col in ['ITEM_NAME', 'ITEM_SERIAL']:
                if col in filtered_df.columns:
                    mask = (
                        mask |
                        filtered_df[col]
                        .astype(str)
                        .str.contains(
                            search,
                            case=False,
                            na=False
                        )
                    )

            filtered_df = filtered_df[mask]

        if (
            category_filter != "All"
            and 'ITEM_CATEGORY' in filtered_df.columns
        ):
            filtered_df = filtered_df[
                filtered_df['ITEM_CATEGORY']
                == category_filter
            ]

        # Wrap Stock Listing in expander - collapsed by default
        with st.expander(f"📋 Stock Listing ({len(filtered_df)} items)", expanded=False):
            display_cols = [
                'ITEM_SERIAL',
                'ITEM_CATEGORY',
                'ITEM_NAME',
                'UNIT_OF_MEASURE',
                'QUANTITY',
                'UNIT PRICE',
                'REORDER LEVEL'
            ]

            display_cols = [
                col for col in display_cols
                if col in filtered_df.columns
            ]

            st.dataframe(
                filtered_df[display_cols],
                use_container_width=True,
                height=400,
                hide_index=True
            )

            # CSV export
            if not filtered_df.empty:
                csv = (
                    filtered_df
                    .to_csv(index=False)
                    .encode('utf-8')
                )

                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"inventory_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )

        # Low stock section (already has expander)
        if not tab_low_df.empty:
            st.divider()

            st.warning(
                f"⚠️ {len(tab_low_df)} items are low in stock and need reordering!"
            )

            with st.expander("📋 View Low Stock Items", expanded=False):
                st.dataframe(
                    tab_low_df,
                    use_container_width=True,
                    height=300
                )

    else:
        st.info("📊 No inventory data found yet.")


# ============================================================
# FULLY PORTED — 📊 Stock Movements (including the nested Stock Take sub-tab)
# ============================================================
def _render_stock_movements_tab(ctx: AllItemsContext) -> None:
    st.markdown("## 📊 Stock Movements & Stock Take")

    supabase_client = ctx.supabase_client
    init_checkout_reconciliation_storage(supabase_client)

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Refresh Movements", use_container_width=True):
            st.cache_data.clear()
    with col2:
        st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    st.divider()

    @st.cache_data(ttl=300, show_spinner=False)
    def load_movement_data():
        """Same three-layer fallback (session_state, then Supabase
        snapshot via inventory_cache) as load_full_inventory_details."""
        try:
            gsheet = GoogleSheetReader()
            if gsheet.authenticate():
                check_in = gsheet.get_check_in()
                check_out = gsheet.get_check_out()
                current_stock = gsheet.get_current_stock()
                if not (check_in.empty and check_out.empty and current_stock.empty):
                    st.session_state['_ai_movement_cache'] = (
                        check_in, check_out, current_stock, datetime.now()
                    )
                    save_snapshot(
                        "movement_data",
                        {"check_in": check_in, "check_out": check_out, "current_stock": current_stock},
                        supabase_client=ctx.supabase_client,
                    )
                return check_in, check_out, current_stock
        except Exception as e:
            logger.error(f"Movement data loading error: {e}")

        cached = st.session_state.get('_ai_movement_cache')
        if cached:
            check_in, check_out, current_stock, cached_at = cached
            st.warning(f"⚠️ Could not refresh movement data just now — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')}.")
            return check_in, check_out, current_stock

        snapshot = load_snapshot("movement_data", supabase_client=ctx.supabase_client)
        if snapshot:
            frames, _, cached_at = snapshot
            st.warning(f"⚠️ Could not reach the movement data source — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')} "
                       f"(from before the app last restarted).")
            return frames["check_in"], frames["check_out"], frames["current_stock"]

        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with st.spinner("📊 Please wait..."):
        check_in_df, check_out_df, current_stock_df = load_movement_data()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📥 Check-Ins", len(check_in_df) if not check_in_df.empty else 0)
    with col2:
        st.metric("📤 Check-Outs", len(check_out_df) if not check_out_df.empty else 0)
    with col3:
        st.metric("📊 Current Stock Records", len(current_stock_df) if not current_stock_df.empty else 0)

    st.divider()

    subtab_options = ["📥 Check-Ins", "📤 Check-Outs", "📊 Current Stock", "📋 Stock Take"]
    if "stock_movements_active_subtab" not in st.session_state or \
            st.session_state.stock_movements_active_subtab not in subtab_options:
        st.session_state.stock_movements_active_subtab = subtab_options[0]

    active_subtab = st.radio(
        "Movement view", subtab_options, horizontal=True,
        key="stock_movements_active_subtab", label_visibility="collapsed",
    )
    st.markdown("---")

    if active_subtab == "📥 Check-Ins":
        st.markdown("### 📥 Check-In Records")
        st.caption("Record new check-ins from the 🔄 Check In / Check Out tab.")

        with st.expander("📜 Historical Records (Google Sheet, before in-app entry)", expanded=False):
            if not check_in_df.empty:
                st.dataframe(check_in_df, use_container_width=True, height=400)
                csv = check_in_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Historical Check-Ins CSV",
                    data=csv,
                    file_name=f"check_ins_historical_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No historical check-in records found.")

        with st.expander("🆕 New Records (recorded in the app)", expanded=False):
            new_checkins = get_checkins(supabase_client=ctx.supabase_client)
            if new_checkins:
                new_checkins_df = pd.DataFrame(new_checkins)
                st.dataframe(new_checkins_df, use_container_width=True, height=400)
                csv = new_checkins_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download New Check-Ins CSV",
                    data=csv,
                    file_name=f"check_ins_new_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No check-in records found in the app yet.")

    elif active_subtab == "📤 Check-Outs":
        st.markdown("### 📤 Check-Out Records")
        st.caption("Record new check-outs from the 🔄 Check In / Check Out tab.")

        with st.expander("📜 Historical Records (Google Sheet, before in-app entry)", expanded=False):
            if not check_out_df.empty:
                st.dataframe(check_out_df, use_container_width=True, height=400)
                csv = check_out_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Historical Check-Outs CSV",
                    data=csv,
                    file_name=f"check_outs_historical_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No historical check-out records found.")

        recorded_checkouts = get_checkouts(supabase_client=supabase_client)
        with st.expander("🆕 New Records (recorded in the app)", expanded=False):
            if recorded_checkouts:
                checkout_df_display = pd.DataFrame(recorded_checkouts)
                st.dataframe(checkout_df_display, use_container_width=True, height=400)
                csv = checkout_df_display.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download New Check-Outs CSV",
                    data=csv,
                    file_name=f"check_outs_new_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No check-out records found in the app yet.")

    elif active_subtab == "📊 Current Stock":
        st.markdown("### 📊 Current Stock Levels")
        with st.expander("📋 View Current Stock Levels", expanded=False):
            if not current_stock_df.empty:
                st.dataframe(current_stock_df, use_container_width=True, height=400)
                csv = current_stock_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Current Stock CSV",
                    data=csv,
                    file_name=f"current_stock_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No current stock records found.")

    elif active_subtab == "📋 Stock Take":
        st.markdown("### 📋 Stock Take")
        st.markdown("""
        <div style="
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(8px);
            border-radius: 12px;
            padding: 12px 16px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="color: #888; font-size: 13px;">
                📋 <strong>Stock Take</strong> - Verify physical inventory against system records.
                Create counts, assign sheets to team members, and reconcile discrepancies.
            </div>
        </div>
        """, unsafe_allow_html=True)

        if ctx.inventory_items:
            stock_take_interface(ctx.inventory_items, supabase_client=ctx.supabase_client)
        else:
            st.warning("⚠️ No inventory data available yet.")
            st.info("Go to the 📦 Inventory tab and refresh to load data.")

            if st.button("📥 Load Sample Inventory Data", key="stock_take_load_sample"):
                from app.core.visual_inventory import get_sample_inventory_data
                st.session_state.stock_take_inventory = get_sample_inventory_data()
                st.rerun()


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_item_demand_forecast(item_key: str, daily_demand: pd.DataFrame, forecast_days: int = 30):
    """Cached wrapper around create_ensemble_forecast() for the per-item
    demand forecast selector in _render_analytics_tab().

    Without this, the multi-model ensemble (Prophet/XGBoost/LightGBM/
    ARIMA/etc. -- same one already gated + cached for Dry Ice Mode via
    get_forecast_data() in main.py) ran uncached here. Because
    st.selectbox persists `selected_item` via its widget key across
    reruns, once any item had been picked in a session this block
    re-ran the full ensemble from scratch on EVERY rerun of the entire
    app -- not just when this tab was visible or the selection changed
    -- which is what was stalling the app past the Stock Movements tab.

    item_key is passed explicitly (redundant with daily_demand's
    content) purely so the cache is legible/scoped per selected item.
    """
    return create_ensemble_forecast(daily_demand, forecast_days=forecast_days)


# ============================================================
# FULLY PORTED — 📈 All Items Analytics
# ============================================================
def _render_analytics_tab(ctx: AllItemsContext) -> None:
    constants = ctx.constants

    st.markdown("## 📈 All Items Analytics")
    st.markdown("Comprehensive analysis across all inventory items")

    # ============================================================
    # 🎯 LAZY LOADING FOR HEAVY ANALYTICS DATA
    # ============================================================

    def load_heavy_analytics_data():
        """Load heavy analytics data only when needed. Same three-layer
        fallback (session_state, then Supabase snapshot) as the other two
        loaders -- LazyLoader's own cache_ttl=600 only covers its own
        window; this covers the underlying data source failing after
        that expires, or a cold start after a restart."""
        try:
            logger.info("Loading heavy analytics data...")
            gsheet = GoogleSheetReader()
            if gsheet.authenticate():
                stock = gsheet.get_stock_with_pricing()

                if not stock.empty:
                    original_memory = stock.memory_usage(deep=True).sum() / 1024 / 1024
                    stock = compress_dataframe(stock)
                    compressed_memory = stock.memory_usage(deep=True).sum() / 1024 / 1024
                    reduction = ((original_memory - compressed_memory) / original_memory) * 100
                    logger.info(f"💾 Analytics stock compressed: {original_memory:.1f}MB → {compressed_memory:.1f}MB (↓{reduction:.0f}%)")

                check_in = gsheet.get_check_in()
                check_out = gsheet.get_check_out()
                current_stock = gsheet.get_current_stock()

                if not check_in.empty:
                    check_in = compress_dataframe(check_in)
                if not check_out.empty:
                    check_out = compress_dataframe(check_out)
                if not current_stock.empty:
                    current_stock = compress_dataframe(current_stock)

                logger.info(f"Analytics data loaded: {len(stock)} items")
                if not stock.empty:
                    st.session_state['_ai_analytics_cache'] = (
                        stock, check_in, check_out, current_stock, datetime.now()
                    )
                    save_snapshot(
                        "analytics_data",
                        {"stock": stock, "check_in": check_in, "check_out": check_out,
                         "current_stock": current_stock},
                        supabase_client=ctx.supabase_client,
                    )
                return stock, check_in, check_out, current_stock
            else:
                logger.warning("Google Sheets authentication failed for analytics")
        except Exception as e:
            logger.error(f"Error loading analytics data: {e}")

        cached = st.session_state.get('_ai_analytics_cache')
        if cached:
            stock, check_in, check_out, current_stock, cached_at = cached
            st.warning(f"⚠️ Could not refresh analytics data just now — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')}.")
            return stock, check_in, check_out, current_stock

        snapshot = load_snapshot("analytics_data", supabase_client=ctx.supabase_client)
        if snapshot:
            frames, _, cached_at = snapshot
            st.warning(f"⚠️ Could not reach the analytics data source — showing the last "
                       f"successful load from {cached_at.strftime('%Y-%m-%d %H:%M')} "
                       f"(from before the app last restarted).")
            return frames["stock"], frames["check_in"], frames["check_out"], frames["current_stock"]

        st.error("❌ Could not load analytics data, and no previous data is available to fall back on.")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # Use lazy loader with caching
    analytics_loader = LazyLoader(
        load_heavy_analytics_data,
        cache_ttl=600,  # 10 minute cache
        key_prefix="analytics"
    )

    # Show load status
    if not analytics_loader.is_loaded:
        st.info(" Click the button below to load analytics data. This may take a moment.")

    # Render load button (or show loaded status)
    analytics_loader.render_load_button("📊 Load Analytics Data")

    # ============================================================
    # 🎯 DISPLAY ANALYTICS (ONLY WHEN LOADED)
    # ============================================================
    if analytics_loader.is_loaded:
        analytics_stock_df, check_in_df, check_out_df, current_stock_df = analytics_loader.data

        # Verify data was loaded successfully
        if analytics_stock_df is None or analytics_stock_df.empty:
            st.warning("⚠️ No analytics data available right now.")
        else:
            # ============================================================
            # 🎯 SECTION 1: ORDER ANALYSIS FOR ALL ITEMS
            # ============================================================
            st.divider()
            st.markdown("### 📊 Order Analysis for All Items")

            # Show summary metrics
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("📦 Total Items", len(analytics_stock_df))
            with col2:
                st.metric("📥 Check-Ins", len(check_in_df) if not check_in_df.empty else 0)
            with col3:
                st.metric("📤 Check-Outs", len(check_out_df) if not check_out_df.empty else 0)
            with col4:
                st.metric("📊 Current Stock", len(current_stock_df) if not current_stock_df.empty else 0)
            with col5:
                if not check_in_df.empty and not check_out_df.empty:
                    net_movement = len(check_in_df) - len(check_out_df)
                    st.metric("📈 Net Movement", net_movement, delta=f"{net_movement:+d}")

            # Define parse_date_safe function once
            def parse_date_safe(date_str):
                if pd.isna(date_str) or date_str == '' or date_str == 'nan' or date_str == 'None' or date_str == 'NaT':
                    return None
                try:
                    return pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                except:
                    try:
                        return pd.to_datetime(date_str, format='%Y-%m-%d %H:%M:%S', errors='coerce')
                    except:
                        try:
                            return pd.to_datetime(date_str, format='mixed', errors='coerce')
                        except:
                            return None

            # --- CHECK-IN ANALYSIS ---
            if not check_in_df.empty:
                st.markdown("#### 📥 Check-In Analysis")

                # Find date column - try multiple approaches
                date_col_in = None
                for col in check_in_df.columns:
                    if 'date' in col.lower():
                        date_col_in = col
                        break

                # Find item column -- code-first (see demand_utils.ITEM_NAME_KEYWORDS)
                # so grouping/counting stays keyed on the stable SKU even when a
                # free-text description column is also present in the sheet.
                item_col_in = detect_column(check_in_df, ITEM_NAME_KEYWORDS)
                # Separate label column (e.g. ITEM_DESCRIPTION) used only to make
                # the "Top Received Items" chart readable -- see below.
                item_label_col_in = detect_column(check_in_df, ITEM_LABEL_KEYWORDS)

                if date_col_in:
                    try:
                        # Parse dates for check-in
                        check_in_df['DATE_STR'] = check_in_df[date_col_in].astype(str).str.strip()
                        check_in_df['DATE'] = check_in_df['DATE_STR'].apply(parse_date_safe)
                        check_in_df = check_in_df.dropna(subset=['DATE'])

                        if not check_in_df.empty:
                            check_in_df['MONTH'] = check_in_df['DATE'].dt.to_period('M')

                            # Monthly check-ins
                            monthly_checkins = check_in_df.groupby('MONTH').size().reset_index(name='Check-In Count')

                            col1, col2 = st.columns(2)

                            with col1:
                                st.markdown("##### 📈 Monthly Check-In Trends")
                                if not monthly_checkins.empty:
                                    monthly_checkins['MONTH_STR'] = monthly_checkins['MONTH'].astype(str)
                                    fig_checkin = px.line(
                                        monthly_checkins,
                                        x='MONTH_STR',
                                        y='Check-In Count',
                                        title='Monthly Check-Ins (Receipts/Incoming)',
                                        markers=True
                                    )
                                    st.plotly_chart(fig_checkin, use_container_width=True)
                                else:
                                    st.info("No monthly check-in data")

                            with col2:
                                st.markdown("##### 🏆 Top Received Items")
                                if item_col_in:
                                    # Count by the stable code first (avoids fragmenting
                                    # counts across minor description text variants for
                                    # the same physical item), then resolve each code to
                                    # its most common description purely for display.
                                    top_checkins = check_in_df[item_col_in].value_counts().head(10)
                                    top_checkins = top_checkins[~top_checkins.index.to_series().apply(is_junk_value)]

                                    if not top_checkins.empty:
                                        if item_label_col_in:
                                            code_to_label = build_code_to_label_map(
                                                check_in_df, item_col_in, item_label_col_in
                                            )
                                            display_labels = [code_to_label.get(code, code) for code in top_checkins.index]
                                        else:
                                            display_labels = top_checkins.index.tolist()

                                        fig_top_in = px.bar(
                                            x=top_checkins.values,
                                            y=display_labels,
                                            title='Top 10 Most Received Items',
                                            labels={'x': 'Receipt Count', 'y': 'Item Name'},
                                            orientation='h'
                                        )
                                        fig_top_in.update_layout(height=400)
                                        st.plotly_chart(fig_top_in, use_container_width=True)
                                    else:
                                        st.info("No item data available for check-ins")
                                else:
                                    st.warning("No item column found. Available columns: " + ", ".join(list(check_in_df.columns)))
                    except Exception as e:
                        st.warning(f"Could not process check-in data: {e}")
                else:
                    st.warning("No date column found in check-in data")

            # --- CHECK-OUT ANALYSIS ---
            if not check_out_df.empty:
                st.markdown("#### 📤 Check-Out Analysis")

                # Find date and item columns for check-out
                date_col_out = None
                for col in check_out_df.columns:
                    if 'date' in col.lower():
                        date_col_out = col
                        break

                item_col_out = detect_column(check_out_df, ITEM_NAME_KEYWORDS)
                item_label_col_out = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)

                if date_col_out:
                    try:
                        # Parse dates for check-out
                        check_out_df['DATE_STR'] = check_out_df[date_col_out].astype(str).str.strip()
                        check_out_df['DATE'] = check_out_df['DATE_STR'].apply(parse_date_safe)
                        check_out_df = check_out_df.dropna(subset=['DATE'])

                        if not check_out_df.empty:
                            check_out_df['MONTH'] = check_out_df['DATE'].dt.to_period('M')
                            check_out_df['WEEKDAY'] = check_out_df['DATE'].dt.day_name()

                            # Monthly check-outs
                            monthly_checkouts = check_out_df.groupby('MONTH').size().reset_index(name='Check-Out Count')

                            col1, col2 = st.columns(2)

                            with col1:
                                st.markdown("##### 📈 Monthly Check-Out Trends")
                                if not monthly_checkouts.empty:
                                    monthly_checkouts['MONTH_STR'] = monthly_checkouts['MONTH'].astype(str)
                                    fig_checkout = px.line(
                                        monthly_checkouts,
                                        x='MONTH_STR',
                                        y='Check-Out Count',
                                        title='Monthly Check-Outs (Issues/Usage)',
                                        markers=True
                                    )
                                    st.plotly_chart(fig_checkout, use_container_width=True)
                                else:
                                    st.info("No monthly check-out data")

                            with col2:
                                st.markdown("##### 🏆 Top Used Items")
                                if item_col_out:
                                    top_checkouts = check_out_df[item_col_out].value_counts().head(10)
                                    top_checkouts = top_checkouts[~top_checkouts.index.to_series().apply(is_junk_value)]

                                    if not top_checkouts.empty:
                                        if item_label_col_out:
                                            code_to_label = build_code_to_label_map(
                                                check_out_df, item_col_out, item_label_col_out
                                            )
                                            display_labels = [code_to_label.get(code, code) for code in top_checkouts.index]
                                        else:
                                            display_labels = top_checkouts.index.tolist()

                                        fig_top_out = px.bar(
                                            x=top_checkouts.values,
                                            y=display_labels,
                                            title='Top 10 Most Used Items',
                                            labels={'x': 'Usage Count', 'y': 'Item Name'},
                                            orientation='h'
                                        )
                                        fig_top_out.update_layout(height=400)
                                        st.plotly_chart(fig_top_out, use_container_width=True)
                                    else:
                                        st.info("No item data available for check-outs")
                                else:
                                    st.warning("No item column found in check-out data")
                    except Exception as e:
                        st.warning(f"Could not process check-out data: {e}")
                else:
                    st.warning("No date column found in check-out data")

            # --- COMPARISON: CHECK-IN vs CHECK-OUT ---
            if not check_in_df.empty and not check_out_df.empty and 'MONTH' in check_in_df.columns and 'MONTH' in check_out_df.columns:
                st.markdown("#### 📊 Check-In vs Check-Out Comparison")

                try:
                    checkin_monthly = check_in_df.groupby('MONTH').size().reset_index(name='Check-Ins')
                    checkout_monthly = check_out_df.groupby('MONTH').size().reset_index(name='Check-Outs')

                    comparison = pd.merge(checkin_monthly, checkout_monthly, on='MONTH', how='outer').fillna(0)
                    comparison['MONTH_STR'] = comparison['MONTH'].astype(str)
                    comparison['Net'] = comparison['Check-Ins'] - comparison['Check-Outs']

                    fig_comparison = go.Figure()
                    fig_comparison.add_trace(go.Bar(
                        x=comparison['MONTH_STR'],
                        y=comparison['Check-Ins'],
                        name='Check-Ins',
                        marker_color='#2ecc71'
                    ))
                    fig_comparison.add_trace(go.Bar(
                        x=comparison['MONTH_STR'],
                        y=comparison['Check-Outs'],
                        name='Check-Outs',
                        marker_color='#e74c3c'
                    ))
                    fig_comparison.update_layout(
                        title='Monthly Check-Ins vs Check-Outs',
                        xaxis_title='Month',
                        yaxis_title='Count',
                        barmode='group',
                        height=400
                    )
                    st.plotly_chart(fig_comparison, use_container_width=True)

                    total_net = comparison['Net'].sum()
                    st.metric(
                        "📊 Net Total Movement",
                        f"{total_net:+.0f}",
                        delta=f"{total_net:+.0f}"
                    )
                except Exception as e:
                    st.warning(f"Could not create comparison chart: {e}")

            # ============================================================
            # 🎯 SECTION 2: DEMAND FORECAST FOR ALL ITEMS
            # ============================================================
            st.divider()
            st.markdown("### 🔮 Demand Forecast for All Items")

            if not check_out_df.empty and 'DATE' in check_out_df.columns:
                item_col = detect_column(check_out_df, ITEM_NAME_KEYWORDS)
                item_label_col = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)

                # Find quantity column
                qty_col = None
                for col in check_out_df.columns:
                    if 'quantity' in col.lower() or 'qty' in col.lower():
                        qty_col = col
                        break

                if item_col and qty_col:
                    items_with_history = check_out_df[item_col].dropna().unique().tolist()
                    items_with_history = [x for x in items_with_history if not is_junk_value(x)]

                    if items_with_history:
                        code_to_label = (
                            build_code_to_label_map(check_out_df, item_col, item_label_col)
                            if item_label_col else {}
                        )

                        selected_item = st.selectbox(
                            "Select Item for Demand Forecast",
                            sorted(items_with_history),
                            format_func=lambda code: code_to_label.get(code, code),
                            key="forecast_item"
                        )

                        if selected_item:
                            item_history = check_out_df[check_out_df[item_col] == selected_item].copy()

                            if not item_history.empty and 'DATE' in item_history.columns and qty_col:
                                try:
                                    daily_demand = compute_daily_demand_for_item(
                                        check_out_df, selected_item,
                                        item_col=item_col, qty_col=qty_col, date_col=date_col_out,
                                    )

                                    if not daily_demand.empty:
                                        if len(daily_demand) >= 5 and daily_demand['Order_Quantity_kg'].sum() > 0:
                                            with st.spinner(f"Generating forecast for {selected_item}..."):
                                                fig_forecast, forecast_values, model_forecasts, accuracy = _cached_item_demand_forecast(
                                                    selected_item,
                                                    daily_demand,
                                                    forecast_days=30
                                                )

                                                if fig_forecast:
                                                    st.plotly_chart(fig_forecast, use_container_width=True)

                                                    col1, col2, col3 = st.columns(3)
                                                    with col1:
                                                        total_forecast = np.sum(forecast_values) if len(forecast_values) > 0 else 0
                                                        st.metric("📊 Total Forecasted Demand (30 days)", f"{total_forecast:,.0f}")
                                                    with col2:
                                                        avg_daily = np.mean(forecast_values) if len(forecast_values) > 0 else 0
                                                        st.metric("📈 Average Daily Demand", f"{avg_daily:.1f}")
                                                    with col3:
                                                        st.metric("🎯 Forecast Accuracy", f"{100-accuracy*100:.1f}%")

                                                    with st.expander("🔬 View Model Performance"):
                                                        for model_name, stats in model_forecasts.items():
                                                            if model_name != 'External Factors' and isinstance(stats, dict):
                                                                st.metric(model_name, f"{stats['avg']:.1f} units/day")
                                                else:
                                                    st.warning("Could not generate forecast for this item.")
                                        else:
                                            st.warning(f"Not enough data for {selected_item}. Need at least 5 positive values.")
                                    else:
                                        st.warning("No valid quantity data after cleaning")
                                except Exception as e:
                                    st.warning(f"Error processing data: {e}")
                            else:
                                st.warning("No quantity data available for this item.")
                    else:
                        st.info("No items with check-out history found.")
                else:
                    st.info(f"Missing required columns. Item column: {item_col}, Quantity column: {qty_col}")
            else:
                st.info("No check-out data available for forecasting.")

            # ============================================================
            # 🎯 SECTION 3: COST OPTIMIZATION FOR ALL ITEMS
            # ============================================================
            st.divider()
            st.markdown("### 💰 Cost Optimization for All Items")

            if 'UNIT PRICE' in analytics_stock_df.columns and 'QUANTITY' in analytics_stock_df.columns:
                cost_df = analytics_stock_df.copy()
                cost_df['QUANTITY'] = pd.to_numeric(cost_df['QUANTITY'], errors='coerce')
                cost_df['UNIT PRICE'] = pd.to_numeric(cost_df['UNIT PRICE'], errors='coerce')
                cost_df = cost_df.dropna(subset=['QUANTITY', 'UNIT PRICE'])

                if not cost_df.empty:
                    cost_df['TOTAL_VALUE'] = cost_df['QUANTITY'] * cost_df['UNIT PRICE']
                    cost_df['ANNUAL_DEMAND'] = cost_df['QUANTITY'] * 12

                    col1, col2 = st.columns(2)
                    with col1:
                        order_cost = st.number_input(
                            "Ordering Cost (KSh)",
                            value=float(constants.ALL_ITEMS_ORDER_COST),
                            step=100.0,
                            key="cost_analysis_order_cost"
                        )
                    with col2:
                        holding_rate = st.number_input(
                            "Holding Rate (%)",
                            value=float(constants.ALL_ITEMS_HOLDING_RATE * 100),
                            step=0.5,
                            key="cost_analysis_holding_rate"
                        ) / 100
                    st.caption(
                        "Defaults are general-inventory assumptions, - adjust freely."
                    )

                    if st.button("📊 Run Cost Analysis", key="run_cost_analysis"):
                        cost_results = []
                        skipped_items = []
                        for _, row in cost_df.iterrows():
                            item_label = row.get('ITEM_NAME', 'Unknown')
                            try:
                                eoq_result = calculate_eoq_costs(
                                    current_qty=row['QUANTITY'],
                                    annual_demand=row['ANNUAL_DEMAND'],
                                    order_cost=order_cost,
                                    holding_rate=holding_rate,
                                    unit_price=row['UNIT PRICE'],
                                )
                                if eoq_result is not None:
                                    cost_results.append({
                                        'Item': item_label,
                                        'Category': row.get('ITEM_CATEGORY', 'Uncategorized'),
                                        'Current Stock': row['QUANTITY'],
                                        'Unit Price': row['UNIT PRICE'],
                                        'Annual Demand': row['ANNUAL_DEMAND'],
                                        'EOQ': eoq_result.eoq,
                                        'Current Cost': eoq_result.current_total_cost,
                                        'Optimal Cost': eoq_result.optimal_total_cost,
                                        'Potential Savings': eoq_result.potential_savings,
                                    })
                                else:
                                    skipped_items.append(f"{item_label}: calculation returned no result")
                            except Exception as e:
                                skipped_items.append(f"{item_label}: {e}")

                        if skipped_items:
                            with st.expander(f"⚠️ {len(skipped_items)} item(s) skipped from cost analysis"):
                                for msg in skipped_items:
                                    st.caption(f"- {msg}")

                        if cost_results:
                            cost_df_results = pd.DataFrame(cost_results)

                            col1, col2, col3, col4 = st.columns(4)
                            with col1:
                                st.metric("📦 Items Analyzed", len(cost_df_results))
                            with col2:
                                total_savings = cost_df_results['Potential Savings'].sum()
                                st.metric("💰 Total Potential Savings", f"KSh {total_savings:,.0f}")
                            with col3:
                                avg_savings = cost_df_results['Potential Savings'].mean()
                                st.metric("📊 Avg Savings per Item", f"KSh {avg_savings:,.0f}")
                            with col4:
                                items_with_savings = len(cost_df_results[cost_df_results['Potential Savings'] > 0])
                                st.metric("✅ Items with Savings", items_with_savings)

                            st.dataframe(cost_df_results, use_container_width=True, hide_index=True)

                            csv = cost_df_results.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="📥 Download Cost Analysis Results",
                                data=csv,
                                file_name=f"cost_analysis_all_items_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime='text/csv'
                            )

                            st.divider()
                            st.markdown("#### 🏆 Top 10 Items with Highest Potential Savings")
                            top_savings = cost_df_results.sort_values('Potential Savings', ascending=False).head(10)
                            if not top_savings.empty:
                                st.dataframe(
                                    top_savings[['Item', 'Category', 'Current Cost', 'Optimal Cost', 'Potential Savings']],
                                    use_container_width=True,
                                    hide_index=True
                                )

                            st.divider()
                            st.markdown("#### 📊 Cost Savings by Category")
                            category_cost_summary = cost_df_results.groupby('Category').agg({
                                'Item': 'count',
                                'Potential Savings': 'sum'
                            }).reset_index()
                            category_cost_summary.columns = ['Category', 'Items', 'Total Savings']
                            st.dataframe(category_cost_summary, use_container_width=True, hide_index=True)

                            fig_savings = px.bar(
                                category_cost_summary,
                                x='Category',
                                y='Total Savings',
                                title='Potential Savings by Category',
                                color='Category',
                                height=400,
                                labels={'Total Savings': 'Savings (KSh)'}
                            )
                            fig_savings.update_layout(showlegend=False)
                            st.plotly_chart(fig_savings, use_container_width=True)
                        else:
                            st.warning("No valid cost results generated. Please check your data.")
                else:
                    st.warning("No valid data with both quantity and price found.")
            else:
                st.warning("Required columns (UNIT PRICE, QUANTITY) not found in stock data.")


# ============================================================
# FULLY PORTED — 🖼️ Visual Inventory
# ============================================================
def _render_visual_inventory_tab(ctx: AllItemsContext) -> None:
    inventory_items = ctx.inventory_items or {}

    st.markdown("### 📦 Visual Inventory Dashboard")

    # Add filters
    search, category_filter, show_low_stock = inventory_filters(inventory_items)

    # Apply filters
    filtered_items = {}
    for item, details in inventory_items.items():
        # Search filter
        if search and search.lower() not in item.lower():
            continue

        # Category filter
        if category_filter != 'All' and details.get('category', 'Uncategorized') != category_filter:
            continue

        # Low stock filter
        if show_low_stock and details.get('stock', 0) >= details.get('reorder', 0):
            continue

        filtered_items[item] = details

    # ============================================================
    # SECTION 1: AI-POWERED RECOMMENDATIONS (TOP)
    # ============================================================
    with st.expander("🤖 View AI-Powered Recommendations", expanded=False):
        # Use inventory_items (all items) for AI recommendations
        if inventory_items:
            ai_powered_recommendations(
                inventory_items=inventory_items,
                filtered_items=filtered_items,
                kpis=ctx.kpis
            )
        else:
            st.info("No inventory data available for AI recommendations")

    # ============================================================
    # SECTION 2: STATUS DASHBOARD (Katana Style)
    # ============================================================
    with st.expander("📊 View Real-Time Status Dashboard", expanded=False):
        st.markdown("""
        <div style="
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="color: #888; font-size: 13px;">
                Real-time inventory status overview for ALL items.
                <span style="color: #4caf50;">🟢 Healthy</span> | 
                <span style="color: #ff9800;">🟠 Low Stock</span> | 
                <span style="color: #dc3545;">🔴 Critical</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Display the status dashboard for ALL inventory
        if inventory_items:
            inventory_status_dashboard(inventory_items, ctx.inventory_tracker)
        else:
            st.info("No inventory data available for status dashboard")

    # ============================================================
    # SECTION 3: GRID VIEW
    # ============================================================
    with st.expander("🖼️ View Inventory Grid", expanded=False):
        # Show stats
        inventory_stats_summary(filtered_items)

        st.markdown("---")

        # Show the grid
        if filtered_items:
            visual_inventory_grid(filtered_items, columns=3)
        else:
            st.info("No items match your filters")

    # ============================================================
    # SECTION 4: HEAT MAP
    # ============================================================
    with st.expander("🔥 View Inventory Heat Map", expanded=False):
        st.markdown("""
        <div style="
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="color: #888; font-size: 13px;">
                Color-coded overview of inventory stock levels. 
                <span style="color: #dc3545;">🔴 Critical</span> | 
                <span style="color: #ff9800;">🟠 Low Stock</span> | 
                <span style="color: #4caf50;">🟢 Good</span> | 
                <span style="color: #2196f3;">🔵 Overstocked</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Use the filtered_items from the visual inventory
        if filtered_items:
            # Create heatmap data from filtered items
            heatmap_data = []
            for item_name, details in filtered_items.items():
                stock = details.get('stock', 0)
                reorder = details.get('reorder', 0)
                eoq = details.get('max', stock * 2)

                if stock <= 0:
                    status = 'Critical'
                elif stock < reorder:
                    status = 'Low'
                elif stock >= reorder and stock < eoq:
                    status = 'Good'
                else:
                    status = 'Overstocked'

                heatmap_data.append({
                    'Item': item_name,
                    'Stock': stock,
                    'Reorder': reorder,
                    'EOQ': eoq,
                    'Status': status,
                    'Category': details.get('category', 'Uncategorized'),
                    'Unit': details.get('unit', 'kg')
                })

            # Add heat map specific filters
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                heatmap_search = st.text_input(
                    "Search Items",
                    placeholder="Type item name...",
                    key="heatmap_search"
                )
            with col2:
                # Get unique statuses for filter
                status_options = ['All'] + sorted(set(item['Status'] for item in heatmap_data))
                heatmap_status = st.selectbox(
                    "📊 Status",
                    status_options,
                    key="heatmap_status_filter"
                )
            with col3:
                # Get unique categories for filter
                cat_options = ['All'] + sorted(set(item['Category'] for item in heatmap_data))
                heatmap_category = st.selectbox(
                    "📂 Category",
                    cat_options,
                    key="heatmap_category_filter"
                )

            # Apply heat map filters
            filtered_heatmap = []
            for item in heatmap_data:
                if heatmap_search and heatmap_search.lower() not in item['Item'].lower():
                    continue
                if heatmap_status != 'All' and item['Status'] != heatmap_status:
                    continue
                if heatmap_category != 'All' and item['Category'] != heatmap_category:
                    continue
                filtered_heatmap.append(item)

            # Convert back to dict
            heatmap_items = {}
            for item in filtered_heatmap:
                heatmap_items[item['Item']] = {
                    'stock': item['Stock'],
                    'reorder': item['Reorder'],
                    'max': item['EOQ'],
                    'unit': item['Unit'],
                    'category': item['Category']
                }

            if heatmap_items:
                inventory_heatmap(heatmap_items, title="Inventory Stock Levels", columns=6)
            else:
                st.info("No items match your heat map filters")
        else:
            st.info("No inventory items to display in heat map")

    # ============================================================
    # SECTION 5: REPLENISHMENT RECOMMENDATIONS
    # ============================================================
    with st.expander("🛒 View Replenishment Recommendations", expanded=False):
        st.markdown("""
        <div style="
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="color: #888; font-size: 13px;">
                Smart replenishment suggestions based on current stock levels.
                <span style="color: #dc3545;">🔴 Urgent (≤3 days)</span> | 
                <span style="color: #ffc107;">🟡 Medium (4-7 days)</span> | 
                <span style="color: #28a745;">🟢 Low (>7 days)</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Use the filtered_items from the visual inventory
        if filtered_items:
            # Generate recommendations
            recommendations_df = get_replenishment_recommendations(filtered_items)

            # Show summary and recommendations
            if not recommendations_df.empty:
                summary = get_replenishment_summary(recommendations_df)
                show_replenishment_summary_cards(summary)
                st.markdown("---")
                show_replenishment_suggestions(recommendations_df)

                # Export button
                col1, col2 = st.columns([1, 4])
                with col1:
                    csv = recommendations_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Download Recommendations",
                        data=csv,
                        file_name=f"replenishment_recommendations_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime='text/csv'
                    )
                with col2:
                    st.caption("Download recommendations as CSV for offline review or sharing")
            else:
                st.success("✅ All items are well-stocked. No replenishment needed at this time.")
        else:
            st.info("No inventory items to analyze for replenishment")


def _render_advanced_analytics_tab(ctx: AllItemsContext) -> None:
    if ctx.df is not None and not ctx.df.empty and ctx.inventory_items:
        create_advanced_analytics_tab(ctx.analytics, ctx.df, ctx.inventory_items, ctx.stock_df)
    else:
        st.warning("No data available for advanced analytics")

LOCATION_KEYWORDS = ["LOCATION", "WAREHOUSE", "SITE", "STORE", "OUTLET"]
DEPARTMENT_KEYWORDS = ["DEPARTMENT", "REQUESTED BY", "ISSUED TO", "ISSUED_TO", "USER", "TEAM", "REQUESTER"]


def _compute_stock_variance(location: str, supabase_client=None):
    """Opening + Check-In + Transfers In - Check-Out - Transfers Out =
    Expected book stock; Physical - Expected = Variance. Computed per
    location -- every term is filtered to `location` specifically, since
    check-in/check-out/transfers/stock takes all happen at every location,
    not just one.

    Check-Out and Transfers come from the app's own tables via
    stock_ledger.py's shared helpers; Check-In still comes from Google
    Sheets until Phase 3 moves it into the app too, so a Check-In outage
    degrades to a warning rather than blocking the whole report."""
    completed = [
        c for c in st.session_state.get('stock_takes', {}).values()
        if c['status'] == 'Completed' and c.get('warehouse') == location
    ]
    completed.sort(key=lambda c: c.get('completed', ''))

    if len(completed) < 2:
        return None, (
            f"Need at least 2 completed Stock Takes labeled '{location}' to compute "
            f"variance here -- currently have {len(completed)}. When creating a count, "
            f"set Warehouse/Location to '{location}' and select only the items actually "
            f"physically at that location."
        )

    previous, latest = completed[-2], completed[-1]
    window_start, window_end = previous.get('completed', ''), latest.get('completed', '')

    try:
        gsheet = GoogleSheetReader()
        check_in_df = gsheet.get_check_in() if gsheet.authenticate() else pd.DataFrame()
        check_in_ok = True
    except Exception as e:
        logger.error(f"Stock Variance: could not reach Check-In source: {e}")
        check_in_df = pd.DataFrame()
        check_in_ok = False

    check_in_loc_col = detect_column(check_in_df, LOCATION_KEYWORDS) if not check_in_df.empty else None
    check_in_warning = None
    if not check_in_ok:
        check_in_warning = "Could not reach the Check-In data source -- Check-In figures below read as 0 rather than reflect reality."
    elif not check_in_df.empty and not check_in_loc_col:
        check_in_warning = (
            f"Check-In data doesn't have a Location column yet, so it can't be split "
            f"by location. Add one to the Check-In sheet (e.g. named 'LOCATION') with "
            f"values matching your locations exactly: {', '.join(COMPANY_LOCATIONS)}."
        )

    rows = []
    for item_name, latest_details in latest['items'].items():
        physical = latest_details.get('counted_qty', 0)
        prev_details = previous['items'].get(item_name)
        opening = prev_details.get('counted_qty', 0) if prev_details else latest_details.get('system_qty', 0)

        check_in = (
            sum_check_in_window(check_in_df, check_in_loc_col, item_name, location, window_start, window_end)
            if check_in_loc_col else 0.0
        )
        check_out = sum_check_out_window(item_name, location, window_start, window_end, supabase_client=supabase_client)
        transfers_in = transfer_total(item_name, location, window_start, window_end, 'in', supabase_client=supabase_client)
        transfers_out = transfer_total(item_name, location, window_start, window_end, 'out', supabase_client=supabase_client)

        expected = opening + check_in + transfers_in - check_out - transfers_out
        rows.append({
            'Item': item_name, 'Opening': opening, 'Check-In': check_in,
            'Transfers In': transfers_in, 'Check-Out': check_out,
            'Transfers Out': transfers_out, 'Expected': expected,
            'Physical': physical, 'Variance': physical - expected,
        })

    result_df = pd.DataFrame(rows)
    meta = {'previous_count': previous['name'], 'previous_date': window_start,
            'latest_count': latest['name'], 'latest_date': window_end,
            'check_in_warning': check_in_warning}
    return result_df, meta


def _find_unusual_checkout_quantities(supabase_client=None, lookback_days=30, mad_threshold=3.5):
    """Flags recent Check-Out rows whose quantity is a statistical
    outlier against that SAME item's own check-out history -- no
    reference number exists to verify against, so this is a 'worth a
    look' signal, not proof of anything. Median + MAD (median absolute
    deviation), not mean + std -- a few genuine outliers shouldn't raise
    the bar for detecting more of them, same lesson as the forecast
    accuracy fix earlier tonight."""
    try:
        gsheet = GoogleSheetReader()
        if not gsheet.authenticate():
            return None, "Could not reach the Check-Out data source right now."
        check_out_df = gsheet.get_check_out()
    except Exception as e:
        logger.error(f"Could not load check-out data for quantity flagging: {e}")
        return None, "Could not reach the Check-Out data source right now."

    if check_out_df.empty:
        return pd.DataFrame(), None

    item_col = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)
    date_col = next((c for c in check_out_df.columns if 'date' in c.lower()), None)
    qty_col = next((c for c in check_out_df.columns if 'quantity' in c.lower() or 'qty' in c.lower()), None)

    if not item_col or not date_col or not qty_col:
        return None, "Check-Out sheet is missing an Item, Date, or Quantity column."

    df = check_out_df[[item_col, date_col, qty_col]].copy()
    df.columns = ['item', 'date', 'qty']
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['qty'] = pd.to_numeric(df['qty'], errors='coerce')
    df = df.dropna(subset=['item', 'date', 'qty'])

    if df.empty:
        return pd.DataFrame(), None

    cutoff = datetime.now() - pd.Timedelta(days=lookback_days)
    recent = df[df['date'] >= cutoff]

    flagged = []
    for item_name, group in df.groupby('item'):
        if len(group) < 5:
            continue  # not enough history to judge what's "normal"
        median_q = group['qty'].median()
        mad = (group['qty'] - median_q).abs().median()
        if mad == 0:
            continue  # no variability to compare against
        item_recent = recent[recent['item'] == item_name]
        for _, row in item_recent.iterrows():
            modified_z = 0.6745 * (row['qty'] - median_q) / mad
            if abs(modified_z) > mad_threshold:
                flagged.append({
                    'Item': item_name,
                    'Date': row['date'].strftime('%Y-%m-%d'),
                    'Quantity': row['qty'],
                    'Typical (median)': median_q,
                    'Deviation Score': round(abs(modified_z), 2),
                })

    return pd.DataFrame(flagged), None


def _find_incomplete_checkout_rows(supabase_client=None):
    """Check-Out rows missing a date, a quantity, or (if a department/
    requester column can be found) that too -- these are the rows most
    likely to represent sloppy or incomplete recording, worth catching
    before they distort Stock Variance or anything else downstream."""
    try:
        gsheet = GoogleSheetReader()
        if not gsheet.authenticate():
            return None, "Could not reach the Check-Out data source right now."
        check_out_df = gsheet.get_check_out()
    except Exception as e:
        logger.error(f"Could not load check-out data for completeness check: {e}")
        return None, "Could not reach the Check-Out data source right now."

    if check_out_df.empty:
        return pd.DataFrame(), None

    item_col = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)
    date_col = next((c for c in check_out_df.columns if 'date' in c.lower()), None)
    qty_col = next((c for c in check_out_df.columns if 'quantity' in c.lower() or 'qty' in c.lower()), None)
    dept_col = detect_column(check_out_df, DEPARTMENT_KEYWORDS)

    problems = []
    for _, row in check_out_df.iterrows():
        issues = []
        if date_col and (pd.isna(row.get(date_col)) or str(row.get(date_col)).strip() == ''):
            issues.append("Missing date")
        if qty_col:
            raw_qty = row.get(qty_col)
            if pd.isna(raw_qty) or str(raw_qty).strip() == '':
                issues.append("Missing quantity")
            else:
                qty_match = re.match(r'^\s*(-?\d+\.?\d*)', str(raw_qty))
                if qty_match:
                    if float(qty_match.group(1)) <= 0:
                        issues.append("Zero quantity")
                else:
                    issues.append("Quantity not a recognizable number")
        if dept_col and (pd.isna(row.get(dept_col)) or str(row.get(dept_col)).strip() == ''):
            issues.append("Missing department/requester")
        if issues:
            problems.append({
                'Item': row.get(item_col, 'Unknown') if item_col else 'Unknown',
                'Date (raw)': row.get(date_col, '') if date_col else '',
                'Quantity (raw)': row.get(qty_col, '') if qty_col else '',
                'Department (raw)': row.get(dept_col, '') if dept_col else 'N/A (column not found)',
                'Issues': ', '.join(issues),
            })

    note = None if dept_col else (
        "No department/requester column detected on the Check-Out sheet -- that "
        "specific check is skipped. Tell me the exact column name to include it."
    )
    return pd.DataFrame(problems), note


def _get_flagged_variance_checkouts(location, supabase_client=None):
    """For items with a shortfall (physical below expected) at
    `location`, pulls the actual Check-Out rows recorded in that
    reconciliation window -- so a manager can look at exactly what the
    sheet claims left, rather than just seeing a number."""
    result_df, meta = _compute_stock_variance(location, supabase_client=supabase_client)
    if result_df is None:
        return None, meta

    shortfalls = result_df[result_df['Variance'] < -0.01]
    if shortfalls.empty:
        return [], meta

    try:
        gsheet = GoogleSheetReader()
        if not gsheet.authenticate():
            return None, "Could not reach the Check-Out data source right now."
        check_out_df = gsheet.get_check_out()
    except Exception as e:
        logger.error(f"Could not load check-out detail: {e}")
        return None, "Could not reach the Check-Out data source right now."

    if check_out_df.empty:
        return [], meta

    loc_col = detect_column(check_out_df, LOCATION_KEYWORDS)
    item_col = detect_column(check_out_df, ITEM_LABEL_KEYWORDS)
    date_col = next((c for c in check_out_df.columns if 'date' in c.lower()), None)

    if not loc_col or not item_col or not date_col:
        return None, "Check-Out sheet is missing a Location, Item, or Date column."

    start_dt = pd.to_datetime(meta['previous_date'], errors='coerce')
    end_dt = pd.to_datetime(meta['latest_date'], errors='coerce')

    results = []
    for _, row in shortfalls.iterrows():
        item_name = row['Item']
        sub = check_out_df[
            (check_out_df[item_col] == item_name)
            & (check_out_df[loc_col].astype(str).str.strip() == location)
        ].copy()
        sub['_DATE'] = pd.to_datetime(sub[date_col], errors='coerce')
        if pd.notna(start_dt):
            sub = sub[sub['_DATE'] >= start_dt]
        if pd.notna(end_dt):
            sub = sub[sub['_DATE'] <= end_dt]
        results.append({
            'item': item_name, 'variance': row['Variance'],
            'expected': row['Expected'], 'physical': row['Physical'],
            'checkout_rows': sub,
        })
    return results, meta


@st.cache_data(ttl=300, show_spinner=False)
def _distinct_check_in_suppliers(_supabase_client=None) -> list:
    """Known suppliers, pulled from Check-In history (sheet) plus anything
    recorded in the app -- self-populating instead of a separately
    managed list. Leading underscore on the client param tells
    st.cache_data not to try to hash an unhashable Supabase client."""
    suppliers = set()
    try:
        gsheet = GoogleSheetReader()
        if gsheet.authenticate():
            check_in_df = gsheet.get_check_in()
            supplier_col = next((c for c in check_in_df.columns if 'supplier' in c.lower()), None)
            if supplier_col:
                suppliers.update(check_in_df[supplier_col].dropna().astype(str).str.strip().tolist())
    except Exception as e:
        logger.warning(f"Could not load supplier history from Check-In sheet: {e}")
    for r in get_checkins(supabase_client=_supabase_client):
        if r.get('supplier'):
            suppliers.add(str(r['supplier']).strip())
    return sorted(s for s in suppliers if s and s.lower() != 'nan')


@st.cache_data(ttl=300, show_spinner=False)
def _distinct_check_out_departments(_supabase_client=None) -> list:
    """Same idea as _distinct_check_in_suppliers, for Department."""
    departments = set()
    try:
        gsheet = GoogleSheetReader()
        if gsheet.authenticate():
            check_out_df = gsheet.get_check_out()
            dept_col = detect_column(check_out_df, DEPARTMENT_KEYWORDS)
            if dept_col:
                departments.update(check_out_df[dept_col].dropna().astype(str).str.strip().tolist())
    except Exception as e:
        logger.warning(f"Could not load department history from Check-Out sheet: {e}")
    for r in get_checkouts(supabase_client=_supabase_client):
        if r.get('destination'):
            departments.add(str(r['destination']).strip())
    return sorted(d for d in departments if d and d.lower() != 'nan')


def _picker_with_add_new(label: str, options: list, key_prefix: str) -> str:
    """A dropdown over known values plus a '+ Add new' option that reveals
    a text input for a value not seen before -- same shape as the
    Transfers tab's _location_picker, generalized to a dynamic list."""
    ADD_NEW = "+ Add new..."
    choices = [""] + options + [ADD_NEW]
    picked = st.selectbox(label, choices, key=f"{key_prefix}_select")
    if picked == ADD_NEW:
        return st.text_input(f"{label} (new)", key=f"{key_prefix}_new").strip()
    return picked


def _render_checkin_checkout_tab(ctx: AllItemsContext) -> None:
    st.markdown("### 🔄 Check In / Check Out")
    supabase_client = ctx.supabase_client
    init_checkout_reconciliation_storage(supabase_client)
    item_options = sorted((ctx.inventory_items or {}).keys())
    item_col_config = (
        st.column_config.SelectboxColumn("Item", options=item_options, required=True)
        if item_options else st.column_config.TextColumn("Item", required=True)
    )

    st.markdown("#### 📥 Record a Check-In")
    st.caption("Add one row per item — everything you add here is recorded under the same date, store, and supplier.")
    with st.form("record_checkin_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            ci_date = st.date_input("Date", value=datetime.now().date(), key="checkin_date")
            ci_store = st.selectbox("Store/Location", COMPANY_LOCATIONS, key="checkin_store")
            ci_supplier = _picker_with_add_new(
                "Supplier", _distinct_check_in_suppliers(_supabase_client=supabase_client), "checkin_supplier"
            )
        with col2:
            ci_invoice = st.text_input("Invoice / LPO No. (optional)", key="checkin_invoice")
            ci_temp = st.text_input("Temperature at receipt (optional, applies to this whole delivery)", key="checkin_temp")
            ci_coa = st.text_input("COA reference (optional, applies to this whole delivery)", key="checkin_coa")

        ci_items_df = st.data_editor(
            pd.DataFrame({"item_name": [None], "quantity": [0.0], "unit_price": [0.0], "batch_no": [""]}),
            num_rows="dynamic",
            column_config={
                "item_name": item_col_config,
                "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0, required=True),
                "unit_price": st.column_config.NumberColumn("Unit Price (KSh)", min_value=0.0, step=1.0),
                "batch_no": st.column_config.TextColumn("Batch No."),
            },
            use_container_width=True,
            hide_index=True,
            key="checkin_items_editor",
        )

        ci_received_by = st.text_input("Received By", key="checkin_received_by")
        ci_confirmed_by = st.text_input("Confirmed By (optional)", key="checkin_confirmed_by")
        ci_notes = st.text_input("Notes (optional)", key="checkin_notes")

        if st.form_submit_button("📥 Record Check-In", type="primary"):
            valid_items = ci_items_df[
                ci_items_df["item_name"].notna()
                & (ci_items_df["item_name"].astype(str).str.strip() != "")
                & (ci_items_df["quantity"] > 0)
            ]
            if valid_items.empty:
                st.error("Add at least one item with a quantity greater than 0.")
            elif not ci_store:
                st.error("Store/Location is required.")
            elif not ci_received_by.strip():
                st.error("Enter who received this.")
            else:
                for _, item_row in valid_items.iterrows():
                    item_details = (ctx.inventory_items or {}).get(item_row["item_name"], {})
                    record_checkin(
                        checkin_date=ci_date, item_name=item_row["item_name"], quantity=item_row["quantity"],
                        unit=item_details.get('unit', 'kg'), item_category=item_details.get('category', ''),
                        unit_price=item_row.get("unit_price", 0.0) or 0.0, supplier=ci_supplier,
                        store=ci_store, batch_no=str(item_row.get("batch_no") or "").strip(),
                        temperature=ci_temp.strip(), coa=ci_coa.strip(), invoice_lpo=ci_invoice.strip(),
                        received_by=ci_received_by.strip(), confirmed_by=ci_confirmed_by.strip(),
                        notes=ci_notes.strip(), created_by=ci_received_by.strip(),
                        supabase_client=supabase_client,
                    )
                st.success(f"✅ Recorded {len(valid_items)} item(s) checked in on {ci_date.strftime('%Y-%m-%d')}.")
                _distinct_check_in_suppliers.clear()
                st.rerun()

    with st.expander("📋 View Recorded Check-Ins (this app only — full history is still on Stock Movements → Check-Ins)", expanded=False):
        recent_checkins = get_checkins(supabase_client=supabase_client)
        if recent_checkins:
            st.dataframe(pd.DataFrame(recent_checkins), use_container_width=True, height=300, hide_index=True)
        else:
            st.info("No check-ins recorded in the app yet.")

    st.markdown("---")
    st.markdown("#### 📤 Record a Check-Out")
    st.caption("Add one row per item — everything you add here is recorded under the same date, store, and department.")
    with st.form("record_checkout_form_v2", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            co_date = st.date_input("Date", value=datetime.now().date(), key="checkout_date")
            co_store = st.selectbox("Store/Location", COMPANY_LOCATIONS, key="checkout_store")
        with col2:
            co_department = _picker_with_add_new(
                "Department", _distinct_check_out_departments(_supabase_client=supabase_client), "checkout_department"
            )
            co_issued_to = st.text_input("Issued To", key="checkout_issued_to")

        co_items_df = st.data_editor(
            pd.DataFrame({"item_name": [None], "quantity": [0.0], "batch_no": [""]}),
            num_rows="dynamic",
            column_config={
                "item_name": item_col_config,
                "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0, required=True),
                "batch_no": st.column_config.TextColumn("Batch No."),
            },
            use_container_width=True,
            hide_index=True,
            key="checkout_items_editor",
        )

        co_recorded_by = st.text_input("Recorded By", key="checkout_recorded_by")
        co_notes = st.text_input("Notes (optional)", key="checkout_notes")

        if st.form_submit_button("📤 Record Check-Out", type="primary"):
            valid_items = co_items_df[
                co_items_df["item_name"].notna()
                & (co_items_df["item_name"].astype(str).str.strip() != "")
                & (co_items_df["quantity"] > 0)
            ]
            if valid_items.empty:
                st.error("Add at least one item with a quantity greater than 0.")
            elif not co_store:
                st.error("Store/Location is required.")
            elif not co_recorded_by.strip():
                st.error("Enter who is recording this.")
            else:
                for _, item_row in valid_items.iterrows():
                    item_details = (ctx.inventory_items or {}).get(item_row["item_name"], {})
                    record_checkout(
                        checkout_date=co_date, item_name=item_row["item_name"], quantity=item_row["quantity"],
                        unit=item_details.get('unit', 'kg'), item_category=item_details.get('category', ''),
                        store=co_store, requested_by=co_issued_to.strip(), destination=co_department,
                        batch_no=str(item_row.get("batch_no") or "").strip(), notes=co_notes.strip(),
                        created_by=co_recorded_by.strip(), supabase_client=supabase_client,
                    )
                st.success(f"✅ Recorded {len(valid_items)} item(s) checked out on {co_date.strftime('%Y-%m-%d')}.")
                _distinct_check_out_departments.clear()
                st.rerun()

    with st.expander("📋 View Recorded Check-Outs", expanded=False):
        recent_checkouts = get_checkouts(supabase_client=supabase_client)
        if recent_checkouts:
            st.dataframe(pd.DataFrame(recent_checkouts), use_container_width=True, height=300, hide_index=True)
        else:
            st.info("No check-outs recorded yet.")


def _render_variance_tab(ctx: AllItemsContext) -> None:
    st.markdown("### 📊 Stock Variance")
    st.caption(
        "Opening + Check-In + Transfers In − Check-Out − Transfers Out = Expected. "
        "Physical − Expected = Variance."
    )

    supabase_client = ctx.supabase_client

    location = st.selectbox("Location", COMPANY_LOCATIONS, key="variance_location_select")

    result_df, meta = _compute_stock_variance(location, supabase_client=supabase_client)
    if result_df is None:
        st.info(meta)
        return

    st.caption(f"Comparing **{meta['previous_count']}** ({meta['previous_date']}) → "
               f"**{meta['latest_count']}** ({meta['latest_date']})")

    if meta.get('check_in_warning'):
        st.warning(f"⚠️ {meta['check_in_warning']}")

    flagged = result_df[result_df['Variance'].abs() > 0.01]
    if not flagged.empty:
        st.error(f"⚠️ {len(flagged)} item(s) show a variance at {location}.")
    st.dataframe(result_df, use_container_width=True, hide_index=True)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Download Variance Report", csv,
                        file_name=f"stock_variance_{location.replace(' ', '_').lower()}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv")    

# ============================================================
# 🔒 CHECKOUT RECONCILIATION (now also reviews transfers)
# ============================================================
def _render_checkout_reconciliation_tab(ctx: AllItemsContext, has_permission=None) -> None:
    def _can(permission) -> bool:
        return has_permission(permission) if has_permission else True

    can_review = _can(Permission.REVIEW_RECONCILIATION)
    if not can_review:
        st.warning("This section isn't available for your current role. If you feel this is a mistake, please contact your administrator.")
        return

    st.markdown("### ⚖️ Reconciliation & Oversight")

    supabase_client = ctx.supabase_client
    init_transfer_storage(supabase_client)

    all_transfers = get_transfers(supabase_client=supabase_client)
    transfers_by_id = {t["id"]: t for t in all_transfers}
    all_grns = get_goods_received_notes(supabase_client=supabase_client)
    grns_matched = [g for g in all_grns if g["receiving_status"] == "Matched"]
    grns_mismatched = [g for g in all_grns if g["receiving_status"] == "Mismatch"]
    grns_resolved = [g for g in all_grns if g["receiving_status"] == "Resolved"]

    open_alerts = get_open_alerts(supabase_client=supabase_client)
    if open_alerts:
        st.error(f"⚖️ {len(open_alerts)} item(s) disappeared from the inventory sheet between two loads — needs review.")
        with st.expander(f"⚖️ Missing Items ({len(open_alerts)})", expanded=True):
            st.caption(
                "Present in a previous successful load, now gone from the sheet "
                "entirely. This is a diffing signal, not proof of anything — a row "
                "can vanish for innocent reasons too. Worth a look, not an accusation."
            )
            for alert in open_alerts:
                st.markdown(f"**{alert['item_name']}**")
                st.caption(
                    f"Last seen: {alert.get('last_seen_quantity')} units @ "
                    f"KSh {alert.get('last_seen_price')} — as of {(alert.get('last_seen_at') or '')[:16]}. "
                    f"Detected missing: {(alert.get('detected_at') or '')[:16]}."
                )
                with st.form(f"ack_alert_form_{alert['id']}"):
                    acker = st.text_input("Reviewed by", key=f"ack_by_{alert['id']}")
                    ack_notes = st.text_area(
                        "What did you find?", key=f"ack_notes_{alert['id']}",
                        placeholder="e.g. Confirmed with warehouse — item was discontinued, sheet cleanup.",
                    )
                    if st.form_submit_button("✅ Mark Reviewed"):
                        if not acker.strip():
                            st.error("Enter who reviewed this.")
                        elif not ack_notes.strip():
                            st.error("Enter what you found before dismissing this.")
                        else:
                            acknowledge_alert(alert['id'], acker.strip(), ack_notes.strip(), supabase_client=supabase_client)
                            st.success("✅ Marked reviewed.")
                            st.rerun()
                st.markdown("---")

    st.markdown("---")
    st.markdown("#### 📤 Check-Out Oversight")
    st.caption(
        "Check-outs can't be verified against a reference number -- the paper "
        "requisition form doesn't have one. These are flags worth a look, not "
        "proof of a problem."
    )

    with st.expander("📊 Unusual Quantities (vs. each item's own history, last 30 days)"):
        unusual_df, unusual_err = _find_unusual_checkout_quantities(supabase_client=supabase_client)
        if unusual_err:
            st.info(unusual_err)
        elif unusual_df is None or unusual_df.empty:
            st.caption("Nothing flagged.")
        else:
            st.dataframe(unusual_df, use_container_width=True, hide_index=True)

    with st.expander("⚠️ Incomplete Check-Out Records"):
        incomplete_df, incomplete_note = _find_incomplete_checkout_rows(supabase_client=supabase_client)
        if incomplete_note:
            st.info(incomplete_note)
        if incomplete_df is not None and not incomplete_df.empty:
            st.dataframe(incomplete_df, use_container_width=True, hide_index=True)
        elif incomplete_df is not None:
            st.caption("No incomplete rows found.")

    with st.expander("🕵️ Shortfall Investigation — Check-Outs Behind a Variance"):
        st.caption("Pick a location; if Stock Variance shows a shortfall there, this pulls the actual check-out rows recorded in that window.")
        investigate_location = st.selectbox("Location", COMPANY_LOCATIONS, key="checkout_investigate_location")
        if st.button("🕵️ Investigate Shortfalls", key="investigate_shortfalls_btn"):
            shortfalls, sf_meta = _get_flagged_variance_checkouts(investigate_location, supabase_client=supabase_client)
            if shortfalls is None:
                st.info(sf_meta)
            elif not shortfalls:
                st.success(f"No shortfalls found for {investigate_location} in the latest comparison window.")
            else:
                st.caption(f"Comparing **{sf_meta['previous_count']}** ({sf_meta['previous_date']}) → **{sf_meta['latest_count']}** ({sf_meta['latest_date']})")
                for item_result in shortfalls:
                    st.markdown(
                        f"**{item_result['item']}** — Physical {item_result['physical']}, "
                        f"Expected {item_result['expected']:.1f}, Variance {item_result['variance']:.1f}"
                    )
                    if item_result['checkout_rows'].empty:
                        st.caption("No check-out rows found for this item in this window at this location.")
                    else:
                        st.dataframe(item_result['checkout_rows'], use_container_width=True, hide_index=True)
                    st.markdown("---")

    st.markdown("---")
    m1, m2, m3 = st.columns(3)
    m1.metric("↔️ GRNs Matched", len(grns_matched))
    m2.metric("↔️ GRNs Mismatched", len(grns_mismatched))
    m3.metric("↔️ GRNs Resolved", len(grns_resolved))

    st.markdown("---")
    st.markdown("#### 🔁 Goods Received Notes Awaiting Reconciliation")
    st.caption(
        "Each GRN is a separate receiving document linked back to the Transfer it's "
        "receiving against. A mismatch means the quantity received didn't match what "
        "the Transfer said was sent."
    )

    if grns_mismatched:
        st.error(f"⚠️ {len(grns_mismatched)} GRN line(s) mismatched — needs investigation.")

    with st.expander(f"🚫 Mismatched ({len(grns_mismatched)})"):
        if grns_mismatched:
            for g in grns_mismatched:
                tr = transfers_by_id.get(g["transfer_item_id"], {})
                st.markdown(f"**{g['grn_number']}** — {g['item_name']} (receiving against **{g['transfer_ref_number']}**)")
                st.caption(
                    f"Issued: {g['quantity_expected']} {g['unit']} — {tr.get('from_location', '?')} → {tr.get('to_location', g['receiving_store'])}, "
                    f"dispatched by {tr.get('issued_by') or '—'}  \n"
                    f"Received: {g['quantity_received']} {g['unit']} at {g['receiving_store']} by {g.get('received_by') or '—'}"
                )
                with st.form(f"resolve_grn_form_{g['id']}"):
                    resolver = st.text_input("Resolved by", key=f"resolver_{g['id']}")
                    resolution_notes = st.text_area(
                        "What happened / how was this resolved?",
                        key=f"resolution_notes_{g['id']}",
                        placeholder="e.g. Recounted — 1kg was left on the truck, delivered separately.",
                    )
                    if st.form_submit_button("✅ Mark Resolved"):
                        if not resolver.strip():
                            st.error("Enter who resolved this before submitting.")
                        elif not resolution_notes.strip():
                            st.error("Enter a resolution note before submitting.")
                        else:
                            ok = resolve_grn_mismatch(
                                grn_id=g["id"], resolved_by=resolver.strip(),
                                resolution_notes=resolution_notes.strip(),
                                supabase_client=supabase_client,
                            )
                            if ok:
                                st.success(f"✅ {g['grn_number']} marked resolved.")
                            else:
                                st.error(f"⚠️ Could not mark {g['grn_number']} resolved — the record may no longer exist.")
                            st.rerun()
                st.markdown("---")
        else:
            st.caption("None.")

    with st.expander(f"✅ Resolved ({len(grns_resolved)})"):
        if grns_resolved:
            for g in grns_resolved:
                st.markdown(f"**{g['grn_number']}** — {g['item_name']} (receiving against **{g['transfer_ref_number']}**)")
                st.caption(f"Resolved by {g.get('resolved_by') or '—'} on {(g.get('resolved_at') or '')[:10]}")
                st.caption(g.get('reconciliation_notes', ''))
                st.markdown("---")
        else:
            st.caption("None yet.")

    with st.expander(f"✅ Matched ({len(grns_matched)})"):
        if grns_matched:
            df = pd.DataFrame(grns_matched)[
                ["grn_number", "transfer_ref_number", "item_name", "receiving_store",
                 "quantity_expected", "quantity_received", "received_by", "receiving_date"]
            ]
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Download Matched GRNs CSV", csv,
                                file_name=f"matched_grns_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime="text/csv")
        else:
            st.caption("None yet.")

    st.markdown("---")
    st.markdown("#### 📅 Transfer & Receiving Trends Report")
    st.caption("Pull every Transfer in a date range, with its linked GRN (if received), for trend review.")
    tcol1, tcol2, tcol3 = st.columns([1, 1, 1])
    with tcol1:
        trend_start = st.date_input("From", key="transfer_trend_start")
    with tcol2:
        trend_end = st.date_input("To", value=datetime.now().date(), key="transfer_trend_end")
    with tcol3:
        st.write("")
        st.write("")
        generate_trend = st.button("📊 Generate Report")

    if generate_trend:
        start_dt = pd.to_datetime(trend_start)
        end_dt = pd.to_datetime(trend_end)
        window = [
            t for t in all_transfers
            if pd.notna(pd.to_datetime(t.get('transfer_date'), errors='coerce'))
            and start_dt <= pd.to_datetime(t.get('transfer_date'), errors='coerce') <= end_dt
        ]
        if not window:
            st.info("No transfers found in that date range.")
        else:
            grns_by_transfer_item = {}
            for g in all_grns:
                grns_by_transfer_item.setdefault(g["transfer_item_id"], []).append(g)

            trend_rows = []
            for t in window:
                matching_grns = grns_by_transfer_item.get(t["id"], [])
                if matching_grns:
                    for g in matching_grns:
                        trend_rows.append({
                            "transfer_ref_number": t["transfer_ref_number"],
                            "transfer_date": t["transfer_date"],
                            "item_name": t["item_name"],
                            "from_location": t["from_location"],
                            "to_location": t["to_location"],
                            "quantity_issued": t["quantity_issued"],
                            "requested_by": t.get("requested_by"),
                            "approved_by": t.get("approved_by"),
                            "dispatched_by": t.get("issued_by"),
                            "grn_number": g["grn_number"],
                            "quantity_received": g["quantity_received"],
                            "receiving_status": g["receiving_status"],
                            "received_by": g.get("received_by"),
                            "receiving_date": g.get("receiving_date"),
                        })
                else:
                    trend_rows.append({
                        "transfer_ref_number": t["transfer_ref_number"],
                        "transfer_date": t["transfer_date"],
                        "item_name": t["item_name"],
                        "from_location": t["from_location"],
                        "to_location": t["to_location"],
                        "quantity_issued": t["quantity_issued"],
                        "requested_by": t.get("requested_by"),
                        "approved_by": t.get("approved_by"),
                        "dispatched_by": t.get("issued_by"),
                        "grn_number": None,
                        "quantity_received": None,
                        "receiving_status": "Not yet received",
                        "received_by": None,
                        "receiving_date": None,
                    })

            trend_df = pd.DataFrame(trend_rows)
            st.dataframe(trend_df, use_container_width=True, hide_index=True)
            csv = trend_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Download Transfer & Receiving Trends CSV", csv,
                file_name=f"transfer_trends_{trend_start}_{trend_end}.csv",
                mime="text/csv",
            )


# ============================================================
# 🔁 TRANSFERS -- ordinary send/receive. Sending creates a Transfer
# (TR-XXXX); receiving creates a separate, linked Goods Received Note
# (GRN-XXXX) -- never a new Transfer number. The comparison happens
# silently in create_grn() and only ever surfaces in the restricted
# section of Checkout Reconciliation above.
# ============================================================
from app.core.locations import COMPANY_LOCATIONS

_TRANSFER_LOCATIONS = COMPANY_LOCATIONS + ["Other (type below)"]


def _location_picker(label: str, key: str) -> str:
    choice = st.selectbox(label, _TRANSFER_LOCATIONS, key=f"{key}_select")
    if choice == "Other (type below)":
        return st.text_input(f"{label} — specify", key=f"{key}_custom").strip()
    return choice


def _render_transfer_reconciliation_tab(ctx: AllItemsContext) -> None:
    st.markdown("### 🔁 Transfers")

    supabase_client = ctx.supabase_client
    init_transfer_storage(supabase_client)

    last_sent = st.session_state.get("_last_sent_transfer")
    if last_sent:
        st.success(
            f"✅ Transfer sent. Reference: **{last_sent['ref']}** "
            f"({last_sent['count']} item{'s' if last_sent['count'] != 1 else ''}) "
            f"— write this on the transfer note."
        )
        if st.button("Got it, dismiss", key="dismiss_transfer_banner"):
            del st.session_state["_last_sent_transfer"]
            st.rerun()

    last_grn = st.session_state.get("_last_grn")
    if last_grn:
        st.success(
            f"✅ Receipt recorded. Reference: **{last_grn['grn']}** "
            f"(receiving against **{last_grn['transfer_ref']}**)."
        )
        if st.button("Got it, dismiss", key="dismiss_grn_banner"):
            del st.session_state["_last_grn"]
            st.rerun()

    pending_lines = get_pending_transfer_lines(supabase_client=supabase_client)
    st.metric("⏳ Awaiting Receipt", len(pending_lines))

    st.markdown("---")
    st.markdown("#### 📤 Send a Transfer")
    st.caption("Add one row per item — everything you add here ships under one shared reference number.")
    item_options = sorted((ctx.inventory_items or {}).keys())

    with st.form("record_transfer_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            transfer_date = st.date_input("Transfer Date", value=datetime.now().date())
            from_location = _location_picker("From", "from_loc")
            requested_by = st.text_input("Requested By (optional)")
        with col2:
            to_location = _location_picker("To", "to_loc")
            approved_by = st.text_input("Approved By (optional)")
            dispatched_by = st.text_input("Dispatched By")

        if item_options:
            item_col_config = st.column_config.SelectboxColumn("Item", options=item_options, required=True)
        else:
            item_col_config = st.column_config.TextColumn("Item", required=True)

        items_df = st.data_editor(
            pd.DataFrame({"item_name": [None], "quantity_issued": [0.0], "unit": ["kg"]}),
            num_rows="dynamic",
            column_config={
                "item_name": item_col_config,
                "quantity_issued": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0, required=True),
                "unit": st.column_config.TextColumn("Unit", default="kg"),
            },
            use_container_width=True,
            hide_index=True,
            key="transfer_items_editor",
        )

        issue_notes = st.text_input("Notes (optional)")

        if st.form_submit_button("📤 Send", type="primary"):
            valid_items = items_df[
                items_df["item_name"].notna()
                & (items_df["item_name"].astype(str).str.strip() != "")
                & (items_df["quantity_issued"] > 0)
            ]
            if valid_items.empty:
                st.error("Add at least one item with a quantity greater than 0.")
            elif not from_location or not to_location:
                st.error("Both From and To locations are required.")
            elif not dispatched_by.strip():
                st.error("Dispatched By is required.")
            else:
                ref_number, new_ids = record_transfer_batch(
                    transfer_date=transfer_date,
                    items=valid_items.to_dict("records"),
                    from_location=from_location, to_location=to_location,
                    requested_by=requested_by.strip(), approved_by=approved_by.strip(),
                    dispatched_by=dispatched_by.strip(), issue_notes=issue_notes,
                    supabase_client=supabase_client,
                )
                st.session_state["_last_sent_transfer"] = {"ref": ref_number, "count": len(new_ids)}
                st.rerun()

    st.markdown("---")
    st.markdown("#### 📥 Receive a Transfer")
    if not pending_lines:
        st.info("Nothing pending right now.")
    else:
        pending_by_ref = {}
        for t in pending_lines:
            pending_by_ref.setdefault(t["transfer_ref_number"], []).append(t)

        for ref, items_in_ref in pending_by_ref.items():
            first = items_in_ref[0]
            with st.container():
                st.markdown(
                    f"**{ref}** — {first['from_location']} → {first['to_location']} "
                    f"— {first['transfer_date']} — dispatched by {first.get('issued_by') or '—'}"
                )
                if first.get("issue_notes"):
                    st.caption(f"Notes: {first['issue_notes']}")

                with st.form(f"receive_ref_form_{ref}"):
                    rc1, rc2, rc3 = st.columns(3)
                    with rc1:
                        default_idx = (
                            COMPANY_LOCATIONS.index(first['to_location'])
                            if first['to_location'] in COMPANY_LOCATIONS else 0
                        )
                        receiving_store = st.selectbox(
                            "Receiving Store", COMPANY_LOCATIONS,
                            index=default_idx, key=f"recv_store_{ref}",
                        )
                    with rc2:
                        receiving_date = st.date_input("Receiving Date", value=datetime.now().date(), key=f"recv_date_{ref}")
                    with rc3:
                        received_by = st.text_input("Received By", key=f"recv_by_{ref}")

                    received_qtys = {}
                    for t in items_in_ref:
                        received_qtys[t["id"]] = st.number_input(
                            f"{t['item_name']} — Quantity Received ({t['unit']})",
                            min_value=0.0, step=1.0, key=f"recv_qty_{t['id']}",
                        )
                    submitted = st.form_submit_button("📥 Record Receipt (creates a GRN)")

                if submitted:
                    if not received_by.strip():
                        st.error("Enter your name before submitting.")
                    else:
                        item_receipts = [
                            {
                                "transfer_item_id": t["id"],
                                "item_name": t["item_name"],
                                "quantity_expected": t["quantity_issued"],
                                "quantity_received": received_qtys[t["id"]],
                                "unit": t["unit"],
                            }
                            for t in items_in_ref
                        ]
                        grn_number, _ = create_grn(
                            transfer_ref_number=ref, receiving_store=receiving_store,
                            receiving_date=receiving_date, received_by=received_by.strip(),
                            item_receipts=item_receipts, supabase_client=supabase_client,
                        )
                        st.session_state["_last_grn"] = {"grn": grn_number, "transfer_ref": ref}
                        st.rerun()

                st.markdown("---")
