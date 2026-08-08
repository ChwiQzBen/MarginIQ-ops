"""
app/core/dry_ice_ui.py
========================
Dry Ice Mode's tabs, extracted from main.py following the same shape as
cheese_production_ui.py: a single render_dry_ice_mode() entry point,
permission-filtered tabs, small _render_x_tab() functions per tab.

Takes has_permission as a callable (not the Permission enum) for the same
reason cheese_production_ui.py does — avoids a circular import, since
main.py is what imports THIS module. Permission is a (str, Enum), so its
members compare equal to their plain string values; passing plain strings
like "view_analytics" here works against main.py's ROLE_PERMISSIONS sets
without needing the enum at all.

STATUS: All seven tabs are fully ported. 🔮 Demand Forecast and
💰 Cost Optimization need a few cross-module functions that still live in
main.py (create_ensemble_forecast, create_scenario_analysis,
render_scenario_analysis, render_scenario_summary) — those are passed in
as callables on DryIceContext, same reasoning as has_permission above.
See the DryIceContext construction snippet in main.py's Dry Ice Mode
branch for exactly what needs to be wired in.
"""
from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from datetime import datetime
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import math
from core.performance import paginate_dataframe, compress_dataframe
from core.error_handling import logger
from app.core.realtime_forecast import get_realtime_forecaster

if hasattr(st, "fragment"):
    fragment = st.fragment
elif hasattr(st, "experimental_fragment"):
    fragment = st.experimental_fragment
else:
    def fragment(func=None, **kwargs):
        if func is None:
            return lambda f: f
        return func

DRY_ICE_TAB_REQUIREMENTS = {
    "📊 Order Analysis": "view_analytics",
    "🔮 Demand Forecast": "view_forecasts",
    "📦 Inventory Management": "view_stock",
    "💰 Cost Optimization": "view_cost_data",
    "📋 Recommendations": "view_strategy",
    "🏗️ Storage Capacity": "view_maintenance",
    "📜 Transaction History": "view_reports",
}

def _style_map(styler, func, subset=None):
    """Styler.applymap() was renamed to .map() in pandas 2.1 and removed in
    pandas 3.0. Use whichever one actually exists on this Styler instance."""
    if hasattr(styler, "map"):
        return styler.map(func, subset=subset)
    return styler.applymap(func, subset=subset)


@st.cache_data(show_spinner=False, ttl=1800)
def _cached_scenario_analysis(_scenario_fn, _df, _ensemble_forecast, fingerprint):
    """Underscore-prefixed args are excluded from hashing (funcs/df/array are
    expensive or impossible to hash meaningfully); `fingerprint` is the real key."""
    return _scenario_fn(_ensemble_forecast, _df)

@st.cache_data(show_spinner=False, ttl=3600)
def _cached_monthly_cost_data(_df, fingerprint, _constants, avg_sublimation):
    df_monthly = _df.copy()
    df_monthly['Date'] = pd.to_datetime(df_monthly['Date'])
    df_monthly['Month'] = df_monthly['Date'].dt.to_period('M').dt.strftime('%Y-%b')

    monthly_data = df_monthly.groupby('Month').agg(
        product_cost_ksh=('Total_Cost', 'sum'),
        transport_cost_ksh=('Transport_Cost', 'sum'),
        product_volume_kg=('Order_Quantity_kg', 'sum')
    ).reset_index()

    monthly_data['holding_cost_ksh'] = (monthly_data['product_volume_kg'] / 2) * _constants.HOLDING_RATE * _constants.PRICE_PER_KG
    monthly_data['sublimation_loss_ksh'] = monthly_data['product_volume_kg'] * avg_sublimation * _constants.PRICE_PER_KG
    monthly_data['sublimation_loss_kg'] = monthly_data['product_volume_kg'] * avg_sublimation

    monthly_data['Month_dt'] = pd.to_datetime(monthly_data['Month'], format='%Y-%b')
    return monthly_data.sort_values('Month_dt')

@dataclass
class DryIceContext:
    """Everything the tabs need, computed once in main() and passed in."""
    df: pd.DataFrame
    kpis: dict
    constants: Any
    mobile_ui: Any
    inventory_tracker: Any
    fig_orders: Any
    fig_cost_overview: Any
    eoq: float
    monthly_savings: float
    # --- added for 📦 Inventory Management ---
    monthly_demand_input: float
    demand_stddev_input: float
    z_score: float
    safety_stock: float
    avg_sublimation: float
    adjusted_demand: float
    reorder_point: float
    annual_transport_savings: float
    annual_transport_cost: float
    current_monthly_orders: float
    eoq_monthly_orders: float
    # --- added for 💰 Cost Optimization ---
    annual_volume: float
    annual_product_cost: float
    annual_holding_cost: float
    annual_sublimation_loss: float
    total_annual_spending: float
    create_ensemble_forecast_fn: Optional[Callable] = None
    create_scenario_analysis_fn: Optional[Callable] = None
    render_scenario_analysis_fn: Optional[Callable] = None
    render_scenario_summary_fn: Optional[Callable] = None
    fig_ensemble: Optional[Any] = None
    ensemble_forecast_values: Optional[Any] = None
    model_forecasts: Optional[dict] = None
    backtest_accuracy: Optional[float] = None
    transactions: list = field(default_factory=list)

def render_dry_ice_mode(ctx: DryIceContext,
                         has_permission: Optional[Callable[[str], bool]] = None) -> None:
    def _allowed(permission: str) -> bool:
        return has_permission(permission) if has_permission else True

    # ============================================================
    # 🎨 DRY ICE THEME + MODE BADGE
    # ============================================================
    st.markdown("""
    <style>
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(4px);
            border-radius: 12px;
            padding: 8px;
            border: 1px solid rgba(255,255,255,0.08);
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #1a237e 0%, #4fc3f7 100%) !important;
            color: white !important;
            box-shadow: 0 4px 15px rgba(26, 35, 126, 0.3) !important;
        }
        .stTabs [data-baseweb="tab"]:hover {
            background: rgba(26, 35, 126, 0.08) !important;
            color: #1a237e !important;
        }
        .mode-badge-dryice {
            background: linear-gradient(135deg, #1a237e, #4fc3f7);
            color: white;
            padding: 6px 16px;
            border-radius: 20px;
            display: inline-block;
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 15px;
            box-shadow: 0 2px 10px rgba(26, 35, 126, 0.3);
        }
    </style>
    <div class="mode-badge-dryice">❄️ DRY ICE MODE</div>
    """, unsafe_allow_html=True)

    visible = [name for name, perm in DRY_ICE_TAB_REQUIREMENTS.items() if _allowed(perm)]
    if not visible:
        st.warning("This section isn't available for your current role. If you feel this is a mistake, please contact your administrator.")
        return

    
    if "dry_ice_active_tab" not in st.session_state or st.session_state.dry_ice_active_tab not in visible:
        st.session_state.dry_ice_active_tab = visible[0]

    active_tab = st.radio(
        "Section", visible, horizontal=True,
        key="dry_ice_active_tab", label_visibility="collapsed",
    )
    st.markdown("---")

    if active_tab == "📊 Order Analysis":
        _render_order_analysis_tab(ctx)
    elif active_tab == "🔮 Demand Forecast":
        _render_demand_forecast_tab(ctx)
    elif active_tab == "📦 Inventory Management":
        _render_inventory_management_tab(ctx)
    elif active_tab == "💰 Cost Optimization":
        _render_cost_optimization_tab(ctx)
    elif active_tab == "📋 Recommendations":
        _render_recommendations_tab(ctx)
    elif active_tab == "🏗️ Storage Capacity":
        _render_storage_capacity_tab(ctx)
    elif active_tab == "📜 Transaction History":
        _render_transaction_history_tab(ctx)


