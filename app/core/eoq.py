"""
app/core/eoq.py
=================
Pure EOQ (Economic Order Quantity) cost math, extracted from the inline
Cost Optimization block in all_items_ui.py's All Items Analytics tab so
JIT purchasing (and anything else) can call the same formula without
going through Streamlit.

No Streamlit, no DB calls -- plain numbers in, plain numbers/dataclass out.
"""
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class EOQResult:
    eoq: float
    current_total_cost: float
    optimal_total_cost: float
    potential_savings: float  # 0.0 if current is already at/below optimal


def calculate_eoq(annual_demand: float, order_cost: float,
                   holding_rate: float, unit_price: float) -> Optional[float]:
    """Classic EOQ = sqrt(2 * D * S / (H * C)). Returns None if inputs can't
    support a valid EOQ (zero/negative demand, holding rate, or price) --
    callers should treat None as "can't compute for this item" rather than
    letting a ZeroDivisionError or negative sqrt through.
    """
    if annual_demand <= 0 or holding_rate <= 0 or unit_price <= 0:
        return None
    return math.sqrt((2 * annual_demand * order_cost) / (holding_rate * unit_price))


def calculate_eoq_costs(current_qty: float, annual_demand: float, order_cost: float,
                         holding_rate: float, unit_price: float) -> Optional[EOQResult]:
    """Current ordering+holding cost at current_qty vs. optimal cost at EOQ.
    Mirrors the inline calc in all_items_ui.py's Cost Optimization section,
    now reusable by JIT (and anything else that needs EOQ-based savings).
    """
    eoq = calculate_eoq(annual_demand, order_cost, holding_rate, unit_price)
    if eoq is None or eoq <= 0 or current_qty <= 0:
        return None

    current_total_cost = (annual_demand / current_qty) * order_cost + (current_qty / 2) * holding_rate * unit_price
    optimal_total_cost = (annual_demand / eoq) * order_cost + (eoq / 2) * holding_rate * unit_price
    savings = current_total_cost - optimal_total_cost if current_total_cost > optimal_total_cost else 0.0

    return EOQResult(
        eoq=eoq,
        current_total_cost=current_total_cost,
        optimal_total_cost=optimal_total_cost,
        potential_savings=savings,
    )


if __name__ == "__main__":
    print("Test 1: basic EOQ")
    eoq = calculate_eoq(annual_demand=1200, order_cost=500, holding_rate=0.2, unit_price=50)
    print(f"  EOQ = {eoq:.1f}")
    assert eoq is not None and eoq > 0

    print("\nTest 2: EOQ costs, current qty far from optimal")
    result = calculate_eoq_costs(current_qty=1200, annual_demand=1200, order_cost=500,
                                  holding_rate=0.2, unit_price=50)
    print(f"  eoq={result.eoq:.1f}, current_cost={result.current_total_cost:.2f}, "
          f"optimal_cost={result.optimal_total_cost:.2f}, savings={result.potential_savings:.2f}")
    assert result.potential_savings > 0, "ordering once a year should be far from optimal"

    print("\nTest 3: EOQ costs, current qty already at EOQ -> zero savings")
    at_optimal = calculate_eoq_costs(current_qty=result.eoq, annual_demand=1200, order_cost=500,
                                      holding_rate=0.2, unit_price=50)
    print(f"  savings={at_optimal.potential_savings:.4f}")
    assert at_optimal.potential_savings < 0.01, "already at EOQ should have ~zero savings"

    print("\nTest 4: invalid inputs return None, not an exception")
    assert calculate_eoq(annual_demand=0, order_cost=500, holding_rate=0.2, unit_price=50) is None
    assert calculate_eoq_costs(current_qty=0, annual_demand=1200, order_cost=500,
                                holding_rate=0.2, unit_price=50) is None

    print("\nAll eoq checks passed.")