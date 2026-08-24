from datetime import datetime
import streamlit as st
import traceback
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm
import sys
import os
import time
_import_start = time.time()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
from core.analyzer import DryIceAnalyzer
from core.inventory_tracker import InventoryTracker
from core.mobile_interface import MobileInterface
from core.smart_alerts import SmartAlerts
from core.predictive_maintenance import PredictiveMaintenance
from core.system_integrations import SystemIntegrations
from core.report_generator import ReportGenerator
from app.core.google_sheet_reader import GoogleSheetReader
from app.core.decision_engine import InventorySnapshot, InventoryDecisionEngine
from app.core.bcpos_ui import render_bcpos_mode
from app.core.all_items_ui import render_all_items_mode, AllItemsContext
from app.core.dry_ice_ui import render_dry_ice_mode, DryIceContext
from app.core.theme import inject_global_css
from app.core.dashboard_home import DashboardContext, render_dashboard_home
from app.core.supabase_client import init_supabase
from app.core.pdf_reports import generate_enhanced_pdf_report
from app.core.visual_inventory import get_sample_inventory_data
from app.core.forecasting import (
      create_ensemble_forecast,
      create_scenario_analysis,
      render_scenario_analysis,
      render_scenario_summary,
  )
from app.core.dry_ice_data_access import (
    fix_order_date,
    init_db,
    add_transaction_to_db,
    get_transactions_from_db,
    get_current_stock_from_db,
    update_current_stock_in_db,
    seed_historical_data,
    get_period_from_date,
    get_historical_orders_from_db,
)
from app.core.rbac import (
    Permission,
    has_permission,
    log_access_denied,
)
from core.error_handling import (
    logger,
    safe_operation,
    validate_date,
    validate_stock_sufficient,
    ServiceStatusManager,
    safe_number_input,
    DatabaseError,
    ValidationError,
)
# External imports for error handling
from requests.exceptions import Timeout, ConnectionError
from core.performance import (
    optimize_session_state,
    compress_dataframe,
    get_memory_usage
)
# Add these to your existing imports
from core.security import (
    AuthManager,
    AuditLogger,
)
from core.advanced_security import (
    TwoFactorAuth,
    rate_limited,
    PasswordReset
)
# 🔐 SECURITY AVAILABILITY FLAG
# ============================================================
SECURITY_AVAILABLE = True
import base64
def get_image_base64(image_path):
    """Convert image to base64 for embedding in HTML"""
    try:
        with open(image_path, "rb") as img_file:
            img_data = img_file.read()
            base64_encoded = base64.b64encode(img_data).decode('utf-8')
            
            # Detect image type
            if image_path.lower().endswith('.png'):
                return f"data:image/png;base64,{base64_encoded}"
            elif image_path.lower().endswith('.jpg') or image_path.lower().endswith('.jpeg'):
                return f"data:image/jpeg;base64,{base64_encoded}"
            else:
                return f"data:image/jpeg;base64,{base64_encoded}"
    except Exception as e:
        print(f"Error loading image: {e}")
        return ""


st.set_page_config(
    page_title="MarginIQ Ops Suite",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

inject_global_css()

st.markdown("""
<style>
div[data-testid="stMetric"] {
    overflow: visible !important;
}
div[data-testid="stMetricValue"] {
    font-size: clamp(0.85rem, 1.5vw, 1.6rem) !important;
    white-space: normal !important;
    overflow-wrap: break-word !important;
    overflow: visible !important;
    line-height: 1.25 !important;
}
div[data-testid="stMetricLabel"] {
    white-space: normal !important;
    overflow: visible !important;
}
</style>
""", unsafe_allow_html=True)

class Constants:
    PRICE_PER_KG = 146.55
    CONTAINER_SIZE = 150
    TRANSPORT_COST = 1741.94
    HOLDING_RATE = 0.03
    SUB_LOSS_RANGE = (1.51, 3.03)
    LEAD_TIME_DAYS = 1
    SERVICE_LEVEL = 0.95
    IMPLEMENTATION_COST = 25700  # training + SOP + signage + supervision + 20% contingency — adjustable in sidebar
    ALL_ITEMS_IMPLEMENTATION_COST = 70000  # baseline count + data cleanup + training + first-cycle supervision — adjustable in sidebar
    SYNERGY_DISCOUNT = 0.12  # combined rollout shares training/supervision overhead
    ALL_ITEMS_ORDER_COST = 500.0   # KSh per purchase order — placeholder, tune to real admin/processing cost
    ALL_ITEMS_HOLDING_RATE = 0.20  # 20%/year — typical general-inventory holding cost, not dry ice's 3%

constants = Constants()

def add_transaction_to_history(transaction_type, quantity, description, date, period):
    """Add transaction to session state and to database for a specific period"""
    transaction = {
        'date': datetime.combine(date, datetime.min.time()) if isinstance(date, type(datetime.today().date())) else date,
        'type': transaction_type,
        'quantity': quantity,
        'item': 'Dry Ice',
        'description': description,
        'notes': f"{description} - {quantity} kg"
    }

    # Add to database with the specified period
    add_transaction_to_db(transaction_type, quantity, description, date, period)

    # Update session state
    if 'transactions' not in st.session_state:
        st.session_state.transactions = []

    st.session_state.transactions.append(transaction)


# ============================================================
# 🔐 PASSWORD RESET WITH RATE LIMITING
# ============================================================

@rate_limited(max_calls=3, period=300)  # 3 password resets per 5 minutes
def request_password_reset(email):
    """
    Request a password reset with rate limiting.
    
    🔐 Rate limited to 3 attempts per 5 minutes to prevent abuse.
    
    Args:
        email: User's email address
    
    Returns:
        dict: Success status and message
    """
    try:
#         from core.advanced_security import PasswordReset
        reset_manager = PasswordReset()
        result = reset_manager.request_reset(email)
        
        # Log the request
        logger.info(f"Password reset requested for: {email}")
        
        return result
    except Exception as e:
        logger.error(f"Password reset request failed: {e}")
        return {
            'success': False, 
            'message': 'Password reset request failed. Please try again later.'
        }

def render_password_reset_form():
    """
    Render password reset form in the sidebar.
    """
    st.sidebar.markdown("""
    <div style="
        background: linear-gradient(135deg, #ff9800 0%, #f57c00 100%);
        padding: 12px 15px;
        border-radius: 8px;
        color: white;
        margin-bottom: 15px;
    ">
        <div style="font-size: 14px; font-weight: 600;">🔑 Reset Password</div>
        <div style="font-size: 12px; opacity: 0.8;">Enter your email to receive reset instructions</div>
    </div>
    """, unsafe_allow_html=True)
    
    reset_email = st.sidebar.text_input(
        "📧 Email Address", 
        placeholder="user@browns.com",
        key="reset_email_input"
    )
    
    col1, col2 = st.sidebar.columns(2)
    
    with col1:
        if st.button("📧 Send Reset Link", type="primary", use_container_width=True):
            if reset_email:
                result = request_password_reset(reset_email)
                if result.get('success'):
                    st.sidebar.success(result.get('message', 'Password reset link sent!'))
                    st.sidebar.info("📧 Check your email for reset instructions")
                    # Clear the form
                    st.session_state.reset_email_input = ""
                else:
                    st.sidebar.error(result.get('message', 'Failed to send reset link'))
            else:
                st.sidebar.warning("⚠️ Please enter your email address")
    
    with col2:
        if st.button("← Back to Login", use_container_width=True):
            st.session_state.show_password_reset = False
            st.rerun()
    
    st.sidebar.info("💡 If an account exists for that email, a reset link has been sent.")


def create_enhanced_charts(df, analyzer, kpis, forecast_data, safety_stock):
    """Create enhanced visualizations with all required parameters"""
    # 1. Order Pattern Analysis
    fig_orders = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Order Quantities Over Time", "Order Size Distribution"),
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
    )

    # Time series
    fig_orders.add_trace(
        go.Scatter(x=df['Date'], y=df['Order_Quantity_kg'],
                  mode='markers+lines', name='Order Quantity',
                  line=dict(color='#1f77b4')), row=1, col=1
    )

    # Distribution
    fig_orders.add_trace(
        go.Histogram(x=df['Order_Quantity_kg'], name='Order Size Distribution',
                    nbinsx=20, opacity=0.7), row=2, col=1
    )

    fig_orders.update_layout(height=600, showlegend=True)

    # 2. Cost Analysis Chart - Calculate costs if columns don't exist
    if 'Transport_Cost' not in df.columns:
        df['Transport_Cost'] = constants.TRANSPORT_COST  # Assuming fixed cost per order

    if 'Total_Cost' not in df.columns:
        df['Total_Cost'] = df['Order_Quantity_kg'] * constants.PRICE_PER_KG

    cost_data = pd.DataFrame({
        'Date': df['Date'],
        'Transport_Cost': df['Transport_Cost'],
        'Product_Cost': df['Total_Cost'],
        'Total_Cost': df['Transport_Cost'] + df['Total_Cost']
    })

    fig_cost_overview = px.area(cost_data, x='Date', y=['Transport_Cost', 'Product_Cost'],
                      title="Cost Breakdown Over Time",
                      color_discrete_map={
                          'Transport_Cost': '#ff6b6b',
                          'Product_Cost': '#4ecdc4'
                      })

    # 3. Forecast Chart
    fig_forecast = None
    if forecast_data is not None:
        fig_forecast = go.Figure()

        # Historical data
        fig_forecast.add_trace(go.Scatter(
            x=df['Date'], y=df['Order_Quantity_kg'],
            mode='markers+lines', name='Historical Orders',
            line=dict(color='#1f77b4')
        ))

        # Forecast
        future_data = forecast_data[forecast_data['ds'] > df['Date'].max()]
        fig_forecast.add_trace(go.Scatter(
            x=future_data['ds'], y=future_data['yhat'],
            mode='lines', name='Forecast',
            line=dict(color='#ff7f0e')
        ))

        # Confidence intervals
        fig_forecast.add_trace(go.Scatter(
            x=future_data['ds'], y=future_data['yhat_upper'],
            fill=None, mode='lines', line_color='rgba(0,0,0,0)',
            showlegend=False
        ))
        fig_forecast.add_trace(go.Scatter(
            x=future_data['ds'], y=future_data['yhat_lower'],
            fill='tonexty', mode='lines', line_color='rgba(0,0,0,0)',
            name='Confidence Interval', fillcolor='rgba(255,127,14,0.3)'
        ))

        # Safety stock line
        fig_forecast.add_hline(y=safety_stock, line_dash="dot",
                              annotation_text=f"Safety Stock: {safety_stock:.1f} kg",
                              line_color="red")

        fig_forecast.update_layout(title="Demand Forecast (30 Days)")

    return fig_orders, fig_cost_overview, fig_forecast

