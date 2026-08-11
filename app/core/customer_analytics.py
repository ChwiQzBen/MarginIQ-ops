"""
app/core/customer_analytics.py
=================================
Pure computation over customer-linked sales history — no Streamlit, no DB
calls. Takes plain lists of dicts (from cheese_data_access.get_sales_history
/ get_customers) and returns dataclasses the UI renders.

Requires customer_id-linked sales (see cheese_data_access.
reconcile_customers_from_history) — rows with customer_id=None are excluded
from per-customer breakdowns, since a freetext-only name can't be reliably
grouped (this is the whole reason the customer_id migration happened before
this module was written).
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from collections import defaultdict
import statistics

# Fallback expected order cadence (days) for customers with fewer than 2
# linked orders, who therefore have no personal avg_days_between_orders yet.
# Used only by compute_churn_risk.
DEFAULT_CADENCE_DAYS = 30

@dataclass
class CustomerOrderingPattern:
    customer_id: int
    customer_name: str
    total_orders: int
    total_kg: float
    total_revenue: float
    first_order_date: str
    last_order_date: str
    avg_days_between_orders: Optional[float]  # None if fewer than 2 orders
    most_common_weekday: Optional[str]


@dataclass
class CustomerProductMix:
    customer_id: int
    customer_name: str
    by_cheese_kg: Dict[str, float] = field(default_factory=dict)
    top_cheese: Optional[str] = None

@dataclass
class CustomerRFM:
    customer_id: int
    customer_name: str
    recency_days: int
    frequency: int
    monetary: float
    recency_score: int      # 1 (long ago) - 5 (very recent)
    frequency_score: int    # 1 (rare) - 5 (frequent)
    monetary_score: int     # 1 (low spend) - 5 (high spend)
    rfm_segment: str


@dataclass
class CustomerCLV:
    customer_id: int
    customer_name: str
    historical_revenue: float
    avg_order_value: float
    orders_per_year_est: Optional[float]     # None if fewer than 2 orders (no cadence yet)
    projected_annual_value: Optional[float]  # None if orders_per_year_est is None


@dataclass
class CustomerChurnRisk:
    customer_id: int
    customer_name: str
    days_since_last_order: int
    expected_gap_days: float
    used_default_cadence: bool  # True when <2 orders forced the DEFAULT_CADENCE_DAYS fallback
    risk_ratio: float           # days_since_last_order / expected_gap_days
    risk_level: str             # "Low" / "Medium" / "High"


@dataclass
class CustomerReturnMetrics:
    customer_id: int
    customer_name: str
    total_returned_kg: float
    estimated_return_value: float  # approximation -- see compute_return_metrics docstring
    return_count: int
    sales_kg: float
    return_rate_pct: Optional[float]  # None if this customer has no linked sales to rate against
    top_reason: Optional[str]


def _linked_sales(sales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sales rows with a real customer_id — the only rows these functions
    can group reliably. Call cheese_data_access.reconcile_customers_from_history
    first if this excludes most of your history."""
    return [s for s in sales if s.get("customer_id") is not None]

def _score_1_to_5(sorted_values: List[float], value: float, reverse: bool = False) -> int:
    """Rank `value` within `sorted_values` (ascending) into a 1-5 bucket,
    relative to the other values in this dataset. Works for any N, including
    N<=1 (returns 3, neutral) — fixed quintile cuts blow up on the small,
    duplicate-heavy datasets typical of an early customer book, and would
    need re-tuning as volume grows anyway. This rescales itself every call.
    """
    n = len(sorted_values)
    if n <= 1:
        return 3
    rank = sorted_values.index(value)
    percentile = rank / (n - 1)
    if reverse:
        percentile = 1 - percentile
    return min(5, max(1, round(1 + percentile * 4)))


