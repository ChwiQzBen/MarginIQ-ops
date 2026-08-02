"""
app/core/visual_inventory.py
=============================
Visual/AI display helpers for inventory, extracted from main.py.
Powers app.core.all_items_ui's 🖼️ Visual Inventory tab.

get_sample_inventory_data() also lives here as a shared fallback dataset —
app.core.stock_take imports it from this module rather than duplicating it.

All functions are pure display/calc helpers — they take inventory_items
(and a few other plain params) and render or return data, with no
main.py-specific state. Kept in the same relative order as main.py for
easy diffing against the original.
"""
import pandas as pd
import streamlit as st
import textwrap

def inventory_filters(items):
    """
    Add filter controls for the inventory grid
    """
    # Get unique categories
    categories = ['All'] + sorted(set(
        details.get('category', 'Uncategorized')
        for details in items.values()
    ))

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        search = st.text_input(
            "🔍 Search Items",
            placeholder="Type item name...",
            key="inventory_search"
        )

    with col2:
        category_filter = st.selectbox(
            "📂 Category",
            categories,
            key="inventory_category_filter"
        )

    with col3:
        show_low_stock = st.checkbox(
            "⚠️ Low Stock Only",
            key="inventory_low_stock_filter"
        )

    return search, category_filter, show_low_stock


def visual_inventory_grid(items, columns=3):
    """
    Display inventory items in a visual grid like inFlow's pictorial view
    """
    if not items:
        st.info("No inventory items to display")
        return

    # Create columns for the grid
    cols = st.columns(columns)

    for idx, (item, details) in enumerate(items.items()):
        with cols[idx % columns]:
            # Determine status colors
            is_low_stock = details.get('stock', 0) < details.get('reorder', 0)
            stock_color = '#dc3545' if is_low_stock else '#28a745'
            bg_color = '#fff5f5' if is_low_stock else '#f8f9fa'

            # Calculate stock percentage for progress bar
            stock_pct = min(100, (details.get('stock', 0) / details.get('max', 1)) * 100)

            # Category badge color
            category_colors = {
                'Dry Ice': '#4fc3f7',
                'Chemicals': '#ff8a65',
                'Packaging': '#81c784',
                'Equipment': '#ffd54f',
                'Default': '#90a4ae'
            }
            cat_color = category_colors.get(details.get('category', 'Default'), '#90a4ae')

            # Build HTML content
            html_content = f"""
            <div style="
                border: 1px solid {'#ffcdd2' if is_low_stock else '#e0e0e0'};
                border-radius: 12px;
                padding: 15px 12px;
                text-align: center;
                background: {bg_color};
                box-shadow: 0 2px 8px rgba(0,0,0,0.06);
                margin-bottom: 12px;
                position: relative;
                min-height: 200px;
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            ">
                <!-- Category Badge -->
                <div style="
                    position: absolute;
                    top: 8px;
                    right: 8px;
                    background: {cat_color};
                    color: white;
                    font-size: 9px;
                    padding: 2px 10px;
                    border-radius: 12px;
                    font-weight: 600;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                ">
                    {details.get('category', 'General')}
                </div>

                <!-- Icon -->
                <div style="font-size: 32px; margin-bottom: 4px;">
                    {details.get('icon', '📦')}
                </div>

                <!-- Item Name -->
                <div style="
                    font-weight: 600;
                    font-size: 13px;
                    color: #333;
                    margin: 4px 0;
                    line-height: 1.2;
                    min-height: 32px;
                ">
                    {item[:30]}{'...' if len(item) > 30 else ''}
                </div>

                <!-- Stock Level -->
                <div style="
                    font-size: 22px;
                    font-weight: 700;
                    color: {stock_color};
                    margin: 2px 0;
                ">
                    {details.get('stock', 0):,.0f} <span style="font-size: 11px; color: #999;">{details.get('unit', 'kg')}</span>
                </div>

                <!-- Reorder Level -->
                <div style="
                    font-size: 11px;
                    color: #888;
                    margin-bottom: 6px;
                ">
                    Reorder: {details.get('reorder', 0):,.0f} {details.get('unit', 'kg')}
                </div>

                <!-- Progress Bar -->
                <div style="
                    margin: 6px 0 4px 0;
                    height: 5px;
                    background: #e9ecef;
                    border-radius: 3px;
                    overflow: hidden;
                ">
                    <div style="
                        width: {stock_pct:.1f}%;
                        height: 5px;
                        background: {stock_color};
                        border-radius: 3px;
                        transition: width 0.6s ease;
                    "></div>
                </div>

                <!-- Status Badge -->
                <div style="
                    display: inline-block;
                    margin-top: 4px;
                    padding: 2px 10px;
                    border-radius: 10px;
                    font-size: 9px;
                    font-weight: 600;
                    background: {'#ffe6e6' if is_low_stock else '#e6f4ea'};
                    color: {'#dc3545' if is_low_stock else '#1e7e34'};
                ">
                    {'⚠️ LOW STOCK' if is_low_stock else '✅ In Stock'}
                </div>
            </div>
            """

            st.markdown(textwrap.dedent(html_content), unsafe_allow_html=True)


