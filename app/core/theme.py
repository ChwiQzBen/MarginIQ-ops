"""
app/core/theme.py
===================
Single source of truth for colors used across main.py and any UI module
that imports it (cheese_production_ui.py, and the new dry_ice_ui.py /
all_items_ui.py). Extracted from colors that were already in consistent
use throughout the app — not new branding, just one place to change it.
"""

THEME = {
    "primary": "#667eea",
    "primary_dark": "#764ba2",
    "primary_gradient": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
    "primary_rgb": "102, 126, 234",
    "info": "#4fc3f7",
    "success": "#28a745",
    "warning": "#ffc107",
    "danger": "#dc3545",
    "purple": "#9c27b0",
    "cyan": "#00bcd4",
    "orange": "#ff9800",
    "orange_dark": "#ff6f00",
    "indigo": "#1a237e",
    "grey": "#90a4ae",
    "text_secondary": "#888",
    "card_bg": "rgba(255,255,255,0.06)",
    "card_radius": "10px",
}


def kpi_card(label, value, icon="", color=None, value_color="#333",
             subtext=None, action=None):
    """One KPI tile. Replaces the ~25-line hand-written HTML block that
    used to be repeated 8 times in main.py's KPI dashboard.

    subtext/action accept either plain text or a raw HTML fragment (e.g.
    a colored <span>) — the wrapping div's own color is inherited unless
    the fragment sets its own, which browsers apply on top regardless.
    """
    import streamlit as st

    color = color or THEME["primary"]
    subtext_html = f'<div style="font-size: 11px; color: #888;">{subtext}</div>' if subtext else ""
    action_html = (
        f'<div style="font-size:10px;color:{THEME["primary"]};font-weight:600;'
        f'margin-top:6px;padding-top:6px;border-top:1px dashed rgba(102,126,234,0.25);">{action}</div>'
        if action else ""
    )
    st.markdown(f"""
    <div style="
        background: {THEME['card_bg']};
        border-radius: {THEME['card_radius']};
        padding: 12px 8px;
        text-align: center;
        border-left: 3px solid {color};
    ">
        <div style="font-size: 10px; color: #888; text-transform: uppercase; font-weight: 600; letter-spacing: 0.3px;">
            {icon} {label}
        </div>
        <div style="font-size: 22px; font-weight: 700; color: {value_color}; margin: 4px 0;">
            {value}
        </div>
        {subtext_html}
        {action_html}
    </div>
    """, unsafe_allow_html=True)


