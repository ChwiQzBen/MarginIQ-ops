"""
app/core/jit_purchasing.py
============================
Pure JIT (Just-In-Time) purchasing calculations: reorder points and order
quantities, built on top of eoq.py (order quantity) and demand statistics
(reorder point). No Streamlit, no DB calls -- same "pure compute engine"
shape as newsvendor_engine.py, so this can be tested and reasoned about
independently of where supplier data ends up living (Google Sheets, per
Benson's call) and before any UI exists.

Reorder point uses the standard formula:
    ROP = (avg_daily_demand * lead_time_days) + safety_stock
    safety_stock = Z * std_daily_demand * sqrt(lead_time_days)
where Z is the z-score for the target service level -- the same
service-level-driven safety stock logic as the newsvendor engine, just
applied to a reorder-point model instead of a single-period order.
"""
from dataclasses import dataclass
from typing import List, Optional
import math
import statistics

from app.core.eoq import calculate_eoq


# Z-scores for common service levels (one-tailed normal distribution).
# Extend this table if you need a level not listed.
SERVICE_LEVEL_Z = {
    0.50: 0.00,
    0.80: 0.84,
    0.85: 1.04,
    0.90: 1.28,
    0.95: 1.65,
    0.975: 1.96,
    0.98: 2.05,
    0.99: 2.33,
    0.999: 3.09,
}

# Default service level target by ABC class, per the JIT plan
# ("95%+ for A items"). Starting points, not fixed policy -- override
# per-call when you want a different target.
DEFAULT_SERVICE_LEVEL_BY_ABC_CLASS = {
    "A": 0.95,
    "B": 0.90,
    "C": 0.80,
}


@dataclass
class Supplier:
    name: str
    lead_time_days: int
    min_order_qty: float = 0.0
    unit_cost: float = 0.0
    reliability_score: float = 1.0  # 0-1, informational for now (Phase 3: supplier performance)
    preferred: bool = True


@dataclass
class ReorderPointResult:
    reorder_point: float
    avg_daily_demand: float
    std_daily_demand: float
    lead_time_demand: float   # avg_daily_demand * lead_time_days
    safety_stock: float
    service_level: float
    z_score: float


@dataclass
class OrderQuantityResult:
    order_quantity: float
    eoq: Optional[float]  # None if EOQ couldn't be computed (see calculate_eoq)
    moq_applied: bool     # True if supplier MOQ raised the quantity above EOQ


def _z_for_service_level(service_level: float) -> float:
    """Smallest z-score whose service level meets or exceeds the requested
    one -- e.g. asking for 93% returns the 95% z (1.65) rather than
    silently under-protecting at the 90% z. A level above the table's max
    falls back to the most conservative entry (99.9%)."""
    eligible = sorted((level, z) for level, z in SERVICE_LEVEL_Z.items() if level >= service_level)
    if eligible:
        return eligible[0][1]
    return SERVICE_LEVEL_Z[max(SERVICE_LEVEL_Z)]


def calculate_jit_reorder_point(daily_demand_kg: List[float], lead_time_days: int,
                                 service_level: float = 0.95) -> Optional[ReorderPointResult]:
    """Reorder point from a list of historical daily demand values (e.g. the
    Order_Quantity_kg column from compute_daily_demand_for_item). Needs at
    least 2 data points to estimate variability -- returns None below that,
    same "not enough data" contract as the rest of this codebase rather
    than guessing at a std of 0.
    """
    if not daily_demand_kg or len(daily_demand_kg) < 2 or lead_time_days <= 0:
        return None

    avg_daily = statistics.mean(daily_demand_kg)
    std_daily = statistics.stdev(daily_demand_kg)
    z = _z_for_service_level(service_level)

    lead_time_demand = avg_daily * lead_time_days
    safety_stock = z * std_daily * math.sqrt(lead_time_days)
    reorder_point = lead_time_demand + safety_stock

    return ReorderPointResult(
        reorder_point=reorder_point,
        avg_daily_demand=avg_daily,
        std_daily_demand=std_daily,
        lead_time_demand=lead_time_demand,
        safety_stock=safety_stock,
        service_level=service_level,
        z_score=z,
    )


def calculate_jit_order_quantity(annual_demand: float, order_cost: float, holding_rate: float,
                                  unit_price: float, supplier: Supplier) -> Optional[OrderQuantityResult]:
    """EOQ as the starting point, raised to the supplier's minimum order
    quantity if EOQ falls short of it -- MOQ is a hard floor a supplier
    imposes, not a suggestion, so it always wins over the theoretical
    optimum when the two conflict.
    """
    eoq = calculate_eoq(annual_demand, order_cost, holding_rate, unit_price)
    if eoq is None:
        if supplier.min_order_qty > 0:
            return OrderQuantityResult(order_quantity=supplier.min_order_qty, eoq=None, moq_applied=True)
        return None

    if supplier.min_order_qty > eoq:
        return OrderQuantityResult(order_quantity=supplier.min_order_qty, eoq=eoq, moq_applied=True)
    return OrderQuantityResult(order_quantity=eoq, eoq=eoq, moq_applied=False)