# ============================================================
# 📊 ORDER ANALYSIS
# ============================================================
def _render_order_analysis_tab(ctx: DryIceContext) -> None:
    df, kpis, constants, mobile_ui = ctx.df, ctx.kpis, ctx.constants, ctx.mobile_ui

    if not df.empty:
        st.markdown("""
        <h2 style='border-bottom: 1px solid #ddd; padding-bottom: 10px;'>
        Order Pattern & Cost Analysis
        </h2>
        """, unsafe_allow_html=True)
        with st.expander("Visual Analysis", expanded=not mobile_ui.should_collapse_advanced()):
            col1, col2 = st.columns(2)
            with col1:
                fig_orders = mobile_ui.optimize_chart_for_mobile(ctx.fig_orders)
                st.plotly_chart(fig_orders, use_container_width=True,
                    config=mobile_ui.get_mobile_chart_config())
            with col2:
                fig_cost_overview = mobile_ui.optimize_chart_for_mobile(ctx.fig_cost_overview)
                st.plotly_chart(fig_cost_overview, use_container_width=True,
                    config=mobile_ui.get_mobile_chart_config())

        st.markdown("""
        <h2 style='border-bottom: 1px solid #ddd; padding-bottom: 10px; margin-top: 30px;'>
        Order Statistics
        </h2>
        """, unsafe_allow_html=True)

        stat_cols = st.columns(4)
        metric_style = """<style>.stMetric {border-left: 3px solid #4e79a7;padding: 10px 15px;border-radius: 5px;background-color: #f9f9f9;}</style>"""
        st.markdown(metric_style, unsafe_allow_html=True)

        metrics = [
            ("Average Order", f"{kpis.get('avg_order_size', 0):.1f} kg", "Average order quantity"),
            ("Order Std Dev", f"{kpis.get('std_order_size', 0):.1f} kg", "Variability in order sizes"),
            ("Monthly Orders", f"{kpis.get('order_frequency', 0):.1f}", "Total orders per month"),
            ("Avg Cost/Order", f"KSh {kpis.get('avg_cost_per_order', 0):,.0f}", "Average cost per order")
        ]

        for i, (label, value, help_text) in enumerate(metrics):
            with stat_cols[i]:
                st.metric(label, value, help=help_text)

        st.markdown("""
        <h2 style='border-bottom: 1px solid #ddd; padding-bottom: 10px; margin-top: 30px;'>
        Additional Insights
        </h2>
        """, unsafe_allow_html=True)

        insights_cols = st.columns([1, 1, 1], gap="large")

        with insights_cols[0]:
            with st.container():
                st.markdown("#### 📊 Order Pattern Analysis")
                order_counts = df['Order_Quantity_kg'].value_counts().sort_index()
                most_common_order = order_counts.idxmax()
                st.metric("Most common order size", f"{most_common_order:.0f} kg", f"{order_counts.max()} orders")

                df['Weekday'] = df['Date'].dt.day_name()
                weekday_pattern = df.groupby('Weekday')['Order_Quantity_kg'].count().sort_values(ascending=False)
                st.metric("Busiest day", weekday_pattern.index[0], f"{weekday_pattern.iloc[0]} orders")

        with insights_cols[1]:
            with st.container():
                st.markdown("#### ⚙️ Efficiency Metrics")
                avg_containers_per_order = df['Containers_Used'].mean()
                container_efficiency = (df['Order_Quantity_kg'].sum() / (df['Containers_Used'].sum() * constants.CONTAINER_SIZE)) * 100
                st.metric("Avg containers per order", f"{avg_containers_per_order:.1f}")
                st.metric("Container fill rate", f"{container_efficiency:.1f}%")

                product_cost = constants.PRICE_PER_KG
                transport_cost = (kpis.get('order_frequency', 0) * constants.TRANSPORT_COST) / kpis.get('current_monthly_volume', 1)
                holding_cost = (kpis.get('avg_order_size', 0)/2) * constants.HOLDING_RATE * constants.PRICE_PER_KG / kpis.get('current_monthly_volume', 1)

                avg_sublimation_rate = sum(constants.SUB_LOSS_RANGE) / 2 / 100
                sublimation_cost_per_kg = constants.PRICE_PER_KG * avg_sublimation_rate

                current_cost_per_kg = product_cost + transport_cost + holding_cost + sublimation_cost_per_kg
                st.metric("Current cost per kg", f"KSh {current_cost_per_kg:.2f}", delta=None)

        with insights_cols[2]:
            with st.container():
                st.markdown("#### 📈 Optimization Impact")
                if ctx.eoq > 0:
                    order_frequency_reduction = ((kpis.get('order_frequency', 0) - (kpis.get('current_monthly_volume', 0) / ctx.eoq)) / kpis.get('order_frequency', 1) * 100)
                    st.metric("Order frequency reduction", f"{order_frequency_reduction:.1f}%")

                    current_turns = kpis.get('current_monthly_volume', 0) / (kpis.get('avg_order_size', 1)/2)
                    eoq_turns = 1.3
                    inventory_turns_improvement = eoq_turns - current_turns
                    st.metric("Inventory Turns Change", f"{inventory_turns_improvement:+.1f}x")

                annual_savings = ctx.monthly_savings * 12
                implementation_cost = st.session_state.get('implementation_cost', constants.IMPLEMENTATION_COST)
                roi = (annual_savings / implementation_cost) * 100 if implementation_cost > 0 else float('inf')
                st.metric("Estimated ROI", f"{roi:.0f}%", help="Return on investment from implementing optimizations")
    else:
        st.warning("📊 No order data exists for this analysis period.")
        st.info("Use the 'Record Receipt' button in the sidebar to add the first order for this period.")


