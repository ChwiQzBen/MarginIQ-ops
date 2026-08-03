from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from datetime import date, timedelta, datetime
from typing import Optional, Callable
import streamlit as st
import pandas as pd

from app.core.sales_service import dispatch_and_record_sale, available_stock_kg
from app.core.cheese_shared_state import ensure_cheese_state
from app.core.cheese_data_access import (
    get_lpo_lines, record_lpo_delivery, cancel_lpo_line,
    get_sales_history, save_customer, get_customers, delete_customer,
    reconcile_customers_from_history,
)
from app.core.lpo_sheet_sync import sync_new_lpo_lines_from_sheet, clear_lpo_sheet_caches, extract_sheet_id
from app.core.customer_analytics import (
    compute_ordering_patterns, compute_product_mix, compute_rfm, compute_clv, compute_churn_risk,
)

from app.core.commercial_reports import (
    build_commercial_report_data, summarize_commercial_report_data, generate_commercial_report,
)

from app.core.commercial_reports import (
    build_commercial_report_data, summarize_commercial_report_data, generate_commercial_report,
)
from app.core.report_ui import render_report_tab, KPI, TableSection, ChartSection

COMMERCIAL_TAB_NAMES = [
    "📄 LPO Register",
    "💰 Sales",
    "👥 Customers",
    "📊 Customer Analytics",
    "📋 Commercial Reports",
]


def render_commercial_mode(supabase_client=None,
                            has_permission: Optional[Callable[[str], bool]] = None) -> None:
    def _allowed(permission: str) -> bool:
        return has_permission(permission) if has_permission else True

    ensure_cheese_state(supabase_client)
    book = st.session_state.cheese_recipe_book
    tracker = st.session_state.cheese_tracker

    visible = [name for name, perm in {
        "📄 LPO Register": "manage_lpo",
        "💰 Sales": "record_cheese_sale",
        "👥 Customers": "manage_customers",
        "📊 Customer Analytics": "view_customer_analytics",
        "📋 Commercial Reports": "view_commercial_reports",
    }.items() if _allowed(perm)]

    if not visible:
        st.warning("This section isn't available for your current role.")
        return

    tabs = st.tabs(visible)
    tab_lookup = dict(zip(visible, tabs))

    if "📄 LPO Register" in tab_lookup:
        with tab_lookup["📄 LPO Register"]:
            _render_lpo_register_tab(book, supabase_client)
    if "💰 Sales" in tab_lookup:
        with tab_lookup["💰 Sales"]:
            _render_sales_tab(book, tracker, supabase_client)
    if "👥 Customers" in tab_lookup:
        with tab_lookup["👥 Customers"]:
            _render_customers_tab(supabase_client)
    if "📊 Customer Analytics" in tab_lookup:
        with tab_lookup["📊 Customer Analytics"]:
            _render_customer_analytics_tab(supabase_client)
    if "📋 Commercial Reports" in tab_lookup:
        with tab_lookup["📋 Commercial Reports"]:
            _render_commercial_reports_tab(supabase_client)


def _customer_picker(customers: list, key_prefix: str):
    """Customer selectbox + always-visible 'or type a new name' fallback.
    Deliberately NOT a conditional reveal (e.g. text_input only shown after
    picking '+ New customer') — widgets inside st.form() don't rerun until
    submit, so a selectbox-triggered reveal wouldn't appear reliably. At
    submit time, a typed name here takes priority over the dropdown pick."""
    options = ["(select existing)"] + [c["name"] for c in customers]
    pick = st.selectbox("Customer", options, key=f"{key_prefix}_customer_pick")
    new_name = st.text_input("...or type a new customer name", key=f"{key_prefix}_customer_new")

    if new_name.strip():
        return None, new_name.strip()
    if pick != "(select existing)":
        match = next((c for c in customers if c["name"] == pick), None)
        return (match["id"], match["name"]) if match else (None, pick)
    return None, ""


def _resolve_customer_id(customer_id, customer_name, supabase_client):
    """If the picker returned a brand-new typed name (customer_id is None
    but a name was given), create the customer record now so the sale/LPO
    being saved links to it immediately."""
    if customer_id is not None:
        return customer_id
    if customer_name:
        return save_customer(name=customer_name, supabase_client=supabase_client)
    return None