STATUS_ORDER_NOW = "🔴 Order Now"
STATUS_ORDER_SOON = "🟡 Order Soon"
STATUS_OK = "🟢 OK"
STATUS_NO_SUPPLIER = "⚫ No supplier data yet"
STATUS_NOT_ENOUGH_HISTORY = "⚪ Not enough demand history"

# Display/sort priority -- most actionable first. Used by the dashboard to
# sort its table without re-deriving this ordering in the UI layer.
STATUS_SORT_ORDER = {
    STATUS_ORDER_NOW: 0,
    STATUS_ORDER_SOON: 1,
    STATUS_NO_SUPPLIER: 2,
    STATUS_NOT_ENOUGH_HISTORY: 3,
    STATUS_OK: 4,
}


@dataclass
class JITItemStatus:
    item_code: str
    item_label: str
    current_stock: float
    avg_daily_demand: Optional[float]
    reorder_point: Optional[float]
    suggested_order_qty: Optional[float]
    supplier_name: Optional[str]
    lead_time_days: Optional[int]
    status: str


def compute_jit_status(item_code: str, item_label: str, current_stock: float,
                        daily_demand_kg: List[float], supplier: Optional[Supplier],
                        order_cost: float, holding_rate: float, unit_price_fallback: float = 0.0,
                        service_level: float = 0.95, reorder_soon_buffer: float = 1.15) -> JITItemStatus:
    """One item's full JIT read: reorder point, suggested order quantity,
    and a status badge -- the single source of truth both the dashboard UI
    and its tests call, so the two can't drift apart.

    Degrades gracefully in the two ways an early-stage rollout actually
    hits: not enough check-out history yet (STATUS_NOT_ENOUGH_HISTORY), or
    no SUPPLIERS row for this item yet (STATUS_NO_SUPPLIER) -- both return
    a real status instead of raising, so the dashboard can show every item
    and let coverage fill in over time rather than hiding incomplete rows.

    reorder_soon_buffer=1.15 means "flag as Order Soon once stock is within
    15% of the reorder point" -- a heads-up window before it's actually
    urgent. A UX choice, not a formula; tune freely.
    """
    if len(daily_demand_kg) < 2:
        return JITItemStatus(
            item_code=item_code, item_label=item_label, current_stock=current_stock,
            avg_daily_demand=None, reorder_point=None, suggested_order_qty=None,
            supplier_name=supplier.name if supplier else None,
            lead_time_days=supplier.lead_time_days if supplier else None,
            status=STATUS_NOT_ENOUGH_HISTORY,
        )

    avg_daily = statistics.mean(daily_demand_kg)

    if supplier is None:
        return JITItemStatus(
            item_code=item_code, item_label=item_label, current_stock=current_stock,
            avg_daily_demand=avg_daily, reorder_point=None, suggested_order_qty=None,
            supplier_name=None, lead_time_days=None, status=STATUS_NO_SUPPLIER,
        )

    rp_result = calculate_jit_reorder_point(daily_demand_kg, supplier.lead_time_days, service_level)
    if rp_result is None:
        return JITItemStatus(
            item_code=item_code, item_label=item_label, current_stock=current_stock,
            avg_daily_demand=avg_daily, reorder_point=None, suggested_order_qty=None,
            supplier_name=supplier.name, lead_time_days=supplier.lead_time_days,
            status=STATUS_NOT_ENOUGH_HISTORY,
        )

    reorder_point = rp_result.reorder_point
    if current_stock <= reorder_point:
        status = STATUS_ORDER_NOW
    elif current_stock <= reorder_point * reorder_soon_buffer:
        status = STATUS_ORDER_SOON
    else:
        status = STATUS_OK

    order_qty = None
    if avg_daily > 0:
        unit_price = supplier.unit_cost or unit_price_fallback
        oq_result = calculate_jit_order_quantity(
            annual_demand=avg_daily * 365, order_cost=order_cost,
            holding_rate=holding_rate, unit_price=unit_price, supplier=supplier,
        )
        if oq_result is not None:
            order_qty = oq_result.order_quantity

    return JITItemStatus(
        item_code=item_code, item_label=item_label, current_stock=current_stock,
        avg_daily_demand=avg_daily, reorder_point=reorder_point, suggested_order_qty=order_qty,
        supplier_name=supplier.name, lead_time_days=supplier.lead_time_days, status=status,
    )


def default_service_level_for_abc_class(abc_class: str) -> float:
    """Maps an ABC class label to its default service level target. Accepts
    either a bare letter ('A') or the full label used elsewhere in the app
    ('🔴 A (70% value)') -- checks for the letter as a substring so callers
    don't need to reformat all_items_ui.py's ABC_CLASS strings first.
    """
    for letter, level in DEFAULT_SERVICE_LEVEL_BY_ABC_CLASS.items():
        if letter in abc_class:
            return level
    return DEFAULT_SERVICE_LEVEL_BY_ABC_CLASS["C"]  # unrecognized class -> least aggressive default