# ============================================================
# 🔮 DEMAND FORECAST
# ============================================================
@fragment
def _render_model_selection_config() -> None:
    """Model checkbox grid + Select All / Deselect All / Update Models.
    Fragment-scoped so toggling a checkbox only reruns this expander, not
    the rest of the Demand Forecast tab or the whole app. 'Update Models'
    is the one action here that still needs a FULL app rerun — it changes
    st.session_state.selected_models, which main.py's cached
    get_forecast_data() depends on — so it explicitly escalates with
    st.rerun() (default scope="app")."""
    st.markdown("#### Select Active Models")
    st.caption("Choose which models to use in the ensemble forecast")
    st.caption("ℹ️ NeuralProphet is currently disabled (dependency unavailable)")

    model_options = {
        'Prophet': True,
        'ARIMA': True,
        'LSTM': True,
        'Monte Carlo': True,
        'XGBoost': True,
        'LightGBM': True,
        'RandomForest': True,
    }

    selected_models = []
    cols = st.columns(4)

    for idx, (model_name, default) in enumerate(model_options.items()):
        with cols[idx % 4]:
            if st.checkbox(
                model_name,
                value=default,
                key=f"tab_model_{model_name}",
                help=f"Enable/disable {model_name} model"
            ):
                internal_name = model_name.lower().replace(' ', '_')
                selected_models.append(internal_name)

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("✅ Select All", use_container_width=True, key="select_all_models"):
            for model in model_options.keys():
                st.session_state[f"tab_model_{model}"] = True

    with col2:
        if st.button("❌ Deselect All", use_container_width=True, key="deselect_all_models"):
            for model in model_options.keys():
                st.session_state[f"tab_model_{model}"] = False

    with col3:
        if st.button("🔄 Update Models", use_container_width=True, type="primary", key="update_models_btn"):
            st.session_state.selected_models = selected_models
            st.session_state.selected_models_count = len(selected_models)
            st.cache_data.clear()

            if selected_models:
                st.success(f"✅ Active models: {len(selected_models)}/7")
                st.info(f"📋 {', '.join([m.replace('_', ' ').title() for m in selected_models])}")
            else:
                st.warning("⚠️ No models selected! Using all models as fallback.")

            st.rerun()


def _render_demand_forecast_tab(ctx: DryIceContext) -> None:
    df = ctx.df

    if df.empty:
        st.warning("📊 No order data found for this period")
        return

    if ctx.create_ensemble_forecast_fn is None:
        st.error(
            "⚙️ Demand Forecast isn't fully wired up yet — "
            "`create_ensemble_forecast_fn` (and the scenario-analysis callables) "
            "need to be passed into DryIceContext from main.py."
        )
        return

    st.markdown("### 🔮 30-Day Demand Forecast")

    # ============================================================
    # MODEL SELECTION CONTROLS (fragment-scoped — see
    # _render_model_selection_config, defined above this function)
    # ============================================================
    with st.expander("⚙️ Model Configuration", expanded=False):
        _render_model_selection_config()

    st.markdown("---")

    # Real-time status
    rt_forecaster = get_realtime_forecaster()

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        rt_forecaster.render_realtime_status()
    with col2:
        if st.button("▶️ Start Live Updates"):
            rt_forecaster.start(df, 30)
            
    with col3:
        if st.button("⏹️ Stop Live Updates"):
            rt_forecaster.stop()

    st.markdown("---")

    with st.spinner("Loading forecast results..."):
        # Aggregate multi-invoice daily data — still needed below for the backtest split
        daily_df_tab2 = df.set_index('Date').resample('D')['Order_Quantity_kg'].sum().reset_index()

        # Pull the model selection saved by the "Update Models" button
        selected_for_forecast = st.session_state.get('selected_models', None)

        # main.py already ran this exact ensemble (same resampled df, same
        # selected_models from session_state) inside its cached get_forecast_data().
        # Reuse that result instead of recomputing all 7 models again here, uncached,
        # on every rerun of this tab.
        fig_ensemble = ctx.fig_ensemble
        ensemble_forecast_values = ctx.ensemble_forecast_values
        model_forecasts = ctx.model_forecasts
        backtest_accuracy = ctx.backtest_accuracy

        ensemble_forecast = ensemble_forecast_values

        # ============================================================
        # Model Performance Dashboard
        # ============================================================
        st.markdown("---")
        st.markdown("#### 📊 Model Performance Dashboard")

        if 'active_models' in st.session_state and 'model_comparison' in st.session_state:
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric(
                    "🧠 Active Models",
                    f"{st.session_state.active_models}/7",
                    delta=f"{st.session_state.active_models} models"
                )

            with col2:
                if 'best_model' in st.session_state:
                    st.metric(
                        "🏆 Best Model",
                        st.session_state.best_model['name'],
                        f"Score: {st.session_state.best_model['score']}"
                    )
                else:
                    st.metric("🏆 Best Model", "Calculating...")

            with col3:
                if 'best_model' in st.session_state:
                    st.metric(
                        "🎯 Accuracy",
                        st.session_state.best_model['accuracy'],
                        "Auto-selected"
                    )
                else:
                    st.metric("🎯 Accuracy", "Calculating...")

            with col4:
                if 'active_models_list' in st.session_state:
                    model_list = st.session_state.active_models_list
                    display_text = ", ".join(model_list[:3])
                    if len(model_list) > 3:
                        display_text += f" +{len(model_list)-3} more"
                    st.metric("📋 Models", display_text)

        # ============================================================
        # Detailed Model Comparison Table
        # ============================================================
        if 'model_comparison' in st.session_state and not st.session_state.model_comparison.empty:
            with st.expander("🔬 View Detailed Model Comparison", expanded=False):
                st.dataframe(
                    st.session_state.model_comparison,
                    use_container_width=True,
                    hide_index=True,
                    height=250
                )

                csv = st.session_state.model_comparison.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Model Comparison",
                    data=csv,
                    file_name=f"model_comparison_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )

        # ============================================================
        # Model Comparison Chart
        # ============================================================
        if 'model_comparison' in st.session_state and not st.session_state.model_comparison.empty:
            try:
                chart_data = []
                for _, row in st.session_state.model_comparison.iterrows():
                    try:
                        avg = float(row['Avg Forecast (kg)'].replace(',', ''))
                        chart_data.append({
                            'Model': row['Model'],
                            'Avg Forecast': avg,
                            'Status': row['Status']
                        })
                    except:
                        continue

                if chart_data:
                    chart_df = pd.DataFrame(chart_data)

                    fig_models = px.bar(
                        chart_df,
                        x='Model',
                        y='Avg Forecast',
                        title='📊 Model Comparison - Average Forecast',
                        color='Status',
                        color_discrete_map={'✅ Active': '#28a745'},
                        text='Avg Forecast'
                    )
                    fig_models.update_traces(
                        texttemplate='%{text:.1f} kg',
                        textposition='outside'
                    )
                    fig_models.update_layout(
                        height=350,
                        yaxis_title='Average Forecast (kg)',
                        showlegend=False,
                        xaxis_tickangle=-45
                    )
                    st.plotly_chart(fig_models, use_container_width=True)
            except Exception as e:
                st.warning(f"Could not display model comparison chart: {e}")

        st.markdown("---")

        # ============================================================
        # Scenario Analysis
        # ============================================================
        scenario_fingerprint = (
            len(df), df['Date'].max(),
            tuple(round(float(v), 4) for v in ensemble_forecast) if ensemble_forecast is not None else None,
        )
        scenario_results = _cached_scenario_analysis(
            ctx.create_scenario_analysis_fn, df, ensemble_forecast, scenario_fingerprint
        )

        scenario_fig = ctx.render_scenario_analysis_fn(scenario_results, 30)
        st.plotly_chart(scenario_fig, use_container_width=True)

        st.markdown("#### 📊 Scenario Summary")
        ctx.render_scenario_summary_fn(scenario_results)

        # ============================================================
        # Model Performance Details
        # ============================================================
        with st.expander("🔬 Model Performance Details", expanded=False):
            st.markdown("#### 📈 Individual Model Forecasts")

            model_cols = st.columns(4)
            col_idx = 0

            if model_forecasts:
                for name, stats in model_forecasts.items():
                    if name != 'External Factors' and isinstance(stats, dict):
                        with model_cols[col_idx % 4]:
                            st.metric(
                                name,
                                f"{stats['avg']:.1f} kg/day",
                                f"min: {stats['min']:.1f} | max: {stats['max']:.1f}"
                            )
                        col_idx += 1

            # Show metrics
            st.markdown("#### 📈 Accuracy Metrics (Backtest)")
            st.caption(
                "Trains on all data except the last 30 days, forecasts those 30 days, "
                "then compares against what actually happened. This is separate from "
                "the live 30-day-ahead forecast above, which has no ground truth yet."
            )

            if len(daily_df_tab2) <= 60:
                st.info("Need more than 60 days of history to run a reliable backtest.")
            else:
                run_backtest = st.button(
                    "🧪 Run Backtest",
                    key="run_backtest_btn",
                    help="Retrains all selected models on held-out data. Takes as long as the main forecast."
                )

                if run_backtest:
                    try:
                        backtest_train = daily_df_tab2.iloc[:-30].reset_index(drop=True)
                        backtest_actual = daily_df_tab2.iloc[-30:]['Order_Quantity_kg'].values

                        with st.spinner("Running backtest (retraining models on held-out data)..."):
                            _, backtest_forecast, _, _ = ctx.create_ensemble_forecast_fn(
                                backtest_train, 30, selected_models=selected_for_forecast
                            )

                        historical_values = backtest_actual
                        forecast_values = np.array(backtest_forecast[:len(historical_values)])

                        wape = np.sum(np.abs(historical_values - forecast_values)) / max(np.sum(historical_values), 1) * 100
                        mae = np.mean(np.abs(historical_values - forecast_values))

                        ss_res = np.sum((historical_values - forecast_values) ** 2)
                        ss_tot = np.sum((historical_values - np.mean(historical_values)) ** 2)
                        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

                        actual_direction = np.sign(np.diff(historical_values))
                        pred_direction = np.sign(np.diff(forecast_values[:len(historical_values)]))
                        direction_accuracy = np.mean(actual_direction == pred_direction) * 100

                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("📊 WAPE", f"{wape:.1f}%")
                        with col2:
                            st.metric("📉 MAE", f"{mae:.1f}")
                        with col3:
                            st.metric("📈 R²", f"{r2:.2f}")
                        with col4:
                            st.metric("🎯 Direction Accuracy", f"{direction_accuracy:.1f}%")

                        metrics_valid = all(np.isfinite(v) for v in [wape, mae, r2, direction_accuracy])
                        if not metrics_valid:
                            st.markdown("#### 📊 Forecast Quality Score")
                            st.progress(0.0, text="0% — invalid metrics")
                            st.error("⚠️ Backtest produced NaN metrics — one of the ensemble models "
                                    "(check NeuralProphet) likely returned NaN forecasts. "
                                    "Quality score forced to 0 instead of falsely showing 100%.")
                        else:
                            mean_actual = max(np.mean(historical_values), 1)
                            mae_normalized = min(mae / mean_actual, 1)
                            r2_normalized = 0 if r2 < 0 else min(r2, 1)
                            direction_normalized = direction_accuracy / 100
                            wape_penalty = max(0, min(1, 1 - (wape - 50) / 150)) if wape > 50 else 1

                            quality_score = (
                                0.4 * (1 - mae_normalized) +
                                0.3 * r2_normalized +
                                0.3 * direction_normalized
                            ) * wape_penalty * 100
                            quality_score = round(max(0, min(100, quality_score)), 1)

                            st.markdown("#### 📊 Forecast Quality Score")
                            st.progress(quality_score / 100, text=f"{quality_score:.0f}%")

                            if quality_score < 30:
                                st.warning("⚠️ Forecast quality is low. Consider retraining models with more data.")
                            elif quality_score < 50:
                                st.info("📊 Forecast quality is moderate. Some models may need tuning.")
                            else:
                                st.success("✅ Forecast quality is good.")

                    except Exception as e:
                        st.warning(f"Could not run backtest: {e}")