def _rfm_segment(r_score: int, f_score: int, m_score: int) -> str:
    total = r_score + f_score + m_score
    if total >= 13:
        return "Champion"
    if total >= 10:
        return "Loyal"
    if r_score <= 2 and (f_score >= 3 or m_score >= 3):
        return "At Risk"  # used to buy well, gone quiet
    if total >= 7:
        return "Needs Attention"
    return "Lost"


def compute_ordering_patterns(sales: List[Dict[str, Any]],
                               customers: List[Dict[str, Any]]) -> List[CustomerOrderingPattern]:
    name_by_id = {c["id"]: c["name"] for c in customers}
    by_customer: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for s in _linked_sales(sales):
        by_customer[s["customer_id"]].append(s)

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    patterns = []
    for cid, rows in by_customer.items():
        rows_sorted = sorted(rows, key=lambda r: r["date"])
        dates = [datetime.fromisoformat(r["date"]).date() for r in rows_sorted]

        avg_gap = None
        if len(dates) >= 2:
            gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            avg_gap = statistics.mean(gaps)

        weekday_counts: Dict[str, int] = defaultdict(int)
        for d in dates:
            weekday_counts[weekday_names[d.weekday()]] += 1
        most_common_weekday = max(weekday_counts, key=weekday_counts.get) if weekday_counts else None

        patterns.append(CustomerOrderingPattern(
            customer_id=cid,
            customer_name=name_by_id.get(cid, f"Customer #{cid}"),
            total_orders=len(rows),
            total_kg=sum(float(r["quantity_kg"]) for r in rows),
            total_revenue=sum(float(r["revenue"]) for r in rows),
            first_order_date=dates[0].isoformat(),
            last_order_date=dates[-1].isoformat(),
            avg_days_between_orders=round(avg_gap, 1) if avg_gap is not None else None,
            most_common_weekday=most_common_weekday,
        ))

    patterns.sort(key=lambda p: p.total_revenue, reverse=True)
    return patterns


