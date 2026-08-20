"""
app/core/rbac.py
=================
Tab/section-visibility role-based access control — extracted out of
main.py. This is independent of AuthManager.check_permission() (that's a
different, finer-grained system) but reads the same underlying role from
st.session_state._auth.current_role, so both stay in sync automatically.

This module owns ONLY the permission model: what a Permission is, which
roles get which permissions, and the lookup helpers. It does not touch
UI rendering — main.py and the mode UI modules (all_items_ui.py,
dry_ice_ui.py, bcpos_ui.py, commercial_ui.py) call has_permission() /
filter_tabs() to decide what to show.

Usage from main.py:

    from app.core.rbac import (
        Permission, ROLE_PERMISSIONS,
        ALL_ITEMS_TAB_REQUIREMENTS, DRY_ICE_TAB_REQUIREMENTS,
        get_current_role, has_permission, filter_tabs,
        get_user_email_safe, log_access_denied,
    )

Note: if any of the mode UI modules currently do
`from app.main import Permission` (or ALL_ITEMS_TAB_REQUIREMENTS,
filter_tabs, etc.) directly, that still works after this move — main.py
re-imports every one of these names below, so they remain reachable at
app.main.<name> exactly as before. Worth a repo-wide grep for
`from app.main import` mentioning any of these names just to confirm
nothing was missed.
"""

from __future__ import annotations
from enum import Enum
import streamlit as st

from core.security import AuditLogger


class Permission(str, Enum):
    VIEW_STOCK = "view_stock"
    VIEW_STOCK_TAKE = "view_stock_take"
    RECORD_USAGE = "record_usage"
    RECORD_RECEIPT = "record_receipt"
    RUN_STOCK_TAKE = "run_stock_take"
    VIEW_ANALYTICS = "view_analytics"
    VIEW_FORECASTS = "view_forecasts"
    VIEW_COST_DATA = "view_cost_data"
    VIEW_STRATEGY = "view_strategy"
    VIEW_MAINTENANCE = "view_maintenance"
    GENERATE_REPORTS = "generate_reports"
    VIEW_REPORTS = "view_reports"
    EDIT_SYSTEM_PARAMS = "edit_system_params"
    CLEAR_TRANSACTIONS = "clear_transactions"
    VIEW_SECURITY_DASHBOARD = "view_security_dashboard"
    MANAGE_USERS = "manage_users"
    VIEW_CHEESE_PRODUCTION = "view_cheese_production"
    VIEW_CHEESE_RECIPES = "view_cheese_recipes"
    RECORD_MILK_RECEIPT = "record_milk_receipt"
    RECORD_CHEESE_SALE = "record_cheese_sale"
    RUN_PRODUCTION_PLAN = "run_production_plan"
    MANAGE_CHEESE_BATCHES = "manage_cheese_batches"
    MANAGE_LPO = "manage_lpo"
    MANAGE_RETURNS = "manage_returns"
    MANAGE_CUSTOMERS = "manage_customers"
    VIEW_COMMERCIAL_REPORTS = "view_commercial_reports"
    VIEW_CUSTOMER_ANALYTICS = "view_customer_analytics"
    MANAGE_PURCHASING = "manage_purchasing"
    RECONCILE_CHECKOUTS = "reconcile_checkouts"
    RECORD_TRANSFERS = "record_transfers"
    REVIEW_RECONCILIATION = "review_reconciliation"