if __name__ == "__main__":
    print("Test 1: reorder point, steady demand")
    demand = [10, 12, 9, 11, 10, 13, 8, 10, 11, 12]
    rp = calculate_jit_reorder_point(demand, lead_time_days=5, service_level=0.95)
    print(f"  ROP={rp.reorder_point:.1f}, avg={rp.avg_daily_demand:.1f}, std={rp.std_daily_demand:.2f}, "
          f"safety_stock={rp.safety_stock:.1f}, z={rp.z_score}")
    assert rp.reorder_point > rp.lead_time_demand, "safety stock should push ROP above raw lead-time demand"

    print("\nTest 2: not enough data / bad lead time returns None")
    assert calculate_jit_reorder_point([10], lead_time_days=5) is None
    assert calculate_jit_reorder_point([], lead_time_days=5) is None
    assert calculate_jit_reorder_point(demand, lead_time_days=0) is None

    print("\nTest 3: higher service level -> higher reorder point")
    rp_90 = calculate_jit_reorder_point(demand, lead_time_days=5, service_level=0.90)
    rp_99 = calculate_jit_reorder_point(demand, lead_time_days=5, service_level=0.99)
    print(f"  ROP@90%={rp_90.reorder_point:.1f}, ROP@99%={rp_99.reorder_point:.1f}")
    assert rp_99.reorder_point > rp_90.reorder_point

    print("\nTest 4: order quantity, EOQ above supplier MOQ")
    supplier = Supplier(name="Test Supplier", lead_time_days=5, min_order_qty=50)
    oq = calculate_jit_order_quantity(annual_demand=1200, order_cost=500, holding_rate=0.2,
                                       unit_price=50, supplier=supplier)
    print(f"  order_qty={oq.order_quantity:.1f}, eoq={oq.eoq:.1f}, moq_applied={oq.moq_applied}")
    assert oq.moq_applied is False, "EOQ (~346) should be well above a 50-unit MOQ"

    print("\nTest 5: order quantity, EOQ below supplier MOQ")
    big_moq_supplier = Supplier(name="Bulk-Only Supplier", lead_time_days=5, min_order_qty=1000)
    oq2 = calculate_jit_order_quantity(annual_demand=1200, order_cost=500, holding_rate=0.2,
                                        unit_price=50, supplier=big_moq_supplier)
    print(f"  order_qty={oq2.order_quantity:.1f}, eoq={oq2.eoq:.1f}, moq_applied={oq2.moq_applied}")
    assert oq2.moq_applied is True
    assert oq2.order_quantity == 1000

    print("\nTest 6: ABC class service level mapping")
    assert default_service_level_for_abc_class("A") == 0.95
    assert default_service_level_for_abc_class("🔴 A (70% value)") == 0.95
    assert default_service_level_for_abc_class("🟡 B (20% value)") == 0.90
    assert default_service_level_for_abc_class("unknown") == 0.80

    print("\nTest 7: compute_jit_status -- not enough history")
    status = compute_jit_status("CHEM-001", "Rennet", current_stock=50, daily_demand_kg=[10],
                                 supplier=supplier, order_cost=500, holding_rate=0.2)
    print(f"  {status.status}")
    assert status.status == STATUS_NOT_ENOUGH_HISTORY
    assert status.reorder_point is None

    print("\nTest 8: compute_jit_status -- no supplier data")
    status = compute_jit_status("CHEM-001", "Rennet", current_stock=50, daily_demand_kg=demand,
                                 supplier=None, order_cost=500, holding_rate=0.2)
    print(f"  {status.status}, avg_daily={status.avg_daily_demand:.1f}")
    assert status.status == STATUS_NO_SUPPLIER
    assert status.avg_daily_demand is not None, "demand stats should still compute even without a supplier"

    print("\nTest 9: compute_jit_status -- Order Now (stock below reorder point)")
    status = compute_jit_status("CHEM-001", "Rennet", current_stock=5, daily_demand_kg=demand,
                                 supplier=supplier, order_cost=500, holding_rate=0.2, unit_price_fallback=50)
    print(f"  {status.status}, ROP={status.reorder_point:.1f}, order_qty={status.suggested_order_qty}")
    assert status.status == STATUS_ORDER_NOW

    print("\nTest 10: compute_jit_status -- OK (stock well above reorder point)")
    status = compute_jit_status("CHEM-001", "Rennet", current_stock=500, daily_demand_kg=demand,
                                 supplier=supplier, order_cost=500, holding_rate=0.2, unit_price_fallback=50)
    print(f"  {status.status}, ROP={status.reorder_point:.1f}")
    assert status.status == STATUS_OK

    print("\nTest 11: compute_jit_status -- Order Soon (stock just above reorder point)")
    rp_probe = calculate_jit_reorder_point(demand, lead_time_days=5, service_level=0.95)
    status = compute_jit_status("CHEM-001", "Rennet", current_stock=rp_probe.reorder_point * 1.05,
                                 daily_demand_kg=demand, supplier=supplier, order_cost=500,
                                 holding_rate=0.2, unit_price_fallback=50)
    print(f"  {status.status}, ROP={status.reorder_point:.1f}, stock={rp_probe.reorder_point * 1.05:.1f}")
    assert status.status == STATUS_ORDER_SOON

    print("\nAll jit_purchasing checks passed.")