def compute_product_mix(sales: List[Dict[str, Any]],
                         customers: List[Dict[str, Any]]) -> List[CustomerProductMix]:
    name_by_id = {c["id"]: c["name"] for c in customers}
    by_customer: Dict[int, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in _linked_sales(sales):
        by_customer[s["customer_id"]][s["cheese_name"]] += float(s["quantity_kg"])

    mixes = []
    for cid, cheese_totals in by_customer.items():
        top_cheese = max(cheese_totals, key=cheese_totals.get) if cheese_totals else None
        mixes.append(CustomerProductMix(
            customer_id=cid,
            customer_name=name_by_id.get(cid, f"Customer #{cid}"),
            by_cheese_kg=dict(cheese_totals),
            top_cheese=top_cheese,
        ))
    mixes.sort(key=lambda m: sum(m.by_cheese_kg.values()), reverse=True)
    return mixes


def compute_rfm(sales: List[Dict[str, Any]], customers: List[Dict[str, Any]],
                 as_of_date: Optional[str] = None) -> List[CustomerRFM]:
    """Recency/Frequency/Monetary, each scored 1-5 relative to the other
    customers in this dataset (self-calibrating, not fixed thresholds — see
    _score_1_to_5). Segments are a simplified classic RFM rubric; revisit the
    cutoffs once there's enough customers for them to matter statistically.
    """
    patterns = compute_ordering_patterns(sales, customers)
    if not patterns:
        return []

    today = datetime.fromisoformat(as_of_date).date() if as_of_date else datetime.now().date()
    recencies = [(today - datetime.fromisoformat(p.last_order_date).date()).days for p in patterns]

    recency_rank = sorted(recencies)
    frequency_rank = sorted(p.total_orders for p in patterns)
    monetary_rank = sorted(p.total_revenue for p in patterns)

    results = []
    for p, recency_days in zip(patterns, recencies):
        r_score = _score_1_to_5(recency_rank, recency_days, reverse=True)  # fewer days = better
        f_score = _score_1_to_5(frequency_rank, p.total_orders)
        m_score = _score_1_to_5(monetary_rank, p.total_revenue)
        results.append(CustomerRFM(
            customer_id=p.customer_id,
            customer_name=p.customer_name,
            recency_days=recency_days,
            frequency=p.total_orders,
            monetary=p.total_revenue,
            recency_score=r_score,
            frequency_score=f_score,
            monetary_score=m_score,
            rfm_segment=_rfm_segment(r_score, f_score, m_score),
        ))

    results.sort(key=lambda r: (r.recency_score + r.frequency_score + r.monetary_score), reverse=True)
    return results


def compute_clv(sales: List[Dict[str, Any]], customers: List[Dict[str, Any]]) -> List[CustomerCLV]:
    """Historical revenue plus a cadence-projected annual value. This is a
    simple heuristic (avg order value x estimated orders/year), not a
    predictive model — good enough to rank customers by value with the
    order counts an early-stage book will have. Revisit with something like
    BG/NBD once there's enough volume to fit one properly.
    """
    patterns = compute_ordering_patterns(sales, customers)
    results = []
    for p in patterns:
        avg_order_value = p.total_revenue / p.total_orders if p.total_orders else 0.0
        orders_per_year = (365.0 / p.avg_days_between_orders) if p.avg_days_between_orders else None
        projected_annual = (avg_order_value * orders_per_year) if orders_per_year else None
        results.append(CustomerCLV(
            customer_id=p.customer_id,
            customer_name=p.customer_name,
            historical_revenue=p.total_revenue,
            avg_order_value=round(avg_order_value, 2),
            orders_per_year_est=round(orders_per_year, 1) if orders_per_year else None,
            projected_annual_value=round(projected_annual, 2) if projected_annual else None,
        ))
    results.sort(key=lambda c: c.projected_annual_value or c.historical_revenue, reverse=True)
    return results


def compute_churn_risk(sales: List[Dict[str, Any]], customers: List[Dict[str, Any]],
                        as_of_date: Optional[str] = None) -> List[CustomerChurnRisk]:
    """Flags customers overdue relative to their own ordering cadence.
    Customers with fewer than 2 linked orders have no personal cadence yet,
    so this falls back to DEFAULT_CADENCE_DAYS and marks used_default_cadence
    so the UI can caveat those rows rather than showing them with the same
    confidence as a cadence-based read.
    """
    patterns = compute_ordering_patterns(sales, customers)
    today = datetime.fromisoformat(as_of_date).date() if as_of_date else datetime.now().date()

    results = []
    for p in patterns:
        days_since = (today - datetime.fromisoformat(p.last_order_date).date()).days
        used_default = p.avg_days_between_orders is None
        expected_gap = p.avg_days_between_orders if p.avg_days_between_orders else DEFAULT_CADENCE_DAYS
        ratio = days_since / expected_gap if expected_gap else 0.0

        if ratio < 1.0:
            level = "Low"
        elif ratio < 2.0:
            level = "Medium"
        else:
            level = "High"

        results.append(CustomerChurnRisk(
            customer_id=p.customer_id,
            customer_name=p.customer_name,
            days_since_last_order=days_since,
            expected_gap_days=round(expected_gap, 1),
            used_default_cadence=used_default,
            risk_ratio=round(ratio, 2),
            risk_level=level,
        ))

    results.sort(key=lambda c: c.risk_ratio, reverse=True)
    return results


def compute_return_metrics(sales: List[Dict[str, Any]], returns: List[Dict[str, Any]],
                            customers: List[Dict[str, Any]]) -> List[CustomerReturnMetrics]:
    """Return rate and estimated return value per customer, matched
    against that customer's linked sales over the same history passed in.

    cheese_returns has no price_per_kg column -- a return is logged as a
    quantity + reason, not a value -- so estimated_return_value here is an
    approximation: returned_kg x that customer's own blended avg revenue
    per kg (from compute_ordering_patterns: total_revenue / total_kg
    across all their linked sales). Good enough to RANK customers by
    return burden. NOT the number to quote in a negotiation -- for an
    exact Kshs figure (e.g. the Carrefour monthly return value), either
    add a price_per_kg column to cheese_returns / the Returns sheet tab,
    or join each return's original_ref back to the specific sale/LPO
    line it came from.

    Rows with customer_id=None on either side are excluded, same
    reasoning as _linked_sales.
    """
    patterns = compute_ordering_patterns(sales, customers)
    sales_kg_by_customer = {p.customer_id: p.total_kg for p in patterns}
    avg_price_per_kg = {
        p.customer_id: (p.total_revenue / p.total_kg) if p.total_kg else 0.0
        for p in patterns
    }
    name_by_id = {c["id"]: c["name"] for c in customers}

    linked_returns = [r for r in returns if r.get("customer_id") is not None]
    by_customer: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in linked_returns:
        by_customer[r["customer_id"]].append(r)

    results = []
    for cid, rows in by_customer.items():
        returned_kg = sum(float(r["quantity_kg"]) for r in rows)
        sales_kg = sales_kg_by_customer.get(cid, 0.0)
        rate = round(100 * returned_kg / sales_kg, 1) if sales_kg > 0 else None

        reason_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            reason_counts[r.get("reason_code") or "unspecified"] += 1
        top_reason = max(reason_counts, key=reason_counts.get) if reason_counts else None

        results.append(CustomerReturnMetrics(
            customer_id=cid,
            customer_name=name_by_id.get(cid, f"Customer #{cid}"),
            total_returned_kg=round(returned_kg, 2),
            estimated_return_value=round(returned_kg * avg_price_per_kg.get(cid, 0.0), 2),
            return_count=len(rows),
            sales_kg=round(sales_kg, 2),
            return_rate_pct=rate,
            top_reason=top_reason,
        ))

    results.sort(key=lambda r: (r.return_rate_pct or 0), reverse=True)
    return results


if __name__ == "__main__":
    customers = [{"id": 1, "name": "Java House"}, {"id": 2, "name": "Carrefour"}, {"id": 3, "name": "Naivas"}]
    sales = [
        {"date": "2026-06-01", "cheese_name": "Mozzarella", "quantity_kg": 10.0, "revenue": 6500.0, "customer_id": 1},
        {"date": "2026-06-08", "cheese_name": "Mozzarella", "quantity_kg": 12.0, "revenue": 7800.0, "customer_id": 1},
        {"date": "2026-06-15", "cheese_name": "Halloumi", "quantity_kg": 5.0, "revenue": 4000.0, "customer_id": 1},
        {"date": "2026-06-03", "cheese_name": "Cheddar", "quantity_kg": 20.0, "revenue": 15000.0, "customer_id": 2},
        {"date": "2026-01-05", "cheese_name": "Gouda", "quantity_kg": 8.0, "revenue": 5200.0, "customer_id": 3},
        {"date": "2026-06-01", "cheese_name": "Gouda", "quantity_kg": 3.0, "revenue": 2400.0, "customer_id": None},  # unlinked
    ]

    print("Test 1: ordering patterns")
    patterns = compute_ordering_patterns(sales, customers)
    for p in patterns:
        print(f"  {p.customer_name}: {p.total_orders} orders, avg gap={p.avg_days_between_orders}d, "
              f"usual day={p.most_common_weekday}, last={p.last_order_date}")
    java = next(p for p in patterns if p.customer_name == "Java House")
    assert java.total_orders == 3
    # 2026-06-01, 2026-06-08, 2026-06-15 are all Mondays
    assert java.most_common_weekday == "Monday", f"got {java.most_common_weekday}"
    assert java.avg_days_between_orders == 7.0, f"got {java.avg_days_between_orders}"
    carrefour = next(p for p in patterns if p.customer_name == "Carrefour")
    assert carrefour.avg_days_between_orders is None, "single order should have no gap"
    assert len(patterns) == 3, "unlinked sale (customer_id=None) must be excluded"

    print("\nTest 2: product mix")
    mixes = compute_product_mix(sales, customers)
    java_mix = next(m for m in mixes if m.customer_name == "Java House")
    print(f"  Java House mix: {java_mix.by_cheese_kg}, top={java_mix.top_cheese}")
    assert java_mix.top_cheese == "Mozzarella"
    assert java_mix.by_cheese_kg["Mozzarella"] == 22.0

    print("\nTest 3: RFM")
    as_of = "2026-06-20"
    rfm = compute_rfm(sales, customers, as_of_date=as_of)
    for r in rfm:
        print(f"  {r.customer_name}: R={r.recency_score} F={r.frequency_score} M={r.monetary_score} "
              f"-> {r.rfm_segment} (recency_days={r.recency_days})")
    java_rfm = next(r for r in rfm if r.customer_name == "Java House")
    naivas_rfm = next(r for r in rfm if r.customer_name == "Naivas")
    assert java_rfm.recency_score == 5, "most recent order of the 3 should score best recency"
    assert naivas_rfm.recency_score == 1, "Naivas ordered in January, should score worst recency"
    assert len(rfm) == 3

    print("\nTest 4: CLV")
    clv = compute_clv(sales, customers)
    for c in clv:
        print(f"  {c.customer_name}: hist_rev={c.historical_revenue}, aov={c.avg_order_value}, "
              f"orders/yr={c.orders_per_year_est}, projected_annual={c.projected_annual_value}")
    java_clv = next(c for c in clv if c.customer_name == "Java House")
    assert java_clv.orders_per_year_est == round(365.0 / 7.0, 1)
    carrefour_clv = next(c for c in clv if c.customer_name == "Carrefour")
    assert carrefour_clv.orders_per_year_est is None, "single order should have no cadence-based projection"
    assert carrefour_clv.projected_annual_value is None

    print("\nTest 5: churn risk")
    churn = compute_churn_risk(sales, customers, as_of_date=as_of)
    for c in churn:
        print(f"  {c.customer_name}: days_since={c.days_since_last_order}, expected_gap={c.expected_gap_days}, "
              f"ratio={c.risk_ratio}, level={c.risk_level}, used_default={c.used_default_cadence}")
    naivas_churn = next(c for c in churn if c.customer_name == "Naivas")
    assert naivas_churn.risk_level == "High", "Naivas hasn't ordered since January, should be High risk"
    carrefour_churn = next(c for c in churn if c.customer_name == "Carrefour")
    assert carrefour_churn.used_default_cadence is True, "single-order customer should use the fallback cadence"

    print("\nTest 6: return metrics")
    returns = [
        {"return_date": "2026-06-10", "customer_id": 2, "cheese_name": "Cheddar",
         "quantity_kg": 4.0, "reason_code": "near_expiry"},
        {"return_date": "2026-06-17", "customer_id": 2, "cheese_name": "Cheddar",
         "quantity_kg": 2.0, "reason_code": "near_expiry"},
        {"return_date": "2026-06-12", "customer_id": 1, "cheese_name": "Mozzarella",
         "quantity_kg": 1.0, "reason_code": "damaged_transit"},
    ]
    ret_metrics = compute_return_metrics(sales, returns, customers)
    for r in ret_metrics:
        print(f"  {r.customer_name}: returned={r.total_returned_kg}kg, rate={r.return_rate_pct}%, "
              f"est_value={r.estimated_return_value}, top_reason={r.top_reason}")
    carrefour_ret = next(r for r in ret_metrics if r.customer_name == "Carrefour")
    assert carrefour_ret.total_returned_kg == 6.0
    assert carrefour_ret.return_rate_pct == round(100 * 6.0 / 20.0, 1)
    assert carrefour_ret.top_reason == "near_expiry"
    assert carrefour_ret.estimated_return_value == round(6.0 * (15000 / 20), 2)

    print("\nAll customer_analytics checks passed.")