ROLE_PERMISSIONS = {
    "admin": set(Permission),
    "manager": {
        Permission.VIEW_STOCK, Permission.VIEW_STOCK_TAKE,
        Permission.RECORD_USAGE, Permission.RECORD_RECEIPT, Permission.RUN_STOCK_TAKE,
        Permission.VIEW_ANALYTICS, Permission.VIEW_FORECASTS, Permission.VIEW_COST_DATA,
        Permission.VIEW_STRATEGY, Permission.VIEW_MAINTENANCE, Permission.MANAGE_PURCHASING,
        Permission.GENERATE_REPORTS, Permission.VIEW_REPORTS, Permission.RECONCILE_CHECKOUTS,
        Permission.RECORD_TRANSFERS, Permission.REVIEW_RECONCILIATION,
        # --- cheese: full operational access ---
        Permission.VIEW_CHEESE_PRODUCTION, Permission.VIEW_CHEESE_RECIPES,
        Permission.RECORD_MILK_RECEIPT, Permission.RECORD_CHEESE_SALE,
        Permission.RUN_PRODUCTION_PLAN, Permission.MANAGE_CHEESE_BATCHES,
        Permission.MANAGE_LPO, Permission.MANAGE_RETURNS, Permission.MANAGE_CUSTOMERS,
        Permission.VIEW_COMMERCIAL_REPORTS, Permission.VIEW_CUSTOMER_ANALYTICS,
    },
    "user": {
        Permission.VIEW_STOCK, Permission.VIEW_STOCK_TAKE,
        Permission.RECORD_USAGE, Permission.RECORD_RECEIPT, Permission.RUN_STOCK_TAKE,
        Permission.RECONCILE_CHECKOUTS, Permission.RECORD_TRANSFERS,
        # --- cheese: day-to-day recording only, no planning/batch release ---
        Permission.VIEW_CHEESE_PRODUCTION, Permission.VIEW_CHEESE_RECIPES,
        Permission.RECORD_MILK_RECEIPT, Permission.RECORD_CHEESE_SALE,
        Permission.MANAGE_LPO, Permission.MANAGE_RETURNS, Permission.MANAGE_CUSTOMERS,
    },
    "viewer": {
        Permission.VIEW_STOCK, Permission.VIEW_ANALYTICS, Permission.VIEW_REPORTS,
        # --- cheese: read-only ---
        Permission.VIEW_CHEESE_PRODUCTION, Permission.VIEW_CHEESE_RECIPES,
        Permission.VIEW_COMMERCIAL_REPORTS, Permission.VIEW_CUSTOMER_ANALYTICS,
    },
}

ALL_ITEMS_TAB_REQUIREMENTS = {
    "📦 Inventory": Permission.VIEW_STOCK,
    "📊 Stock Movements": Permission.RUN_STOCK_TAKE,
    "📈 All Items Analytics": Permission.VIEW_ANALYTICS,
    "🖼️ Visual Inventory": Permission.VIEW_ANALYTICS,
    "🤖 Advanced Analytics": Permission.VIEW_ANALYTICS,
    "🔄 JIT Purchasing": Permission.MANAGE_PURCHASING,
    "🔁 Transfers": Permission.RECORD_TRANSFERS,
    "🔒 Checkout Reconciliation": Permission.RECONCILE_CHECKOUTS,
}

DRY_ICE_TAB_REQUIREMENTS = {
    "📊 Order Analysis": Permission.VIEW_ANALYTICS,
    "🔮 Demand Forecast": Permission.VIEW_FORECASTS,
    "📦 Inventory Management": Permission.VIEW_STOCK,
    "💰 Cost Optimization": Permission.VIEW_COST_DATA,
    "📋 Recommendations": Permission.VIEW_STRATEGY,
    "🏗️ Storage Capacity": Permission.VIEW_MAINTENANCE,
    "📜 Transaction History": Permission.VIEW_REPORTS,
}


def get_current_role() -> str:
    auth = st.session_state.get('_auth')
    if auth and getattr(auth, 'is_authenticated', False):
        return getattr(auth, 'current_role', None) or None
    return None


def has_permission(permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(get_current_role(), set())


def filter_tabs(tab_requirements: dict) -> list:
    role_perms = ROLE_PERMISSIONS.get(get_current_role(), set())
    return [tab for tab, req in tab_requirements.items() if req in role_perms]


def get_user_email_safe() -> str:
    auth = st.session_state.get('_auth')
    if auth and getattr(auth, 'is_authenticated', False):
        return auth.current_user.get('email', 'unknown')
    return 'anonymous'


def log_access_denied(action: str):
    AuditLogger().log(
        action='ACCESS_DENIED',
        details=f"Role '{get_current_role()}' attempted: {action}",
        user=get_user_email_safe()
    )