# ============================================================
# TAB: LPO REGISTER
# ============================================================
def _render_lpo_register_tab(book, supabase_client) -> None:
    st.markdown("### 📄 LPO Register")
    st.caption(
        "Confirmed customer orders. Open quantities here floor tomorrow's "
        "production plan in 🧀 Manufacturing → 📋 Production Planning."
    )
    if not book.list_names():
        st.info("Add a recipe in 🧀 Manufacturing → Recipes before receiving LPOs.")
        return

    customers = get_customers(supabase_client=supabase_client)

    tomorrow = date.today() + timedelta(days=1)
    tomorrow_lines = get_lpo_lines(delivery_date=tomorrow, supabase_client=supabase_client)
    tomorrow_open = [l for l in tomorrow_lines if l["status"] not in ("Cancelled", "Delivered")]
    if tomorrow_open:
        total_kg = sum(l["quantity_kg"] for l in tomorrow_open)
        st.metric(f"Confirmed for {tomorrow.strftime('%b %d')} (tomorrow)", f"{total_kg:,.1f} kg")
    else:
        st.caption(f"No open LPOs due {tomorrow.strftime('%b %d')} (tomorrow) yet.")

    with st.expander("🔄 Sync from Google Sheet", expanded=not tomorrow_open):
        st.caption(
            "LPOs are entered in the Google Sheet, not here — this just pulls in "
            "whatever's new. Already-synced rows are never re-touched, even if "
            "the Sheet row changes afterward."
        )
        col_refresh, _ = st.columns([1, 4])
        with col_refresh:
            if st.button("🔄 Refresh from Sheet", key="lpo_sheet_refresh"):
                clear_lpo_sheet_caches()
                st.rerun()

        sheet_url = st.secrets.get("LPO_SHEET_URL")
        if not sheet_url:
            st.error("No `LPO_SHEET_URL` set in Streamlit secrets — can't sync LPOs.")
        else:
            sheet_id = extract_sheet_id(sheet_url)
        
            try:
                result = sync_new_lpo_lines_from_sheet(
                    sheet_id, valid_cheese_names=book.list_names(), supabase_client=supabase_client,
                )
                if result["created"]:
                    st.success(f"✅ Synced {result['created']} new LPO line(s) from the Sheet.")
                    st.rerun()
                else:
                    st.caption("No new LPO lines to sync — everything in the Sheet is already recorded.")
                if result["skipped_invalid"]:
                    with st.expander(f"⚠️ {result['skipped_invalid']} row(s) need a fix in the Sheet"):
                        for err in result["errors"]:
                            st.caption(f"- {err}")
            except Exception as e:
                st.error(f"❌ Could not sync from the Sheet: {e}")

    st.markdown("---")
    st.markdown("#### Pending LPOs")
    all_lines = get_lpo_lines(supabase_client=supabase_client)
    pending = [l for l in all_lines if l["status"] in ("Pending", "Confirmed", "Partially Delivered")]
    pending.sort(key=lambda l: l["delivery_date"])
    if not pending:
        st.info("No open LPOs.")
    else:
        today_str = date.today().isoformat()
        for line in pending:
            overdue = line["delivery_date"] < today_str
            already_delivered_kg = float(line.get("quantity_delivered_kg") or 0.0)
            remaining_kg = max(0.0, float(line["quantity_kg"]) - already_delivered_kg)

            expiry_flag = ""
            lpo_expiry_str = line.get("lpo_expiry_date")
            if lpo_expiry_str:
                try:
                    expiry_date_val = datetime.fromisoformat(lpo_expiry_str).date()
                    days_left = (expiry_date_val - date.today()).days
                    if days_left < 0:
                        expiry_flag = f" · 🔴 LPO expired {abs(days_left)}d ago"
                    elif days_left <= 7:
                        expiry_flag = f" · 🟡 LPO expires in {days_left}d"
                except ValueError:
                    pass

            item_display = line.get("sku_description") or line["cheese_name"]
            with st.container():
                st.markdown(
                    f"{'⚠️ OVERDUE — ' if overdue else ''}**{line['lpo_number']}** — "
                    f"{line['customer_name']} — {item_display} — "
                    f"{line['quantity_kg']:.1f}kg — due {line['delivery_date']} — *{line['status']}*{expiry_flag}"
                )
                if already_delivered_kg > 0:
                    st.caption(f"📦 {already_delivered_kg:.1f}kg delivered so far — "
                               f"{remaining_kg:.1f}kg remaining")
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    deliver_now_kg = st.number_input(
                        "Deliver now (kg)", min_value=0.0, value=float(remaining_kg),
                        step=1.0, key=f"deliver_kg_{line['id']}", label_visibility="collapsed",
                    )
                with c2:
                    if st.button("✅ Record Delivery", key=f"deliver_{line['id']}"):
                        cumulative_delivered = already_delivered_kg + deliver_now_kg
                        record_lpo_delivery(line["id"], cumulative_delivered, supabase_client)
                        st.success(f"Recorded delivery for {line['lpo_number']}.")
                        st.rerun()
                with c3:
                    if st.button("🚫 Cancel", key=f"cancel_{line['id']}"):
                        cancel_lpo_line(line["id"], supabase_client)
                        st.warning(f"Cancelled {line['lpo_number']}.")
                        st.rerun()
                st.markdown("---")

    with st.expander("📜 LPO History (delivered / cancelled)", expanded=False):
        history = [l for l in all_lines if l["status"] in ("Delivered", "Partially Delivered", "Cancelled")]
        if history:
            df = pd.DataFrame(history)
            cols = ["lpo_number", "customer_name", "cheese_name", "delivery_date",
                    "quantity_kg", "quantity_delivered_kg", "status"]
            st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Download LPO History CSV", csv,
                                file_name=f"lpo_history_{date.today().strftime('%Y%m%d')}.csv",
                                mime="text/csv")
        else:
            st.caption("Nothing delivered or cancelled yet.")


