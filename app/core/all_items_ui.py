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
from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from datetime import datetime
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from app.core.performance import LazyLoader, compress_dataframe
from core.error_handling import logger
from app.core.google_sheet_reader import GoogleSheetReader
from app.core.advanced_analytics import create_advanced_analytics_tab
from app.core.forecasting import create_ensemble_forecast
from app.core.eoq import calculate_eoq_costs
from app.core.demand_utils import (
    compute_daily_demand_for_item, detect_column, is_junk_value,
    build_code_to_label_map, ITEM_NAME_KEYWORDS, ITEM_LABEL_KEYWORDS,
)
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
from app.core.rbac import ALL_ITEMS_TAB_REQUIREMENTS
from app.core.checkout_reconciliation import (
    init_checkout_reconciliation_storage, record_checkout, get_checkouts, reconcile_checkout,
)

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
        _render_inventory_tab()
    elif active_tab == "📊 Stock Movements":
        _render_stock_movements_tab(ctx)
    elif active_tab == "📈 All Items Analytics":
        _render_analytics_tab(ctx)
    elif active_tab == "🖼️ Visual Inventory":
        _render_visual_inventory_tab(ctx)
    elif active_tab == "🤖 Advanced Analytics":
        _render_advanced_analytics_tab(ctx)
    elif active_tab == "🔄 JIT Purchasing":
        render_jit_purchasing_tab(ctx.constants)
    elif active_tab == "🔒 Checkout Reconciliation":
        _render_checkout_reconciliation_tab(ctx)


# ============================================================
# FULLY PORTED — 📦 Inventory
# ============================================================
def _render_inventory_tab() -> None:
    st.markdown("## 📦 Company Inventory (Google Sheets)")

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
        st.caption(
            f"Data source: Google Sheets | Updated: "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    st.divider()

    @st.cache_data(ttl=300)
    def load_full_inventory_details():
        """Renamed from load_inventory_data() — that name collided with the
        module-level load_inventory_data() used for the KPI dashboard in
        main(). Same name, different return shape; this scoping only
        mattered when this tab lived inline in main.py — kept the rename
        here anyway for continuity with the original diff history."""
        try:
            gsheet = GoogleSheetReader()

            if gsheet.authenticate():
                stock = gsheet.get_stock_with_pricing()
                current = gsheet.get_current_stock()
                low = gsheet.get_low_stock_items()

                # Count categories
                category_count = (
                    stock['ITEM_CATEGORY'].nunique()
                    if 'ITEM_CATEGORY' in stock.columns
                    else 0
                )

                return stock, current, low, category_count

        except Exception as e:
            st.error(f"Inventory loading error: {e}")

        # Fallback if authentication fails
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            0
        )

    with st.spinner("📊Loading inventory data..."):
        tab_stock_df, tab_current_df, tab_low_df, tab_category_count = load_full_inventory_details()

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

                        styled_cycle_df = cycle_df.style.applymap(style_priority, subset=['Priority'])

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

                        styled_replenishment = replenishment_df.style.applymap(
                            style_abc_class, subset=['ABC Class']
                        )

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
        st.info(
            "📊 No inventory data found. "
            "Please check your Google Sheets connection."
        )


