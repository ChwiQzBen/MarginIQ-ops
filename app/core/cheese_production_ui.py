from __future__ import annotations
"""
app/core/cheese_production_ui.py
==================================
Streamlit tab bodies for "🧀 Cheese Production Mode" — the third mode
alongside "All Items Mode" and "Dry Ice Mode" in main.py.

Deliberately decoupled from main.py's Permission/RBAC machinery: this module
takes a `has_permission` callable so it never imports from main.py (avoids a
circular import, since main.py is the one that imports THIS module).

Entry point:

    from app.core.cheese_production_ui import render_cheese_production_mode
    render_cheese_production_mode(supabase_client=init_supabase(), has_permission=has_permission)

See CHEESE_INTEGRATION_GUIDE.md for the exact main.py wiring (Permission
enum entries, tab-requirements dict, mode radio option, sidebar section).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from datetime import datetime, date, timedelta
from typing import Optional, Callable, Tuple
import streamlit as st
import pandas as pd

from newsvendor_engine import AgingConfig
from production_tracking import (
    RecipeBook, CheeseRecipe, BOMLineItem, OperationStep,
    BatchTracker, BatchStatus, DEFAULT_PRODUCTION_CHECKPOINTS, FEFOInventory,
)
from production_plan import ProductionPlanner
from demand_forecast import CheeseDemandForecaster
from app.core.cheese_forecast_adapter import make_ensemble_demand_forecaster
from app.core.production_reports import build_report_data, generate_production_report_pdf, summarize_report_data
from app.core.report_ui import render_report_tab, KPI, TableSection, ChartSection
from scenario_analysis import Scenario, run_scenario, compare_scenarios

from app.core.cheese_shared_state import ensure_cheese_state
from app.core.cheese_data_access import (
    init_cheese_storage, load_recipe_book, save_recipe, delete_recipe,
    save_milk_receipt, get_milk_receipts, get_milk_liters_for_date,
    save_cheese_sale, get_sales_history, get_daily_sales_kg,
    load_batch_tracker, get_aging_room_capacity_kg, set_aging_room_capacity_kg,
    get_aging_room_used_kg, check_aging_room_capacity,
    get_confirmed_demand_for_date,
)


CHEESE_TAB_NAMES = [
    "🧀 Recipes",
    "🥛 Milk Receipts",
    "📋 Production Planning",
    "🏭 Batch Tracking & QC",
    "🧊 Aging Room",
    "📦 FEFO Inventory",
    "📄 Production Reports",
]


@st.cache_data(ttl=3600, show_spinner="Running demand forecast ensemble...")
def _cached_ensemble_demand(cheese_name: str, daily_sales_kg: Tuple[float, ...]) -> Tuple[float, float]:
    """Cached wrapper around the ensemble adapter. Without this, the
    ensemble would re-train on EVERY Streamlit rerun — i.e. every widget
    interaction anywhere on the page, not just this tab — for every cheese
    with 60+ days of sales history. Cached 1hr per (cheese, sales-history)
    combination instead."""
    return make_ensemble_demand_forecaster()(cheese_name, list(daily_sales_kg))


# ============================================================
# SESSION STATE / SETUP
# ============================================================
def _ensure_state(supabase_client) -> None:
    """Thin wrapper — the actual init now lives in cheese_shared_state.py so
    Commercial Mode can call the same function and share the same
    RecipeBook/BatchTracker instances within a session, instead of each
    mode silently loading its own independent copy."""
    ensure_cheese_state(supabase_client)


def render_cheese_production_mode(supabase_client=None,
                                   has_permission: Optional[Callable[[str], bool]] = None,
                                   milk_cost_per_liter: float = 45.0,
                                   raw_milk_price_per_liter: float = 35.0) -> None:
    """Main entry point — call this from main.py inside the
    '🧀 Cheese Production Mode' branch, after filtering visible tab names
    the same way the other two modes already do."""

    def _allowed(permission: str) -> bool:
        return has_permission(permission) if has_permission else True

    _ensure_state(supabase_client)
    book: RecipeBook = st.session_state.cheese_recipe_book
    tracker: BatchTracker = st.session_state.cheese_tracker

    visible = [name for name, perm in {
        "🧀 Recipes": "view_cheese_recipes",
        "🥛 Milk Receipts": "record_milk_receipt",
        "📋 Production Planning": "run_production_plan",
        "🏭 Batch Tracking & QC": "manage_cheese_batches",
        "🧊 Aging Room": "view_cheese_production",
        "📦 FEFO Inventory": "view_cheese_production",
        "📄 Production Reports": "view_reports",
    }.items() if _allowed(perm)]

    if not visible:
        st.warning("This section isn't available for your current role. If you feel this is a mistake, please contact your administrator.")
        return

    if "cheese_active_tab" not in st.session_state or st.session_state.cheese_active_tab not in visible:
        st.session_state.cheese_active_tab = visible[0]

    active_tab = st.radio(
        "Section", visible, horizontal=True,
        key="cheese_active_tab", label_visibility="collapsed",
    )
    st.markdown("---")

    if active_tab == "🧀 Recipes":
        _render_recipes_tab(book, supabase_client)
    elif active_tab == "🥛 Milk Receipts":
        _render_milk_receipts_tab(supabase_client)
    elif active_tab == "📋 Production Planning":
        _render_production_planning_tab(book, tracker, supabase_client,
                                        milk_cost_per_liter, raw_milk_price_per_liter)
    elif active_tab == "🏭 Batch Tracking & QC":
        _render_batch_tracking_tab(book, tracker, supabase_client)
    elif active_tab == "🧊 Aging Room":
        _render_aging_room_tab(tracker, supabase_client)
    elif active_tab == "📦 FEFO Inventory":
        _render_fefo_inventory_tab(book, tracker)
    elif active_tab == "📄 Production Reports":
        _render_reports_tab(book, tracker)


# ============================================================
# TAB 1: RECIPES
# ============================================================
def _render_recipes_tab(book: RecipeBook, supabase_client) -> None:
    st.markdown("### 🧀 Cheese Recipes")

    if book.list_names():
        rows = []
        for name in book.list_names():
            r = book.get(name)
            rows.append({
                "Name": r.name, "Code": r.product_code, "Category": r.category,
                "Batch Size (kg)": r.batch_size_kg, "Milk (L/batch)": r.milk_liters_per_batch,
                "Yield (kg/L)": round(r.yield_kg_per_liter_milk(), 3),
                "Unit Cost (KSh/kg, excl. milk)": round(r.unit_cost(), 2),
                "Shelf Life (days)": r.shelf_life_days,
                "Aging": f"{r.aging.aging_years:.1f}yr" if r.aging else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No recipes yet — add your first one below.")

    with st.expander("➕ Add / Edit Recipe", expanded=not book.list_names()):
        existing_names = ["(new recipe)"] + book.list_names()
        pick = st.selectbox("Recipe", existing_names, key="recipe_picker")
        editing = book.get(pick) if pick != "(new recipe)" else None

        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name", value=editing.name if editing else "", key="recipe_name")
            product_code = st.text_input("Product Code", value=editing.product_code if editing else "",
                                          key="recipe_code")
            category = st.text_input("Category", value=editing.category if editing else "Fresh Cheese",
                                      key="recipe_category")
            batch_size_kg = st.number_input("Batch Size (kg finished cheese)",
                                             value=float(editing.batch_size_kg) if editing else 6.0,
                                             min_value=0.1, step=0.5, key="recipe_batch_size")
        with col2:
            milk_liters = st.number_input("Milk per Batch (liters)",
                                           value=float(editing.milk_liters_per_batch) if editing else 50.0,
                                           min_value=0.1, step=5.0, key="recipe_milk_liters")
            shelf_life = st.number_input("Shelf Life (days, once finished)",
                                          value=int(editing.shelf_life_days) if editing else 30,
                                          min_value=1, step=1, key="recipe_shelf_life")
            lead_time = st.number_input("Lead Time (days, order to finished good)",
                                         value=int(editing.lead_time_days) if editing else 1,
                                         min_value=0, step=1, key="recipe_lead_time")

        st.markdown("**Non-milk ingredients** (cost per batch — milk itself is costed separately)")
        default_ing = pd.DataFrame([
            {"name": i.name, "quantity": i.quantity, "unit": i.unit, "cost_per_unit": i.cost_per_unit}
            for i in editing.non_milk_ingredients
        ]) if editing else pd.DataFrame(columns=["name", "quantity", "unit", "cost_per_unit"])
        ing_df = st.data_editor(default_ing, num_rows="dynamic", use_container_width=True,
                                 key="recipe_ingredients_editor")

        st.markdown("**Packaging**")
        default_pkg = pd.DataFrame([
            {"name": i.name, "quantity": i.quantity, "unit": i.unit, "cost_per_unit": i.cost_per_unit}
            for i in editing.packaging
        ]) if editing else pd.DataFrame(columns=["name", "quantity", "unit", "cost_per_unit"])
        pkg_df = st.data_editor(default_pkg, num_rows="dynamic", use_container_width=True,
                                 key="recipe_packaging_editor")

        st.markdown("**Operations**")
        default_ops = pd.DataFrame([
            {"name": o.name, "hours": o.hours, "machine": o.machine, "cost_per_hour": o.cost_per_hour}
            for o in editing.operations
        ]) if editing else pd.DataFrame(columns=["name", "hours", "machine", "cost_per_hour"])
        ops_df = st.data_editor(default_ops, num_rows="dynamic", use_container_width=True,
                                 key="recipe_operations_editor")

        st.markdown("**Aging** (leave unchecked for fresh cheese)")
        has_aging = st.checkbox("This cheese ages", value=bool(editing.aging) if editing else False,
                                 key="recipe_has_aging")
        aging = None
        if has_aging:
            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                aging_years = st.number_input("Aging Years", value=editing.aging.aging_years if (editing and editing.aging) else 1.0,
                                               min_value=0.1, step=0.5, key="recipe_aging_years")
                yield_rate = st.number_input("Yield Rate (survives aging, %/yr)",
                                              value=(editing.aging.yield_rate * 100) if (editing and editing.aging) else 90.0,
                                              min_value=1.0, max_value=100.0, step=1.0, key="recipe_yield_rate") / 100
            with ac2:
                storage_rate = st.number_input("Storage Cost Rate (%/yr)",
                                                value=(editing.aging.storage_cost_rate * 100) if (editing and editing.aging) else 3.0,
                                                min_value=0.0, step=0.5, key="recipe_storage_rate") / 100
                aging_loss_rate = st.number_input("Aging Loss Rate (%/yr)",
                                                   value=(editing.aging.aging_loss_rate * 100) if (editing and editing.aging) else 2.0,
                                                   min_value=0.0, step=0.5, key="recipe_aging_loss") / 100
            with ac3:
                financing_rate = st.number_input("Financing Rate (%/yr)",
                                                  value=(editing.aging.financing_rate * 100) if (editing and editing.aging) else 5.0,
                                                  min_value=0.0, step=0.5, key="recipe_financing_rate") / 100
                overage_mult = st.number_input("Overage Penalty Multiplier",
                                                value=editing.aging.overage_penalty_multiplier if (editing and editing.aging) else 1.0,
                                                min_value=1.0, step=0.1, key="recipe_overage_mult")
            aging = AgingConfig(aging_years=aging_years, storage_cost_rate=storage_rate,
                                 aging_loss_rate=aging_loss_rate, financing_rate=financing_rate,
                                 yield_rate=yield_rate, overage_penalty_multiplier=overage_mult)

        col_save, col_delete = st.columns(2)
        with col_save:
            if st.button("💾 Save Recipe", type="primary", use_container_width=True, key="save_recipe_btn"):
                if not name:
                    st.error("Name is required.")
                else:
                    try:
                        recipe = CheeseRecipe(
                            name=name, product_code=product_code, category=category,
                            batch_size_kg=batch_size_kg, milk_liters_per_batch=milk_liters,
                            shelf_life_days=int(shelf_life), lead_time_days=int(lead_time),
                            non_milk_ingredients=[BOMLineItem(**row) for row in ing_df.to_dict("records") if row.get("name")],
                            packaging=[BOMLineItem(**row) for row in pkg_df.to_dict("records") if row.get("name")],
                            operations=[OperationStep(**row) for row in ops_df.to_dict("records") if row.get("name")],
                            aging=aging,
                            recipe_version=editing.recipe_version if editing else "v1.0",
                        )
                        wrote_to_supabase, save_error = save_recipe(recipe, supabase_client)
                        book.add(recipe)
                        if wrote_to_supabase:
                            st.success(f"✅ Saved '{name}' to Supabase. Unit cost (excl. milk): KSh {recipe.unit_cost():.2f}/kg, "
                                       f"yield {recipe.yield_kg_per_liter_milk():.3f} kg/L milk.")
                        else:
                            st.warning(f"⚠️ '{name}' saved LOCALLY ONLY — Supabase write failed. "
                                       f"This recipe will be LOST on the next app restart or redeploy.\n\n"
                                       f"**Reason:** {save_error or 'Unknown error'}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Could not save recipe: {e}")
        with col_delete:
            if editing and st.button("🗑️ Delete Recipe", use_container_width=True, key="delete_recipe_btn"):
                delete_recipe(editing.name, supabase_client)
                book.remove(editing.name)  
                st.success(f"Deleted '{editing.name}'.")
                st.rerun()

# ============================================================
# TAB 2: MILK RECEIPTS
# ============================================================
def _render_milk_receipts_tab(supabase_client) -> None:
    st.markdown("### 🥛 Milk Receipts")
    st.caption("This is the daily milk supply the Production Planning tab allocates across cheeses.")

    today_total = get_milk_liters_for_date(date.today(), supabase_client)
    st.metric("Milk received today", f"{today_total:,.0f} L")

    with st.form("milk_receipt_form"):
        col1, col2 = st.columns(2)
        with col1:
            receipt_date = st.date_input("Receipt Date", value=date.today())
            liters = st.number_input("Liters", min_value=0.0, value=300.0, step=10.0)
        with col2:
            cost_per_liter = st.number_input("Cost per Liter (KSh)", min_value=0.0, value=45.0, step=1.0)
            supplier = st.text_input("Supplier", placeholder="e.g. Kiambu Dairy Farmers Co-op")
        notes = st.text_input("Notes (optional)")

        if st.form_submit_button("📥 Record Milk Receipt", type="primary"):
            if liters <= 0:
                st.error("Enter a quantity greater than 0.")
            else:
                try:
                    new_id = save_milk_receipt(receipt_date, liters, cost_per_liter, supplier, notes, supabase_client)
                    st.success(f"✅ Recorded {liters:,.0f}L at KSh {cost_per_liter:.2f}/L (receipt #{new_id}).")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Could not record milk receipt: {e}")

    st.markdown("---")
    st.markdown("#### Recent Receipts")
    start = date.today() - timedelta(days=30)
    receipts = get_milk_receipts(start_date=start, supabase_client=supabase_client)
    if receipts:
        df = pd.DataFrame(receipts)
        df["total_cost"] = df["liters"] * df["cost_per_liter"]
        st.dataframe(
            df[["id", "date", "liters", "cost_per_liter", "total_cost", "supplier", "notes"]],
            use_container_width=True, hide_index=True,
        )
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Receipts CSV", csv,
                            file_name=f"milk_receipts_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
    else:
        st.info("No milk receipts in the last 30 days.")


def _render_whatif_expander(book: RecipeBook, tracker: BatchTracker,
                             milk_cost_per_liter: float, raw_milk_price_per_liter: float,
                             milk_available_l: float, demand_forecast: dict, selling_prices: dict,
                             base_plan, aging_room_capacity_kg: float, aging_room_used_kg: float,
                             confirmed_demand: dict) -> None:
    """
    'What if?' analysis for today's production plan — sliders react
    immediately (same pattern as the Dry Ice What-If Simulator in main.py),
    plus a named-scenario comparison table. Lives as an expander directly
    under the plan it's perturbing rather than a separate tab, since it's
    a continuation of the same planning workflow, not a separate feature.
    """
    if base_plan is None:
        st.info("🔮 What-If Analysis will be available once you build a production plan above.")
        return

    # Same filter the main plan build uses — only cheeses with real demand + price
    active_demand = {k: v for k, v in demand_forecast.items() if v[0] > 0 and selling_prices.get(k, 0) > 0}
    active_prices = {k: selling_prices[k] for k in active_demand}
    active_confirmed = {k: v for k, v in confirmed_demand.items() if k in active_demand}

    if not active_demand:
        st.info("🔮 What-If Analysis needs at least one cheese with demand and price set above.")
        return

    with st.expander("🔮 What-If Analysis", expanded=False):
        st.caption("Adjust assumptions below to see how they'd affect today's production plan. "
                   "This does not change your live data or the plan above.")

        sim_col1, sim_col2 = st.columns(2)
        with sim_col1:
            milk_change_pct = st.slider(
                "Milk supply change (%)", min_value=-50, max_value=50, value=0, step=5,
                help="Simulate a supplier shortfall or surplus vs. today's milk available",
                key="cheese_whatif_milk_change",
            )
        with sim_col2:
            demand_change_pct = st.slider(
                "Demand change (%)", min_value=-50, max_value=100, value=0, step=5,
                help="Simulate a demand spike or slump across all cheeses",
                key="cheese_whatif_demand_change",
            )

        with st.expander("Per-cheese demand override (optional)", expanded=False):
            override_names = st.multiselect(
                "Apply a different demand change to specific cheeses",
                list(active_demand.keys()), key="cheese_whatif_override_names",
            )
            per_cheese_overrides = {}
            if override_names:
                override_cols = st.columns(min(len(override_names), 3))
                for i, name in enumerate(override_names):
                    with override_cols[i % 3]:
                        per_cheese_overrides[name] = st.slider(
                            f"{name} (%)", -50, 100, 0, 5, key=f"cheese_whatif_override_{name}",
                        )

        with st.expander("Aging room capacity override (optional)", expanded=False):
            max_slider = float(aging_room_capacity_kg * 2) if aging_room_capacity_kg > 0 else 100.0
            sim_aging_capacity = st.slider(
                "Simulated aging room capacity (kg)",
                min_value=0.0, max_value=max_slider,
                value=float(aging_room_capacity_kg), step=5.0,
                help="Test a tighter (or looser) physical aging-room constraint",
                key="cheese_whatif_aging_capacity",
            )

        anything_changed = (
            milk_change_pct != 0
            or demand_change_pct != 0
            or any(v != 0 for v in per_cheese_overrides.values())
            or sim_aging_capacity != aging_room_capacity_kg
        )

        # Rebuilt locally rather than reusing the outer planner variable, which
        # (as the Execute-Plan block below already has to account for) may be
        # out of scope after a rerun — this is a cheap, stateless wrapper.
        whatif_planner = ProductionPlanner(book, tracker, milk_cost_per_liter, raw_milk_price_per_liter)

        if anything_changed:
            sim_scenario = Scenario(
                name="Custom What-If",
                description="Simulated via What-If Analysis controls",
                milk_multiplier=1 + milk_change_pct / 100,
                demand_multiplier=1 + demand_change_pct / 100,
                per_cheese_demand_multiplier={
                    k: 1 + v / 100 for k, v in per_cheese_overrides.items() if v != 0
                },
            )
            sim_plan = run_scenario(
                whatif_planner, milk_available_l, active_demand, active_prices, sim_scenario,
                aging_room_capacity_kg=sim_aging_capacity, aging_room_used_kg=aging_room_used_kg,
                confirmed_demand=active_confirmed,
            )

            st.markdown("---")
            compare_col1, compare_col2 = st.columns(2)
            with compare_col1:
                st.markdown("**📍 Current Plan (Baseline)**")
                st.metric("Total Profit", f"KSh {base_plan.total_profit:,.0f}")
                st.metric("Unmet Demand", f"{base_plan.unmet_demand_kg:.1f} kg")
                st.metric("Milk Utilization", f"{base_plan.allocation.utilization_rate:.0%}")
            with compare_col2:
                st.markdown("**🔮 Simulated Scenario**")
                delta_profit = sim_plan.total_profit - base_plan.total_profit
                delta_unmet = sim_plan.unmet_demand_kg - base_plan.unmet_demand_kg
                delta_util = sim_plan.allocation.utilization_rate - base_plan.allocation.utilization_rate
                st.metric("Total Profit", f"KSh {sim_plan.total_profit:,.0f}", delta=f"{delta_profit:+,.0f}")
                st.metric("Unmet Demand", f"{sim_plan.unmet_demand_kg:.1f} kg",
                          delta=f"{delta_unmet:+.1f} kg", delta_color="inverse")
                st.metric("Milk Utilization", f"{sim_plan.allocation.utilization_rate:.0%}",
                          delta=f"{delta_util:+.0%}")

            if sim_plan.warnings:
                for w in sim_plan.warnings:
                    st.warning(f"⚠️ {w}")
        else:
            st.caption("Move a slider above to see the simulated impact.")

        # ---- Named scenario comparison against baseline ----
        st.markdown("---")
        st.markdown("#### 📊 Compare Named Scenarios")
        st.caption("Run a fixed set of scenarios side-by-side against today's baseline plan.")

        preset_map = {
            "Milk drops 20%": Scenario(name="Milk drops 20%", description="Supplier shortfall",
                                        milk_multiplier=0.8),
            "Milk drops 40%": Scenario(name="Milk drops 40%", description="Severe supplier shortfall",
                                        milk_multiplier=0.6),
            "Demand spike +30%": Scenario(name="Demand spike +30%", description="Unexpected order surge",
                                           demand_multiplier=1.3),
            "Demand spike +50%": Scenario(name="Demand spike +50%", description="Major demand spike",
                                           demand_multiplier=1.5),
            "Pasteurizer down": Scenario(
                name="Pasteurizer down", description="Approximated as today's milk unusable",
                milk_multiplier=0.0,
                equipment_note=("Models 'no milk processed today,' not the pasteurizer itself — "
                                 "there's no equipment-capacity constraint in the engine yet."),
            ),
        }

        preset_names = st.multiselect(
            "Select scenarios to compare",
            list(preset_map.keys()),
            default=["Milk drops 20%", "Demand spike +30%"],
            key="cheese_whatif_presets",
        )

        if st.button("🔄 Run Comparison", key="cheese_whatif_run_comparison"):
            if not preset_names:
                st.warning("Select at least one scenario to compare.")
            else:
                selected_scenarios = [preset_map[n] for n in preset_names]
                results = compare_scenarios(
                    whatif_planner, milk_available_l, active_demand, active_prices, selected_scenarios,
                    aging_room_capacity_kg=aging_room_capacity_kg, aging_room_used_kg=aging_room_used_kg,
                    confirmed_demand=active_confirmed,
                )

                rows = []
                for r in results[1:]:  # skip baseline row — already shown as "Current Plan" above
                    if r.error:
                        rows.append({
                            "Scenario": r.scenario.name, "Profit (KSh)": "Error",
                            "Δ Profit": "-", "Unmet Demand (kg)": "-", "Δ Unmet": "-",
                            "Milk Util.": "-", "Note": r.error,
                        })
                        continue
                    rows.append({
                        "Scenario": r.scenario.name,
                        "Profit (KSh)": f"{r.plan.total_profit:,.0f}",
                        "Δ Profit": f"{r.profit_delta_vs_base:+,.0f}",
                        "Unmet Demand (kg)": f"{r.plan.unmet_demand_kg:.1f}",
                        "Δ Unmet": f"{r.unmet_demand_delta_vs_base:+.1f}",
                        "Milk Util.": f"{r.plan.allocation.utilization_rate:.0%}",
                        "Note": r.scenario.equipment_note or "",
                    })

                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                for r in results:
                    if not r.error and r.plan.warnings:
                        for w in r.plan.warnings:
                            st.caption(f"⚠️ [{r.scenario.name}] {w}")

# ============================================================
# TAB 3: PRODUCTION PLANNING
# ============================================================
def _render_production_planning_tab(book: RecipeBook, tracker: BatchTracker, supabase_client,
                                     milk_cost_per_liter: float, raw_milk_price_per_liter: float) -> None:
    st.markdown("### 📋 Today's Production Plan")

    if not book.list_names():
        st.warning("Add at least one recipe in the 🧀 Recipes tab before planning production.")
        return

    planning_date = date.today() + timedelta(days=1)
    st.caption(f"Planning production for delivery on {planning_date.strftime('%Y-%m-%d')} (tomorrow).")
    confirmed_demand = get_confirmed_demand_for_date(planning_date, supabase_client)

    planning_date = date.today() + timedelta(days=1)
    confirmed_demand = get_confirmed_demand_for_date(planning_date, supabase_client)
    if confirmed_demand:
        st.caption(f"📄 {sum(confirmed_demand.values()):.0f}kg confirmed via open LPOs "
                   f"for delivery on {planning_date.strftime('%b %d')}.")

    default_milk = get_milk_liters_for_date(date.today(), supabase_client)
    milk_available_l = st.number_input(
        "Milk available today (liters)", min_value=0.0,
        value=float(default_milk) if default_milk > 0 else 0.0, step=10.0,
        help="Defaults to today's total from the 🥛 Milk Receipts tab — override if needed.",
    )

    capacity_kg = get_aging_room_capacity_kg(supabase_client=supabase_client)
    used_kg = get_aging_room_used_kg(tracker)
    remaining_kg = max(0.0, capacity_kg - used_kg)
    cap_col1, cap_col2, cap_col3 = st.columns(3)
    cap_col1.metric("Aging Room Capacity", f"{capacity_kg:,.0f} kg")
    cap_col2.metric("Currently Aging", f"{used_kg:,.0f} kg")
    cap_col3.metric("Room Remaining", f"{remaining_kg:,.0f} kg")

    st.markdown("#### Demand & Pricing per Cheese")
    st.caption(
        "Calculated automatically from your sales history once a cheese has 60+ days "
        "of data. You can always type over it — use 📈 to reset back to the calculated "
        "number."
    )
    forecaster = CheeseDemandForecaster(
        ml_forecaster=lambda name, sales: _cached_ensemble_demand(name, tuple(sales))
    )
    demand_forecast = {}
    selling_prices = {}
    for name in book.list_names():
        recipe = book.get(name)
        with st.expander(f"{name}" + (" (aged)" if recipe.aging else ""), expanded=False):
            prior_mean, prior_std = st.session_state.cheese_demand_overrides.get(name, (0.0, 0.0))
            sales_history = get_daily_sales_kg(name, days=90, supabase_client=supabase_client)
            fcast = forecaster.forecast(name, sales_history,
                                         fallback_mean=prior_mean if prior_mean > 0 else 1.0)

            has_override = prior_mean > 0 or prior_std > 0
            default_mean = prior_mean if has_override else round(fcast.mean, 1)
            default_std = prior_std if has_override else round(fcast.std, 1)

            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            with c1:
                mean_kg = st.number_input(f"Expected demand (kg)", min_value=0.0,
                                           value=float(default_mean), step=1.0, key=f"demand_mean_{name}")
            with c2:
                std_kg = st.number_input(f"Demand std dev (kg)", min_value=0.0,
                                          value=float(default_std) if default_std else round(mean_kg * 0.3, 1),
                                          step=1.0, key=f"demand_std_{name}")
            with c3:
                price = st.number_input(f"Selling price (KSh/kg)", min_value=0.0,
                                         value=float(st.session_state.cheese_selling_prices.get(name, 0.0)),
                                         step=10.0, key=f"price_{name}")
            with c4:
                st.write("")
                st.write("")
                if st.button("📈", key=f"use_forecast_{name}",
                             help=f"Use forecast: {fcast.mean:.1f}kg ± {fcast.std:.1f}kg ({fcast.method})"):
                    st.session_state.cheese_demand_overrides[name] = (round(fcast.mean, 1), round(fcast.std, 1))
                    st.rerun()

            confirmed_kg = confirmed_demand.get(name, 0.0)
            if confirmed_kg > 0:
                st.caption(f"📄 {confirmed_kg:.1f}kg confirmed via LPO for "
                           f"{planning_date.strftime('%b %d')} — production won't go below this")
            st.caption(f"ℹ️ {fcast.confidence_note}")
            demand_forecast[name] = (mean_kg, std_kg if std_kg > 0 else mean_kg * 0.3)
            selling_prices[name] = price
            st.session_state.cheese_demand_overrides[name] = (mean_kg, std_kg)
            st.session_state.cheese_selling_prices[name] = price

    ready = milk_available_l > 0 and all(v > 0 for v in selling_prices.values()) and any(
        v[0] > 0 for v in demand_forecast.values())

    if st.button("🧮 Build Production Plan", type="primary", disabled=not ready):
        try:
            planner = ProductionPlanner(book, tracker, milk_cost_per_liter, raw_milk_price_per_liter)
            active_demand = {k: v for k, v in demand_forecast.items() if v[0] > 0 and selling_prices.get(k, 0) > 0}
            plan = planner.build_plan(
                milk_available_l=milk_available_l,
                demand_forecast=active_demand,
                selling_prices={k: selling_prices[k] for k in active_demand},
                aging_room_capacity_kg=capacity_kg,
                aging_room_used_kg=used_kg,
                confirmed_demand={k: v for k, v in confirmed_demand.items() if k in active_demand},
            )
            st.session_state.cheese_last_plan = plan
        except Exception as e:
            st.error(f"❌ Could not build production plan: {e}")
            st.caption("Usually means one cheese's selling price doesn't cover its effective unit "
                    "cost (incl. milk, and for aged cheeses, storage/financing/loss). Check that "
                    "cheese's price and cost inputs above.")

    if not ready:
        st.info("Enter milk available, and at least one cheese's demand + selling price, to enable planning.")

    plan = st.session_state.cheese_last_plan
    if plan:
        st.markdown("---")
        st.markdown("#### Plan Result")
        st.code(plan.summary(), language=None)
        if plan.warnings:
            for w in plan.warnings:
                st.warning(w)

        _render_whatif_expander(
            book, tracker, milk_cost_per_liter, raw_milk_price_per_liter,
            milk_available_l, demand_forecast, selling_prices,
            plan, capacity_kg, used_kg, confirmed_demand,
        )

        with st.expander("🚀 Execute this plan (creates real production batches)"):
            operator = st.text_input("Operator name", key="execute_plan_operator")
            recent_receipts = get_milk_receipts(start_date=date.today() - timedelta(days=3),
                                                 supabase_client=supabase_client)
            receipt_labels = {f"#{r['id']} — {r['date']} — {r['liters']:.0f}L": str(r["id"]) for r in recent_receipts}
            picked_labels = st.multiselect("Milk receipt(s) used", list(receipt_labels.keys()),
                                            default=list(receipt_labels.keys())[:1] if receipt_labels else [])
            milk_receipt_ids = [receipt_labels[l] for l in picked_labels]

            if st.button("✅ Confirm & Create Batches", type="primary", key="confirm_execute_plan"):
                if not operator:
                    st.error("Enter an operator name for traceability.")
                else:
                    batch_ids = planner.execute_plan(plan, operator=operator,
                                                       milk_receipt_ids=milk_receipt_ids) \
                        if 'planner' in dir() else None
                    # planner may be out of scope if the page re-ran between build & execute;
                    # rebuild a lightweight one bound to the same tracker/book to execute safely.
                    if batch_ids is None:
                        planner = ProductionPlanner(book, tracker, milk_cost_per_liter, raw_milk_price_per_liter)
                        batch_ids = planner.execute_plan(plan, operator=operator, milk_receipt_ids=milk_receipt_ids)
                    st.success(f"✅ Created {len(batch_ids)} production batch(es): {', '.join(batch_ids)}")
                    st.session_state.cheese_last_plan = None
                    st.rerun()


# ============================================================
# TAB 4: BATCH TRACKING & QC
# ============================================================
def _render_batch_tracking_tab(book: RecipeBook, tracker: BatchTracker, supabase_client) -> None:
    st.markdown("### 🏭 Batch Tracking & Quality Control")

    if not tracker.production_batches:
        st.info("No production batches yet — build and execute a plan in 📋 Production Planning.")
        return

    status_filter = st.selectbox("Filter by status", ["All"] + [s.value for s in BatchStatus],
                                  key="batch_status_filter")
    batches = list(tracker.production_batches.values())
    if status_filter != "All":
        batches = [b for b in batches if b.status.value == status_filter]
    batches.sort(key=lambda b: b.created_at, reverse=True)

    for batch in batches:
        with st.container():
            st.markdown(f"**{batch.batch_id}** — {batch.cheese_name} — {batch.quantity_kg:.1f} kg "
                        f"— *{batch.status.value}* — operator: {batch.operator}")
            cp_cols = st.columns(len(batch.checkpoints) or 1)
            for i, cp in enumerate(batch.checkpoints):
                with cp_cols[i % len(cp_cols)]:
                    icon = {"Pending": "⏳", "Passed": "✅", "Failed": "❌"}.get(cp.status, "⏳")
                    st.caption(f"{icon} {cp.stage}")
                    if cp.status == "Pending":
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Pass", key=f"pass_{batch.batch_id}_{cp.stage}"):
                                tracker.record_production_checkpoint(batch.batch_id, cp.stage, passed=True)
                                st.rerun()
                        with c2:
                            if st.button("Fail", key=f"fail_{batch.batch_id}_{cp.stage}"):
                                tracker.record_production_checkpoint(batch.batch_id, cp.stage, passed=False)
                                st.rerun()

            recipe = book.get(batch.cheese_name) if batch.cheese_name in book else None
            if batch.status == BatchStatus.PASSED_QC:
                if recipe and recipe.aging:
                    if st.button(f"🧊 Start Aging ({recipe.aging.aging_years:.1f}yr)",
                                 key=f"start_aging_{batch.batch_id}"):
                        ok, used, capacity, remaining = check_aging_room_capacity(
                            tracker, batch.quantity_kg, supabase_client=supabase_client)
                        if not ok:
                            st.error(f"⚠️ Aging room only has {remaining:.1f}kg free "
                                     f"(capacity {capacity:.0f}kg, {used:.0f}kg already aging) — "
                                     f"this batch is {batch.quantity_kg:.1f}kg. Free up room first, "
                                     f"or reduce the batch quantity before starting aging.")
                        else:
                            tracker.start_aging(batch.batch_id, recipe.aging.aging_years)
                            st.success("Aging started.")
                            st.rerun()
                else:
                    shelf_life = recipe.shelf_life_days if recipe else 30
                    if st.button("📦 Release to Finished Goods", key=f"release_fresh_{batch.batch_id}"):
                        tracker.release_fresh_to_finished(batch.batch_id, shelf_life)
                        st.success("Released to finished goods.")
                        st.rerun()
            elif batch.status == BatchStatus.FAILED_QC:
                st.error("❌ Failed QC — not eligible for aging or release.")
            st.markdown("---")

    st.markdown("#### Batches Currently Aging")
    aging_batches = [b for b in tracker.aging_batches.values() if b.status == BatchStatus.AGING]
    if aging_batches:
        for ab in aging_batches:
            recipe = book.get(ab.cheese_name) if ab.cheese_name in book else None
            shelf_life = recipe.shelf_life_days if recipe else 365

            # Expected post-loss yield the model assumed — used as the input default below
            if recipe and recipe.aging:
                expected_yield = ab.starting_quantity_kg * (recipe.aging.yield_rate ** ab.aging_years)
            else:
                expected_yield = ab.starting_quantity_kg

            st.write(f"**{ab.batch_id}** — {ab.cheese_name} — {ab.starting_quantity_kg:.1f}kg started, "
                    f"{ab.days_remaining()} days remaining")

            # Quarterly checkpoint pass/fail — this was previously data with no UI
            if ab.checkpoints:
                cp_cols = st.columns(len(ab.checkpoints))
                for i, cp in enumerate(ab.checkpoints):
                    with cp_cols[i]:
                        icon = {"Pending": "⏳", "Passed": "✅", "Failed": "❌"}.get(cp.status, "⏳")
                        st.caption(f"{icon} {cp.stage}")
                        if cp.status == "Pending":
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.button("Pass", key=f"aging_pass_{ab.batch_id}_{cp.stage}"):
                                    tracker.record_aging_checkpoint(ab.batch_id, cp.stage, passed=True)
                                    st.rerun()
                            with c2:
                                if st.button("Fail", key=f"aging_fail_{ab.batch_id}_{cp.stage}"):
                                    tracker.record_aging_checkpoint(ab.batch_id, cp.stage, passed=False)
                                    st.rerun()

            if ab.any_failed():
                st.error("⚠️ Failed a quarterly aging check — this batch cannot be released.")

            col1, col2 = st.columns([1, 1])
            with col1:
                actual_yield = st.number_input(
                    "Actual yield (kg)", value=float(round(expected_yield, 1)), min_value=0.0,
                    help=f"Model expected ~{expected_yield:.1f}kg after aging loss — adjust if the "
                        f"real weigh-in differs.",
                    key=f"yield_{ab.batch_id}"
                )
            with col2:
                st.write("")
                if st.button("Release", key=f"release_aged_{ab.batch_id}", disabled=ab.any_failed()):
                    try:
                        tracker.release_from_aging(ab.batch_id, actual_yield, shelf_life)
                        st.success(f"Released {actual_yield:.1f}kg of {ab.cheese_name} to finished goods.")
                        st.rerun()
                    except ValueError as e:
                        st.error(f"❌ {e}")
            st.markdown("---")
    else:
        st.caption("Nothing currently aging.")

# ============================================================
# TAB 5: AGING ROOM
# ============================================================
def _render_aging_room_tab(tracker: BatchTracker, supabase_client) -> None:
    st.markdown("### 🧊 Aging Room Capacity")

    current_capacity = get_aging_room_capacity_kg(supabase_client=supabase_client)
    used = get_aging_room_used_kg(tracker)
    remaining = max(0.0, current_capacity - used)
    pct = min(1.0, used / current_capacity) if current_capacity > 0 else 0.0

    st.progress(pct, text=f"{used:,.0f} / {current_capacity:,.0f} kg used ({pct:.0%})")
    col1, col2, col3 = st.columns(3)
    col1.metric("Capacity", f"{current_capacity:,.0f} kg")
    col2.metric("Used", f"{used:,.0f} kg")
    col3.metric("Remaining", f"{remaining:,.0f} kg")

    with st.expander("⚙️ Adjust Room Capacity"):
        new_capacity = st.number_input("Max Capacity (kg)", min_value=0.0, value=float(current_capacity), step=10.0)
        notes = st.text_input("Notes", placeholder="e.g. Aging Room 1, north wall shelving")
        if st.button("Save Capacity", type="primary"):
            set_aging_room_capacity_kg(new_capacity, notes=notes, supabase_client=supabase_client)
            st.success(f"✅ Aging room capacity set to {new_capacity:,.0f} kg.")
            st.rerun()

    st.markdown("#### Batches in the Room")
    aging_batches = [b for b in tracker.aging_batches.values() if b.status == BatchStatus.AGING]
    if aging_batches:
        rows = [{
            "Batch": b.batch_id, "Cheese": b.cheese_name, "Kg": b.starting_quantity_kg,
            "Days Remaining": b.days_remaining(), "Started": b.start_date.strftime("%Y-%m-%d"),
        } for b in aging_batches]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("The aging room is currently empty.")


# ============================================================
# TAB 6: FEFO INVENTORY
# ============================================================
def _render_fefo_inventory_tab(book: RecipeBook, tracker: BatchTracker) -> None:
    st.markdown("### 📦 Finished Goods Inventory (FEFO)")
    fefo = FEFOInventory(tracker)

    rows = []
    for name in book.list_names():
        stock = fefo.total_available_kg(name)
        batches = fefo.stock_by_cheese(name)
        next_expiry = batches[0].expiry_date.strftime("%Y-%m-%d") if batches else "—"
        rows.append({"Cheese": name, "Total Stock (kg)": stock, "Batches": len(batches),
                      "Next Expiry": next_expiry})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    days = st.slider("Show items expiring within (days)", min_value=1, max_value=90, value=14)
    expiring = fefo.expiring_within(days)
    if expiring:
        st.warning(f"⚠️ {len(expiring)} finished batch(es) expiring within {days} days")
        exp_rows = [{
            "Batch": b.batch_id, "Cheese": b.cheese_name, "Kg": b.quantity_kg,
            "Expiry": b.expiry_date.strftime("%Y-%m-%d"),
            "Days Left": b.days_to_expiry(),
        } for b in expiring]
        st.dataframe(pd.DataFrame(exp_rows), use_container_width=True, hide_index=True)
    else:
        st.success(f"✅ Nothing expiring within {days} days.")

    st.markdown("---")
    st.markdown("#### Simulate an Order (FEFO Allocation)")
    if book.list_names():
        c1, c2, c3 = st.columns(3)
        with c1:
            sim_cheese = st.selectbox("Cheese", book.list_names(), key="fefo_sim_cheese")
        with c2:
            sim_qty = st.number_input("Quantity requested (kg)", min_value=0.0, value=10.0, step=1.0)
        with c3:
            st.write("")
            st.write("")
            run_sim = st.button("Preview")

        if run_sim:
            preview = fefo.allocate(sim_cheese, sim_qty, commit=False)
            st.write(f"Would allocate **{preview.allocated_kg:.1f}kg** of {sim_qty:.1f}kg requested "
                    f"(shortfall: {preview.shortfall_kg:.1f}kg)")
            if preview.lines:
                st.dataframe(pd.DataFrame([{
                    "Batch": l.batch_id, "Kg Taken": l.quantity_kg,
                    "Expiry": l.expiry_date.strftime("%Y-%m-%d"),
                } for l in preview.lines]), use_container_width=True, hide_index=True)
            st.session_state.fefo_pending_allocation = (sim_cheese, sim_qty)

        if st.session_state.get("fefo_pending_allocation"):
            st.warning("⚠️ This will permanently dispatch the batches shown above.")
            if st.button("✅ Confirm & Dispatch", type="primary", key="fefo_confirm_dispatch"):
                cheese, qty = st.session_state.fefo_pending_allocation
                result = fefo.allocate(cheese, qty, commit=True)
                st.success(f"Dispatched {result.allocated_kg:.1f}kg of {cheese}.")
                st.session_state.fefo_pending_allocation = None
                st.rerun()

# ============================================================
# TAB 7: REPORTS
# ============================================================

def _render_reports_tab(book: RecipeBook, tracker: BatchTracker) -> None:
    render_report_tab(
        title="📄 Production Reports",
        caption="Generate production reports covering batch yield and QC statistics.",
        session_key="production_report",
        build_fn=lambda start_date, end_date: build_report_data(tracker, book, start_date, end_date),
        summarize_fn=summarize_report_data,
        pdf_fn=generate_production_report_pdf,
        pdf_filename_prefix="production_report",
        date_key_prefix="cheese_report",
        kpis=[
            KPI("Batches Started", lambda d: d.total_batches_started),
            KPI("Produced", lambda d: f"{d.total_kg_produced:.1f} kg"),
            KPI("Released", lambda d: f"{d.total_kg_released:.1f} kg"),
            KPI("Currently Aging", lambda d: len(d.currently_aging)),
            KPI("Failed QC", lambda d: len(d.failed_qc_batches)),
            KPI("Failed Aging", lambda d: len(d.failed_aging_batches)),
        ],
        chart_sections=[
            ChartSection(
                "Kg Produced Over Time",
                lambda d: bool(d.kg_produced_by_day),
                lambda d: d.kg_produced_by_day,
                x="date", y="kg", kind="bar",
            ),
        ],
        table_sections=[
            TableSection(
                "Production Status",
                lambda d: bool(d.batches_by_status),
                lambda d: [{"Status": k, "Count": v} for k, v in d.batches_by_status.items()],
            ),
            TableSection(
                "Production QC Statistics",
                lambda d: bool(d.production_qc_stats),
                lambda d: [{"Stage": s.stage, "Passed": s.passed, "Failed": s.failed,
                            "Pending": s.pending, "Pass Rate": f"{s.pass_rate:.0%}"}
                           for s in d.production_qc_stats],
            ),
            TableSection(
                "Aging QC Statistics",
                lambda d: bool(d.aging_qc_stats),
                lambda d: [{"Stage": s.stage, "Passed": s.passed, "Failed": s.failed,
                            "Pending": s.pending, "Pass Rate": f"{s.pass_rate:.0%}"}
                           for s in d.aging_qc_stats],
            ),
            TableSection(
                "Yield Comparison",
                lambda d: bool(d.yield_comparisons),
                lambda d: [{"Batch": y.aging_batch_id, "Cheese": y.cheese_name,
                            "Expected (kg)": round(y.expected_yield_kg, 2),
                            "Actual (kg)": round(y.actual_yield_kg, 2),
                            "Variance (%)": round(y.variance_pct, 2)}
                           for y in d.yield_comparisons],
            ),
        ],
    )