def inject_global_css() -> None:
    """
    Injects the app's global CSS (liquid-glass design system + Streamlit
    layout fixes) — extracted verbatim out of main.py, where it lived as
    a raw st.markdown(unsafe_allow_html=True) call sitting between
    st.set_page_config() and the Constants class.

    Call this once, in the same spot it used to occupy in main.py
    (right after st.set_page_config()).
    """
    import streamlit as st

    root_vars = f"""
    <style>
        :root {{
            --primary: {THEME['primary']};
            --primary-dark: {THEME['primary_dark']};
            --primary-rgb: {THEME['primary_rgb']};
        }}
    """

    st.markdown(root_vars + """
        /* ===== LIQUID GLASS DESIGN SYSTEM (Inspired by Zoho) ===== */
        
        /* Main glass effect for cards */
        .glass-card {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 16px;
            padding: 20px;
            margin: 10px 0;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.15);
            transition: all 0.3s ease;
        }
        
        .glass-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 40px 0 rgba(31, 38, 135, 0.25);
            background: rgba(255, 255, 255, 0.25);
        }
        
        /* Glass metric cards */
        .glass-metric {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);
        }
        
        .glass-metric:hover {
            transform: scale(1.02);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            background: rgba(255, 255, 255, 0.2);
        }
        
        /* Floating Quick Action Button (like Zoho's + menu) */
        .quick-action-fab {
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 30px;
            box-shadow: 0 4px 20px rgba(var(--primary-rgb), 0.4);
            cursor: pointer;
            z-index: 999;
            transition: all 0.3s ease;
            border: none;
        }
        
        .quick-action-fab:hover {
            transform: scale(1.1) rotate(90deg);
            box-shadow: 0 6px 30px rgba(var(--primary-rgb), 0.6);
        }
        
        /* Status cards with glass effect */
        .status-card {
            background: rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            border-radius: 12px;
            padding: 12px 16px;
            margin: 6px 0;
            border-left: 4px solid;
            transition: all 0.3s ease;
        }
        
        .status-card:hover {
            background: rgba(255, 255, 255, 0.15);
            transform: translateX(4px);
        }
        
        .status-critical {
            border-left-color: #dc3545;
            background: rgba(220, 53, 69, 0.08);
        }
        
        .status-warning {
            border-left-color: #ffc107;
            background: rgba(255, 193, 7, 0.08);
        }
        
        .status-success {
            border-left-color: #28a745;
            background: rgba(40, 167, 69, 0.08);
        }
        
        .status-info {
            border-left-color: #17a2b8;
            background: rgba(23, 162, 184, 0.08);
        }
        
        /* Modern sidebar with glass effect */
        section[data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.05) !important;
            backdrop-filter: blur(10px) !important;
            -webkit-backdrop-filter: blur(10px) !important;
            border-right: 1px solid rgba(255, 255, 255, 0.1) !important;
        }
        
        /* Enhanced metric styling */
        .stMetric {
            background: rgba(255, 255, 255, 0.05) !important;
            backdrop-filter: blur(4px) !important;
            -webkit-backdrop-filter: blur(4px) !important;
            border-radius: 12px !important;
            padding: 12px 16px !important;
            margin: 8px 0 !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            transition: all 0.3s ease !important;
        }
        
        .stMetric:hover {
            background: rgba(255, 255, 255, 0.1) !important;
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.05);
        }
        
        .stMetric label {
            font-size: 13px !important;
            line-height: 1.4 !important;
            margin-bottom: 6px !important;
            font-weight: 500 !important;
            color: #444 !important;
            white-space: normal !important;
            word-wrap: break-word !important;
        }
        
        .stMetric .stMetricValue {
            font-size: 24px !important;
            line-height: 1.3 !important;
            font-weight: 600 !important;
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-top: 2px !important;
        }
        
        .stMetric .stMetricDelta {
            font-size: 13px !important;
            margin-top: 2px !important;
        }
        
        /* Enhanced button styling */
        .stButton button {
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%) !important;
            color: white !important;
            border: none !important;
            border-radius: 8px !important;
            padding: 8px 20px !important;
            font-weight: 500 !important;
            transition: all 0.3s ease !important;
            box-shadow: 0 4px 15px rgba(var(--primary-rgb), 0.2) !important;
        }
        
        .stButton button:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 25px rgba(var(--primary-rgb), 0.3) !important;
        }
        
        .stButton button:active {
            transform: scale(0.98) !important;
        }
        
        /* Enhanced tab styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(4px);
            border-radius: 12px;
            padding: 8px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        
        .stTabs [data-baseweb="tab"] {
            background: transparent !important;
            border-radius: 8px !important;
            padding: 8px 16px !important;
            transition: all 0.3s ease !important;
            color: #666 !important;
        }
        
        .stTabs [data-baseweb="tab"]:hover {
            background: rgba(var(--primary-rgb), 0.08) !important;
            color: var(--primary) !important;
        }
        
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%) !important;
            color: white !important;
            box-shadow: 0 4px 15px rgba(var(--primary-rgb), 0.3) !important;
        }
        
        /* Expander styling (merged — see theme.py cleanup notes) */
        .streamlit-expanderHeader {
            background: rgba(255, 255, 255, 0.05) !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
            padding: 10px 0 !important;
            transition: all 0.3s ease !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
        }
        
        .streamlit-expanderHeader:hover {
            background: rgba(255, 255, 255, 0.1) !important;
            transform: translateX(4px);
        }
        
        /* Enhanced sidebar */
        section[data-testid="stSidebar"] div[data-testid="stSelectbox"] {
            background: rgba(255, 255, 255, 0.03) !important;
            border-radius: 8px !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
        }
        
        /* Smooth scrolling */
        .main {
            scroll-behavior: smooth;
        }
        
        /* Loading animation */
        @keyframes shimmer {
            0% { background-position: -200% center; }
            100% { background-position: 200% center; }
        }
        
        .shimmer-loading {
            background: linear-gradient(90deg, 
                rgba(255,255,255,0.05) 25%, 
                rgba(255,255,255,0.1) 50%, 
                rgba(255,255,255,0.05) 75%);
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
            border-radius: 8px;
            padding: 20px;
        }
        
        /* Notification toast */
        .toast {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 16px 24px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.2);
            z-index: 1000;
            animation: slideInRight 0.5s ease;
        }
        
        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        /* Data tables (merged — see theme.py cleanup notes) */
        .stDataFrame {
            background: rgba(255, 255, 255, 0.03) !important;
            border-radius: 12px !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            overflow: auto !important;
            min-height: 100px !important;
            transition: none !important;
            animation: none !important;
        }
        
        .stDataFrame table {
            border-collapse: separate !important;
            border-spacing: 0 !important;
            table-layout: fixed !important;
            width: 100% !important;
        }
        
        .stDataFrame thead tr th {
            background: rgba(var(--primary-rgb), 0.08) !important;
            font-weight: 600 !important;
            padding: 10px 12px !important;
            border-bottom: 2px solid rgba(var(--primary-rgb), 0.2) !important;
        }
        
        .stDataFrame tbody tr:hover {
            background: rgba(var(--primary-rgb), 0.05) !important;
            transition: background 0.3s ease;
        }

        .stDataFrame iframe {
            min-height: 200px !important;
            transition: none !important;
            animation: none !important;
        }
        
        [data-testid="stDataFrame"] > div {
            transition: none !important;
            animation: none !important;
        }
        
        /* ===== YOUR ORIGINAL CSS KEPT BELOW ===== */
        
        .main-header {
            font-size: 2.5rem;
            font-weight: bold;
            color: #1f77b4;
            text-align: center;
            margin-bottom: 2rem;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
        }
        
        .metric-card {
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            padding: 1rem;
            border-radius: 10px;
            color: white;
            text-align: center;
            margin: 0.5rem 0;
        }
        
        .success-box {
            background-color: #d4edda;
            border: 1px solid #c3e6cb;
            color: #155724;
            padding: 1rem;
            border-radius: 5px;
            margin: 1rem 0;
        }
        
        .info-box {
            background-color: #d1ecf1;
            border: 1px solid #bee5eb;
            color: #0c5460;
            padding: 1rem;
            border-radius: 5px;
            margin: 1rem 0;
        }
        
        .alert-critical {
            background-color: #f8d7da;
            border-left: 5px solid #dc3545;
            padding: 10px;
            margin: 10px 0;
        }
        
        .alert-warning {
            background-color: #fff3cd;
            border-left: 5px solid #ffc107;
            padding: 10px;
            margin: 10px 0;
        }
        
        /* Fix tab content spacing */
        .stTabs [role="tabpanel"] {
            padding-top: 30px !important;
            padding-bottom: 20px !important;
        }
        
        /* Fix all column spacing */
        .stColumns {
            gap: 15px !important;
            margin-top: 10px !important;
            margin-bottom: 10px !important;
        }
        
        /* Fix markdown spacing */
        .stMarkdown {
            margin-bottom: 15px !important;
        }
        
        /* Fix heading spacing */
        .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
            margin-top: 20px !important;
            margin-bottom: 15px !important;
        }
        
        /* Fix plotly chart spacing */
        .stPlotlyChart {
            margin-top: 15px !important;
            margin-bottom: 25px !important;
        }
        
        /* Fix container padding */
        .stContainer {
            padding: 5px 0 !important;
        }
        
        /* Fix horizontal rule spacing */
        hr {
            margin: 30px 0 !important;
        }
         
        
        /* ===== MOBILE RESPONSIVENESS ===== */
        @media (max-width: 768px) {
            .glass-card {
                padding: 12px !important;
                margin: 6px 0 !important;
            }
            
            .stColumns {
                gap: 5px !important;
                flex-wrap: wrap !important;
            }
            
            .stMetric {
                padding: 8px 3px !important;
                min-height: 60px !important;
            }
            
            .stMetric label {
                font-size: 11px !important;
            }
            
            .stMetric .stMetricValue {
                font-size: 18px !important;
            }
            
            .stTabs [role="tabpanel"] {
                padding-top: 15px !important;
            }
            
            [data-testid="stMetricValue"] {
                font-size: 16px !important;
            }
            
            [data-testid="stDataFrame"] iframe {
                min-height: 150px !important;
            }
            
            .quick-action-fab {
                width: 50px;
                height: 50px;
                font-size: 24px;
                bottom: 20px;
                right: 20px;
            }
            
            .stButton button {
                padding: 6px 12px !important;
                font-size: 13px !important;
            }
        }
    </style>
    """, unsafe_allow_html=True)    