# ============================================================
# TAB: SALES
# ============================================================
def _render_sales_tab(book, tracker, supabase_client) -> None:
    st.markdown("### 💰 Record a Sale")
    st.caption(
        "Dispatches stock via FEFO (earliest-expiry batches consumed first) and "
        "saves the sale to history, so 🧀 Manufacturing → Production Planning's "
        "demand forecast has real data."
    )

    if not book.list_names():
        st.info("Add a recipe in 🧀 Manufacturing → Recipes first, then produce "
                 "and release some stock before recording sales.")
        return

    customers = get_customers(supabase_client=supabase_client)

    with st.form("record_sale_form"):
        col1, col2 = st.columns(2)
        with col1:
            sale_date = st.date_input("Sale Date", value=date.today())
            cheese_name = st.selectbox("Cheese", book.list_names(), key="sale_cheese")
        with col2:
            quantity_kg = st.number_input("Quantity Sold (kg)", min_value=0.0, value=1.0, step=0.5)
            price_per_kg = st.number_input("Price per kg (KSh)", min_value=0.0, value=0.0, step=10.0)
        customer_id, customer = _customer_picker(customers, "sale")
        notes = st.text_input("Notes (optional)")

        available = available_stock_kg(tracker, cheese_name) if cheese_name else 0.0
        st.caption(f"Available stock: {available:,.1f} kg")

        if st.form_submit_button("💰 Record Sale", type="primary"):
            if quantity_kg <= 0:
                st.error("Enter a quantity greater than 0.")
            elif price_per_kg <= 0:
                st.error("Enter a price greater than 0.")
            elif quantity_kg > available:
                st.error(f"Only {available:,.1f}kg of {cheese_name} available — "
                         f"can't record a sale for {quantity_kg:,.1f}kg.")
            else:
                try:
                    resolved_id = _resolve_customer_id(customer_id, customer, supabase_client)
                    result = dispatch_and_record_sale(
                        tracker, cheese_name, quantity_kg, price_per_kg, sale_date,
                        customer, notes, supabase_client, customer_id=resolved_id,
                    )
                    st.success(f"✅ Sold {result.allocated_kg:,.1f}kg of {cheese_name} "
                               f"for KSh {result.revenue:,.0f}, drawn from "
                               f"{len(result.batch_lines)} batch(es).")
                    if result.shortfall_kg > 0:
                        st.warning(f"⚠️ {result.shortfall_kg:.1f}kg short — recorded what was "
                                   f"actually available. Stock may have changed since the page loaded.")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Could not record sale: {e}")

    st.markdown("---")
    st.markdown("#### Recent Sales")
    start = date.today() - timedelta(days=30)
    sales = get_sales_history(start_date=start, supabase_client=supabase_client)
    if sales:
        df = pd.DataFrame(sales)
        st.dataframe(
            df[["id", "date", "cheese_name", "quantity_kg", "price_per_kg", "revenue", "customer", "notes"]],
            use_container_width=True, hide_index=True,
        )
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Sales CSV", csv,
                            file_name=f"cheese_sales_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
    else:
        st.info("No sales recorded in the last 30 days.")