# ============================================================
# FULLY PORTED — 📊 Stock Movements (including the nested Stock Take sub-tab)
# ============================================================
def _render_stock_movements_tab(ctx: AllItemsContext) -> None:
    st.markdown("## 📊 Stock Movements & Stock Take")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Refresh Movements", use_container_width=True):
            st.cache_data.clear()
    with col2:
        st.caption(f"Data source: Google Sheets | Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    st.divider()

    @st.cache_data(ttl=300, show_spinner=False)
    def load_movement_data():
        gsheet = GoogleSheetReader()
        if gsheet.authenticate():
            check_in = gsheet.get_check_in()
            check_out = gsheet.get_check_out()
            current_stock = gsheet.get_current_stock()
            return check_in, check_out, current_stock
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
        with st.expander("📋 View Check-In Records", expanded=False):
            if not check_in_df.empty:
                st.dataframe(check_in_df, use_container_width=True, height=400)
                csv = check_in_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Check-Ins CSV",
                    data=csv,
                    file_name=f"check_ins_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No check-in records found.")

    elif active_subtab == "📤 Check-Outs":
        st.markdown("### 📤 Check-Out Records")
        with st.expander("📋 View Check-Out Records", expanded=False):
            if not check_out_df.empty:
                st.dataframe(check_out_df, use_container_width=True, height=400)
                csv = check_out_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Check-Outs CSV",
                    data=csv,
                    file_name=f"check_outs_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.info("No check-out records found.")

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
            stock_take_interface(ctx.inventory_items)
        else:
            st.warning("⚠️ No inventory data available. Please load inventory from Google Sheets first.")
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
    st.markdown("Comprehensive analysis for all inventory items using Google Sheets data")

    # ============================================================
    # 🎯 LAZY LOADING FOR HEAVY ANALYTICS DATA
    # ============================================================

    def load_heavy_analytics_data():
        """Load heavy analytics data only when needed."""
        try:
            logger.info("Loading heavy analytics data...")
            gsheet = GoogleSheetReader()
            if gsheet.authenticate():
                stock = gsheet.get_stock_with_pricing()

                # ============================================================
                # 🚀 COMPRESS DATAFRAME TO REDUCE MEMORY USAGE
                # ============================================================
                if not stock.empty:
                    original_memory = stock.memory_usage(deep=True).sum() / 1024 / 1024
                    stock = compress_dataframe(stock)
                    compressed_memory = stock.memory_usage(deep=True).sum() / 1024 / 1024
                    reduction = ((original_memory - compressed_memory) / original_memory) * 100
                    logger.info(f"💾 Analytics stock compressed: {original_memory:.1f}MB → {compressed_memory:.1f}MB (↓{reduction:.0f}%)")

                check_in = gsheet.get_check_in()
                check_out = gsheet.get_check_out()
                current_stock = gsheet.get_current_stock()

                # Compress the other DataFrames too
                if not check_in.empty:
                    check_in = compress_dataframe(check_in)
                if not check_out.empty:
                    check_out = compress_dataframe(check_out)
                if not current_stock.empty:
                    current_stock = compress_dataframe(current_stock)

                logger.info(f"Analytics data loaded: {len(stock)} items")
                return stock, check_in, check_out, current_stock
            else:
                logger.warning("Google Sheets authentication failed for analytics")
        except Exception as e:
            logger.error(f"Error loading analytics data: {e}")
            st.error(f"❌ Error loading analytics data: {str(e)}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # Use lazy loader with caching
    analytics_loader = LazyLoader(
        load_heavy_analytics_data,
        cache_ttl=600,  # 10 minute cache
        key_prefix="analytics"
    )

    # Show load status
    if not analytics_loader.is_loaded:
        st.info("💡 Click the button below to load analytics data. This may take a moment.")

    # Render load button (or show loaded status)
    analytics_loader.render_load_button("📊 Load Analytics Data")

    # ============================================================
    # 🎯 DISPLAY ANALYTICS (ONLY WHEN LOADED)
    # ============================================================
    if analytics_loader.is_loaded:
        analytics_stock_df, check_in_df, check_out_df, current_stock_df = analytics_loader.data

        # Verify data was loaded successfully
        if analytics_stock_df is None or analytics_stock_df.empty:
            st.warning("⚠️ No analytics data available. Please check your Google Sheets connection.")
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
                        "Defaults are general-inventory assumptions, independent of the "
                        "Dry Ice tier's transport cost / holding rate — adjust freely."
                    )

                    if st.button("📊 Run Cost Analysis", key="run_cost_analysis"):
                        cost_results = []
                        for _, row in cost_df.iterrows():
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
                                        'Item': row.get('ITEM_NAME', 'Unknown'),
                                        'Category': row.get('ITEM_CATEGORY', 'Uncategorized'),
                                        'Current Stock': row['QUANTITY'],
                                        'Unit Price': row['UNIT PRICE'],
                                        'Annual Demand': row['ANNUAL_DEMAND'],
                                        'EOQ': eoq_result.eoq,
                                        'Current Cost': eoq_result.current_total_cost,
                                        'Optimal Cost': eoq_result.optimal_total_cost,
                                        'Potential Savings': eoq_result.potential_savings,
                                    })
                            except:
                                pass

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
    else:
        st.info("💡 Click 'Load Analytics Data' to view the analysis.")


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