# ============================================================
# 📦 INVENTORY MANAGEMENT
# ============================================================
def _render_inventory_management_tab(ctx: DryIceContext) -> None:
    df, kpis, constants, mobile_ui = ctx.df, ctx.kpis, ctx.constants, ctx.mobile_ui
    eoq, monthly_demand_input, demand_stddev_input = ctx.eoq, ctx.monthly_demand_input, ctx.demand_stddev_input
    z_score, safety_stock, avg_sublimation = ctx.z_score, ctx.safety_stock, ctx.avg_sublimation
    adjusted_demand, reorder_point = ctx.adjusted_demand, ctx.reorder_point
    annual_transport_savings, annual_transport_cost = ctx.annual_transport_savings, ctx.annual_transport_cost
    current_monthly_orders, eoq_monthly_orders = ctx.current_monthly_orders, ctx.eoq_monthly_orders

    if not df.empty:
        st.markdown("### 📦 Proactive Inventory Policy")
        st.success("✅ Inventory policy is dynamically calculated using the live forecast data from Tab 2.")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### Economic Order Quantity (EOQ)")
            st.markdown("**Where:**")
            st.markdown(f"<span style='color:green;'>- D = Forecasted Monthly Demand = {monthly_demand_input:,.1f} kg</span>", unsafe_allow_html=True)
            st.write(f"- S = Ordering Cost = KSh {constants.TRANSPORT_COST:,.2f}")
            st.write(f"- H = Holding Rate = {constants.HOLDING_RATE*100:.1f}%")
            st.write(f"- C = Unit Cost = KSh {constants.PRICE_PER_KG:.2f}/kg")
            st.markdown(f"<p style='color:green; font-weight:bold;'>Result: EOQ = {eoq:.1f} kg</p>", unsafe_allow_html=True)

        with col2:
            st.markdown("#### Safety Stock")
            st.markdown("**Where:**")
            st.write(f"- z = Z-score ({constants.SERVICE_LEVEL*100:.0f}%) = {z_score:.2f}")
            st.markdown(f"<span style='color:green;'>- σ = Forecast Demand Std Dev = {demand_stddev_input:,.1f} kg</span>", unsafe_allow_html=True)
            st.write(f"- LT = Lead Time = {constants.LEAD_TIME_DAYS} days")
            st.write(f"- Sublimation Rate = {avg_sublimation:.2%}")
            st.markdown(f"<p style='color:green; font-weight:bold;'>Result: Safety Stock = {safety_stock:.1f} kg</p>", unsafe_allow_html=True)

        st.markdown("### 🔄 Reorder Point")
        st.markdown(f"**Reorder Point = ({adjusted_demand:.1f}/30 × {constants.LEAD_TIME_DAYS}) + {safety_stock:.1f} = {reorder_point:.1f} kg**")
        st.caption("*This policy is now dynamically updated based on the 30-day demand forecast.*")

        st.markdown("### 📊 Recommended Inventory Policy")
        policy_data = pd.DataFrame({'Metric': ['Economic Order Quantity', 'Safety Stock', 'Reorder Point', 'Maximum Inventory'], 'Value (kg)': [eoq, safety_stock, reorder_point, eoq + safety_stock]})
        st.dataframe(policy_data, use_container_width=True, height=180)

        st.markdown("### 🎯 EOQ Implementation Impact")
        annual_savings_percentage = (annual_transport_savings / annual_transport_cost) * 100 if annual_transport_cost > 0 else 0

        impact_cols = st.columns(4)
        with impact_cols[0]:
            order_freq_delta_percent = ((eoq_monthly_orders - current_monthly_orders) / current_monthly_orders) * 100 if current_monthly_orders > 0 else 0
            st.metric("Order Frequency Change", f"{eoq_monthly_orders:.1f} orders/month", f"{order_freq_delta_percent:.1f}%")
        with impact_cols[1]:
            st.metric("Annual Transport Savings", f"KSh {annual_transport_savings:,.0f}", f"{annual_savings_percentage:.1f}% of total")

        implementation_cost = st.session_state.get('implementation_cost', constants.IMPLEMENTATION_COST)
        payback_period = implementation_cost / (annual_transport_savings / 12) if annual_transport_savings > 0 else 0
        roi_percentage = (annual_transport_savings / implementation_cost) * 100 if implementation_cost > 0 else 0
        with impact_cols[2]:
            st.metric("Payback Period", f"{payback_period:.1f} months")
        with impact_cols[3]:
            st.metric("Implementation ROI", f"{roi_percentage:.0f}%")

        st.markdown("### 🔄 Current vs. Forecast-Driven System Comparison")
        comparison_data = {
            'Metric': ['Orders per Month', 'Avg. Order Size (kg)', 'Monthly Transport Cost (KSh)', 'Annual Transport Cost (KSh)'],
            'Current System (Historical)': [
                f"{current_monthly_orders:.1f}", f"{kpis.get('avg_order_size', 0):.0f}",
                f"{current_monthly_orders * constants.TRANSPORT_COST:,.0f}", f"{annual_transport_cost:,.0f}"
            ],
            'EOQ System (Forecast-Driven)': [
                f"{eoq_monthly_orders:.1f}", f"{eoq:.0f}",
                f"{eoq_monthly_orders * constants.TRANSPORT_COST:,.0f}", f"{eoq_monthly_orders * 12 * constants.TRANSPORT_COST:,.0f}"
            ]
        }
        st.dataframe(pd.DataFrame(comparison_data), use_container_width=True, height=185, hide_index=True)

        st.markdown("#### 📈 5-Year Cumulative Savings Projection")
        years = list(range(1, 6))
        cumulative_savings = [annual_transport_savings * year for year in years]
        fig_savings = go.Figure()
        fig_savings.add_trace(go.Scatter(x=years, y=cumulative_savings, name="Cumulative Savings", line=dict(color='#3498db', width=3), mode='lines+markers'))
        fig_savings.add_hline(y=implementation_cost, line_dash="dot", annotation_text="Implementation Cost", line_color="red")
        fig_savings.update_layout(title="Projected Savings from Adopting Forecast-Driven EOQ", xaxis_title="Year", yaxis_title="Cumulative Savings (KSh)", height=mobile_ui.get_chart_height())
        fig_savings = mobile_ui.optimize_chart_for_mobile(fig_savings)
        st.plotly_chart(fig_savings, use_container_width=True,
            config=mobile_ui.get_mobile_chart_config())

        
    else:
        st.warning("📦 Cannot calculate inventory policy without historical data for this period.")
        st.info("Please record some receipts to build up an order history for inventory optimization.")


