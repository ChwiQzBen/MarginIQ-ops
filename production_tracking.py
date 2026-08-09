"""
production_tracking.py
========================
Step 1 (BOM / master data), Step 3 (batch tracking + quality / traceability),
and Step 4 (FEFO inventory) for Browns Cheese, built to plug directly into
newsvendor_engine.py.

Design rules carried over from newsvendor_engine.py:
- Framework-free (no Streamlit, no SQLite) so it's unit-testable in isolation.
- One source of truth per concept: FEFOInventory does NOT keep its own stock
  ledger, it reads live off BatchTracker.finished_batches. Two ledgers that
  can drift apart was a real problem in the original brainstorm - avoided here.
- All quantities are in kg throughout, matching newsvendor_engine's CheeseLine,
  so a recipe's unit_cost() is directly usable as NewsvendorModel(unit_cost=...).

Swap the in-memory dicts for Supabase tables when you wire persistence; the
method signatures are what your UI/allocator code should call against, and
shouldn't need to change when the storage layer changes.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from enum import Enum

from newsvendor_engine import AgingConfig


# ============================================================
# STEP 1: BOM / MASTER DATA
# ============================================================

@dataclass
class BOMLineItem:
    name: str
    quantity: float
    unit: str
    cost_per_unit: float

    @property
    def total_cost(self) -> float:
        return self.quantity * self.cost_per_unit


@dataclass
class OperationStep:
    name: str
    hours: float
    machine: str
    cost_per_hour: float

    @property
    def total_cost(self) -> float:
        return self.hours * self.cost_per_hour


@dataclass
class CheeseRecipe:
    """Master data record for one cheese product - the single source of
    truth a NewsvendorModel's unit_cost should come from. Quantities are in
    kg of finished cheese per batch, so unit_cost() lines up directly with
    the engine's CheeseLine.production_cost.

    Milk is intentionally excluded from unit_cost(): milk is a shared daily
    resource costed by MilkAllocator, not a per-recipe ingredient cost, to
    avoid double-charging it (the same bug fixed in newsvendor_engine.py).
    """
    name: str
    product_code: str
    category: str
    batch_size_kg: float               # kg of finished cheese per production run
    milk_liters_per_batch: float       # liters of milk consumed per batch
    shelf_life_days: int
    lead_time_days: int
    non_milk_ingredients: List[BOMLineItem] = field(default_factory=list)
    packaging: List[BOMLineItem] = field(default_factory=list)
    operations: List[OperationStep] = field(default_factory=list)
    aging: Optional[AgingConfig] = None
    recipe_version: str = "v1.0"

    def cost_breakdown(self) -> Dict[str, float]:
        breakdown = {}
        for item in self.non_milk_ingredients:
            breakdown[f"Ingredient: {item.name}"] = item.total_cost
        for item in self.packaging:
            breakdown[f"Packaging: {item.name}"] = item.total_cost
        for op in self.operations:
            breakdown[f"Operation: {op.name}"] = op.total_cost
        return breakdown

    def total_batch_cost(self) -> float:
        return sum(self.cost_breakdown().values())

    def unit_cost(self) -> float:
        """KSh per kg, excluding milk. Feed this straight into
        NewsvendorModel(unit_cost=...) or CheeseLine(production_cost=...)."""
        if self.batch_size_kg <= 0:
            raise ValueError(f"{self.name}: batch_size_kg must be > 0")
        return self.total_batch_cost() / self.batch_size_kg

    def yield_kg_per_liter_milk(self) -> float:
        if self.milk_liters_per_batch <= 0:
            raise ValueError(f"{self.name}: milk_liters_per_batch must be > 0")
        return self.batch_size_kg / self.milk_liters_per_batch


class RecipeBook:
    """In-memory registry of cheese recipes. Swap for a Supabase table later;
    keep the get/add/list interface the same for callers."""

    def __init__(self):
        self._recipes: Dict[str, CheeseRecipe] = {}

    def add(self, recipe: CheeseRecipe) -> None:
        self._recipes[recipe.name] = recipe

    def remove(self, name: str) -> None:
        if name not in self._recipes:
            raise KeyError(f"No recipe registered for '{name}'")
        del self._recipes[name]

    def get(self, name: str) -> CheeseRecipe:
        if name not in self._recipes:
            raise KeyError(f"No recipe registered for '{name}'")
        return self._recipes[name]

    def list_names(self) -> List[str]:
        return list(self._recipes.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._recipes


# ============================================================
# STEP 3: BATCH TRACKING, QUALITY CHECKPOINTS & TRACEABILITY
# ============================================================

class BatchStatus(str, Enum):
    IN_PRODUCTION = "In Production"
    AGING = "Aging"
    PASSED_QC = "Passed QC"
    FAILED_QC = "Failed QC"
    FINISHED = "Finished Good"
    DISPATCHED = "Dispatched"
    RECALLED = "Recalled"


DEFAULT_PRODUCTION_CHECKPOINTS = [
    "Raw Milk Inspection", "Pasteurization Check", "Curd Quality Check", "Final Product Inspection"
]


def _sanitize_batch_code(code: str) -> str:
    """Turns a product code (or, as fallback, a cheese name) into something
    safe to use inside a batch ID: uppercase, whitespace collapsed to a
    single hyphen. Real codes like 'MOZZ-5' pass through unchanged."""
    cleaned = "-".join(str(code).strip().split())
    return cleaned.upper() if cleaned else "ITEM"


@dataclass
class QualityCheckpoint:
    stage: str
    required: bool = True
    status: str = "Pending"            # Pending / Passed / Failed
    checked_at: Optional[datetime] = None
    notes: str = ""

    def record(self, passed: bool, notes: str = "") -> None:
        self.status = "Passed" if passed else "Failed"
        self.checked_at = datetime.now()
        self.notes = notes


@dataclass
class ProductionBatch:
    batch_id: str
    cheese_name: str
    recipe_version: str
    quantity_kg: float
    milk_receipt_ids: List[str]        # traceability link back to today's milk source(s)
    operator: str
    status: BatchStatus = BatchStatus.IN_PRODUCTION
    checkpoints: List[QualityCheckpoint] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def all_required_passed(self) -> bool:
        return all(c.status == "Passed" for c in self.checkpoints if c.required)

    def any_failed(self) -> bool:
        return any(c.status == "Failed" for c in self.checkpoints if c.required)


@dataclass
class AgingBatch:
    batch_id: str
    production_batch_id: str
    cheese_name: str
    aging_years: float
    start_date: datetime
    scheduled_end_date: datetime
    starting_quantity_kg: float
    status: BatchStatus = BatchStatus.AGING
    checkpoints: List[QualityCheckpoint] = field(default_factory=list)

    def days_remaining(self, as_of: Optional[datetime] = None) -> int:
        as_of = as_of or datetime.now()
        return max(0, (self.scheduled_end_date - as_of).days)

    def any_failed(self) -> bool:
        """Check if any required checkpoint has failed."""
        return any(cp.status == "Failed" for cp in self.checkpoints if cp.required)


@dataclass
class FinishedGoodBatch:
    batch_id: str
    production_batch_id: str
    aging_batch_id: Optional[str]
    cheese_name: str
    quantity_kg: float
    packaging_date: datetime
    expiry_date: datetime
    status: BatchStatus = BatchStatus.FINISHED

    def days_to_expiry(self, as_of: Optional[datetime] = None) -> int:
        as_of = as_of or datetime.now()
        return (self.expiry_date - as_of).days


class BatchTracker:
    """The single store for production, aging, and finished-good batches.
    FEFOInventory reads directly from `finished_batches` below - there is no
    separate stock table to keep in sync."""

    def __init__(self):
        self.production_batches: Dict[str, ProductionBatch] = {}
        self.aging_batches: Dict[str, AgingBatch] = {}
        self.finished_batches: Dict[str, FinishedGoodBatch] = {}

    def _next_production_batch_id(self, product_code: str, as_of: Optional[datetime] = None) -> str:
        """Batch number = ITEM-YYYYMMDD-SEQ (e.g. MOZZ-5-20260809-01) — item
        code + date of manufacture, the standard food-industry lot-numbering
        convention, instead of a random suffix. SEQ increments per (item,
        day) so two batches of the same cheese on the same day stay
        distinct rather than colliding. Recomputed from self.production_batches
        every call (not a stored counter), so it stays correct across app
        restarts once load_batch_tracker() rehydrates existing batches."""
        as_of = as_of or datetime.now()
        prefix = f"{_sanitize_batch_code(product_code)}-{as_of.strftime('%Y%m%d')}-"
        existing_today = [bid for bid in self.production_batches if bid.startswith(prefix)]
        return f"{prefix}{len(existing_today) + 1:02d}"   

    # -- Production -------------------------------------------------
    def start_production(self, cheese_name: str, recipe_version: str, quantity_kg: float,
                          milk_receipt_ids: List[str], operator: str,
                          checkpoint_stages: Optional[List[str]] = None,
                          product_code: str = "") -> ProductionBatch:
        batch = ProductionBatch(
            batch_id=self._next_production_batch_id(product_code or cheese_name),
            cheese_name=cheese_name,
            recipe_version=recipe_version,
            quantity_kg=quantity_kg,
            milk_receipt_ids=milk_receipt_ids,
            operator=operator,
            checkpoints=[QualityCheckpoint(stage=s)
                         for s in (checkpoint_stages or DEFAULT_PRODUCTION_CHECKPOINTS)],
        )
        self.production_batches[batch.batch_id] = batch
        return batch

    def record_production_checkpoint(self, batch_id: str, stage: str,
                                      passed: bool, notes: str = "") -> ProductionBatch:
        batch = self.production_batches[batch_id]
        for cp in batch.checkpoints:
            if cp.stage == stage:
                cp.record(passed, notes)
                break
        else:
            raise KeyError(f"No checkpoint '{stage}' on batch {batch_id}")

        if batch.any_failed():
            batch.status = BatchStatus.FAILED_QC
        elif batch.all_required_passed():
            batch.status = BatchStatus.PASSED_QC
        return batch

    # -- Aging --------------------------------------------------------
    def start_aging(self, production_batch_id: str, aging_years: float,
                     quarterly_checks: bool = True) -> AgingBatch:
        pb = self.production_batches[production_batch_id]
        if pb.status != BatchStatus.PASSED_QC:
            raise ValueError(
                f"Cannot start aging on {production_batch_id}: QC not passed "
                f"(status={pb.status.value})"
            )
        start = datetime.now()
        end = start + timedelta(days=int(aging_years * 365))
        checkpoints = []
        if quarterly_checks:
            months = int(aging_years * 12)
            checkpoints = [QualityCheckpoint(stage=f"Month {m} check")
                            for m in range(3, months + 1, 3)]

        batch = AgingBatch(
            batch_id=f"{production_batch_id}-AGE",
            production_batch_id=production_batch_id,
            cheese_name=pb.cheese_name,
            aging_years=aging_years,
            start_date=start,
            scheduled_end_date=end,
            starting_quantity_kg=pb.quantity_kg,
            checkpoints=checkpoints,
        )
        self.aging_batches[batch.batch_id] = batch
        pb.status = BatchStatus.AGING
        return batch
    
    def record_aging_checkpoint(self, aging_batch_id: str, stage: str,
                                  passed: bool, notes: str = "") -> AgingBatch:
        """Record a checkpoint result for an aging batch."""
        if aging_batch_id not in self.aging_batches:
            raise KeyError(f"Aging batch {aging_batch_id} not found")
        
        batch = self.aging_batches[aging_batch_id]
        for cp in batch.checkpoints:
            if cp.stage == stage:
                cp.record(passed, notes)
                break
        else:
            raise KeyError(f"No checkpoint '{stage}' on aging batch {aging_batch_id}")
        
        if batch.any_failed():
            batch.status = BatchStatus.FAILED_QC
        return batch

    def release_from_aging(self, aging_batch_id: str, actual_yield_kg: float,
                        shelf_life_days: int) -> FinishedGoodBatch:
        """Release an aged batch to finished goods. Raises ValueError if QC failed."""
        ab = self.aging_batches[aging_batch_id]
        
        # 🔴 BLOCK RELEASE IF ANY AGING CHECKPOINT FAILED
        if ab.any_failed():
            raise ValueError(
                f"Cannot release {aging_batch_id}: a quarterly aging check failed. "
                f"This batch must be quarantined/recalled, not released to finished goods."
            )
        
        now = datetime.now()
        fg = FinishedGoodBatch(
            batch_id=f"{ab.production_batch_id}-FG",
            production_batch_id=ab.production_batch_id,
            aging_batch_id=ab.batch_id,
            cheese_name=ab.cheese_name,
            quantity_kg=actual_yield_kg,
            packaging_date=now,
            expiry_date=now + timedelta(days=shelf_life_days),
        )
        self.finished_batches[fg.batch_id] = fg
        ab.status = BatchStatus.FINISHED
        return fg

    def release_fresh_to_finished(self, production_batch_id: str,
                                   shelf_life_days: int) -> FinishedGoodBatch:
        """For cheeses with no aging step: production -> finished directly."""
        pb = self.production_batches[production_batch_id]
        if pb.status != BatchStatus.PASSED_QC:
            raise ValueError(f"Cannot release {production_batch_id}: QC not passed")
        now = datetime.now()
        fg = FinishedGoodBatch(
            batch_id=f"{production_batch_id}-FG",
            production_batch_id=production_batch_id,
            aging_batch_id=None,
            cheese_name=pb.cheese_name,
            quantity_kg=pb.quantity_kg,
            packaging_date=now,
            expiry_date=now + timedelta(days=shelf_life_days),
        )
        self.finished_batches[fg.batch_id] = fg
        pb.status = BatchStatus.FINISHED
        return fg

    # -- Traceability ---------------------------------------------------
    def trace(self, finished_batch_id: str) -> Dict:
        """Farm-to-fork query: walk a finished-good batch back to its
        production batch, aging batch (if any), and milk receipt IDs."""
        fg = self.finished_batches[finished_batch_id]
        pb = self.production_batches.get(fg.production_batch_id)
        ab = self.aging_batches.get(fg.aging_batch_id) if fg.aging_batch_id else None
        return {
            "finished_good": fg,
            "aging_batch": ab,
            "production_batch": pb,
            "milk_receipt_ids": pb.milk_receipt_ids if pb else [],
        }


# ============================================================
# STEP 4: FEFO INVENTORY
# ============================================================

@dataclass
class FEFOAllocationLine:
    batch_id: str
    quantity_kg: float
    expiry_date: datetime


@dataclass
class FEFOAllocationResult:
    cheese_name: str
    requested_kg: float
    allocated_kg: float
    shortfall_kg: float
    lines: List[FEFOAllocationLine] = field(default_factory=list)


class FEFOInventory:
    """First-Expiry-First-Out allocation over BatchTracker.finished_batches.
    No separate stock ledger - reads and mutates the batch tracker's records
    directly, so there's exactly one place stock quantity lives."""

    def __init__(self, tracker: BatchTracker):
        self.tracker = tracker

    def stock_by_cheese(self, cheese_name: str) -> List[FinishedGoodBatch]:
        batches = [b for b in self.tracker.finished_batches.values()
                   if b.cheese_name == cheese_name
                   and b.quantity_kg > 0
                   and b.status != BatchStatus.DISPATCHED]
        return sorted(batches, key=lambda b: b.expiry_date)  # earliest expiry first

    def total_available_kg(self, cheese_name: str) -> float:
        return sum(b.quantity_kg for b in self.stock_by_cheese(cheese_name))

    def allocate(self, cheese_name: str, quantity_requested_kg: float,
             commit: bool = True) -> FEFOAllocationResult:
        remaining = quantity_requested_kg
        lines: List[FEFOAllocationLine] = []

        for batch in self.stock_by_cheese(cheese_name):
            if remaining <= 0:
                break
            take = min(batch.quantity_kg, remaining)
            if take <= 0:
                continue
            if commit:
                batch.quantity_kg -= take
                if batch.quantity_kg <= 1e-9:
                    batch.status = BatchStatus.DISPATCHED
            lines.append(FEFOAllocationLine(batch_id=batch.batch_id, quantity_kg=take,
                                            expiry_date=batch.expiry_date))
            remaining -= take

        allocated = quantity_requested_kg - remaining
        return FEFOAllocationResult(
            cheese_name=cheese_name, requested_kg=quantity_requested_kg,
            allocated_kg=allocated, shortfall_kg=max(0.0, remaining), lines=lines,
        )

    def expiring_within(self, days: int) -> List[FinishedGoodBatch]:
        cutoff = datetime.now() + timedelta(days=days)
        return sorted(
            [b for b in self.tracker.finished_batches.values()
             if b.status != BatchStatus.DISPATCHED and b.expiry_date <= cutoff],
            key=lambda b: b.expiry_date
        )