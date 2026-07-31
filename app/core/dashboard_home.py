"""
Dashboard home surface: KPI grid and Decision Center.

Rendered once per page load in main.py, between the sidebar and the
mode branch (All Items / BCPOS / Dry Ice). Mirrors the AllItemsContext /
DryIceContext + render_*_mode() pattern already used elsewhere in the app.
"""
from dataclasses import dataclass
from typing import Any, Optional
import streamlit as st
from app.core.theme import THEME, kpi_card

@dataclass
class DashboardContext:
    # Forecast / inventory-policy pipeline outputs (computed earlier in main())
    kpis: dict
    eoq: float
    eoq_monthly_orders: float
    safety_stock: float
    annual_transport_savings: float
    total_annual_spending: float
    current_monthly_orders: float

    # Live objects / shared state
    inventory_tracker: Any
    constants: Any  # main.py's Constants instance (TRANSPORT_COST, PRICE_PER_KG, HOLDING_RATE, LEAD_TIME_DAYS, IMPLEMENTATION_COST, ALL_ITEMS_IMPLEMENTATION_COST, SYNERGY_DISCOUNT)
    decision: Optional[dict]

def render_dashboard_home(ctx: DashboardContext) -> None:
    """Render the KPI grid and Decision Center."""

    kpis = ctx.kpis
    eoq = ctx.eoq
    eoq_monthly_orders = ctx.eoq_monthly_orders
    safety_stock = ctx.safety_stock
    annual_transport_savings = ctx.annual_transport_savings
    total_annual_spending = ctx.total_annual_spending
    current_monthly_orders = ctx.current_monthly_orders
    inventory_tracker = ctx.inventory_tracker
    constants = ctx.constants
    decision = ctx.decision

    # NOTE: stock_status used to be read from a sidebar-scoped variable of the
    # same name computed earlier in main(). That implicit reliance doesn't
    # survive extraction into a separate module, so it's computed explicitly
    # here instead (same call, same result — inventory_tracker.current_stock
    # hasn't changed between the sidebar render and this point).
    stock_status = inventory_tracker.get_stock_status()

    # ============================================================
    # 🎨 SINGLE KPI CARD - Like Sidebar Container Style
    # Calculate monthly savings and percentage
    monthly_savings = annual_transport_savings / 12
    monthly_transport_cost = (current_monthly_orders * constants.TRANSPORT_COST)
    percent_savings = (monthly_savings / monthly_transport_cost) * 100 if monthly_transport_cost > 0 else 0

    # ============================================================
    # SINGLE UNIFIED KPI CARD
    # ============================================================

    st.markdown("""
    <div style="
        border: 2px solid #667eea;
        border-radius: 16px;
        padding: 20px;
        margin: 20px 0;
        background: rgba(102, 126, 234, 0.04);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
    ">
        <!-- Card Header -->
        <div style="
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 2px solid rgba(102, 126, 234, 0.15);
        ">
            <div style="
                display: flex;
                align-items: center;
                gap: 12px;
            ">
                <span style="font-size: 28px;">📈</span>
                <span style="
                    font-size: 20px;
                    font-weight: 700;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                ">
                    Key Performance Indicators
                </span>
            </div>
            <div style="
                background: rgba(102, 126, 234, 0.1);
                padding: 4px 14px;
                border-radius: 20px;
                font-size: 11px;
                color: #667eea;
                font-weight: 600;
            ">
                Real-time
            </div>
        </div>
    """, unsafe_allow_html=True)

    # ============================================================
    # KPI GRID - 4 Columns Inside Single Card
    # ============================================================
    container_eff = kpis.get('container_utilization', 0) * 100

    if stock_status['status'] in ['Critical', 'Low Stock']:
        action_stock = f"→ Order {eoq:,.0f} kg now"
    else:
        action_stock = "→ No action needed"

    if eoq_monthly_orders > 0:
        action_orders = f"→ Target {eoq_monthly_orders:.1f}/mo (EOQ-optimal)"
    else:
        action_orders = "→ —"

    if inventory_tracker.current_stock < safety_stock * 1.2:
        action_safety = "→ Near threshold — reorder soon"
    else:
        action_safety = "→ Buffer adequate"

    avg_order = kpis.get('avg_order_size', 0)
    if avg_order > 0 and eoq > 0 and abs(avg_order - eoq) / eoq > 0.25:
        action_eoq = f"→ Align orders closer to {eoq:,.0f} kg"
    else:
        action_eoq = "→ Order sizes aligned"

    action_spending = "→ See cost breakdown below"

    if annual_transport_savings > 0:
        action_transport = "→ Implement EOQ to realize this"
    else:
        action_transport = "→ Already optimized"

    if percent_savings > 0:
        action_monthly = "→ On track — maintain policy"
    else:
        action_monthly = "→ Review order frequency"

    if container_eff < 85:
        action_container = "→ Consolidate orders to improve fill"
    else:
        action_container = "→ Fill rate optimal"

    # Row 1: Main KPIs (4 columns)
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        kpi_card("Current Stock", f"{inventory_tracker.current_stock:,.0f}", icon="📦",
                  color=stock_status['color'],
                  subtext=f"<span style='color:{stock_status['color']};font-weight:600;'>{stock_status['status']}</span>",
                  action=action_stock)
    with col2:
        kpi_card("Total Orders", f"{kpis.get('total_orders', 0):,}", icon="📋",
                  color=THEME["info"], subtext=f"{kpis.get('total_volume', 0):,.0f} kg total",
                  action=action_orders)
    with col3:
        kpi_card("Safety Stock", f"{safety_stock:,.1f}", icon="🛡️",
                  color=THEME["orange"], subtext=f"{kpis.get('order_frequency', 0):.1f} orders/mo",
                  action=action_safety)
    with col4:
        kpi_card("Economic EOQ", f"{eoq:,.1f}", icon="📦",
                  color=THEME["purple"], subtext="Optimal order size",
                  action=action_eoq)

    # Divider inside card
    st.markdown("""
    <div style="
        margin: 12px 0;
        border-top: 1px solid rgba(255,255,255,0.08);
    "></div>
    """, unsafe_allow_html=True)

    # Row 2: Financial KPIs (4 columns)
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        kpi_card("Annual Spending", f"KSh {total_annual_spending:,.0f}", icon="💰",
                  color="#e74c3c", subtext="Total cost", action=action_spending)
    with col2:
        kpi_card("Annual Transport Savings", f"KSh {annual_transport_savings:,.0f}", icon="🚀",
                  color=THEME["success"], value_color=THEME["success"],
                  subtext="From EOQ optimization", action=action_transport)
    with col3:
        delta_color = THEME["success"] if percent_savings > 0 else THEME["danger"]
        delta_arrow = "▲" if percent_savings > 0 else "▼"
        kpi_card("Monthly Savings", f"KSh {monthly_savings:,.0f}", icon="📈",
                  color=THEME["orange_dark"],
                  subtext=f"<span style='color:{delta_color};font-weight:600;'>{delta_arrow} {percent_savings:+.1f}%</span>",
                  action=action_monthly)
    with col4:
        kpi_card("Container Efficiency", f"{container_eff:.1f}%", icon="📊",
                  color=THEME["cyan"], subtext="Fill rate", action=action_container)

    # Optional: Add a progress bar for stock level at the bottom
    if eoq > 0 and safety_stock > 0:
        max_stock = eoq + safety_stock
        current_pct = min(100, (inventory_tracker.current_stock / max_stock) * 100) if max_stock > 0 else 0

        # Determine color
        if current_pct < 30:
            gauge_color = "#dc3545"
            gauge_text = "Critical"
        elif current_pct < 50:
            gauge_color = "#ffc107"
            gauge_text = "Low"
        else:
            gauge_color = "#28a745"
            gauge_text = "Healthy"

        st.markdown(f"""
        <div style="
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid rgba(255,255,255,0.08);
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <div style="font-size: 11px; color: #888; font-weight: 500;">
                    📊 Stock Level Indicator
                </div>
                <div style="font-size: 12px; font-weight: 600; color: {gauge_color};">
                    {current_pct:.0f}% - {gauge_text}
                </div>
            </div>
            <div style="
                height: 6px;
                background: rgba(255,255,255,0.1);
                border-radius: 4px;
                overflow: hidden;
            ">
                <div style="
                    width: {current_pct:.1f}%;
                    height: 6px;
                    background: linear-gradient(90deg, {gauge_color}, {gauge_color});
                    border-radius: 4px;
                    transition: width 0.8s ease;
                "></div>
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 2px;">
                <span style="font-size: 8px; color: #999;">0%</span>
                <span style="font-size: 8px; color: #999;">EOQ + Safety Stock</span>
                <span style="font-size: 8px; color: #999;">100%</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Close the card
    st.markdown("</div>", unsafe_allow_html=True)

    # 🎯 DECISION CENTER
    # ============================================================
    if decision:
        RISK_COLORS = {
            "Critical": "#dc3545",
            "High": "#ff9800",
            "Medium": "#ffc107",
            "Low": "#28a745",
        }
        decision_color = RISK_COLORS.get(decision["risk"]["level"], "#888888")
        inv = decision["inventory"]
        risk = decision["risk"]
        fin = decision["financial"]

        st.markdown(f"""
        <div style="
            border: 2px solid {decision_color};
            border-radius: 16px;
            padding: 20px;
            margin: 20px 0;
            background: {decision_color}0d;
        ">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-size:18px; font-weight:700;">🎯 Decision Center</span>
                <span style="background:{decision_color}; color:white; padding:4px 14px; border-radius:20px; font-size:12px; font-weight:600;">
                    {risk['level']} Risk ({risk['score']}/100)
                </span>
            </div>
            <div style="font-size:20px; font-weight:700; color:{decision_color}; margin-bottom:6px;">
                {inv['action']}
            </div>
            <div style="font-size:14px; color:#555; margin-bottom:10px;">
                {inv['recommendation']}
            </div>
            <div style="display:flex; gap:24px; flex-wrap:wrap; font-size:13px; color:#888; margin-bottom:12px;">
                <span>📅 Days remaining: <strong>{inv['days_remaining']}</strong></span>
                <span>📦 Recommended qty: <strong>{inv['recommended_quantity']:,.0f} kg</strong></span>
                <span>🎯 Forecast confidence: <strong>{decision['forecast_accuracy']:.0f}%</strong></span>
                <span>💰 Potential savings: <strong>KSh {fin['potential_monthly_savings']:,.0f}/mo</strong></span>
            </div>
            <div style="border-top: 1px solid rgba(0,0,0,0.08); padding-top: 10px;">
                <div style="font-size:12px; font-weight:600; color:#666; margin-bottom:4px;">Why?</div>
                {''.join(f'<div style="font-size:13px; color:#555; margin-bottom:2px;">• {reason}</div>' for reason in decision['explanation'])}
            </div>
        </div>
        """, unsafe_allow_html=True)