def get_sample_inventory_data():
    """
    Get sample inventory data for testing the visual grid
    """
    return {
        "Dry Ice Block (10kg)": {
            "icon": "🧊",
            "stock": 450,
            "reorder": 200,
            "max": 600,
            "unit": "kg",
            "category": "Dry Ice",
            "location": "Warehouse A",
            "price": 146.55
        },
        "Dry Ice Pellets (5kg)": {
            "icon": "❄️",
            "stock": 320,
            "reorder": 150,
            "max": 500,
            "unit": "kg",
            "category": "Dry Ice",
            "location": "Warehouse A"
        },
        "Dry Ice Slices (2kg)": {
            "icon": "💎",
            "stock": 180,
            "reorder": 100,
            "max": 300,
            "unit": "kg",
            "category": "Dry Ice",
            "location": "Warehouse B"
        },
        "Insulated Containers": {
            "icon": "📦",
            "stock": 45,
            "reorder": 20,
            "max": 60,
            "unit": "units",
            "category": "Packaging",
            "location": "Warehouse B"
        },
        "CO2 Gas Cylinders": {
            "icon": "🛢️",
            "stock": 12,
            "reorder": 5,
            "max": 20,
            "unit": "units",
            "category": "Equipment",
            "location": "Storage Unit #1"
        },
        "Dry Ice Bags (25kg)": {
            "icon": "🎒",
            "stock": 85,
            "reorder": 40,
            "max": 150,
            "unit": "kg",
            "category": "Dry Ice",
            "location": "Warehouse A"
        },
        "Safety Gloves": {
            "icon": "🧤",
            "stock": 28,
            "reorder": 15,
            "max": 50,
            "unit": "pairs",
            "category": "Safety",
            "location": "Storage Unit #2"
        }
    }


def inventory_stats_summary(items):
    """
    Display quick summary statistics for inventory
    """
    total_items = len(items)
    total_stock = sum(details.get('stock', 0) for details in items.values())
    low_stock_items = sum(1 for details in items.values() if details.get('stock', 0) < details.get('reorder', 0))
    categories = set(details.get('category', 'Uncategorized') for details in items.values())

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        html = f"""
        <div style="
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border-radius: 12px;
            padding: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="font-size: 24px;">📦</div>
            <div style="font-size: 20px; font-weight: 700;">{total_items}</div>
            <div style="font-size: 12px; color: #888;">Total Items</div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)

    with col2:
        html = f"""
        <div style="
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border-radius: 12px;
            padding: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="font-size: 24px;">📊</div>
            <div style="font-size: 20px; font-weight: 700;">{total_stock:,.0f}</div>
            <div style="font-size: 12px; color: #888;">Total Stock (kg)</div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)

    with col3:
        color = '#dc3545' if low_stock_items > 0 else '#28a745'
        html = f"""
        <div style="
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border-radius: 12px;
            padding: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="font-size: 24px;">⚠️</div>
            <div style="font-size: 20px; font-weight: 700; color: {color};">{low_stock_items}</div>
            <div style="font-size: 12px; color: #888;">Low Stock Items</div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)

    with col4:
        html = f"""
        <div style="
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border-radius: 12px;
            padding: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="font-size: 24px;">📂</div>
            <div style="font-size: 20px; font-weight: 700;">{len(categories)}</div>
            <div style="font-size: 12px; color: #888;">Categories</div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)


