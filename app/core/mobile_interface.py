# core/mobile_interface.py
import streamlit as st
from datetime import datetime

class MobileInterface:
    def __init__(self):
        self.is_mobile = self._detect_mobile()
        self.mobile_features = {
            'stock_level_alerts': True,
            'responsive_layout': True,
            'touch_optimized': True
        }    
    def _detect_mobile(self):
        """Detect if user is on a mobile device"""
        try:
            user_agent = st.context.headers.get('User-Agent', '').lower()
            # Added more mobile keywords for better detection
            mobile_keywords = ['android', 'iphone', 'ipad', 'mobile', 'blackberry', 
                              'windows phone', 'opera mini', 'iemobile']
            return any(keyword in user_agent for keyword in mobile_keywords)
        except:
            return False
    
    def optimize_for_mobile(self):
        """Apply mobile-friendly styles and layouts"""
        st.markdown("""
        <style>
        @media (max-width: 768px) {
            .main-header { font-size: 1.8rem; }
            .metric-card { padding: 0.5rem; }
            .stButton > button { width: 100%; min-height: 44px; }
            .stDataFrame { font-size: 0.8rem; }
            .stPlotlyChart { height: 300px; }
        }
        .mobile-view {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .mobile-card {
            background: white;
            border-radius: 10px;
            padding: 15px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        </style>
        """, unsafe_allow_html=True)
        
        if self.is_mobile:
            st.markdown("""
            <style>
                [data-testid="stMetric"] {
                    background: #f0f2f6;
                    padding: 0.5rem;
                    border-radius: 10px;
                    margin-bottom: 0.5rem;
                }
                [data-testid="stMetricValue"] { font-size: 1.5rem !important; }
                [data-testid="stMetricLabel"] { font-size: 0.8rem !important; }
                .row-widget.stHorizontal { flex-wrap: wrap !important; }
                .stColumns > div {
                    flex: 1 1 50% !important;
                    min-width: 45% !important;
                }
                .stNumberInput input, 
                .stDateInput input, 
                .stSelectbox select {
                    font-size: 1rem !important;
                    min-height: 44px !important;
                }
                .streamlit-expanderHeader { min-height: 44px !important; }
                .dataframe {
                    display: block !important;
                    overflow-x: auto !important;
                    white-space: nowrap !important;
                    font-size: 0.8rem !important;
                }
            </style>
            """, unsafe_allow_html=True)
    
    
    def show_stock_alerts(self, current_stock, reorder_point, safety_stock):
        """Display stock level alerts - works on both mobile and desktop"""
        if current_stock <= safety_stock:
            st.error(f"""
            🚨 **CRITICAL STOCK ALERT**
            Current stock: {current_stock:.0f} kg | Safety stock: {safety_stock:.0f} kg
            **IMMEDIATE ACTION REQUIRED!**
            """)
        elif current_stock <= reorder_point:
            st.warning(f"""
            ⚠️ **REORDER ALERT**
            Current stock: {current_stock:.0f} kg | Reorder point: {reorder_point:.0f} kg
            Please place an order soon.
            """)
        elif current_stock <= reorder_point * 1.5:
            st.info(f"""
            ℹ️ **STOCK MONITORING**
            Current stock: {current_stock:.0f} kg — Consider reviewing inventory levels.
            """)
    
    def get_columns_count(self):
        """Return appropriate number of columns based on device"""
        return 2 if self.is_mobile else 4
    
    def get_chart_height(self):
        """Return appropriate chart height based on device"""
        return 300 if self.is_mobile else 400
    
    def is_mobile_device(self):
        """Return mobile detection status"""
        return self.is_mobile
    
    def get_mobile_chart_config(self):
        """Return chart configuration optimized for mobile"""
        return {
            'displayModeBar': False,
            'responsive': True,
            'staticPlot': False,
            'doubleClick': 'reset',
            'showTips': False,
            'modeBarButtonsToRemove': [
                'zoom2d', 'pan2d', 'select2d', 
                'lasso2d', 'zoomIn2d', 'zoomOut2d'
            ]
        }
    
    def optimize_chart_for_mobile(self, fig):
        """Apply mobile optimizations to a plotly figure"""
        if self.is_mobile:
            fig.update_layout(
                height=self.get_chart_height(),
                font=dict(size=10),
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(
                    orientation="h", 
                    yanchor="bottom", 
                    y=1.02, 
                    xanchor="center", 
                    x=0.5,
                    font=dict(size=10)
                )
            )
        return fig
    
    def show_mobile_welcome(self):
        """Show a welcome banner for mobile users - shown only once per session"""
        if self.is_mobile and 'mobile_welcome_shown' not in st.session_state:
            st.info("📱 Mobile view — charts and tables are sized for touch. Pinch to zoom on charts.")
            if st.button("Got it", use_container_width=True, key="mobile_welcome_btn"):
                st.session_state.mobile_welcome_shown = True
                

    def create_touch_button(self, label, key, help_text=None):
        """Create a button optimized for touch"""
        return st.button(
            label,
            key=key,
            help=help_text,
            use_container_width=True if self.is_mobile else False
        )
    
    def get_responsive_metric_display(self, metrics_list):
        """Display metrics in a responsive grid based on device"""
        cols_per_row = self.get_columns_count()
        for i in range(0, len(metrics_list), cols_per_row):
            cols = st.columns(cols_per_row)
            row_metrics = metrics_list[i:i + cols_per_row]
            for j, (label, value, delta) in enumerate(row_metrics):
                with cols[j]:
                    if delta:
                        st.metric(label, value, delta=delta)
                    else:
                        st.metric(label, value)
    
    def should_collapse_advanced(self):
        """Determine if advanced features should be collapsed - use in expanders"""
        return self.is_mobile
    
    # Optional: Add a helper method for mobile-optimized tables
    def display_mobile_table(self, dataframe, max_height=300):
        """Display a dataframe optimized for mobile viewing"""
        if self.is_mobile:
            st.dataframe(dataframe, use_container_width=True, height=max_height)
        else:
            st.dataframe(dataframe, use_container_width=True)
    
    # Optional: Add device info for debugging (remove in production)
    def show_device_info(self):
        """Display device detection info - useful for testing"""
        if self.is_mobile and st.sidebar.checkbox("Show Device Info", False):
            st.sidebar.info(f"📱 Mobile mode: {self.is_mobile}")