# ============================================================
# 🔒 CHECKOUT RECONCILIATION
# ============================================================
def _render_checkout_reconciliation_tab(ctx: AllItemsContext) -> None:
    st.markdown("### 🔒 Checkout Reconciliation")
    st.caption(
        "Security control: every check-out recorded here starts as **Pending** and is "
        "excluded from confirmed-usage figures until an authorized user reconciles it "
        "against the physical dispatch slip / gate pass number. A mismatch or missing "
        "slip should be marked **Blocked**, not Reconciled."
    )

    supabase_client = None
    try:
        from app.core.supabase_client import init_supabase
        supabase_client = init_supabase()
    except Exception:
        pass
    init_checkout_reconciliation_storage(supabase_client)

    all_checkouts = get_checkouts(supabase_client=supabase_client)
    pending = [c for c in all_checkouts if c["status"] == "Pending"]
    reconciled = [c for c in all_checkouts if c["status"] == "Reconciled"]
    blocked = [c for c in all_checkouts if c["status"] == "Blocked"]

    m1, m2, m3 = st.columns(3)
    m1.metric("⏳ Pending", len(pending))
    m2.metric("✅ Reconciled", len(reconciled))
    m3.metric("🚫 Blocked", len(blocked))

    st.markdown("---")
    st.markdown("#### 📝 Record a Check-Out")
    item_options = sorted((ctx.inventory_items or {}).keys())
    with st.form("record_checkout_form"):
        col1, col2 = st.columns(2)
        with col1:
            checkout_date = st.date_input("Checkout Date", value=datetime.now().date())
            if item_options:
                item_name = st.selectbox("Item", item_options)
            else:
                item_name = st.text_input("Item")
            quantity = st.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
            unit = st.text_input("Unit", value="kg")
        with col2:
            requested_by = st.text_input("Requested By")
            destination = st.text_input("Destination (optional)")
            dispatch_slip_number = st.text_input(
                "Dispatch Slip / Gate Pass Number",
                help="Required — this is the physical reference this check-out will be reconciled against.",
            )
        notes = st.text_input("Notes (optional)")

        if st.form_submit_button("📤 Record Check-Out", type="primary"):
            if not item_name:
                st.error("Select or enter an item.")
            elif quantity <= 0:
                st.error("Enter a quantity greater than 0.")
            elif not dispatch_slip_number.strip():
                st.error("Dispatch slip / gate pass number is required.")
            else:
                new_id = record_checkout(
                    checkout_date=checkout_date, item_name=item_name, quantity=quantity,
                    dispatch_slip_number=dispatch_slip_number.strip(), unit=unit,
                    requested_by=requested_by, destination=destination, notes=notes,
                    supabase_client=supabase_client,
                )
                st.success(f"✅ Recorded as **Pending** (#{new_id}). It won't count as "
                           f"confirmed usage until reconciled below.")
                st.rerun()

    st.markdown("---")
    st.markdown("#### ⏳ Pending Reconciliation")
    if not pending:
        st.info("Nothing pending — every check-out is reconciled.")
    else:
        for co in pending:
            with st.container():
                st.markdown(
                    f"**#{co['id']}** — {co['item_name']} — {co['quantity']:.1f} {co.get('unit', '')} "
                    f"— slip **{co['dispatch_slip_number']}** — {co['checkout_date']} "
                    f"— requested by {co.get('requested_by') or '—'}"
                )
                if co.get("destination"):
                    st.caption(f"Destination: {co['destination']}")
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    reconciler = st.text_input(
                        "Reconciled by", key=f"reconciler_{co['id']}", label_visibility="collapsed",
                        placeholder="Your name",
                    )
                with c2:
                    if st.button("✅ Verified — Reconcile", key=f"verify_{co['id']}"):
                        if not reconciler.strip():
                            st.error("Enter your name before reconciling.")
                        else:
                            reconcile_checkout(co["id"], reconciler.strip(), verified=True,
                                                supabase_client=supabase_client)
                            st.success(f"✅ #{co['id']} reconciled.")
                            st.rerun()
                with c3:
                    if st.button("🚫 Could Not Verify — Block", key=f"block_{co['id']}"):
                        if not reconciler.strip():
                            st.error("Enter your name before blocking.")
                        else:
                            reconcile_checkout(co["id"], reconciler.strip(), verified=False,
                                                supabase_client=supabase_client)
                            st.warning(f"🚫 #{co['id']} blocked — excluded from confirmed usage.")
                            st.rerun()
                st.markdown("---")

    with st.expander(f"🚫 Blocked ({len(blocked)}) — need correction or re-verification"):
        if blocked:
            st.dataframe(pd.DataFrame(blocked)[
                ["id", "checkout_date", "item_name", "quantity", "dispatch_slip_number",
                 "reconciled_by", "reconciliation_notes"]
            ], use_container_width=True, hide_index=True)
        else:
            st.caption("None.")

    with st.expander(f"✅ Reconciled history ({len(reconciled)})"):
        if reconciled:
            df = pd.DataFrame(reconciled)[
                ["id", "checkout_date", "item_name", "quantity", "dispatch_slip_number",
                 "reconciled_by", "reconciled_at"]
            ]
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Download Reconciled CSV", csv,
                                file_name=f"reconciled_checkouts_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime="text/csv")
        else:
            st.caption("None yet.")