def inventory_heatmap(inventory_items, title="Inventory Heat Map", columns=6):
    """
    Display an inventory heat map showing stock levels with color coding
    """
    if not inventory_items:
        st.info("No inventory items to display in heat map")
        return

    # Convert inventory_items dict to heatmap data
    heatmap_data = []
    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)
        eoq = details.get('eoq', details.get('max', stock * 2))

        # Determine status
        if stock <= 0:
            status = 'Critical'
            color = '#dc3545'  # Red
        elif stock < reorder:
            status = 'Low'
            color = '#ff9800'  # Orange
        elif stock >= reorder and stock < eoq:
            status = 'Good'
            color = '#4caf50'  # Green
        else:
            status = 'Overstocked'
            color = '#2196f3'  # Blue

        heatmap_data.append({
            'Item': item_name[:20] + ('...' if len(item_name) > 20 else ''),
            'Item_Full': item_name,
            'Stock': stock,
            'Reorder': reorder,
            'EOQ': eoq,
            'Status': status,
            'Color': color,
            'Unit': details.get('unit', 'kg'),
            'Category': details.get('category', 'Uncategorized'),
            'Stock_Percentage': min(100, (stock / eoq) * 100) if eoq > 0 else 0
        })

    # Sort items: Critical first, then Low, then Good, then Overstocked
    status_order = {'Critical': 0, 'Low': 1, 'Good': 2, 'Overstocked': 3}
    heatmap_data.sort(key=lambda x: status_order.get(x['Status'], 4))

    # Display title
    st.markdown(f"""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 15px 20px;
        margin-bottom: 15px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.2rem;
            font-weight: 600;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            🔥 {title}
        </div>
        <div style="color: #888; font-size: 13px; margin-top: 4px;">
            Color legend: 🔴 Critical | 🟠 Low Stock | 🟢 Good | 🔵 Overstocked
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Create columns for the grid
    cols = st.columns(columns)

    for idx, item in enumerate(heatmap_data):
        with cols[idx % columns]:
            # Get status icon
            status_icons = {
                'Critical': '🔴',
                'Low': '🟠',
                'Good': '🟢',
                'Overstocked': '🔵'
            }
            status_icon = status_icons.get(item['Status'], '⚪')

            stock_pct = item['Stock_Percentage']

            # Build HTML content
            html_content = f"""
            <div style="
                background: {item['Color']};
                color: white;
                padding: 12px 8px;
                border-radius: 10px;
                text-align: center;
                margin: 4px 0;
                min-height: 80px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
                cursor: pointer;
            "
            title="{item['Item_Full']} - Stock: {item['Stock']} {item['Unit']} | Reorder: {item['Reorder']} {item['Unit']}"
            >
                <!-- Status Indicator -->
                <div style="
                    position: absolute;
                    top: 4px;
                    left: 8px;
                    font-size: 12px;
                ">
                    {status_icon}
                </div>

                <!-- Item Name -->
                <div style="
                    font-size: 11px;
                    font-weight: 500;
                    opacity: 0.9;
                    margin-bottom: 4px;
                    line-height: 1.2;
                    min-height: 24px;
                ">
                    {item['Item']}
                </div>

                <!-- Stock Level -->
                <div style="
                    font-size: 20px;
                    font-weight: 700;
                    line-height: 1.2;
                ">
                    {item['Stock']:,.0f}
                </div>

                <!-- Unit -->
                <div style="
                    font-size: 9px;
                    opacity: 0.7;
                    margin-top: 1px;
                ">
                    {item['Unit']}
                </div>

                <!-- Progress Bar (Stock Level Indicator) -->
                <div style="
                    margin-top: 4px;
                    height: 3px;
                    background: rgba(255,255,255,0.3);
                    border-radius: 2px;
                    overflow: hidden;
                ">
                    <div style="
                        width: {stock_pct:.1f}%;
                        height: 3px;
                        background: rgba(255,255,255,0.8);
                        border-radius: 2px;
                        transition: width 0.6s ease;
                    "></div>
                </div>
            </div>
            """

            # st.markdown() instead of st.components.v1.html() -- same
            # per-item iframe cost issue as visual_inventory_grid() above.
            # dedent() strips the Python-source indentation so markdown
            # doesn't mistake the HTML block for an indented code block.
            st.markdown(textwrap.dedent(html_content), unsafe_allow_html=True)

    # Display summary statistics
    st.markdown("---")
    col1, col2, col3, col4, col5 = st.columns(5)

    total_items = len(heatmap_data)
    critical_items = sum(1 for item in heatmap_data if item['Status'] == 'Critical')
    low_items = sum(1 for item in heatmap_data if item['Status'] == 'Low')
    good_items = sum(1 for item in heatmap_data if item['Status'] == 'Good')
    overstocked_items = sum(1 for item in heatmap_data if item['Status'] == 'Overstocked')

    with col1:
        st.metric("📦 Total Items", total_items)
    with col2:
        st.metric("🔴 Critical", critical_items, delta=f"-{critical_items}" if critical_items > 0 else None)
    with col3:
        st.metric("🟠 Low Stock", low_items, delta=f"-{low_items}" if low_items > 0 else None)
    with col4:
        st.metric("🟢 Good", good_items)
    with col5:
        st.metric("🔵 Overstocked", overstocked_items)


def inventory_heatmap_filters(heatmap_data):
    """
    Add filter controls for the inventory heat map.
    NOTE: not currently called by any tab — the heat map section in
    _render_visual_inventory_tab builds its own inline filters instead
    (search/status/category text_input+selectboxes with their own keys).
    Kept here since it's part of the same helper family and main.py
    defined it right after inventory_heatmap(); safe to wire in later if
    you want to consolidate the inline version with this one.
    """
    # Get unique statuses and categories
    statuses = ['All'] + sorted(set(item['Status'] for item in heatmap_data))
    categories = ['All'] + sorted(set(item['Category'] for item in heatmap_data))

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        search = st.text_input(
            "🔍 Search Items",
            placeholder="Type item name...",
            key="heatmap_search"
        )

    with col2:
        status_filter = st.selectbox(
            "📊 Status",
            statuses,
            key="heatmap_status_filter"
        )

    with col3:
        category_filter = st.selectbox(
            "📂 Category",
            categories,
            key="heatmap_category_filter"
        )

    return search, status_filter, category_filter


@st.cache_data(ttl=300)
def get_replenishment_recommendations(inventory_items, daily_usage_rate=None):
    """
    Generate replenishment recommendations based on current stock levels

    Args:
        inventory_items: Dictionary with item names as keys and details as values
        daily_usage_rate: Optional daily usage rate (if not provided, will be estimated)

    Returns:
        DataFrame with replenishment recommendations
    """
    if not inventory_items:
        return pd.DataFrame()

    recommendations = []

    for item_name, details in inventory_items.items():
        current_stock = details.get('stock', 0)
        reorder_point = details.get('reorder', 0)
        eoq = details.get('eoq', details.get('max', current_stock * 2))
        max_stock = details.get('max', current_stock * 2)
        unit = details.get('unit', 'kg')

        # Estimate daily usage if not provided
        if daily_usage_rate:
            daily_usage = daily_usage_rate
        else:
            # Estimate based on reorder point and assumed lead time
            if reorder_point > 0:
                daily_usage = reorder_point / 7  # Assume 7 days lead time
            else:
                daily_usage = max(1, current_stock * 0.05)  # 5% of current stock per day

        # Check if reorder is needed
        needs_reorder = current_stock < reorder_point

        if needs_reorder:
            # Calculate days until stockout (estimated)
            stock_deficit = reorder_point - current_stock
            days_to_reorder = max(1, int(stock_deficit / daily_usage)) if daily_usage > 0 else 1

            # Calculate suggested quantity (EOQ or minimum)
            suggested_qty = max(eoq, reorder_point * 1.2)  # Order enough to cover reorder point + buffer

            # Determine urgency
            if days_to_reorder <= 3:
                urgency = 'High'
                urgency_color = '#dc3545'  # Red
                action = '⚠️ Order Immediately'
            elif days_to_reorder <= 7:
                urgency = 'Medium'
                urgency_color = '#ffc107'  # Yellow
                action = '📋 Schedule Order'
            else:
                urgency = 'Low'
                urgency_color = '#28a745'  # Green
                action = '📝 Plan Order'

            # Determine priority score (higher = more urgent)
            priority_score = 100 - (days_to_reorder * 10)  # Lower days = higher priority
            priority_score = max(0, min(100, priority_score))

            # Store the numeric value for Suggested Order (without unit)
            suggested_order_value = f"{suggested_qty:,.0f} {unit}"

            recommendations.append({
                'Item': item_name,
                'Current Stock': f"{current_stock:,.0f} {unit}",
                'Reorder Point': f"{reorder_point:,.0f} {unit}",
                'Suggested Order': f"{suggested_qty:,.0f} {unit}",  # This is for display
                'Suggested Order Value': suggested_qty,  # This is the numeric value for calculations
                'Days Until Stockout': days_to_reorder,
                'Urgency': urgency,
                'Action': action,
                'Priority Score': priority_score,
                'Category': details.get('category', 'Uncategorized')
            })

    # Sort by priority (most urgent first)
    recommendations.sort(key=lambda x: x['Priority Score'], reverse=True)

    return pd.DataFrame(recommendations)


def show_replenishment_suggestions(recommendations_df, title="🛒 Replenishment Suggestions"):
    """
    Display replenishment recommendations in a styled table

    Args:
        recommendations_df: DataFrame from get_replenishment_recommendations()
        title: Title for the section
    """
    if recommendations_df.empty:
        st.info("✅ All items are well-stocked. No replenishment needed at this time.")
        return

    # Display header with count
    urgent_count = len(recommendations_df[recommendations_df['Urgency'] == 'High'])
    medium_count = len(recommendations_df[recommendations_df['Urgency'] == 'Medium'])

    st.markdown(f"""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 15px 20px;
        margin-bottom: 15px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.2rem;
            font-weight: 600;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            {title}
        </div>
        <div style="color: #888; font-size: 13px; margin-top: 4px;">
            {len(recommendations_df)} items need attention ·
            <span style="color: #dc3545;">🔴 {urgent_count} Urgent</span> ·
            <span style="color: #ffc107;">🟡 {medium_count} Medium</span> ·
            <span style="color: #28a745;">🟢 {len(recommendations_df) - urgent_count - medium_count} Low</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Display as styled dataframe with urgency highlighting
    display_df = recommendations_df.copy()

    # Color coding function for urgency
    def color_urgency(val):
        if val == 'High':
            return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
        elif val == 'Medium':
            return 'background-color: #fff3cd; color: #856404; font-weight: bold;'
        else:
            return 'background-color: #d4edda; color: #155724;'

    # Color coding for action
    def color_action(val):
        if 'Immediately' in val:
            return 'background-color: #f8d7da;'
        elif 'Schedule' in val:
            return 'background-color: #fff3cd;'
        else:
            return 'background-color: #d4edda;'

    # Apply styling
    styled_df = display_df.style.applymap(
        color_urgency, subset=['Urgency']
    ).applymap(
        color_action, subset=['Action']
    )

    # Hide index and display
    st.dataframe(
        styled_df,
        use_container_width=True,
        height=400,
        hide_index=True,
        column_config={
            'Item': st.column_config.TextColumn('Item', width='medium'),
            'Current Stock': st.column_config.TextColumn('Current Stock', width='small'),
            'Reorder Point': st.column_config.TextColumn('Reorder Point', width='small'),
            'Suggested Order': st.column_config.TextColumn('Suggested Order', width='medium'),
            'Days Until Stockout': st.column_config.NumberColumn('Days Until Stockout', width='small'),
            'Urgency': st.column_config.TextColumn('Urgency', width='small'),
            'Action': st.column_config.TextColumn('Action', width='medium'),
            'Priority Score': st.column_config.NumberColumn('Priority', width='small'),
            'Category': st.column_config.TextColumn('Category', width='small')
        }
    )

    # Quick action buttons for urgent items
    if urgent_count > 0:
        st.markdown("---")
        st.markdown("#### ⚡ Quick Actions for Urgent Items")

        urgent_items = recommendations_df[recommendations_df['Urgency'] == 'High']

        cols = st.columns(min(3, len(urgent_items)))
        for idx, (_, item) in enumerate(urgent_items.head(3).iterrows()):
            with cols[idx % 3]:
                st.markdown(f"""
                <div style="
                    border: 1px solid #dc3545;
                    border-radius: 8px;
                    padding: 12px;
                    margin: 4px 0;
                    background: rgba(220, 53, 69, 0.05);
                ">
                    <div style="font-weight: 600; font-size: 13px; color: #721c24;">
                        {item['Item']}
                    </div>
                    <div style="font-size: 12px; color: #666;">
                        Suggested: {item['Suggested Order']}
                    </div>
                    <div style="font-size: 12px; color: #666;">
                        Days until stockout: {item['Days Until Stockout']}
                    </div>
                    <div style="margin-top: 6px;">
                        <span style="
                            background: #dc3545;
                            color: white;
                            padding: 2px 10px;
                            border-radius: 12px;
                            font-size: 10px;
                            font-weight: 600;
                        ">
                            {item['Action']}
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)


def get_replenishment_summary(recommendations_df):
    """
    Get summary statistics for replenishment recommendations

    Args:
        recommendations_df: DataFrame from get_replenishment_recommendations()

    Returns:
        Dictionary with summary statistics
    """
    if recommendations_df.empty:
        return {
            'total_items': 0,
            'urgent_count': 0,
            'medium_count': 0,
            'low_count': 0,
            'average_days': 0,
            'total_suggested_qty': 0
        }

    # Helper function to extract numeric value from strings like "100 kg", "50 units", "100 pcs"
    def extract_numeric(value):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove common unit suffixes and commas
            import re
            # Extract first number found in the string
            numbers = re.findall(r'[\d,]+\.?\d*', value.replace(',', ''))
            if numbers:
                try:
                    return float(numbers[0])
                except ValueError:
                    return 0
        return 0

    # Calculate total suggested quantity
    total_qty = 0
    for qty in recommendations_df['Suggested Order'].values:
        total_qty += extract_numeric(qty)

    return {
        'total_items': len(recommendations_df),
        'urgent_count': len(recommendations_df[recommendations_df['Urgency'] == 'High']),
        'medium_count': len(recommendations_df[recommendations_df['Urgency'] == 'Medium']),
        'low_count': len(recommendations_df[recommendations_df['Urgency'] == 'Low']),
        'average_days': recommendations_df['Days Until Stockout'].mean(),
        'total_suggested_qty': total_qty
    }


def show_replenishment_summary_cards(summary):
    """
    Display replenishment summary as metric cards
    """
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "📋 Items Needing Action",
            summary['total_items']
        )
    with col2:
        st.metric(
            "🔴 Urgent",
            summary['urgent_count'],
            delta=f"-{summary['urgent_count']}" if summary['urgent_count'] > 0 else None
        )
    with col3:
        st.metric(
            "🟡 Medium",
            summary['medium_count']
        )
    with col4:
        st.metric(
            "🟢 Low",
            summary['low_count']
        )
    with col5:
        st.metric(
            "📦 Suggested Order Volume",
            f"{summary['total_suggested_qty']:,.0f} kg"
        )


def get_incoming_orders(inventory_items=None):
    """
    Calculate expected incoming orders (items on order) based on all inventory

    Args:
        inventory_items: Dictionary with all inventory items

    Returns:
        Dictionary with total expected incoming by category and overall total
    """
    if not inventory_items:
        return {'total': 0, 'by_category': {}}

    expected_total = 0
    by_category = {}

    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)
        category = details.get('category', 'Uncategorized')
        unit = details.get('unit', 'kg')

        # If stock is below reorder point, assume an order is expected
        if stock < reorder:
            # Estimate expected quantity as the difference or EOQ
            expected_qty = max(reorder * 1.5, details.get('max', stock * 2)) - stock
            expected_total += expected_qty

            if category not in by_category:
                by_category[category] = 0
            by_category[category] += expected_qty

    return {'total': expected_total, 'by_category': by_category}


def get_committed_orders(inventory_items=None):
    """
    Calculate committed orders (orders to fulfill) based on all inventory

    Args:
        inventory_items: Dictionary with all inventory items

    Returns:
        Dictionary with total committed by category and overall total
    """
    if not inventory_items:
        return {'total': 0, 'by_category': {}}

    committed_total = 0
    by_category = {}

    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)
        category = details.get('category', 'Uncategorized')
        unit = details.get('unit', 'kg')

        # Committed is typically what's needed to fulfill upcoming orders
        # Estimate based on reorder point and current stock
        if stock < reorder:
            committed_qty = (reorder - stock) * 0.3  # 30% of deficit
            committed_total += committed_qty

            if category not in by_category:
                by_category[category] = 0
            by_category[category] += committed_qty

    return {'total': committed_total, 'by_category': by_category}


def inventory_status_dashboard(inventory_items, inventory_tracker=None):
    """
    Real-time inventory status dashboard (Katana style) for ALL inventory

    Args:
        inventory_items: Dictionary with all inventory items
        inventory_tracker: Optional InventoryTracker instance (accepted for
            signature parity with main.py's original call site — not
            actually read in the body below, same as the original)
    """
    if not inventory_items:
        st.info("No inventory data available for status dashboard")
        return

    # Calculate total stock across all items
    total_stock = sum(details.get('stock', 0) for details in inventory_items.values())
    total_items = len(inventory_items)

    # Get expected incoming orders
    expected_data = get_incoming_orders(inventory_items)
    expected_total = expected_data['total']

    # Get committed orders
    committed_data = get_committed_orders(inventory_items)
    committed_total = committed_data['total']

    # Count items by status
    low_stock_items = 0
    critical_items = 0
    overstocked_items = 0
    healthy_items = 0

    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)
        max_stock = details.get('max', stock * 2)

        if stock <= 0:
            critical_items += 1
        elif stock < reorder:
            low_stock_items += 1
        elif stock > max_stock * 1.5:
            overstocked_items += 1
        else:
            healthy_items += 1

    # Display status cards in a row
    col1, col2, col3 = st.columns(3)

    with col1:
        # Total Stock - Green
        st.markdown(f"""
        <div style="
            background: rgba(232, 245, 233, 0.9);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            padding: 16px 20px;
            border-radius: 12px;
            border-left: 5px solid #4caf50;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            transition: all 0.3s ease;
        ">
            <div style="font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">
                📦 TOTAL STOCK
            </div>
            <div style="font-size: 28px; font-weight: 700; color: #2e7d32; margin: 4px 0;">
                {total_stock:,.0f} <span style="font-size: 14px; font-weight: 400; color: #666;">units</span>
            </div>
            <div style="font-size: 12px; color: #888; margin-top: 2px;">
                Across {total_items} items
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        # Expected - Blue
        st.markdown(f"""
        <div style="
            background: rgba(227, 242, 253, 0.9);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            padding: 16px 20px;
            border-radius: 12px;
            border-left: 5px solid #2196f3;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            transition: all 0.3s ease;
        ">
            <div style="font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">
                📋 EXPECTED
            </div>
            <div style="font-size: 28px; font-weight: 700; color: #1565c0; margin: 4px 0;">
                {expected_total:,.0f} <span style="font-size: 14px; font-weight: 400; color: #666;">units</span>
            </div>
            <div style="font-size: 12px; color: #888; margin-top: 2px;">
                Incoming orders needed
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        # Committed - Orange
        st.markdown(f"""
        <div style="
            background: rgba(255, 243, 224, 0.9);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            padding: 16px 20px;
            border-radius: 12px;
            border-left: 5px solid #ff9800;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            transition: all 0.3s ease;
        ">
            <div style="font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">
                🎯 COMMITTED
            </div>
            <div style="font-size: 28px; font-weight: 700; color: #e65100; margin: 4px 0;">
                {committed_total:,.0f} <span style="font-size: 14px; font-weight: 400; color: #666;">units</span>
            </div>
            <div style="font-size: 12px; color: #888; margin-top: 2px;">
                Demand to fulfill
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Add a quick status bar showing the overall health
    st.markdown("---")

    # Calculate health metrics
    total_items_analyzed = total_items
    healthy_percentage = (healthy_items / total_items_analyzed * 100) if total_items_analyzed > 0 else 0
    warning_items = low_stock_items + critical_items

    health_color = '#4caf50' if healthy_percentage > 70 else '#ff9800' if healthy_percentage > 40 else '#f44336'
    health_status = '✅ Healthy' if healthy_percentage > 70 else '⚠️ Moderate' if healthy_percentage > 40 else '🔴 Critical'

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.markdown(f"""
        <div style="
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(8px);
            border-radius: 8px;
            padding: 10px 15px;
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="font-size: 13px; color: #888;">Inventory Health</div>
            <div style="font-size: 18px; font-weight: 600; color: {health_color};">
                {health_status}
            </div>
            <div style="
                margin-top: 4px;
                height: 4px;
                background: #eee;
                border-radius: 2px;
                overflow: hidden;
            ">
                <div style="
                    width: {healthy_percentage:.0f}%;
                    height: 4px;
                    background: {health_color};
                    border-radius: 2px;
                    transition: width 0.6s ease;
                "></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.metric("📊 Healthy Items", f"{healthy_items}/{total_items}")

    with col3:
        st.metric("⚠️ Low Stock", low_stock_items, delta=f"-{low_stock_items}" if low_stock_items > 0 else None)

    with col4:
        st.metric("🔴 Critical", critical_items, delta=f"-{critical_items}" if critical_items > 0 else None)

    with col5:
        st.metric("📦 Overstocked", overstocked_items)

    # Category breakdown (if multiple categories exist)
    categories = set(details.get('category', 'Uncategorized') for details in inventory_items.values())
    if len(categories) > 1:
        st.markdown("---")
        st.markdown("#### 📊 Category Breakdown")

        # Create category metrics
        cat_cols = st.columns(min(4, len(categories)))
        for idx, category in enumerate(sorted(categories)):
            if idx < 4:
                with cat_cols[idx]:
                    cat_items = [item for item, details in inventory_items.items()
                                if details.get('category', 'Uncategorized') == category]
                    cat_stock = sum(details.get('stock', 0) for item, details in inventory_items.items()
                                   if details.get('category', 'Uncategorized') == category)
                    cat_count = len(cat_items)

                    st.metric(
                        f"📂 {category}",
                        f"{cat_stock:,.0f} units",
                        f"{cat_count} items"
                    )


def display_recommendations(recommendations):
    """
    Display AI recommendations as styled cards
    """
    if not recommendations:
        st.info("✅ No AI recommendations at this time. All metrics look good!")
        return

    # Sort by priority
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    recommendations.sort(key=lambda x: priority_order.get(x.get('priority', 'low'), 3))

    # Display as cards in a grid (2 columns)
    cols = st.columns(min(2, len(recommendations)))

    for idx, rec in enumerate(recommendations):
        with cols[idx % 2]:
            st.markdown(f"""
            <div style="
                border-left: 4px solid {rec['color']};
                padding: 14px 16px;
                margin: 6px 0;
                background: rgba(255,255,255,0.06);
                backdrop-filter: blur(4px);
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,0.08);
                transition: all 0.3s ease;
                min-height: 120px;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
            "
            onmouseover="this.style.transform='translateX(4px)'; this.style.boxShadow='0 4px 15px rgba(0,0,0,0.1)';"
            onmouseout="this.style.transform='translateX(0)'; this.style.boxShadow='none';"
            >
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div>
                        <span style="font-size: 20px;">{rec['icon']}</span>
                        <span style="font-weight: 600; font-size: 14px; color: #333; margin-left: 8px;">{rec['title']}</span>
                    </div>
                    <span style="
                        font-size: 10px;
                        background: {rec['color']};
                        color: white;
                        padding: 2px 10px;
                        border-radius: 12px;
                        font-weight: 600;
                        text-transform: uppercase;
                    ">
                        {rec.get('priority', 'info').upper()}
                    </span>
                </div>
                <div style="font-size: 13px; color: #666; margin-top: 6px; flex: 1;">
                    {rec['desc']}
                </div>
                <div style="font-size: 12px; color: #999; margin-top: 6px;">
                    {rec['details']}
                </div>
                <div style="margin-top: 8px;">
                    <span style="
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        padding: 4px 14px;
                        border-radius: 20px;
                        font-size: 12px;
                        font-weight: 500;
                        cursor: pointer;
                        transition: all 0.3s ease;
                        display: inline-block;
                    "
                    onmouseover="this.style.transform='scale(1.05)'; this.style.boxShadow='0 2px 10px rgba(102,126,234,0.3)';"
                    onmouseout="this.style.transform='scale(1)'; this.style.boxShadow='none';"
                    onclick="this.style.transform='scale(0.95)'; setTimeout(() => this.style.transform='scale(1)', 200);"
                    >
                        {rec['action']}
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)


def ai_powered_recommendations(inventory_items, filtered_items, kpis=None):
    """
    AI-style smart recommendations based on all inventory items

    Args:
        inventory_items: Dictionary with all inventory items
        filtered_items: Filtered inventory items (for specific views)
        kpis: Optional KPI dictionary
    """
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
            🤖 AI-powered insights based on your inventory data.
            <span style="color: #dc3545;">🔴 Critical</span> |
            <span style="color: #ffc107;">🟡 Warning</span> |
            <span style="color: #28a745;">🟢 Good</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    recommendations = []

    if not inventory_items:
        recommendations.append({
            'icon': 'ℹ️',
            'title': 'No Inventory Data',
            'desc': 'Load inventory data to see AI recommendations',
            'action': '📊 Load Data',
            'details': 'Connect to Google Sheets',
            'priority': 'low',
            'color': '#90a4ae'
        })

        # Display the message
        display_recommendations(recommendations)
        return

    # 1. Low Stock Alerts - Check ALL items
    low_stock_items = []
    critical_items = []

    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)
        unit = details.get('unit', 'kg')

        if stock <= 0:
            critical_items.append({'name': item_name, 'stock': stock, 'unit': unit})
        elif stock < reorder:
            low_stock_items.append({'name': item_name, 'stock': stock, 'reorder': reorder, 'unit': unit})

    # Critical items (zero stock)
    if critical_items:
        item_list = ', '.join([f"{item['name']} ({item['stock']} {item['unit']})" for item in critical_items[:5]])
        if len(critical_items) > 5:
            item_list += f" and {len(critical_items) - 5} more"

        recommendations.append({
            'icon': '🔴',
            'title': f'⚠️ {len(critical_items)} Items Out of Stock',
            'desc': f'Critical items: {item_list}',
            'action': '🛒 Order Now',
            'details': f'These items need immediate attention',
            'priority': 'high',
            'color': '#dc3545'
        })

    # Low stock items
    if low_stock_items:
        item_list = ', '.join([f"{item['name']} ({item['stock']} {item['unit']})" for item in low_stock_items[:3]])
        if len(low_stock_items) > 3:
            item_list += f" and {len(low_stock_items) - 3} more"

        recommendations.append({
            'icon': '🟡',
            'title': f'⚠️ {len(low_stock_items)} Items Low in Stock',
            'desc': f'Items below reorder point: {item_list}',
            'action': '📋 Review Stock',
            'details': f'Consider replenishing these items',
            'priority': 'medium',
            'color': '#ffc107'
        })

    # 2. Overstocked Items
    overstocked_items = []
    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        max_stock = details.get('max', stock * 2)
        unit = details.get('unit', 'kg')

        if stock > max_stock * 1.5:  # 50% above max
            overstocked_items.append({'name': item_name, 'stock': stock, 'max': max_stock, 'unit': unit})

    if overstocked_items:
        item_list = ', '.join([f"{item['name']} ({item['stock']} {item['unit']})" for item in overstocked_items[:3]])
        if len(overstocked_items) > 3:
            item_list += f" and {len(overstocked_items) - 3} more"

        recommendations.append({
            'icon': '📦',
            'title': f'📦 {len(overstocked_items)} Items Overstocked',
            'desc': f'Items above recommended levels: {item_list}',
            'action': '📊 Review Inventory',
            'details': f'Consider reducing orders for these items',
            'priority': 'medium',
            'color': '#2196f3'
        })

    # 3. Category Analysis
    categories = {}
    for item_name, details in inventory_items.items():
        category = details.get('category', 'Uncategorized')
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)

        if category not in categories:
            categories[category] = {'total_stock': 0, 'total_reorder': 0, 'count': 0}

        categories[category]['total_stock'] += stock
        categories[category]['total_reorder'] += reorder
        categories[category]['count'] += 1

    # Find categories with low coverage
    low_coverage_categories = []
    for cat, data in categories.items():
        if data['total_reorder'] > 0:
            coverage_ratio = data['total_stock'] / data['total_reorder']
            if coverage_ratio < 1.5:
                low_coverage_categories.append({
                    'category': cat,
                    'ratio': coverage_ratio,
                    'items': data['count']
                })

    if low_coverage_categories:
        cat_list = ', '.join([f"{cat['category']} ({cat['ratio']:.1f}x)" for cat in low_coverage_categories[:3]])
        if len(low_coverage_categories) > 3:
            cat_list += f" and {len(low_coverage_categories) - 3} more"

        recommendations.append({
            'icon': '📊',
            'title': f'📊 Low Category Coverage',
            'desc': f'Categories with low stock coverage: {cat_list}',
            'action': '📋 Review Categories',
            'details': f'Target coverage ratio: 1.5x reorder level',
            'priority': 'medium',
            'color': '#ff9800'
        })

    # 4. Top 5 Most Valuable Items (by stock value)
    valuable_items = []
    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        price = details.get('price', 0)
        if price > 0 and stock > 0:
            value = stock * price
            valuable_items.append({
                'name': item_name,
                'value': value,
                'stock': stock,
                'price': price
            })

    if valuable_items:
        valuable_items.sort(key=lambda x: x['value'], reverse=True)
        top_items = valuable_items[:5]

        item_list = ', '.join([f"{item['name']} (KSh {item['value']:,.0f})" for item in top_items[:3]])
        if len(top_items) > 3:
            item_list += f" and {len(top_items) - 3} more"

        recommendations.append({
            'icon': '💰',
            'title': f'💰 Top {len(top_items)} Most Valuable Items',
            'desc': f'Highest value inventory: {item_list}',
            'action': '📊 View Details',
            'details': f'Total value of top items: KSh {sum(item["value"] for item in top_items):,.0f}',
            'priority': 'low',
            'color': '#4caf50'
        })

    # 5. Total Inventory Health Score
    total_items = len(inventory_items)
    healthy_items = 0
    warning_items = 0
    critical_items_count = 0

    for item_name, details in inventory_items.items():
        stock = details.get('stock', 0)
        reorder = details.get('reorder', 0)

        if stock <= 0:
            critical_items_count += 1
        elif stock < reorder:
            warning_items += 1
        else:
            healthy_items += 1

    health_score = (healthy_items / total_items * 100) if total_items > 0 else 0
    health_status = '✅ Healthy' if health_score > 70 else '⚠️ Moderate' if health_score > 40 else '🔴 Critical'
    health_color = '#4caf50' if health_score > 70 else '#ff9800' if health_score > 40 else '#dc3545'

    recommendations.append({
        'icon': '🏥',
        'title': f'🏥 Inventory Health: {health_score:.0f}%',
        'desc': f'{health_status} - {healthy_items}/{total_items} items well-stocked',
        'action': '📊 View Dashboard',
        'details': f'Critical: {critical_items_count} | Warning: {warning_items} | Healthy: {healthy_items}',
        'priority': 'low',
        'color': health_color
    })

    # 6. Fastest Moving Items (if we have historical data)
    if kpis and kpis.get('total_orders', 0) > 0:
        recommendations.append({
            'icon': '🚀',
            'title': '🚀 Demand Pattern Detected',
            'desc': f'Total orders: {kpis.get("total_orders", 0):,} | Avg order: {kpis.get("avg_order_size", 0):.1f} kg',
            'action': '📈 View Analysis',
            'details': f'Order frequency: {kpis.get("order_frequency", 0):.1f} orders/month',
            'priority': 'low',
            'color': '#9c27b0'
        })

    # Display recommendations
    display_recommendations(recommendations)