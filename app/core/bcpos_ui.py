"""
app/core/bcpos_ui.py
======================
Top-level entry point for "🧀 BCPOS Mode" — dispatches between
🏭 Manufacturing (cheese_production_ui.py) and 💰 Commercial (commercial_ui.py).

BCPOS is ONE product with two operational sides, not two separate app
modes — this keeps a single top-level radio entry in main.py's sidebar
(alongside All Items Mode / Dry Ice Mode), with Manufacturing/Commercial
as an internal choice, rather than treating Commercial as a sibling to
those genuinely separate businesses.

Deliberately a thin dispatcher, not merged into cheese_production_ui.py:
keeps that file's job to "render Manufacturing tabs" only, so it doesn't
need to import commercial_ui.py (or vice versa) — avoids a circular-import
risk and mirrors the existing all_items_ui.py / dry_ice_ui.py split, where
each *_ui.py module owns exactly one tab set.

Entry point:
    from app.core.bcpos_ui import render_bcpos_mode
    render_bcpos_mode(supabase_client=init_supabase(), has_permission=has_permission,
                       milk_cost_per_liter=..., raw_milk_price_per_liter=...)
"""
import streamlit as st
from typing import Optional, Callable

from app.core.cheese_shared_state import ensure_cheese_state
from app.core.cheese_production_ui import render_cheese_production_mode
from app.core.commercial_ui import render_commercial_mode


def render_bcpos_mode(supabase_client=None,
                       has_permission: Optional[Callable[[str], bool]] = None,
                       milk_cost_per_liter: float = 45.0,
                       raw_milk_price_per_liter: float = 35.0) -> None:
    
    # ============================================================
    # 🎨 BCPOS THEME + MODE BADGE  
    # ============================================================
    st.markdown("""
    <style>
        /* BCPOS Theme - Green/Cheese Colors */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(4px);
            border-radius: 12px;
            padding: 8px;
            border: 1px solid rgba(255,255,255,0.08);
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #2e7d32 0%, #66bb6a 100%) !important;
            color: white !important;
            box-shadow: 0 4px 15px rgba(46, 125, 50, 0.3) !important;
        }
        .stTabs [data-baseweb="tab"]:hover {
            background: rgba(46, 125, 50, 0.08) !important;
            color: #2e7d32 !important;
        }

        div[role="radiogroup"][style*="row"] label:has(input:checked),
        div[role="radiogroup"][aria-label="Section"] label:has(input:checked) {
            background: linear-gradient(135deg, #2e7d32 0%, #66bb6a 100%) !important;
            color: white !important;
            box-shadow: 0 4px 15px rgba(46, 125, 50, 0.3) !important;
        }

        .mode-badge-bcpos {
            background: linear-gradient(135deg, #2e7d32, #66bb6a);
            color: white;
            padding: 6px 16px;
            border-radius: 20px;
            display: inline-block;
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 15px;
            box-shadow: 0 2px 10px rgba(46, 125, 50, 0.3);
        }
    </style>
    
    <div class="mode-badge-bcpos">🧀 BCPOS MODE</div>
    """, unsafe_allow_html=True)

    # Called unconditionally, before the sub-mode radio, so correctness
    # doesn't depend on Manufacturing happening to run first — belt and
    # suspenders alongside the idempotent init inside each sub-mode.
    ensure_cheese_state(supabase_client)

    if has_permission and has_permission("view_cheese_production"):
        sub_mode = st.radio(
            "", ["🏭 Manufacturing", "💰 Commercial"],
            horizontal=True, key="bcpos_sub_mode",
        )
        st.markdown("---")
    else:
        from app.core.rbac import get_current_role
        # Don't force Manufacturing on a transient permission check —
        # only fall back if we've never set a sub-mode this session.
        sub_mode = st.session_state.get("bcpos_sub_mode", "🏭 Manufacturing")

    if sub_mode == "🏭 Manufacturing":
        render_cheese_production_mode(
            supabase_client=supabase_client, has_permission=has_permission,
            milk_cost_per_liter=milk_cost_per_liter,
            raw_milk_price_per_liter=raw_milk_price_per_liter,
        )
    else:  # "💰 Commercial"
        render_commercial_mode(supabase_client=supabase_client, has_permission=has_permission)