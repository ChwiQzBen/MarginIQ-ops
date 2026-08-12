"""
sales_service.py
==================
Thin orchestration layer between Commercial (which records a sale/delivery)
and Manufacturing (which owns FEFO stock dispatch and BatchTracker). Exists
so commercial_ui.py never needs to import FEFOInventory or BatchTracker
directly — Commercial owns the transaction and customer relationship,
Manufacturing owns physical stock dispatch, and this module is the seam
between them.

Also the natural place for LPO fulfillment to route through later, so
"record a walk-in sale" and "deliver against an open LPO" share ONE
allocation code path instead of two independently-maintained ones.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Dict, Any, Optional

from production_tracking import BatchTracker, FEFOInventory
from app.core.cheese_data_access import save_cheese_sale


@dataclass
class SaleResult:
    cheese_name: str
    requested_kg: float
    allocated_kg: float
    shortfall_kg: float
    revenue: float
    batch_lines: List[Dict[str, Any]]
    sale_id: Optional[int]


@dataclass
class ShelfLifeLineCheck:
    batch_id: str
    quantity_kg: float
    expiry_date: date
    days_remaining: int
    min_required_days: int
    passes: bool


@dataclass
class ShelfLifeCheckResult:
    cheese_name: str
    requested_kg: float
    allocated_kg: float
    shortfall_kg: float
    min_required_days: int
    lines: List[ShelfLifeLineCheck]
    all_pass: bool
    worst_days_remaining: Optional[int]


def check_shelf_life_before_dispatch(tracker: BatchTracker,
                                      cheese_name: str,
                                      quantity_kg: float,
                                      dispatch_date: date,
                                      shelf_life_days: int,
                                      min_shelf_life_fraction: float) -> ShelfLifeCheckResult:
    """Previews (does NOT commit) which FEFO batches a dispatch of this size
    would actually draw on, and checks each one's remaining shelf life
    against min_shelf_life_fraction of the cheese's total shelf life.

    This is the piece that PREVENTS a return rather than just measuring
    one after the fact: call this before dispatch_and_record_sale, and if
    all_pass is False, surface the warning and require explicit
    confirmation before calling dispatch_and_record_sale for real.

    Uses the same FEFOInventory.allocate(commit=False) preview mechanism
    the Manufacturing FEFO Inventory tab's "Simulate an Order" already
    uses -- no new allocation logic, just a shelf-life read on top of the
    existing preview.
    """
    fefo = FEFOInventory(tracker)
    preview = fefo.allocate(cheese_name, quantity_kg, commit=False)
    min_required_days = max(1, round(shelf_life_days * min_shelf_life_fraction))

    lines = []
    for l in preview.lines:
        expiry = l.expiry_date.date() if hasattr(l.expiry_date, "date") else l.expiry_date
        days_remaining = (expiry - dispatch_date).days
        lines.append(ShelfLifeLineCheck(
            batch_id=l.batch_id, quantity_kg=l.quantity_kg, expiry_date=expiry,
            days_remaining=days_remaining, min_required_days=min_required_days,
            passes=days_remaining >= min_required_days,
        ))

    all_pass = all(line.passes for line in lines) if lines else True
    worst = min((line.days_remaining for line in lines), default=None)

    return ShelfLifeCheckResult(
        cheese_name=cheese_name, requested_kg=quantity_kg,
        allocated_kg=preview.allocated_kg, shortfall_kg=preview.shortfall_kg,
        min_required_days=min_required_days, lines=lines, all_pass=all_pass,
        worst_days_remaining=worst,
    )


def dispatch_and_record_sale(tracker: BatchTracker,
                              cheese_name: str,
                              quantity_kg: float,
                              price_per_kg: float,
                              sale_date: date,
                              customer: str = "",
                              notes: str = "",
                              supabase_client=None,
                              customer_id: Optional[int] = None) -> SaleResult:
    """Allocates FEFO stock for a sale/delivery and persists the sale in one
    call. This is the ONE path both the Sales tab and (later) LPO delivery
    fulfillment should call — never allocate FEFO stock or call
    save_cheese_sale directly from UI code."""
    fefo = FEFOInventory(tracker)
    result = fefo.allocate(cheese_name, quantity_kg, commit=True)
    batch_lines = [{"batch_id": l.batch_id, "quantity_kg": l.quantity_kg} for l in result.lines]

    sale_id = save_cheese_sale(
        sale_date, cheese_name, result.allocated_kg, price_per_kg,
        batch_lines, customer, notes, supabase_client, customer_id=customer_id,
    )

    return SaleResult(
        cheese_name=cheese_name,
        requested_kg=quantity_kg,
        allocated_kg=result.allocated_kg,
        shortfall_kg=result.shortfall_kg,
        revenue=result.allocated_kg * price_per_kg,
        batch_lines=batch_lines,
        sale_id=sale_id,
    )


def available_stock_kg(tracker: BatchTracker, cheese_name: str) -> float:
    """Read-only stock check — used by the Sales form to show available
    stock without duplicating FEFOInventory construction in UI code."""
    return FEFOInventory(tracker).total_available_kg(cheese_name)