logger.info(f"⏱️ All imports finished in {time.time() - _import_start:.1f}s")

def main():
    global px
    # Initialize all session state variables with defaults
    session_defaults = {
        'initialized': True,
        'db_initialized': False,
        'transactions': [],
        'selected_period': '2024/2025',
        'last_loaded_period': None,
        'stock_takes': {},
        'active_count_id': None,
        'count_sheets': {},
        'count_assignments': {},
        'stock_take_menu': "📊 Dashboard",
        'stock_take_selected_menu': "📊 Dashboard",
        'inventory_items_count': 0,
        'inventory_sample': {},
        'load_attempts': 0,
        'confirm_clear_pressed': False,
        'quick_orders': [],
        'stock_take_inventory': {},
        'show_password_reset': False,  
        'reset_email_input': '',       
        'rate_limit_warning': None,
        # Excludes slow models (ARIMA, LSTM) by default; users can enable via Model Configuration
        'selected_models': ['prophet', 'monte_carlo', 'xgboost', 'lightgbm', 'random_forest'],
    }
    
    for key, value in session_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # 🚀 OPTIMIZE SESSION STATE
    # Remove unnecessary large data from session state
    try:
        optimize_session_state()
        logger.info("Session state optimized")
    except Exception as e:
        logger.warning(f"Session state optimization skipped: {e}")
    
    # Log memory usage
    try:
        memory_mb = get_memory_usage()
        logger.info(f"💾 Memory usage at startup: {memory_mb:.1f} MB")
    except Exception as e:
        logger.warning(f"Memory usage logging skipped: {e}")

    if not st.session_state.db_initialized:
        init_db()
        fix_order_date()
        seed_historical_data()
        get_historical_orders_from_db.clear()
        st.session_state.db_initialized = True
        print("✅ Database initialized")


    # Sidebar - Header with Glass Design (ABOVE Analysis Period)
    st.sidebar.markdown("""
        <div style="
            background: linear-gradient(135deg, #1a237e 0%, #4fc3f7 100%);
            padding: 15px 20px;
            border-radius: 12px;
            color: white;
            margin-bottom: 20px;
            text-align: center;
            box-shadow: 0 4px 15px rgba(26, 35, 126, 0.3);
        ">
            <div style="font-size: 20px; font-weight: 700;">Browns Ops Suite</div>
            <div style="font-size: 9.5px; opacity: 0.7; margin-top: 4px; white-space: nowrap;">
                Inventory • Dry Ice • Production Optimization
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    # ============================================================
    # 🔐 AUTHENTICATION STATUS INDICATOR
    # ============================================================
    auth_check = st.session_state.get('_auth')
    if auth_check and auth_check.is_authenticated:
        _user = auth_check.current_user
        st.sidebar.markdown(f"✅ **{_user['name']}**")
        # Only show role to admins
        if _user.get('role') == 'admin':
            st.sidebar.caption(f"🏷️ {_user['role'].title()}")
    else:
        st.sidebar.markdown("🔴 Logged out")
    # ============================================================
    # 🔐 AUTHENTICATION SECTION (Compact)
    # ============================================================
    with st.sidebar.expander("🔐", expanded=False):
        try:
            auth = st.session_state.get('_auth')
            if auth is None:
                if SECURITY_AVAILABLE:
                    auth = AuthManager(supabase_client=init_supabase())
                    st.session_state._auth = auth
                else:
                    class DummyAuth:
                        is_authenticated = False
                        current_user = None
                        current_role = None
                        def render_login_form(self):
                            st.warning("🔒 Security module unavailable")
                        def render_password_reset_form(self):
                            st.warning("🔒 Password reset unavailable")
                    auth = DummyAuth()
            
            if st.session_state.get('show_password_reset', False):
                render_password_reset_form()
            else:
                if '2fa_pending' in st.session_state or st.session_state.get('_2fa_pending'):
                    st.markdown("""
                    <div style="
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        padding: 6px 12px;
                        border-radius: 6px;
                        color: white;
                        margin-bottom: 10px;
                        text-align: center;
                        font-size: 13px;
                    ">
                        🔐 2FA
                    </div>
                    """, unsafe_allow_html=True)
                    
                    two_fa_code = st.text_input(
                        "6-digit code", 
                        type="password", 
                        placeholder="123456",
                        label_visibility="collapsed"
                    )
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("✅", use_container_width=True):
                            two_factor = TwoFactorAuth()
                            if hasattr(auth, 'verify_2fa'):
                                result = auth.verify_2fa(two_fa_code)
                                if result['success']:
                                    st.success(result['message'])
                                    st.rerun()
                                else:
                                    st.error(result['message'])
                            else:
                                if two_factor.verify_2fa_login(two_fa_code):
                                    st.success("✅ Verified!")
                                    st.rerun()
                                else:
                                    st.error("❌ Invalid")
                    with col2:
                        if st.button("✕", use_container_width=True):
                            if hasattr(auth, 'cancel_2fa'):
                                auth.cancel_2fa()
                            st.rerun()
                else:
                    auth.render_login_form()
            
            # Security Dashboard (Admin Only)
            if auth.is_authenticated and auth.current_role == 'admin':
                if st.button("🛡️ Dashboard", use_container_width=True):
                    st.session_state.show_security_dashboard = True

        except Exception as e:
            st.error(f"❌ {str(e)[:50]}")
            import traceback
            st.code(traceback.format_exc())
            logger.error(f"Auth UI error: {e}", exc_info=True)

    # ============================================================
    # VIEW MODE SELECTOR 
    st.sidebar.markdown("""
    <div style="
        border: 2px solid #667eea;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 15px;
        background: rgba(102, 126, 234, 0.05);
    ">
        <div style="
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 6px 12px;
            border-radius: 8px;
            margin-bottom: 12px;
            display: inline-block;
            font-size: 14px;
            font-weight: 700;
        ">
            🔄 View Mode
        </div>
    """, unsafe_allow_html=True)

    from app.core.rbac import MODE_REQUIREMENTS
    if auth_check and auth_check.is_authenticated:
        available_modes = [name for name, perm in MODE_REQUIREMENTS.items() if has_permission(perm)]
        if not available_modes:
            available_modes = ["📦 All Items Mode"]
    else:
        available_modes = list(MODE_REQUIREMENTS.keys())

    if st.session_state.get("inventory_mode") not in available_modes:
        st.session_state.inventory_mode = available_modes[0]

    inventory_mode = st.sidebar.radio(
        "Select inventory view:",
        available_modes,
        help="Switch between general inventory management, Cheese Production Optimization, or Dry Ice analysis",
        key="inventory_mode"
    )
    # Captured early (not just right before the mode branch at the bottom, where
    # it's still also read) so everything below -- the 8-model forecast, EOQ/
    # safety stock, the Dry-Ice sidebar widgets, and Dashboard Home -- can be
    # gated on it instead of running unconditionally on every rerun regardless
    # of which mode is selected.
    mode = st.session_state.get('inventory_mode', '📦 All Items Mode')

    st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # Analysis Period - only shown in Dry Ice Mode
    if mode == "❄️ Dry Ice Mode":
        st.sidebar.header("🗓️ Analysis Period")
        analysis_periods = ['2024/2025', '2025/2026', '2026/2027', '2027/2028', '2028/2029']

        if 'selected_period' not in st.session_state:
            st.session_state.selected_period = '2024/2025'

        selected_period = st.sidebar.selectbox(
            "Choose a period to analyze or update:",
            analysis_periods,
            index=analysis_periods.index(st.session_state.selected_period)
        )
        st.session_state.selected_period = selected_period
    else:
        # Still need a default period for data loading, but hide the UI
        if 'selected_period' not in st.session_state:
            st.session_state.selected_period = '2024/2025'
        selected_period = st.session_state.selected_period

    # Get today's date once
    today = datetime.today()

    # Parse the start year from the selected period string (e.g., '2025/2026' -> 2025).
    # (This used to run twice in a row with two different fallback behaviors — the
    # first attempt's "use current year" fallback was always immediately
    # overwritten or halted by the second, identical parse. Dead code.)
    try:
        start_year = int(selected_period.split('/')[0])
    except (ValueError, IndexError):
        st.error("Invalid analysis period format. Check the 'analysis_periods' list.")
        st.stop()

    # 2. Define the fixed start date and the potential (full year) end date
    display_start_date = datetime(start_year, 7, 1)
    display_end_date = datetime(start_year + 1, 6, 30)

    # 3. Determine the final end_date using your logic: the earlier of the potential end date or today
    # --- SET 2: DATES FOR FILTERING THE DATA (Cannot go past today) ---
    filter_start_date = display_start_date
    filter_end_date = min(display_end_date, today) # The end date for analysis is the earlier of the two

    alert = None
   
    # Ensure database is initialized before any queries
    if not st.session_state.get('db_initialized', False):
        init_db()
        fix_order_date()
        seed_historical_data()
        get_historical_orders_from_db.clear()
        st.session_state.db_initialized = True
        print("✅ Database initialized")

    
    def _load_dry_ice_analysis_data():
        d = get_historical_orders_from_db(st.session_state.selected_period)
        if d.empty:
            st.info(f"No order data found for the {st.session_state.selected_period} period. Record a receipt in the sidebar to begin the analysis.")
            d = pd.DataFrame(columns=['Date', 'Order_Quantity_kg', 'Containers_Used', 'Transport_Cost', 'Total_Cost'])
        a = DryIceAnalyzer(d)
        k = a.calculate_kpis(period=st.session_state.selected_period)
        return d, a, k

    @st.cache_data(ttl=600, show_spinner=False)
    def _load_all_items_analysis_data():
        """Build a genuine All Items daily-quantity series for Advanced
        Analytics. Previously All Items Mode reused Dry Ice's
        historical_orders table via _load_dry_ice_analysis_data(), so
        Advanced Analytics' anomaly detection and forecast-accuracy metric
        were silently analyzing Dry Ice order data while labeled as All
        Items. Sources total daily quantity checked OUT (usage) across ALL
        items from Google Sheets, summed to one row per day — the shape
        AdvancedAnalytics.detect_anomalies() expects: 'Date' + a numeric
        target column ('Order_Quantity_kg')."""
        empty = pd.DataFrame(columns=['Date', 'Order_Quantity_kg'])
        try:
            gsheet = GoogleSheetReader()
            if not gsheet.authenticate():
                return empty

            check_out_df = gsheet.get_check_out()
            if check_out_df.empty:
                return empty

            date_col = next((c for c in check_out_df.columns if 'date' in c.lower()), None)
            qty_col = next((c for c in check_out_df.columns
                             if 'quantity' in c.lower() or 'qty' in c.lower()), None)
            if not date_col or not qty_col:
                return empty

            d = check_out_df[[date_col, qty_col]].copy()
            d.columns = ['Date', 'Order_Quantity_kg']
            d['Date'] = pd.to_datetime(d['Date'], errors='coerce')
            d['Order_Quantity_kg'] = pd.to_numeric(d['Order_Quantity_kg'], errors='coerce')
            d = d.dropna(subset=['Date', 'Order_Quantity_kg'])
            if d.empty:
                return empty

            # One row per day, summed across every item — the daily
            # "activity volume" signal anomaly/pattern detection expects,
            # not per-item granularity.
            d = d.groupby(d['Date'].dt.normalize())['Order_Quantity_kg'].sum().reset_index()
            return d.sort_values('Date').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Could not load All Items analysis data: {e}")
            return empty

    if mode == "❄️ Dry Ice Mode":
        df, analyzer, kpis = _load_dry_ice_analysis_data()
    elif mode == "📦 All Items Mode":
        df = _load_all_items_analysis_data()
        analyzer = None
        kpis = {}
    else:
        df = pd.DataFrame(columns=['Date', 'Order_Quantity_kg', 'Containers_Used', 'Transport_Cost', 'Total_Cost'])
        analyzer = None
        kpis = {}

    # Try to load from Google Sheets first
    @st.cache_data(ttl=600)
    @safe_operation(error_message="Failed to load inventory data")
    def load_inventory_data():
        """
        Load inventory data from Google Sheets with comprehensive error handling.
        """
        inventory_items = {}
        stock_df = None
        
        try:
            logger.info("Loading inventory data from Google Sheets...")
            gsheet = GoogleSheetReader()
            
            # Check authentication
            if not gsheet.authenticate():
                logger.warning("Google Sheets authentication failed. Using sample data.")
                st.info("📊 Could not connect to the inventory data source right now. Showing sample data for now.")
                return get_sample_inventory_data(), None
            
            logger.info("Google Sheets authentication successful. Fetching data...")
            stock_df = gsheet.get_stock_with_pricing()
            
            # ============================================================
            # 🚀 COMPRESS DATAFRAME TO REDUCE MEMORY USAGE
            # ============================================================
            if not stock_df.empty:
                original_size = len(stock_df)
                original_memory = stock_df.memory_usage(deep=True).sum() / 1024 / 1024
                
                stock_df = compress_dataframe(stock_df)
                
                compressed_memory = stock_df.memory_usage(deep=True).sum() / 1024 / 1024
                reduction = ((original_memory - compressed_memory) / original_memory) * 100
                
                logger.info(f"📊 Stock DataFrame compressed: {original_size} rows")
                logger.info(f"💾 Memory: {original_memory:.1f}MB → {compressed_memory:.1f}MB (↓{reduction:.0f}%)")
            else:
                logger.warning("Google Sheets returned empty data. Using sample data.")
                st.info("📊 No inventory data found yet. Showing sample data for now.")
                return get_sample_inventory_data(), None
            
            # Check if data is empty
            if stock_df.empty:
                logger.warning("Google Sheets returned empty data. Using sample data.")
                st.info("📊 No inventory data found yet. Showing sample data for now.")
                return get_sample_inventory_data(), None
            
            logger.info(f"Retrieved {len(stock_df)} rows from Google Sheets")
            
            # Process the data with error handling for each row
            processed_count = 0
            skipped_count = 0
            
            for _, row in stock_df.iterrows():
                try:
                    # Get item name
                    item_name = row.get('ITEM_NAME', 'Unknown')
                    if not item_name or str(item_name).strip() == '':
                        skipped_count += 1
                        continue
                    
                    # Safe conversion for stock
                    try:
                        stock_val = row.get('QUANTITY', 0)
                        if pd.isna(stock_val) or str(stock_val).strip() == '':
                            stock = 0
                        else:
                            stock = float(stock_val)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error converting stock for {item_name}: {e}")
                        stock = 0
                    
                    # Skip items with zero or negative stock
                    if stock <= 0:
                        skipped_count += 1
                        continue
                    
                    # Safe conversion for reorder level
                    try:
                        reorder_val = row.get('REORDER LEVEL', 0)
                        if pd.isna(reorder_val) or str(reorder_val).strip() == '':
                            reorder = stock * 0.5
                        else:
                            reorder = float(reorder_val)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error converting reorder for {item_name}: {e}")
                        reorder = stock * 0.5
                    
                    # Safe conversion for price
                    try:
                        price_val = row.get('UNIT PRICE', 0)
                        if pd.isna(price_val) or str(price_val).strip() == '':
                            price = 0
                        else:
                            price = float(price_val)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error converting price for {item_name}: {e}")
                        price = 0
                    
                    # Map icons based on category
                    icon_map = {
                        'Dry Ice': '🧊',
                        'Chemicals': '🧪',
                        'Packaging': '📦',
                        'Equipment': '⚙️',
                        'Safety': '🛡️',
                        'Default': '📦'
                    }
                    
                    category = row.get('ITEM_CATEGORY', 'Default')
                    if pd.isna(category) or str(category).strip() == '':
                        category = 'Default'
                    icon = icon_map.get(category, icon_map['Default'])
                    
                    # Get unit of measure
                    unit = row.get('UNIT_OF_MEASURE', 'kg')
                    if pd.isna(unit) or str(unit).strip() == '':
                        unit = 'kg'
                    
                    # Create inventory item
                    inventory_items[item_name] = {
                        'icon': icon,
                        'stock': stock,
                        'reorder': reorder,
                        'max': max(stock * 2, reorder * 3, 100),
                        'unit': unit,
                        'category': category if category else 'Uncategorized',
                        'location': 'Warehouse',
                        'price': price
                    }
                    processed_count += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing row for {row.get('ITEM_NAME', 'Unknown')}: {e}")
                    skipped_count += 1
                    continue
            
            logger.info(f"Processed {processed_count} items, skipped {skipped_count} items")
            
            # Check if we have any valid items
            if not inventory_items:
                logger.warning("No valid inventory items found after processing. Using sample data.")
                st.warning("⚠️ No valid inventory items found. Showing sample data for now.")
                return get_sample_inventory_data(), None
            
            logger.info(f"Successfully loaded {len(inventory_items)} inventory items")
            return inventory_items, stock_df
            
        except Timeout as e:
            logger.error(f"Timeout loading inventory: {e}")
            st.warning("⏰ Request timed out. Using sample data for now.")
            st.info("💡 Please check your internet connection and try again.")
            return get_sample_inventory_data(), None
            
        except ConnectionError as e:
            logger.error(f"Connection error loading inventory: {e}")
            st.warning("🌐 Network error. Using sample data for now.")
            st.info("💡 Please check your internet connection and try again.")
            return get_sample_inventory_data(), None
            
        except Exception as e:
            logger.error(f"Unexpected error loading inventory: {e}", exc_info=True)
            st.warning("⚠️ Could not load inventory data right now. Showing sample data.")
            st.info("💡 If this continues, contact support.")
            return get_sample_inventory_data(), None

    
    if mode != "🧀 BCPOS Mode":
        inventory_items, stock_df = load_inventory_data()
    else:
        inventory_items, stock_df = {}, None
    
    # Initialize session state for transactions
    if 'last_loaded_period' not in st.session_state or st.session_state.last_loaded_period != st.session_state.selected_period:
        print(f"Period changed. Loading transactions for {st.session_state.selected_period}...")
        st.session_state.transactions = get_transactions_from_db(st.session_state.selected_period)
        st.session_state.last_loaded_period = st.session_state.selected_period # Update the tracker
    
    
    #1. Generate Forecast (Logic from former Tab 2) ---
    @st.cache_data(ttl=1800)
    def get_forecast_data(df):
        """Get cached forecast data with proper error handling"""
        # Check if df is None or empty
        if df is None or df.empty:
            return None, np.array([0]), {}, 0, 0, 0
        
        # Check if DataFrame has required columns
        if 'Date' not in df.columns or 'Order_Quantity_kg' not in df.columns:
            return None, np.array([0]), {}, 0, 0, 0
        
        # Check if there's enough data
        if len(df) < 5:
            st.warning(f"⚠️ Limited historical data ({len(df)} points). Forecast reliability may be reduced.")
            # Use simple average for limited data
            avg_value = df['Order_Quantity_kg'].mean() if 'Order_Quantity_kg' in df.columns else 0
            # Create a simple forecast using average
            ensemble_forecast_values = np.full(30, max(0, avg_value))
            total_forecasted_demand = np.sum(ensemble_forecast_values)
            forecast_std_dev = np.std(ensemble_forecast_values)
            
            # Create a simple visualization
            dates = pd.to_datetime(df['Date'])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dates, y=df['Order_Quantity_kg'], name='Actual Demand', 
                                    line=dict(color='blue', width=2)))
            future_dates = pd.date_range(dates.max(), periods=31)[1:]
            fig.add_trace(go.Scatter(x=future_dates, y=ensemble_forecast_values, 
                                    name='Conservative Forecast', 
                                    line=dict(color='orange', width=3)))
            fig.update_layout(title='Conservative Forecast (Limited Historical Data)', 
                             xaxis_title='Date', yaxis_title='Demand (kg)')
            
            return fig, ensemble_forecast_values, {'Simple Average': avg_value}, 0.2, total_forecasted_demand, forecast_std_dev
        
        try:
            daily_df = df.set_index('Date').resample('D')['Order_Quantity_kg'].sum().reset_index()
            daily_df = daily_df.rename(columns={'Date': 'Date', 'Order_Quantity_kg': 'Order_Quantity_kg'})
            
            # Check if resampled data has any values
            if daily_df.empty or daily_df['Order_Quantity_kg'].sum() == 0:
                return None, np.array([0]), {}, 0, 0, 0
            
            fig_ensemble, ensemble_forecast_values, model_forecasts, backtest_accuracy = create_ensemble_forecast(
            daily_df, forecast_days=30, selected_models=st.session_state.get('selected_models')
        )
            
            # Check if forecast returned valid values
            if ensemble_forecast_values is None or len(ensemble_forecast_values) == 0:
                return None, np.array([0]), {}, 0, 0, 0
            
            total_forecasted_demand = np.sum(ensemble_forecast_values)
            forecast_std_dev = np.std(ensemble_forecast_values)
            
            return fig_ensemble, ensemble_forecast_values, model_forecasts, backtest_accuracy, total_forecasted_demand, forecast_std_dev
            
        except Exception as e:
            st.warning(f"⚠️ Forecast generation failed: {str(e)}")
            return None, np.array([0]), {}, 0, 0, 0
    
    if mode == "❄️ Dry Ice Mode":
        # Check if we have data before generating forecast
        if not df.empty and len(df) >= 5:
            with st.spinner("⏳ Please wait as we work on your request..."):
                fig_ensemble, ensemble_forecast_values, model_forecasts, backtest_accuracy, total_forecasted_demand, forecast_std_dev = get_forecast_data(df)
            
                # Update model forecasts with proper names
                if model_forecasts:
                    # Add backtest accuracy to model forecasts
                    model_forecasts['Backtest Accuracy'] = f"{backtest_accuracy*100:.1f}%"
            
                # Log success
                logger.info(f"Forecast generated: {len(ensemble_forecast_values)} days, models: {len(model_forecasts)-1}")
            
        else:
            # No data or insufficient data for forecasting
            fig_ensemble = None
            ensemble_forecast_values = np.array([0])
            model_forecasts = {}
            backtest_accuracy = 0
            total_forecasted_demand = 0
            forecast_std_dev = 0
        
            if df.empty:
                st.info("📊 No order data found for this period. Record a receipt to begin analysis.")
            else:
                st.warning(f"⚠️ Insufficient data ({len(df)} orders). Need at least 5 orders for reliable forecasting.")

    
        if not df.empty and len(df) >= 5 and total_forecasted_demand <= 0:
            st.warning("⚠️ Forecast resulted in zero/negative demand. Using intelligent fallback.")
            fallback_monthly_demand = max(
                kpis.get('current_monthly_volume', 0),
                kpis.get('avg_order_size', 300) * 4,
                1200
            )
            total_forecasted_demand = fallback_monthly_demand
            forecast_std_dev = total_forecasted_demand * 0.25
            st.info(f"Using fallback monthly demand: {total_forecasted_demand:,.0f} kg")

        if df.empty:
            # Nothing to build a policy from — zero out instead of synthesizing
            # a recommendation. This is what was firing for periods like
            # 2027/2028 with zero historical orders: the fallback above used
            # to run unconditionally and invent a 1200kg/month floor, which
            # then flowed into EOQ/safety stock/annual spending as if it were
            # a real recommendation for a period with no data behind it.
            monthly_demand_input = 0
            demand_stddev_input = 0
            avg_sublimation = sum(constants.SUB_LOSS_RANGE) / 2 / 100
            adjusted_demand = 0
            z_score = norm.ppf(constants.SERVICE_LEVEL)
            eoq = 0
            safety_stock = 0
            reorder_point = 0
            current_monthly_orders = 0
            eoq_monthly_orders = 0
            annual_transport_savings = 0
            monthly_savings = 0
            annual_volume = 0
            annual_product_cost = 0
            annual_transport_cost = 0
            annual_sublimation_loss = 0
            annual_holding_cost = 0
            total_annual_spending = 0
        else:
            # --- 2. Calculate Inventory Policy (Logic from former Tab 3) ---
            monthly_demand_input = total_forecasted_demand if total_forecasted_demand > 0 else kpis.get('current_monthly_volume', 0)
            demand_stddev_input = forecast_std_dev * np.sqrt(30) if forecast_std_dev > 0 else kpis.get('std_order_size', 0) * math.sqrt(4)

            avg_sublimation = sum(constants.SUB_LOSS_RANGE) / 2 / 100
            sublimation_factor = 1 + avg_sublimation
            adjusted_demand = monthly_demand_input * sublimation_factor

            z_score = norm.ppf(constants.SERVICE_LEVEL)
            eoq = math.sqrt((2 * adjusted_demand * constants.TRANSPORT_COST) / (constants.HOLDING_RATE * constants.PRICE_PER_KG)) if (constants.HOLDING_RATE * constants.PRICE_PER_KG) > 0 else 0
            safety_stock = z_score * demand_stddev_input * math.sqrt(constants.LEAD_TIME_DAYS) * sublimation_factor
            reorder_point = (adjusted_demand / 30 * constants.LEAD_TIME_DAYS) + safety_stock

            # --- 3. Calculate the Definitive Annual Transport Savings ---
            current_monthly_orders = kpis.get('order_frequency', 0)
            eoq_monthly_orders = adjusted_demand / eoq if eoq > 0 else 0
            annual_transport_savings = (current_monthly_orders - eoq_monthly_orders) * 12 * constants.TRANSPORT_COST
            annual_transport_savings = max(0, annual_transport_savings)
            monthly_savings = annual_transport_savings / 12 if annual_transport_savings else 0

            # --- 4. Recalculate Total Annual Spending & Other Costs ---
            annual_volume = kpis.get('total_volume', 0)
            annual_product_cost = annual_volume * constants.PRICE_PER_KG
            annual_transport_cost = kpis.get('total_orders', 0) * constants.TRANSPORT_COST
            annual_sublimation_loss = annual_volume * constants.PRICE_PER_KG * avg_sublimation

            average_inventory_level = (kpis.get('avg_order_size', 0) / 2) + safety_stock
            annual_holding_cost = average_inventory_level * constants.PRICE_PER_KG * constants.HOLDING_RATE

            total_annual_spending = annual_product_cost + annual_transport_cost + annual_holding_cost + annual_sublimation_loss

        # --- 5. Generate Other Charts and Visualizations ---
        fig_orders, fig_cost_overview, fig_forecast = create_enhanced_charts(
            df=df, analyzer=analyzer, kpis=kpis, forecast_data=None, safety_stock=safety_stock
        )
    else:
        fig_ensemble = None
        ensemble_forecast_values = np.array([0])
        model_forecasts = {}
        backtest_accuracy = 0
        eoq = 0
        safety_stock = 0
        reorder_point = 0
        fig_orders = None
        fig_cost_overview = None
        fig_forecast = None
        monthly_demand_input = 0
        demand_stddev_input = 0
        z_score = norm.ppf(constants.SERVICE_LEVEL)
        avg_sublimation = sum(constants.SUB_LOSS_RANGE) / 2 / 100
        adjusted_demand = 0
        current_monthly_orders = 0
        eoq_monthly_orders = 0
        annual_transport_savings = 0
        monthly_savings = 0
        annual_volume = 0
        annual_product_cost = 0
        annual_transport_cost = 0
        annual_sublimation_loss = 0
        annual_holding_cost = 0
        total_annual_spending = 0
    
    if mode == "❄️ Dry Ice Mode":
        # Current stock is now scoped to the SELECTED period: "what stock
        # was as of that period's end", using filter_end_date (already
        # computed above as min(display_end_date, today)) as the as-of
        # date. This is what makes switching periods actually change the
        # displayed stock, instead of always showing today's live number.
        period_has_started = display_start_date <= today
        is_active_period = display_start_date <= today <= display_end_date

        if period_has_started:
            current_stock = get_current_stock_from_db(as_of_date=filter_end_date.date())
        else:
            # Period hasn't started — there's no legitimate "stock as of
            # period end" yet. Must special-case this rather than querying
            # with as_of_date=min(period_end, today), which would silently
            # resolve to today's live stock and reproduce the original bug.
            current_stock = 0

        # Only seed a default when there's truly no data ANYWHERE and this
        # is the live/active period — seeding a fake number into a past or
        # future period's empty slot would be inventing history.
        if current_stock == 0 and is_active_period:
            target_days_coverage = 45
            avg_daily_forecast = np.mean(ensemble_forecast_values) if total_forecasted_demand > 0 else (kpis.get('avg_order_size', 0) / 7)
            initial_stock = max(
                eoq * 2,
                safety_stock * 4,
                avg_daily_forecast * target_days_coverage
            )
            if initial_stock <= 0:
                initial_stock = 1000
            current_stock = initial_stock
            update_current_stock_in_db(current_stock, datetime.now())
            print(f"Database was empty for the active period. Seeded initial stock with: {current_stock:.2f} kg")

        inventory_tracker = InventoryTracker(
            initial_stock=current_stock,
            analyzer=analyzer
        )
    else:
        inventory_tracker = None

    # ============================================================
    # 🎯 DECISION ENGINE (stored in session_state so the sidebar,
    # which renders earlier in this function, can read it too)
    # ============================================================
    if mode == "❄️ Dry Ice Mode":
        snapshot = InventorySnapshot(
            current_stock=inventory_tracker.current_stock,
            eoq=eoq,
            safety_stock=safety_stock,
            reorder_point=reorder_point,
            forecast_values=ensemble_forecast_values,
            forecast_accuracy=backtest_accuracy * 100,
            lead_time_days=constants.LEAD_TIME_DAYS,
            transport_cost=constants.TRANSPORT_COST,
            avg_order_size=kpis.get('avg_order_size', 300),
            monthly_holding_cost=annual_holding_cost / 12,
        )
        decision_engine = InventoryDecisionEngine(snapshot)
        st.session_state.decision = decision_engine.executive_summary()

    # Initialize other components
    mobile_ui = MobileInterface()
    #alerts_system = SmartAlerts(inventory_tracker)
    maintenance_system = PredictiveMaintenance()
    integration_system = SystemIntegrations()
    
    if mode == "❄️ Dry Ice Mode":
        # ============================================================
        # SECTION 1: REAL-TIME INVENTORY (Container Style)
        # ============================================================
        st.sidebar.markdown("""
        <div style="
            border: 2px solid #4fc3f7;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            background: rgba(79, 195, 247, 0.05);
        ">
            <div style="
                background: #4fc3f7;
                color: white;
                padding: 6px 12px;
                border-radius: 8px;
                margin-bottom: 12px;
                display: inline-block;
                font-size: 14px;
                font-weight: 700;
            ">
                📦 Real-time Inventory
            </div>
        """, unsafe_allow_html=True)

        # Editing this field writes a new inventory row — the date it's
        # dated with must match the selected period, or an edit made while
        # browsing a past period would silently insert a TODAY-dated row
        # and corrupt that period's timeline.
        if is_active_period:
            stock_edit_help = "Enter the current stock level in kilograms"
            stock_anchor_date = today
            stock_edit_disabled = False
        elif period_has_started:
            stock_edit_help = (
                f"Editing here backfills the stock snapshot for "
                f"{selected_period}'s period end ({display_end_date.strftime('%Y-%m-%d')})."
            )
            stock_anchor_date = display_end_date
            stock_edit_disabled = False
        else:
            stock_edit_help = f"{selected_period} hasn't started yet — nothing to record."
            stock_anchor_date = None
            stock_edit_disabled = True

        current_stock_input = st.sidebar.number_input(
            "Current Stock (kg)",
            min_value=0.0,
            value=float(inventory_tracker.current_stock),
            step=50.0,
            help=stock_edit_help,
            disabled=stock_edit_disabled,
        )

        if not stock_edit_disabled and current_stock_input != inventory_tracker.current_stock:
            inventory_tracker.current_stock = current_stock_input
            update_current_stock_in_db(current_stock_input, stock_anchor_date)

        # Display status (LOW/CRITICAL/OK)
        status = inventory_tracker.get_stock_status()
        st.sidebar.markdown(
            f"**Status:** <span style='color:{status['color']};font-weight:bold'>"
            f"{status['status']}</span>",
            unsafe_allow_html=True
        )

        st.sidebar.markdown("</div>", unsafe_allow_html=True)

        # ============================================================
        # SECTION 2: UPDATE INVENTORY (Compact Expanders)
        # ============================================================
        if has_permission(Permission.RECORD_USAGE):
            with st.sidebar.expander("📤 Record Usage", expanded=False):
                usage_date = st.date_input("Usage Date", value=datetime.today())

                usage = safe_number_input(
                    "Quantity Used (kg)",
                    min_value=0.0,
                    max_value=10000.0,
                    value=150.0,
                    step=10.0,
                    validate=True,
                    key="usage_qty",
                    container=st
                )

                current_stock_val = inventory_tracker.current_stock
                st.caption(f"📊 Available: {current_stock_val:,.0f} kg")

                if usage is not None and usage > 0:
                    is_valid, msg = validate_stock_sufficient(usage, current_stock_val)
                    if not is_valid:
                        st.error(msg)
                    elif "Warning" in msg:
                        st.warning(msg)
                    else:
                        st.success(msg)

                if st.button("Record Usage", type="primary", key="record_usage_btn"):
                    auth = AuthManager()
                    if not auth.is_authenticated:
                        st.error("🔒 Please login to record usage")
                        return
                    if not auth.check_permission('record_usage'):
                        st.error("⛔ You don't have permission to record usage")
                        return

                    if usage is None or usage <= 0:
                        st.error("❌ Please enter a valid quantity")
                        return

                    is_valid, msg = validate_stock_sufficient(usage, current_stock_val)
                    if not is_valid:
                        st.error(msg)
                        return

                    if usage > 500:
                        if not st.checkbox("☑️ Confirm large usage (>500kg)", key="confirm_large_usage"):
                            st.warning("⚠️ Please confirm large usage before proceeding")
                            return

                    try:
                        correct_period = get_period_from_date(usage_date)

                        audit = AuditLogger()
                        audit.log(
                            action='RECORD_USAGE',
                            details=f"Usage: {usage}kg, Date: {usage_date}",
                            user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                        )

                        alert = inventory_tracker.update_stock(usage, "Daily Consumption", usage_date)
                        add_transaction_to_history(
                            "usage", usage, "Daily Consumption", usage_date,
                            correct_period
                        )

                        if alert is not None:
                            st.error(alert["message"])
                        else:
                            audit.log(
                                action='RECORD_USAGE_SUCCESS',
                                details=f"Usage: {usage}kg recorded successfully, New stock: {inventory_tracker.current_stock}kg",
                                user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                            )
                            st.success(f"✅ Usage of {usage:.0f} kg recorded on {usage_date.strftime('%Y-%m-%d')}.")
                            new_stock_val = inventory_tracker.current_stock
                            st.info(f"📊 New stock level: {new_stock_val:,.0f} kg")
                            if new_stock_val < safety_stock:
                                st.warning(f"⚠️ Stock below safety stock ({safety_stock:,.0f} kg). Consider reordering.")

                            if st.session_state.selected_period != correct_period:
                                st.session_state.selected_period = correct_period
                                st.info(f"📊 Dashboard view switched to {correct_period} to show your new entry.")

                    except Exception as e:
                        logger.error(f"Failed to record usage: {e}", exc_info=True)
                        st.error("❌ Failed to record usage. Please try again.")

        if has_permission(Permission.RECORD_RECEIPT):
            with st.sidebar.expander("📥 Record Receipt", expanded=False):
                receipt_date = st.date_input("Receipt Date", value=datetime.today(), key="receipt_date")

                current_stock_val = inventory_tracker.current_stock
                st.caption(f"📊 Current stock: {current_stock_val:,.0f} kg")

                new_stock = safe_number_input(
                    "New Stock Received (kg)",
                    min_value=0.0,
                    max_value=100000.0,
                    value=0.0,
                    step=50.0,
                    validate=True,
                    key="receipt_qty",
                    container=st
                )

                if new_stock is not None and new_stock > 0:
                    new_total = current_stock_val + new_stock
                    st.info(f"📊 New stock after receipt: {new_total:,.0f} kg (+{new_stock:,.0f} kg)")
                    if new_stock > 1000:
                        st.warning(f"⚠️ Large receipt: {new_stock:.0f} kg. Please confirm below.")
                    max_recommended = safety_stock * 3
                    if new_total > max_recommended:
                        st.warning(f"⚠️ New stock ({new_total:,.0f} kg) exceeds recommended maximum ({max_recommended:,.0f} kg)")

                if receipt_date:
                    is_valid, msg = validate_date(receipt_date, allow_future=False)
                    if not is_valid:
                        st.error(msg)

                if st.button("Record Receipt", type="primary", key="record_receipt_btn"):
                    if new_stock is None or new_stock <= 0:
                        st.error("❌ Please enter a valid quantity greater than 0")
                        return

                    is_valid, msg = validate_date(receipt_date, allow_future=False)
                    if not is_valid:
                        st.error(msg)
                        return

                    if new_stock > 1000:
                        if not st.checkbox("☑️ Confirm large receipt (>1000kg)", key="confirm_large_receipt"):
                            st.warning("⚠️ Please confirm large receipt before proceeding")
                            return

                    try:
                        correct_period = get_period_from_date(receipt_date)
                        old_stock = inventory_tracker.current_stock

                        inventory_tracker.current_stock += new_stock
                        update_current_stock_in_db(inventory_tracker.current_stock, receipt_date)

                        add_transaction_to_history(
                            transaction_type="receipt",
                            quantity=new_stock,
                            description="Stock Receipt",
                            date=receipt_date,
                            period=correct_period
                        )

                        st.success(
                            f"✅ Order for {new_stock:.0f} kg on {receipt_date.strftime('%Y-%m-%d')} recorded. "
                            f"It has been automatically assigned to the {correct_period} period."
                        )

                        new_total = inventory_tracker.current_stock
                        st.info(f"📊 Stock updated: {old_stock:,.0f} → {new_total:,.0f} kg (+{new_stock:,.0f} kg)")

                        max_recommended = safety_stock * 3
                        if new_total > max_recommended:
                            st.warning(f"⚠️ Stock ({new_total:,.0f} kg) exceeds recommended maximum ({max_recommended:,.0f} kg)")
                            st.info("💡 Consider reducing future orders or increasing usage")

                        if st.session_state.selected_period != correct_period:
                            st.session_state.selected_period = correct_period
                            st.info(f"📊 Dashboard view switched to {correct_period} to show your new entry.")

                    except ValidationError as e:
                        st.error(f"❌ Validation Error: {e}")
                    except DatabaseError as e:
                        st.error(f"⚠️ Database Error: {e}")
                        st.info("💡 Your data was saved locally. It will sync when the cloud is available.")
                    except Exception as e:
                        logger.error(f"Failed to record receipt: {e}", exc_info=True)
                        st.error("❌ Failed to record receipt. Please try again.")
                        st.info("💡 If the problem persists, please contact support.")

            st.sidebar.markdown("</div>", unsafe_allow_html=True)

    if mode == "❄️ Dry Ice Mode":
        # ============================================================
        # SECTION 3: ENHANCED STOCK STATUS (Container Style)
        # ============================================================
        st.sidebar.markdown("""
        <div style="
            border: 2px solid #ffd54f;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            background: rgba(255, 213, 79, 0.05);
        ">
            <div style="
                background: #ffd54f;
                color: #333;
                padding: 6px 12px;
                border-radius: 8px;
                margin-bottom: 12px;
                display: inline-block;
                font-size: 14px;
                font-weight: 700;
            ">
                📊 Stock Status
            </div>
        """, unsafe_allow_html=True)

        # Enhanced Stock Status Display
        stock_status = inventory_tracker.get_stock_status()
        st.sidebar.markdown(
            f"**Status:** <span style='color:{stock_status['color']};font-weight:bold'>"
            f"{stock_status['status']}</span>",
            unsafe_allow_html=True
        )

        max_stock = eoq + safety_stock * 2
        current_stock_val = inventory_tracker.current_stock
        progress_value = min(1.0, current_stock_val / max_stock) if max_stock > 0 else 0
        st.sidebar.progress(progress_value)
        st.sidebar.caption(f"Stock Level: {current_stock_val:.1f}/{max_stock:.1f} kg ({progress_value:.1%})")

        if stock_status['status'] in ['Low Stock', 'Critical']:
            st.sidebar.warning(f"⚠️ Consider reordering. Recommended order: {eoq:.1f} kg")

        st.sidebar.markdown("</div>", unsafe_allow_html=True)
        
        # Initialize SmartAlerts after inventory_tracker is created
        alerts_system = SmartAlerts(inventory_tracker)

    # Mobile CSS stays OUTSIDE the gate (runs in ALL modes)
    mobile_ui.optimize_for_mobile()
    mobile_ui.show_mobile_welcome()

    # Header - Logo always visible, Date Range only in Dry Ice Mode
    start_date_str = display_start_date.strftime('%B %d, %Y')
    end_date_str = display_end_date.strftime('%B %d, %Y')

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        logo_path = "assets/browns_logo.jpg"
        logo_src = get_image_base64(logo_path) if os.path.exists(logo_path) else ""
        
        # ============================================================
        # LOGO + TITLE (Always visible in ALL modes)
        # ============================================================
        st.markdown(
            f"""
            <div style="
                background: white;
                border-radius: 20px;
                padding: 30px 20px 25px 20px;
                margin: 10px 0 5px 0;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
                border: 1px solid rgba(0, 0, 0, 0.06);
                text-align: center;
            ">
                <div style="margin-bottom: 12px;">
                    <img src="{logo_src}" style="height: 80px; width: auto; object-fit: contain; border-radius: 8px;">
                </div>
                <div style="
                    font-size: 28px;
                    font-weight: 700;
                    color: #1a237e;
                    letter-spacing: -0.3px;
                    margin-bottom: 8px;
                ">
                    MarginIQ Ops Suite
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        # ============================================================
        # DATE RANGE (Only in Dry Ice Mode)
        # ============================================================
        if mode == "❄️ Dry Ice Mode":
            st.markdown(
                f"""
                <div style="
                    text-align: center;
                    margin-top: -5px;
                    margin-bottom: 15px;
                ">
                    <div style="
                        height: 2px;
                        background: linear-gradient(90deg, transparent, #1565c0, transparent);
                        margin: 0 auto 14px auto;
                        width: 60%;
                    "></div>
                    <div style="
                        display: inline-block;
                        font-size: 28px;
                        font-weight: 700;
                        color: #1a237e;
                        letter-spacing: -0.3px;
                        padding: 0;
                    ">
                        {start_date_str} – {end_date_str}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

    # ============================================================
    # SECTION 4: REPORT GENERATOR (Container Style)
    # ============================================================
    st.sidebar.markdown("""
    <div style="
        border: 2px solid #ff8a65;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 15px;
        background: rgba(255, 138, 101, 0.05);
    ">
        <div style="
            background: #ff8a65;
            color: white;
            padding: 6px 12px;
            border-radius: 8px;
            margin-bottom: 12px;
            display: inline-block;
            font-size: 14px;
            font-weight: 700;
        ">
            📄 Report Generation
        </div>
    """, unsafe_allow_html=True)

    # Report type selection -- Dry Ice Report (Legacy) only offered to
    # whoever can see Dry Ice Mode itself (currently admin only), same
    # permission that already gates the mode selector above.
    report_type_options = ["📊 All Inventory Report", "📋 Low Stock Report", "💰 Valuable Items Report"]
    if has_permission(Permission.ACCESS_DRY_ICE_MODE):
        report_type_options = ["❄️ Dry Ice Report (Legacy)"] + report_type_options

    if st.session_state.get("report_type_sidebar") not in report_type_options:
        st.session_state.report_type_sidebar = report_type_options[0]

    report_type = st.sidebar.selectbox(
        "Report Type",
        report_type_options,
        key="report_type_sidebar"
    )

    # ============================================================
    # GENERATE REPORT BUTTON WITH SECURITY & VALIDATION
    # ============================================================
    if st.sidebar.button("Generate Report", type="primary"):
        # ============================================================
        # 🔐 AUTHENTICATION CHECK
        # ============================================================
        auth = AuthManager()
        if not auth.is_authenticated:
            st.sidebar.error("🔒 Please login to generate reports")
            return
        if not has_permission(Permission.VIEW_REPORTS):
            st.sidebar.error("⛔ You don't have permission to view reports")
            log_access_denied("generate_report")
            return
        
        with st.spinner(f"Generating {report_type}..."):
            try:
                # ============================================================
                # 🔐 AUDIT LOGGING
                # ============================================================
                audit = AuditLogger()
                audit.log(
                    action='GENERATE_REPORT',
                    details=f"Report type: {report_type}",
                    user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                )
                
                report_path = None

                if analyzer is None:
                    df, analyzer, kpis = _load_dry_ice_analysis_data()
                if not inventory_items:
                    inventory_items, stock_df = load_inventory_data()

                if report_type == "❄️ Dry Ice Report (Legacy)":
                    if df is not None and not df.empty:
                        report = ReportGenerator(analyzer=analyzer, df=df)
                        report_path = report.generate_pdf()
                    else:
                        st.sidebar.error("No Dry Ice data available.")
                
                elif report_type == "📊 All Inventory Report":
                    if inventory_items:
                        report_path = generate_enhanced_pdf_report(
                            inventory_items=inventory_items,
                            stock_df=stock_df if 'stock_df' in locals() else None,
                            kpis=kpis,
                            report_type="All Inventory",
                        )
                    else:
                        st.sidebar.error("No inventory data available yet.")
                
                elif report_type == "📋 Low Stock Report":
                    if inventory_items:
                        low_stock_items = {}
                        for item_name, details in inventory_items.items():
                            stock = details.get('stock', 0)
                            reorder = details.get('reorder', 0)
                            if stock < reorder:
                                low_stock_items[item_name] = details
                        
                        if low_stock_items:
                            report_path = generate_enhanced_pdf_report(
                                inventory_items=low_stock_items,
                                stock_df=stock_df if 'stock_df' in locals() else None,
                                kpis=kpis,
                                report_type="Low Stock",
                            )
                        else:
                            st.sidebar.success("✅ No low stock items found!")
                    else:
                        st.sidebar.error("No inventory data available.")
                
                elif report_type == "💰 Valuable Items Report":
                    if inventory_items:
                        valuable_items = {}
                        for item_name, details in inventory_items.items():
                            price = details.get('price', 0)
                            stock = details.get('stock', 0)
                            if price > 0 and stock > 0:
                                valuable_items[item_name] = details
                        
                        if valuable_items:
                            report_path = generate_enhanced_pdf_report(
                                inventory_items=valuable_items,
                                stock_df=stock_df if 'stock_df' in locals() else None,
                                kpis=kpis,
                                report_type="Valuable Items",
                            )
                        else:
                            st.sidebar.warning("No items with price data found.")
                    else:
                        st.sidebar.error("No inventory data available.")
                
                if report_path:
                    # ============================================================
                    # 🔐 AUDIT LOGGING - SUCCESS
                    # ============================================================
                    audit.log(
                        action='GENERATE_REPORT_SUCCESS',
                        details=f"Report type: {report_type} generated successfully",
                        user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                    )
                    
                    with open(report_path, "rb") as f:
                        st.sidebar.download_button(
                            label="📥 Download Report",
                            data=f,
                            file_name=f"inventory_report_{datetime.now().strftime('%Y%m%d')}.pdf",
                            mime="application/pdf",
                        )
                    st.sidebar.success(f"✅ {report_type} generated successfully!")
                else:
                    st.sidebar.error("Failed to generate report.")
                    
            except Exception as e:
                # ============================================================
                # 🔐 AUDIT LOGGING - ERROR
                # ============================================================
                audit.log(
                    action='GENERATE_REPORT_ERROR',
                    details=f"Report type: {report_type}, Error: {str(e)}",
                    user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                )
                st.sidebar.error(f"Error generating report: {str(e)}")

    st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # ============================================================
    # SECTION 6: STOCK TAKE MENU (Container Style)
    # ============================================================
    st.sidebar.markdown("""
    <div style="
        border: 2px solid #90a4ae;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 15px;
        background: rgba(144, 164, 174, 0.05);
    ">
        <div style="
            background: #90a4ae;
            color: white;
            padding: 6px 12px;
            border-radius: 8px;
            margin-bottom: 12px;
            display: inline-block;
            font-size: 14px;
            font-weight: 700;
        ">
            📋 Stock Take Menu
        </div>
    """, unsafe_allow_html=True)

    menu_options = [
        "📊 Dashboard",
        "📝 New Count",
        "📋 Active Counts",
        "📜 History"
    ]

    if 'stock_take_selected_menu' not in st.session_state:
        st.session_state.stock_take_selected_menu = "📊 Dashboard"

    selected_menu = st.sidebar.radio(
        "Select Action",
        menu_options,
        index=menu_options.index(st.session_state.stock_take_selected_menu),
        key="stock_take_menu_main"
    )

    if selected_menu != st.session_state.stock_take_selected_menu:
        st.session_state.stock_take_selected_menu = selected_menu

    st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # ============================================================
    # SECTION 7: SYSTEM PARAMETERS (Container Style)
    # ============================================================
    st.sidebar.markdown("""
    <div style="
        border: 2px solid #b39ddb;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 15px;
        background: rgba(179, 157, 219, 0.05);
    ">
        <div style="
            background: #b39ddb;
            color: white;
            padding: 6px 12px;
            border-radius: 8px;
            margin-bottom: 12px;
            display: inline-block;
            font-size: 14px;
            font-weight: 700;
        ">
            ⚙️ System Parameters
        </div>
    """, unsafe_allow_html=True)

    if has_permission(Permission.EDIT_SYSTEM_PARAMS):
        with st.sidebar.expander("Inventory Parameters"):
            st.write(f"**Price per kg:** KSh {constants.PRICE_PER_KG:.2f}")
            st.write(f"**Container size:** {constants.CONTAINER_SIZE} kg")
            st.write(f"**Transport cost:** KSh {constants.TRANSPORT_COST:,.2f}")
            st.write(f"**Holding rate:** {constants.HOLDING_RATE*100:.1f}%")
            st.write(f"**Sublimation loss:** {constants.SUB_LOSS_RANGE[0]:.1f}-{constants.SUB_LOSS_RANGE[1]:.1f}%")
            st.write(f"**Lead time:** {constants.LEAD_TIME_DAYS} day(s)")
            st.write(f"**Service level:** {constants.SERVICE_LEVEL*100:.0f}%")

            st.markdown("---")
            st.markdown("**Implementation Cost — Dry Ice Tier**")
            implementation_cost_input = st.number_input(
                "Dry Ice Implementation Cost (KSh)",
                min_value=0.0,
                value=float(st.session_state.get('implementation_cost', constants.IMPLEMENTATION_COST)),
                step=1000.0,
                help="Staff training + SOP updates + signage + supervision during rollout + contingency buffer",
                key="implementation_cost_input"
            )
            st.session_state.implementation_cost = implementation_cost_input
            st.caption("Illustrative breakdown: training ~6,000 | SOP docs ~5,000 | signage ~4,000 | supervision ~6,400 | contingency ~20% — replace with real figures when available.")

            st.markdown("**Implementation Cost — All Items Tier**")
            all_items_implementation_cost_input = st.number_input(
                "All Items Implementation Cost (KSh)",
                min_value=0.0,
                value=float(st.session_state.get('all_items_implementation_cost', constants.ALL_ITEMS_IMPLEMENTATION_COST)),
                step=1000.0,
                help="Baseline physical count + data cleanup + staff training + first-cycle supervision + contingency",
                key="all_items_implementation_cost_input"
            )
            st.session_state.all_items_implementation_cost = all_items_implementation_cost_input
            st.caption("Illustrative breakdown: baseline count ~28,800 | data cleanup ~11,200 | training ~7,500 | supervision ~12,800 | contingency ~15% — replace with real figures when available.")

        st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # ============================================================
    # SECTION 8: DATA SUMMARY (Container Style) - Dry Ice Only
    # ============================================================
    if mode == "❄️ Dry Ice Mode":
        st.sidebar.markdown("""
        <div style="
            border: 2px solid #4dd0e1;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            background: rgba(77, 208, 225, 0.05);
        ">
            <div style="
                background: #4dd0e1;
                color: white;
                padding: 6px 12px;
                border-radius: 8px;
                margin-bottom: 12px;
                display: inline-block;
                font-size: 14px;
                font-weight: 700;
            ">
                📊 Data Summary
            </div>
        """, unsafe_allow_html=True)

        # --- OPTIMIZE DATA DISPLAY ---
        # Show only summary stats, not full data
        sidebar_start_str = display_start_date.strftime('%d/%m/%Y')
        sidebar_end_str = display_end_date.strftime('%d/%m/%Y')

        # Use len(df) efficiently
        total_orders = len(df) if df is not None else 0
        data_points = df.shape[0] if df is not None and hasattr(df, 'shape') else 0

        st.sidebar.write(f"**Analysis Period:** {sidebar_start_str} to {sidebar_end_str}")
        st.sidebar.write(f"**Total Orders:** {total_orders:,}")
        st.sidebar.write(f"**Data Points:** {data_points:,}")

        # ============================================================
        # 🆕 SHOW MEMORY USAGE (Enterprise Monitoring)
        # ============================================================
        try:
            memory_mb = get_memory_usage()
            st.sidebar.caption(f"💾 Memory: {memory_mb:.0f} MB")
        except:
            pass

        st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # ============================================================
    # 🔗 SERVICE STATUS (Compact Expander)
    # ============================================================
    with st.sidebar.expander("🔗 Service Status", expanded=False):
        service_manager = ServiceStatusManager()
        service_manager.show_service_status()

    st.sidebar.markdown("</div>", unsafe_allow_html=True)
    
    # ============================================================
    # SECTION 9: FOOTER (Container Style) - INCREASED FONT SIZES
    # ============================================================
    st.sidebar.markdown("""
        <div style="
            border-radius: 12px;
            padding: 16px 15px;
            text-align: center;
            background: rgba(0,0,0,0.03);
            border: 1px solid rgba(0,0,0,0.05);
        ">
            <div style="font-size: 16px; font-weight: 700; color: #1a237e; margin-bottom: 3px;">
                🧀 Browns Ops Suite
            </div>
            <div style="font-size: 12px; color: #888; margin-bottom: 5px;">
                Inventory • Dry Ice • Production
            </div>
            <div style="font-size: 10px; color: #999; border-top: 2px solid #1a237e; padding-top: 6px;">
                © 2025 MarginIQ Ltd
            </div>
        </div>
        """, unsafe_allow_html=True)

    
    # ============================================================
    # 🏠 DASHBOARD HOME (KPI grid + Decision Center)
    if mode == "❄️ Dry Ice Mode":
        dashboard_ctx = DashboardContext(
            kpis=kpis,
            eoq=eoq,
            eoq_monthly_orders=eoq_monthly_orders,
            safety_stock=safety_stock,
            annual_transport_savings=annual_transport_savings,
            total_annual_spending=total_annual_spending,
            current_monthly_orders=current_monthly_orders,
            inventory_tracker=inventory_tracker,
            constants=constants,
            decision=st.session_state.get('decision'),
        )
        render_dashboard_home(dashboard_ctx)

    
    # ============================================================
    # 🔐 SECURITY DASHBOARD
    if st.session_state.get('show_security_dashboard', False):
        from core.security import render_security_dashboard
        render_security_dashboard(supabase_client=init_supabase())
        st.stop()

    # ============================================================
    if mode == "📦 All Items Mode":
        # ============================================================
        # 🖼️ BANNER IMAGE - RENDER FIRST
        st.image("assets/all_items_dashboard.jpg", use_container_width=True)
        
        # ============================================================
        # 🎨 ALL ITEMS THEME + MODE BADGE
        st.markdown("""
        <style>
            /* All Items Theme - Warm Colors */
            .stTabs [data-baseweb="tab-list"] {
                gap: 8px;
                background: rgba(255,255,255,0.05);
                backdrop-filter: blur(4px);
                border-radius: 12px;
                padding: 8px;
                border: 1px solid rgba(255,255,255,0.08);
            }
            .stTabs [aria-selected="true"] {
                background: linear-gradient(135deg, #e65100 0%, #ff9800 100%) !important;
                color: white !important;
                box-shadow: 0 4px 15px rgba(230, 81, 0, 0.3) !important;
            }
            .stTabs [data-baseweb="tab"]:hover {
                background: rgba(230, 81, 0, 0.08) !important;
                color: #e65100 !important;
            }

            div[role="radiogroup"][style*="row"] label:has(input:checked),
            div[role="radiogroup"][aria-label="Section"] label:has(input:checked),
            div[role="radiogroup"][aria-label="Movement view"] label:has(input:checked) {
                background: linear-gradient(135deg, #e65100 0%, #ff9800 100%) !important;
                color: white !important;
                box-shadow: 0 4px 15px rgba(230, 81, 0, 0.3) !important;
            } 

            /* Mode Badge */
            .mode-badge-all {
                background: linear-gradient(135deg, #e65100, #ff9800);
                color: white;
                padding: 6px 16px;
                border-radius: 20px;
                display: inline-block;
                font-weight: 600;
                font-size: 14px;
                margin-bottom: 15px;
                box-shadow: 0 2px 10px rgba(230, 81, 0, 0.3);
            }
        </style>
        
        <div class="mode-badge-all">📦 ALL ITEMS MODE</div>
        """, unsafe_allow_html=True)
        
        
        def _is_dry_ice(name: str, details: dict) -> bool:
            """Substring match (not exact-equality) against both category
            AND item name. Exact-equality on category alone (previous
            attempt) missed variants like 'Dry Ice - Blocks' or
            'Dry Ice (kg)', which stayed silently included."""
            category = str(details.get('category', '')).strip().lower()
            item_name = str(name).strip().lower()
            return 'dry ice' in category or 'dry ice' in item_name

        all_items_only = {
            name: details for name, details in inventory_items.items()
            if not _is_dry_ice(name, details)
        }

        if stock_df is not None and not stock_df.empty and 'ITEM_CATEGORY' in stock_df.columns:
            _cat_lower = stock_df['ITEM_CATEGORY'].astype(str).str.strip().str.lower()
            _name_lower = (
                stock_df['ITEM_NAME'].astype(str).str.strip().str.lower()
                if 'ITEM_NAME' in stock_df.columns else pd.Series([''] * len(stock_df))
            )
            all_items_stock_df = stock_df[
                ~(_cat_lower.str.contains('dry ice', na=False) | _name_lower.str.contains('dry ice', na=False))
            ]
        else:
            all_items_stock_df = stock_df

        
        from app.core.advanced_analytics import AdvancedAnalytics
        analytics = AdvancedAnalytics()

        # Create context and render
        all_items_ctx = AllItemsContext(
            inventory_items=all_items_only,
            df=df,
            stock_df=all_items_stock_df,
            analytics=analytics,
            constants=constants,
            kpis=kpis,
            inventory_tracker=inventory_tracker,
            supabase_client=init_supabase(),
        )
        render_all_items_mode(all_items_ctx, has_permission=has_permission)

    # ============================================================
    # CONTAINER 2: BCPOS MODE (Cheese Production)
    # ============================================================
    elif mode == "🧀 BCPOS Mode":
        # 🖼️ BANNER IMAGE - RENDER FIRST
        st.image("assets/bcpos_dashboard.jpg", use_container_width=True)
        
        from app.core.cheese_data_access import get_weighted_milk_cost_for_date
        todays_milk_cost = get_weighted_milk_cost_for_date(datetime.today().date())
        render_bcpos_mode(
            supabase_client=init_supabase(),
            has_permission=has_permission,
            milk_cost_per_liter=todays_milk_cost if todays_milk_cost > 0 else 45.0,
            raw_milk_price_per_liter=35.0
        )
    # ============================================================
    # CONTAINER 3: DRY ICE MODE (7 TABS)
    else:  # "❄️ Dry Ice Mode"
        # Create context and render (badge is rendered inside the module)
        dry_ice_ctx = DryIceContext(
            df=df, kpis=kpis, constants=constants, mobile_ui=mobile_ui,
            inventory_tracker=inventory_tracker, fig_orders=fig_orders,
            fig_cost_overview=fig_cost_overview, eoq=eoq,
            monthly_demand_input=monthly_demand_input, demand_stddev_input=demand_stddev_input,
            z_score=z_score, safety_stock=safety_stock, avg_sublimation=avg_sublimation,
            adjusted_demand=adjusted_demand, reorder_point=reorder_point,
            annual_transport_savings=annual_transport_savings, annual_transport_cost=annual_transport_cost,
            current_monthly_orders=current_monthly_orders, eoq_monthly_orders=eoq_monthly_orders,
            annual_volume=annual_volume, annual_product_cost=annual_product_cost,
            annual_holding_cost=annual_holding_cost, annual_sublimation_loss=annual_sublimation_loss,
            total_annual_spending=total_annual_spending,
            monthly_savings=monthly_savings, 
            create_ensemble_forecast_fn=create_ensemble_forecast,
            create_scenario_analysis_fn=create_scenario_analysis,
            render_scenario_analysis_fn=render_scenario_analysis,
            render_scenario_summary_fn=render_scenario_summary,
            fig_ensemble=fig_ensemble,
            ensemble_forecast_values=ensemble_forecast_values,
            model_forecasts=model_forecasts,
            backtest_accuracy=backtest_accuracy,
            transactions=st.session_state.transactions,
        )
        render_dry_ice_mode(dry_ice_ctx, has_permission=has_permission)
         
if __name__ == "__main__":
    main()