# ============================================================
# TAB: CUSTOMERS
# ============================================================
def _render_customers_tab(supabase_client) -> None:
    st.markdown("### 👥 Customers")
    st.caption(
        "Customer registry. New Sales/LPOs can now pick a customer directly — "
        "existing history still needs reconciliation (below) before Customer "
        "Analytics can trust it."
    )

    customers = get_customers(supabase_client=supabase_client)

    if customers:
        df = pd.DataFrame(customers)
        cols = ["name", "contact_person", "phone", "email", "credit_terms_days"]
        st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True, hide_index=True)
    else:
        st.info("No customers yet — add your first one below.")

    with st.expander("➕ Add / Edit Customer", expanded=not customers):
        names = ["(new customer)"] + [c["name"] for c in customers]
        pick = st.selectbox("Customer", names, key="customer_picker")
        editing = next((c for c in customers if c["name"] == pick), None) if pick != "(new customer)" else None

        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name", value=editing["name"] if editing else "", key="customer_name")
            contact_person = st.text_input("Contact Person",
                                            value=editing.get("contact_person", "") if editing else "",
                                            key="customer_contact")
            phone = st.text_input("Phone", value=editing.get("phone", "") if editing else "",
                                   key="customer_phone")
        with col2:
            email = st.text_input("Email", value=editing.get("email", "") if editing else "",
                                   key="customer_email")
            credit_terms = st.number_input(
                "Credit Terms (days)", min_value=0,
                value=int(editing.get("credit_terms_days", 0)) if editing else 0,
                step=1, key="customer_credit_terms", help="0 = cash / COD",
            )
            address = st.text_input("Address", value=editing.get("address", "") if editing else "",
                                     key="customer_address")
        notes = st.text_input("Notes (optional)", value=editing.get("notes", "") if editing else "",
                               key="customer_notes")

        col_save, col_delete = st.columns(2)
        with col_save:
            if st.button("💾 Save Customer", type="primary", use_container_width=True, key="save_customer_btn"):
                if not name:
                    st.error("Name is required.")
                else:
                    try:
                        save_customer(
                            name=name, contact_person=contact_person, phone=phone, email=email,
                            address=address, credit_terms_days=int(credit_terms), notes=notes,
                            customer_id=editing["id"] if editing else None,
                            supabase_client=supabase_client,
                        )
                        st.success(f"✅ Saved '{name}'.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Could not save customer: {e}")
        with col_delete:
            if editing and st.button("🗑️ Delete Customer", use_container_width=True, key="delete_customer_btn"):
                delete_customer(editing["id"], supabase_client)
                st.success(f"Deleted '{editing['name']}'.")
                st.rerun()

    with st.expander("🔄 Reconcile existing Sales/LPO history", expanded=False):
        st.caption(
            "One-time backfill: matches freetext customer names on past sales "
            "and LPOs to this registry (case-insensitive), creating a new "
            "customer record for any name that doesn't already exist. Safe to "
            "run again later — already-linked rows are skipped."
        )
        if st.button("Run Reconciliation", key="run_customer_reconciliation"):
            with st.spinner("Matching historical records..."):
                result = reconcile_customers_from_history(supabase_client=supabase_client)
            st.success(
                f"✅ Linked {result['sales_linked']} sale(s) and {result['lpo_linked']} "
                f"LPO(s); created {result['customers_created']} new customer record(s)."
            )
            st.rerun()