# ============================================================
# 💰 COST OPTIMIZATION
# ============================================================
def _render_cost_optimization_tab(ctx: DryIceContext) -> None:
    df, constants, mobile_ui = ctx.df, ctx.constants, ctx.mobile_ui

    if df.empty:
        st.warning("🧮 Cost analysis requires historical order data.")
        st.info("Please add the first order for this period using the 'Record Receipt' button in the sidebar to see the cost breakdown.")
        return

    st.markdown("### 🧮 Detailed Annual Cost Breakdown")

    # 1. Core Cost Components Table
    st.markdown("#### 📋 Annual Cost Component Details")
    avg_inventory_for_display = (ctx.kpis.get('avg_order_size', 0) / 2) + ctx.safety_stock
    cost_components_data = {
        'Component': ['Product Purchase', 'Transport', 'Holding', 'Sublimation Loss', 'Total'],
        'Calculation': [
            f"{ctx.annual_volume:,.0f} kg × KSh {constants.PRICE_PER_KG:.2f}",
            f"{ctx.kpis.get('total_orders', 0):,} orders × KSh {constants.TRANSPORT_COST:,.0f}",
            f"({avg_inventory_for_display:,.1f} kg avg inv) × KSh {constants.PRICE_PER_KG:.2f} × {constants.HOLDING_RATE * 100:.1f}%",
            f"{ctx.annual_volume:,.0f} kg × {sum(constants.SUB_LOSS_RANGE) / 2:.2f}% loss × KSh {constants.PRICE_PER_KG:.2f}",
            "Sum of all components"
        ],
        'Annual Cost (KSh)': [
            ctx.annual_product_cost, ctx.annual_transport_cost, ctx.annual_holding_cost,
            ctx.annual_sublimation_loss, ctx.total_annual_spending
        ],
        '% of Total': [
            (ctx.annual_product_cost / ctx.total_annual_spending) * 100 if ctx.total_annual_spending > 0 else 0,
            (ctx.annual_transport_cost / ctx.total_annual_spending) * 100 if ctx.total_annual_spending > 0 else 0,
            (ctx.annual_holding_cost / ctx.total_annual_spending) * 100 if ctx.total_annual_spending > 0 else 0,
            (ctx.annual_sublimation_loss / ctx.total_annual_spending) * 100 if ctx.total_annual_spending > 0 else 0,
            100
        ]
    }
    cost_components = pd.DataFrame(cost_components_data)
    styled_cost_components = cost_components.style.format(
        {'Annual Cost (KSh)': '{:,.0f}', '% of Total': '{:.1f}%'}
    )
    styled_cost_components = _style_map(
        styled_cost_components, lambda x: 'font-weight: bold', subset=['Component']
    ).bar(subset=['Annual Cost (KSh)'], color='#5fba7d')

    st.dataframe(
        styled_cost_components,
        use_container_width=True,
        height=220,
        hide_index=True
    )

    st.markdown("---")
    st.markdown("#### 📊 Cost Structure Visualization")
    fig_cost_breakdown = make_subplots(
        rows=1, cols=2,
        specs=[[{"type": "pie"}, {"type": "bar"}]],
        subplot_titles=("Cost Distribution", "Cost per kg Analysis"),
        column_widths=[0.5, 0.5]
    )

    fig_cost_breakdown.add_trace(go.Pie(
        labels=cost_components['Component'][:-1],
        values=cost_components['Annual Cost (KSh)'][:-1],
        marker_colors=['#3498db', '#e74c3c', '#f39c12', '#2ecc71'],
        textinfo='label+percent',
        textposition='inside',
        insidetextorientation='radial',
        textfont=dict(size=12),
        hoverinfo='label+percent+value',
        domain={'x': [0, 0.45]}
    ), row=1, col=1)

    cost_per_kg_data = {
        'Metric': ['Product Cost', 'Transport Cost', 'Holding Cost', 'Sublimation'],
        'Cost per kg (KSh)': [
            constants.PRICE_PER_KG,
            ctx.annual_transport_cost / ctx.annual_volume if ctx.annual_volume > 0 else 0,
            ctx.annual_holding_cost / ctx.annual_volume if ctx.annual_volume > 0 else 0,
            ctx.annual_sublimation_loss / ctx.annual_volume if ctx.annual_volume > 0 else 0
        ]
    }
    cost_per_kg = pd.DataFrame(cost_per_kg_data)
    fig_cost_breakdown.add_trace(go.Bar(
        x=cost_per_kg['Metric'],
        y=cost_per_kg['Cost per kg (KSh)'],
        marker_color=['#3498db', '#e74c3c', '#f39c12', '#2ecc71'],
        text=cost_per_kg['Cost per kg (KSh)'].round(2),
        textposition='auto'
    ), row=1, col=2)

    fig_cost_breakdown.update_layout(height=400, showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
    fig_cost_breakdown = mobile_ui.optimize_chart_for_mobile(fig_cost_breakdown)
    st.plotly_chart(fig_cost_breakdown, use_container_width=True,
        config=mobile_ui.get_mobile_chart_config())

    st.markdown("---")
    st.markdown("#### 📈 Monthly Cost Trends (KSh)")

    cost_fingerprint = (
        len(df), df['Date'].max(), round(float(df['Order_Quantity_kg'].sum()), 2),
        constants.HOLDING_RATE, constants.PRICE_PER_KG, round(ctx.avg_sublimation, 6),
    )
    monthly_data = _cached_monthly_cost_data(df, cost_fingerprint, constants, ctx.avg_sublimation)

    cost_cols = ['product_cost_ksh', 'transport_cost_ksh', 'holding_cost_ksh', 'sublimation_loss_ksh']
    colors = ['#3498db', '#e74c3c', '#f39c12', '#2ecc71']
    color_map = {col: color for col, color in zip(cost_cols, colors)}

    fig_monthly_cost = px.area(
        monthly_data, x='Month', y=cost_cols,
        title="Monthly Cost Fluctuations (KSh)",
        labels={'value': 'Cost (KSh)', 'variable': 'Cost Type'},
        color_discrete_map=color_map,
        height=mobile_ui.get_chart_height()
    )

    for col, color in zip(cost_cols, colors):
        avg_value = monthly_data[col].mean()
        annotation_label = col.replace('_ksh', '').replace('_', ' ').title()
        fig_monthly_cost.add_hline(y=avg_value, line_dash="dot", line_color=color,
                                annotation_text=f"Avg {annotation_label}: {avg_value:,.0f}",
                                annotation_position="bottom right")

    fig_monthly_cost.update_traces(
        mode="lines+markers",
        hovertemplate="<b>%{x}</b><br>%{y:,.0f} KSh<extra></extra>"
    )
    fig_monthly_cost.update_layout(
        hovermode="x unified",
        xaxis_title=None,
        yaxis_title="Cost (KSh)",
        legend_title="Cost Type"
    )

    fig_monthly_cost = mobile_ui.optimize_chart_for_mobile(fig_monthly_cost)
    st.plotly_chart(fig_monthly_cost, use_container_width=True,
        config=mobile_ui.get_mobile_chart_config())

    st.markdown("---")
    st.markdown("#### 📊 Monthly Physical Quantities (kg)")
    fig_monthly_volume = px.bar(
        monthly_data, x='Month', y=['product_volume_kg', 'sublimation_loss_kg'],
        title="Monthly Dry Ice Quantities (kg)", labels={'value': 'Quantity (kg)', 'variable': 'Quantity Type'},
        height=400
    )
    fig_monthly_volume = mobile_ui.optimize_chart_for_mobile(fig_monthly_volume)
    st.plotly_chart(fig_monthly_volume, use_container_width=True,
        config=mobile_ui.get_mobile_chart_config())

    # 4. Savings Summary
    st.markdown("---")
    st.markdown("### 💰 Savings Summary")

    monthly_transport_savings = ctx.annual_transport_savings / 12
    current_monthly_transport_cost = ctx.current_monthly_orders * constants.TRANSPORT_COST

    monthly_savings_percent = (monthly_transport_savings / current_monthly_transport_cost) * 100 if current_monthly_transport_cost > 0 else 0
    annual_savings_percent = (ctx.annual_transport_savings / ctx.annual_transport_cost) * 100 if ctx.annual_transport_cost > 0 else 0
    implementation_cost = st.session_state.get('implementation_cost', constants.IMPLEMENTATION_COST)
    roi_percentage = (ctx.annual_transport_savings / implementation_cost) * 100 if implementation_cost > 0 else float('inf')
    payback_period = implementation_cost / monthly_transport_savings if monthly_transport_savings > 0 else 0

    savings_cols = st.columns(5)
    with savings_cols[0]:
        st.metric("Monthly Savings", f"KSh {monthly_transport_savings:,.0f}")
    with savings_cols[1]:
        st.metric("Annual Savings", f"KSh {ctx.annual_transport_savings:,.0f}")
    with savings_cols[2]:
        st.metric("Monthly Savings %", f"{monthly_savings_percent:.1f}%")
    with savings_cols[3]:
        st.metric("Annual Savings %", f"{annual_savings_percent:.1f}%")
    with savings_cols[4]:
        st.metric("ROI", f"{roi_percentage:.0f}%")

    # ----------------------------------
    # 5. Key Insights & Recommendations
    # ----------------------------------
    st.markdown("---")
    st.markdown("#### 📋 Key Insights & Recommendations")
    st.markdown(f"""
    - **EOQ Implementation**: Save KSh {ctx.annual_transport_savings:,.0f} annually on transport costs.
    - **Order Frequency**: Reduce from {ctx.current_monthly_orders:.1f} to {ctx.eoq_monthly_orders:.1f} orders/month.
    - **Payback Period**: {payback_period:.1f} months to recover implementation costs.
    - **Largest Cost**: Product cost ({(ctx.annual_product_cost/ctx.total_annual_spending)*100:.1f}% of total).
    - **Loss Prevention**: Sublimation losses cost KSh {ctx.annual_sublimation_loss:,.0f} annually.
    """)


# ============================================================
# 📋 RECOMMENDATIONS
# ============================================================
def _render_recommendations_tab(ctx: DryIceContext) -> None:
    kpis, constants, mobile_ui = ctx.kpis, ctx.constants, ctx.mobile_ui
    eoq, avg_sublimation, safety_stock = ctx.eoq, ctx.avg_sublimation, ctx.safety_stock
    reorder_point, eoq_monthly_orders = ctx.reorder_point, ctx.eoq_monthly_orders
    current_monthly_orders = ctx.current_monthly_orders

    st.markdown("### 📋 Strategic Recommendations")

    # Immediate actions
    st.markdown("#### 🎯 Recommended Actions")

    recommendations = [
        f"**Implement optimized ordering quantity:** Order {eoq:.0f} kg per shipment (accounts for {avg_sublimation*100:.2f}% sublimation losses)",
        f"**Maintain safety stock:** Keep minimum inventory of {safety_stock:.0f} kg to buffer against demand variability and sublimation",
        f"**Set reorder point:** Initiate new orders when inventory reaches {reorder_point:.0f} kg",
        f"**Optimize order frequency:** Target {eoq_monthly_orders:.1f} orders per month based on demand patterns"
    ]

    for i, rec in enumerate(recommendations, 1):
        st.markdown(f"{i}. {rec}")

    # Key metrics to monitor
    st.markdown("#### 📊 Key Metrics to Monitor")

    avg_order_size = kpis.get('avg_order_size', 0)
    current_monthly_volume = kpis.get('current_monthly_volume', 0)

    if avg_order_size > 0:
        current_inventory_turnover = f"{current_monthly_volume / (avg_order_size/2):.1f}x/month"
    else:
        current_inventory_turnover = "N/A"

    if eoq > 0:
        target_inventory_turnover = f"{current_monthly_volume / eoq:.1f}x/month"
    else:
        target_inventory_turnover = "N/A"

    metrics_to_track = pd.DataFrame({
        'Metric': [
            'Service Level',
            'Inventory Turnover',
            'Stockout Frequency',
            'Order Frequency',
            'Container Utilization',
            'Total Transport Cost'
        ],
        'Current Value': [
            f"{constants.SERVICE_LEVEL*100:.0f}%",
            current_inventory_turnover,
            "Monitor",
            f"{current_monthly_orders:.1f}/month",
            f"{kpis.get('container_utilization', 0)*100:.1f}%",
            f"KSh {current_monthly_orders * constants.TRANSPORT_COST:,.0f}/month"
        ],
        'Target Value': [
            f"{constants.SERVICE_LEVEL*100:.0f}%",
            target_inventory_turnover,
            "<5%",
            f"{eoq_monthly_orders:.1f}/month",
            ">85%",
            f"KSh {eoq_monthly_orders * constants.TRANSPORT_COST:,.0f}/month"
        ]
    })

    st.dataframe(metrics_to_track, use_container_width=True, height=250, hide_index=True)


# ============================================================
# 🏗️ STORAGE CAPACITY PLANNING
# ============================================================
@fragment
def _render_storage_capacity_tab(ctx: DryIceContext) -> None:
    """Answers one question: if you switch to EOQ-sized bulk orders instead
    of today's smaller, more frequent ones, do you have anywhere to put it?

    Deliberately does NOT simulate a container fleet — Browns doesn't track
    one today, so the old version of this tab (hardcoded health scores,
    fake 2024 maintenance dates, invented uptime %) was actively misleading
    rather than just empty. Everything below is either computed live from
    ctx (EOQ, safety stock, container size, sublimation rate) or entered by
    the user in a plain number_input that only lives in session state —
    no new table, nothing implied to persist across sessions.
    """
    df, kpis, constants = ctx.df, ctx.kpis, ctx.constants

    st.markdown("### 🏗️ Storage Capacity Planning")
    st.caption(
        "For order-frequency savings and payback, see 📦 Inventory Management "
        "and 💰 Cost Optimization — this tab is only about whether you have "
        "somewhere to physically hold an EOQ-sized order."
    )

    if df.empty or ctx.eoq <= 0:
        st.info(
            "Need order history and a working EOQ figure before capacity "
            "planning means anything — check 📊 Order Analysis and "
            "📦 Inventory Management first."
        )
        return

    container_size = constants.CONTAINER_SIZE
    avg_order_size = kpis.get('avg_order_size', 0)
    peak_storage_kg = ctx.eoq + ctx.safety_stock

    # Containers-per-order from your own historical data when it's there —
    # falls back to a straight division by CONTAINER_SIZE otherwise.
    if 'Containers_Used' in df.columns and not df['Containers_Used'].empty:
        containers_today = math.ceil(df['Containers_Used'].mean())
    elif avg_order_size > 0 and container_size > 0:
        containers_today = math.ceil(avg_order_size / container_size)
    else:
        containers_today = 0

    containers_at_peak = math.ceil(peak_storage_kg / container_size) if container_size > 0 else 0

    # ------------------------------------------------------------
    # Order size: today vs. EOQ
    # ------------------------------------------------------------
    st.markdown("#### 📦 What changes if you switch to EOQ ordering")
    size_cols = st.columns(3)
    with size_cols[0]:
        st.metric("Current avg order", f"{avg_order_size:,.0f} kg")
    with size_cols[1]:
        size_delta_pct = ((ctx.eoq - avg_order_size) / avg_order_size * 100) if avg_order_size > 0 else 0
        st.metric("EOQ-recommended order", f"{ctx.eoq:,.0f} kg", f"{size_delta_pct:+.0f}% vs. today")
    with size_cols[2]:
        st.metric("Peak on-hand (EOQ + safety stock)", f"{peak_storage_kg:,.0f} kg")

    st.markdown("#### 🧊 Containers needed")
    st.caption(f"Using your standard container size of {container_size:,.0f} kg.")
    container_cols = st.columns(2)
    with container_cols[0]:
        st.metric("Containers today (typical order)", f"{containers_today}")
    with container_cols[1]:
        container_delta = containers_at_peak - containers_today
        st.metric(
            "Containers at EOQ + safety stock",
            f"{containers_at_peak}",
            f"+{container_delta}" if container_delta > 0 else f"{container_delta}",
        )

    # ------------------------------------------------------------
    # Self-reported capacity check — genuinely unknown to the app,
    # so ask rather than assume. Session-only; nothing persisted.
    # ------------------------------------------------------------
    st.markdown("---")
    st.markdown("#### 🏗️ Do you actually have room for that?")
    st.caption(
        "There's no container or storage-space data on file yet, so enter "
        "your real on-site capacity here to check it against the EOQ "
        "scenario above. This resets each session — it isn't saved anywhere."
    )

    available_capacity = st.number_input(
        "On-site dry ice storage capacity (kg)",
        min_value=0.0,
        value=float(peak_storage_kg),
        step=50.0,
        key="storage_capacity_input",
        help="Total kg you could realistically hold in insulated storage at once, today.",
    )

    capacity_gap = peak_storage_kg - available_capacity
    if available_capacity <= 0:
        st.info("Enter your real capacity above to see whether it covers the EOQ scenario.")
    elif capacity_gap > 0:
        gap_containers = math.ceil(capacity_gap / container_size) if container_size > 0 else 0
        st.warning(
            f"⚠️ Short by {capacity_gap:,.0f} kg (~{gap_containers} container(s)) at peak. "
            f"You'd need more storage before ordering at full EOQ — or order a smaller, "
            f"more frequent quantity that fits what you have today."
        )
    else:
        st.success(f"✅ Your reported capacity covers the EOQ scenario, with {abs(capacity_gap):,.0f} kg to spare.")

    # ------------------------------------------------------------
    # Sublimation: what bulk buying costs you in loss
    # ------------------------------------------------------------
    st.markdown("---")
    st.markdown("#### ❄️ Sublimation loss at current modeled rate")
    st.metric(
        "Estimated annual sublimation loss",
        f"KSh {ctx.annual_sublimation_loss:,.0f}",
        help=f"At the app's modeled average loss rate of {ctx.avg_sublimation:.2%}, applied to your annual volume.",
    )
    st.caption(
        "This uses the same flat average loss rate as the rest of the app — it does NOT "
        "scale up for the longer dwell time a bigger EOQ order would sit in storage before "
        "it's used. Treat it as a floor on the real cost once you're holding stock longer "
        "between deliveries, not a ceiling."
    )

    # ------------------------------------------------------------
    # Storage investment payback — only relevant if there's a gap
    # ------------------------------------------------------------
    if available_capacity > 0 and capacity_gap > 0:
        st.markdown("---")
        st.markdown("#### 💰 Is closing the gap worth it?")
        st.caption(
            "Weighs the one-off cost of adding storage against the annual transport "
            "savings EOQ ordering already gets you (from 📦 Inventory Management)."
        )
        storage_investment_cost = st.number_input(
            "Estimated cost to add the missing storage capacity (KSh)",
            min_value=0.0,
            value=0.0,
            step=5000.0,
            key="storage_investment_cost_input",
            help="Extra insulated containers, cold-room space, racking, etc.",
        )
        if storage_investment_cost > 0 and ctx.annual_transport_savings > 0:
            monthly_transport_savings = ctx.annual_transport_savings / 12
            payback_months = storage_investment_cost / monthly_transport_savings
            st.metric("Payback period", f"{payback_months:.1f} months")
        elif storage_investment_cost > 0:
            st.info("No transport savings modeled yet for this period — payback can't be estimated.")


# ============================================================
# 📜 TRANSACTION HISTORY
# ============================================================
@fragment
def _render_transaction_history_tab(ctx: DryIceContext) -> None:
    mobile_ui, inventory_tracker = ctx.mobile_ui, ctx.inventory_tracker

    st.markdown("## 📜 Inventory Transaction History")

    # Use transactions from context instead of session_state
    transactions = ctx.transactions

    if not transactions:
        st.info("No transactions recorded for this period yet. Use the sidebar to record usage or receipts.")
    else:
        trans_df = pd.DataFrame(transactions)
        trans_df['date'] = pd.to_datetime(trans_df['date'])
        trans_df = trans_df.sort_values('date', ascending=False)
        trans_df = compress_dataframe(trans_df)
        logger.info(f"Transactions loaded and compressed: {len(trans_df)} records")

        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            transaction_type = st.selectbox("Transaction Type", ["All", "usage", "receipt"], key="trans_type_filter")
        with filter_col2:
            date_min = trans_df['date'].min().date()
            date_max = trans_df['date'].max().date()
            selected_dates = st.date_input("Date Range", [date_min, date_max], min_value=date_min, max_value=date_max, key="date_range_filter")
        with filter_col3:
            show_limit = st.selectbox("Show Last", ["All", "10", "25", "50", "100"], key="show_limit_filter")

        filtered_df = trans_df.copy()
        if transaction_type != "All":
            filtered_df = filtered_df[filtered_df['type'] == transaction_type]
        if len(selected_dates) == 2:
            start_date, end_date = selected_dates
            filtered_df = filtered_df[(filtered_df['date'].dt.date >= start_date) & (filtered_df['date'].dt.date <= end_date)]
        if show_limit != "All":
            filtered_df = filtered_df.head(int(show_limit))

        with st.expander("📋 View Transaction Records", expanded=True):
            if len(filtered_df) > 100:
                paginated_df, paginator = paginate_dataframe(
                    filtered_df, page_size=50, key_prefix="transactions",
                    show_controls=True, compact=False,
                )
                display_df = paginated_df
                show_pagination = True
            else:
                display_df = filtered_df
                show_pagination = False

            display_df_display = display_df.copy()
            display_df_display['Date'] = display_df_display['date'].dt.strftime('%Y-%m-%d %H:%M')
            display_df_display['Type'] = display_df_display['type'].str.title()
            display_df_display['Quantity (kg)'] = display_df_display['quantity'].apply(lambda x: f"{x:,.2f}")

            st.dataframe(
                display_df_display[['Date', 'Type', 'Quantity (kg)', 'description']],
                use_container_width=True, height=400, hide_index=True,
            )

            if show_pagination:
                st.caption(f"📊 Page {paginator.current_page} of {paginator.total_pages} | Showing {len(display_df):,} of {len(filtered_df):,} records")
            else:
                st.caption(f"📊 Showing {len(filtered_df):,} records")

        st.markdown("### 📈 Transaction Summary (Filtered Period)")
        total_used = filtered_df[filtered_df['type']=='usage']['quantity'].sum()
        total_received = filtered_df[filtered_df['type']=='receipt']['quantity'].sum()
        net_change = total_received - total_used

        stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
        stat_col1.metric("Total Used", f"{total_used:,.1f} kg")
        stat_col2.metric("Total Received", f"{total_received:,.1f} kg")
        stat_col3.metric("Net Change", f"{net_change:,.1f} kg", delta=f"{net_change:,.1f} kg")
        stat_col4.metric("Total Transactions", len(filtered_df))

        st.markdown("### 📥 Export Filtered Data")
        if not filtered_df.empty:
            csv = filtered_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download CSV", data=csv,
                file_name=f"transaction_history_{st.session_state.selected_period.replace('/', '-')}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv', use_container_width=True,
            )
        else:
            st.caption("No data in the current filter to export.")

    st.divider()
    st.markdown("### 📊 Current Status")
    st.metric("Current Stock Level", f"{inventory_tracker.current_stock:,.1f} kg")