# ============================================================
# TAB: CUSTOMER ANALYTICS  (Product Preferences + Ordering Patterns —
# the two foundational views RFM/CLV/churn will build on next)
# ============================================================
def _render_customer_analytics_tab(supabase_client) -> None:
    st.markdown("### 📊 Customer Analytics")
    st.caption(
        "Ordering Patterns, Product Preferences, RFM segmentation, CLV, and "
        "churn risk. Only customer_id-linked sales count here — scores are "
        "most meaningful once there's a real book of customers and order "
        "history, so treat early numbers as directional."
    )

    customers = get_customers(supabase_client=supabase_client)
    sales = get_sales_history(supabase_client=supabase_client)
    linked_count = sum(1 for s in sales if s.get("customer_id") is not None)

    if not sales:
        st.info("No sales recorded yet.")
        return
    if linked_count == 0:
        st.warning(
            "No sales are linked to a customer yet. Run reconciliation in the "
            "👥 Customers tab, or start picking customers from the dropdown "
            "in 💰 Sales going forward."
        )
        return
    if linked_count < len(sales):
        st.caption(f"ℹ️ {linked_count} of {len(sales)} sales are customer-linked — "
                    f"{len(sales) - linked_count} unlinked sale(s) excluded below.")

    patterns = compute_ordering_patterns(sales, customers)
    mixes = compute_product_mix(sales, customers)
    mix_by_id = {m.customer_id: m for m in mixes}

    st.markdown("#### Ordering Patterns")
    if patterns:
        rows = []
        for p in patterns:
            rows.append({
                "Customer": p.customer_name,
                "Orders": p.total_orders,
                "Total Kg": f"{p.total_kg:,.1f}",
                "Revenue": f"KSh {p.total_revenue:,.0f}",
                "Avg Days Between Orders": p.avg_days_between_orders if p.avg_days_between_orders else "—",
                "Usual Day": p.most_common_weekday or "—",
                "Last Order": p.last_order_date,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No customer-linked orders yet.")

    st.markdown("---")
    st.markdown("#### Product Preferences")
    if patterns:
        selected_name = st.selectbox(
            "Customer", [p.customer_name for p in patterns], key="analytics_customer_pick",
        )
        selected = next((p for p in patterns if p.customer_name == selected_name), None)
        mix = mix_by_id.get(selected.customer_id) if selected else None
        if mix and mix.by_cheese_kg:
            mix_df = pd.DataFrame(
                sorted(mix.by_cheese_kg.items(), key=lambda kv: kv[1], reverse=True),
                columns=["Cheese", "Kg Purchased"],
            )
            st.dataframe(mix_df, use_container_width=True, hide_index=True)
            st.caption(f"Top product: **{mix.top_cheese}**")
        else:
            st.caption("No purchase history for this customer.")

    st.markdown("---")
    st.markdown("#### RFM Segments")
    st.caption("Recency / Frequency / Monetary, scored 1-5 relative to your other customers.")
    rfm = compute_rfm(sales, customers)
    if rfm:
        rfm_df = pd.DataFrame([{
            "Customer": r.customer_name,
            "Segment": r.rfm_segment,
            "Recency (days)": r.recency_days,
            "R": r.recency_score, "F": r.frequency_score, "M": r.monetary_score,
        } for r in rfm])
        st.dataframe(rfm_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No customer-linked orders yet.")

    st.markdown("---")
    st.markdown("#### Customer Lifetime Value (CLV)")
    st.caption(
        "Historical revenue plus a cadence-projected annual value. Projection "
        "needs at least 2 orders to estimate a cadence — shows '—' until then."
    )
    clv = compute_clv(sales, customers)
    if clv:
        clv_df = pd.DataFrame([{
            "Customer": c.customer_name,
            "Historical Revenue": f"KSh {c.historical_revenue:,.0f}",
            "Avg Order Value": f"KSh {c.avg_order_value:,.0f}",
            "Est. Orders/Year": c.orders_per_year_est if c.orders_per_year_est else "—",
            "Projected Annual Value": f"KSh {c.projected_annual_value:,.0f}" if c.projected_annual_value else "—",
        } for c in clv])
        st.dataframe(clv_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No customer-linked orders yet.")

    st.markdown("---")
    st.markdown("#### Churn Risk")
    st.caption(
        "Flags customers overdue relative to their own ordering cadence. "
        "Customers with under 2 orders don't have a personal cadence yet, "
        "so a 30-day default is used instead (marked with *)."
    )
    churn = compute_churn_risk(sales, customers)
    if churn:
        risk_order = {"High": 0, "Medium": 1, "Low": 2}
        churn_sorted = sorted(churn, key=lambda c: risk_order.get(c.risk_level, 3))
        churn_df = pd.DataFrame([{
            "Customer": c.customer_name + (" *" if c.used_default_cadence else ""),
            "Days Since Last Order": c.days_since_last_order,
            "Expected Gap (days)": c.expected_gap_days,
            "Risk": c.risk_level,
        } for c in churn_sorted])
        st.dataframe(churn_df, use_container_width=True, hide_index=True)
        high_risk = [c for c in churn if c.risk_level == "High"]
        if high_risk:
            names = ", ".join(c.customer_name for c in high_risk)
            st.warning(f"⚠️ High churn risk: {names}")
    else:
        st.caption("No customer-linked orders yet.")


# ============================================================
# TAB: COMMERCIAL REPORTS
# ============================================================
def _render_commercial_reports_tab(supabase_client) -> None:
    def _build(start_date, end_date):
        sales = get_sales_history(start_date=start_date, end_date=end_date, supabase_client=supabase_client)
        all_lpo = get_lpo_lines(supabase_client=supabase_client)
        lpo_lines = [l for l in all_lpo
                     if start_date.isoformat() <= l["delivery_date"] <= end_date.isoformat()]
        return build_commercial_report_data(sales, lpo_lines, start_date, end_date)

    render_report_tab(
        title="📋 Commercial Reports",
        caption="Rollups over Sales and LPO data. Customer-level breakdowns use freetext "
                "customer names — expect some fragmentation until Sales/LPO are fully "
                "linked to the Customers registry (see 👥 Customers → Reconciliation).",
        session_key="commercial_report",
        build_fn=_build,
        summarize_fn=summarize_commercial_report_data,
        pdf_fn=generate_commercial_report,
        pdf_filename_prefix="commercial_report",
        date_key_prefix="comm_report",
        kpis=[
            KPI("Total Revenue", lambda d: f"KSh {d.total_revenue:,.0f}"),
            KPI("Total Volume", lambda d: f"{d.total_kg:,.1f} kg"),
            KPI("Sales Transactions", lambda d: d.total_sales_transactions),
            KPI("LPO Volume", lambda d: f"{d.lpo_total_kg:,.1f} kg"),
            KPI("LPO Fill Rate", lambda d: f"{d.lpo_fill_rate_pct:.0f}%"),
            KPI("LPOs Cancelled", lambda d: d.lpo_cancelled_count),
        ],
        chart_sections=[
            ChartSection(
                "Revenue Over Time",
                lambda d: bool(d.revenue_by_day),
                lambda d: d.revenue_by_day,
                x="date", y="revenue", kind="line",
            ),
        ],
        table_sections=[
            TableSection(
                "Revenue by Product",
                lambda d: bool(d.revenue_by_product),
                lambda d: [{"Cheese": r["cheese_name"], "Revenue": f"KSh {r['revenue']:,.0f}",
                            "Kg": f"{r['kg']:,.1f}", "Sales": r["count"]}
                           for r in d.revenue_by_product],
            ),
            TableSection(
                "Revenue by Customer",
                lambda d: bool(d.revenue_by_customer),
                lambda d: [{"Customer": r["customer"], "Revenue": f"KSh {r['revenue']:,.0f}",
                            "Kg": f"{r['kg']:,.1f}", "Sales": r["count"]}
                           for r in d.revenue_by_customer],
            ),
            TableSection(
                "LPO Status Breakdown",
                lambda d: bool(d.lpo_status_counts),
                lambda d: [{"Status": k, "Count": v} for k, v in d.lpo_status_counts.items()],
            ),
        ],
    )