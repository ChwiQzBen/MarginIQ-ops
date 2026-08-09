"""
production_plan.py
====================
Ties recipes, milk allocation, and FEFO stock into ONE object per day's
decision, with plain-language reasons attached to each recommendation.

This is the single idea worth taking from a "proper APS system" pitch:
everything flows through one ProductionPlan object rather than scattered
function calls. It deliberately does NOT bring in base-class hierarchies,
repositories, or a 25-module framework — those solve a team-coordination
problem this project doesn't have.

The explanation pattern below mirrors InventoryDecisionEngine.executive_summary()
in the existing Dry Ice app (app/core/decision_engine.py) — same idea,
applied to the cheese/milk decision instead of the dry ice reorder decision.

CHANGE LOG
----------
- build_plan() now accepts optional aging_room_capacity_kg / aging_room_used_kg
  so the aging-room space constraint is enforced INSIDE the optimization
  (via MilkAllocator's new capacity_by_cheese), not just flagged after the
  fact. Remaining room is applied as a per-line cap on every aged cheese;
  see the note on ProductionPlan.warnings if multiple aged cheeses together
  would still overflow the room (a true joint constraint needs an LP solver,
  which is deliberately out of scope here — this gets you 95% of the value
  with none of the added complexity).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from newsvendor_engine import MilkAllocator, CheeseLine, AllocationResult
from production_tracking import RecipeBook, BatchTracker, FEFOInventory


@dataclass
class ProductionRecommendation:
    cheese_name: str
    recommended_kg: float
    milk_allocated_l: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class ProductionPlan:
    """The single object a day's production decision lives in — pass this
    around, don't pass the raw allocation result and recipe book separately."""
    plan_date: datetime
    milk_available_l: float
    allocation: AllocationResult
    recommendations: List[ProductionRecommendation]
    total_profit: float
    unmet_demand_kg: float
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Production Plan — {self.plan_date.date()}"]
        lines.append(f"Milk: {self.milk_available_l:.0f}L available, "
                      f"{self.allocation.utilization_rate:.0%} allocated")
        lines.append("")
        for rec in self.recommendations:
            lines.append(f"  {rec.cheese_name}: produce {rec.recommended_kg:.1f}kg "
                         f"({rec.milk_allocated_l:.0f}L milk)")
            for reason in rec.reasons:
                lines.append(f"    - {reason}")
        lines.append("")
        lines.append(f"Expected profit today: KSh {self.total_profit:,.0f}")
        if self.unmet_demand_kg > 0:
            lines.append(f"Unmet demand: {self.unmet_demand_kg:.1f}kg")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


class ProductionPlanner:
    """
    Builds a ProductionPlan from today's milk supply + demand forecast,
    reading unit costs from RecipeBook and current stock from FEFOInventory
    (so stock already on hand reduces what gets recommended for production —
    this is the same current_inventory wiring proven in production_tracking.py's
    self-test).
    """

    def __init__(self, recipe_book: RecipeBook, tracker: BatchTracker,
                 milk_cost_per_liter: float, raw_milk_price_per_liter: float):
        self.recipes = recipe_book
        self.tracker = tracker
        self.fefo = FEFOInventory(tracker)
        self.allocator = MilkAllocator(milk_cost_per_liter, raw_milk_price_per_liter)

    def build_plan(self,
                   milk_available_l: float,
                   demand_forecast: Dict[str, Tuple[float, float]],  # name -> (mean, std)
                   selling_prices: Dict[str, float],
                   aging_room_capacity_kg: Optional[float] = None,
                   aging_room_used_kg: float = 0.0,
                   confirmed_demand: Optional[Dict[str, float]] = None) -> ProductionPlan:
        """
        Args:
            aging_room_capacity_kg: total aging room capacity, if you want the
                room's physical space to constrain today's production of any
                cheese with an `aging` config. Omit (None) to skip the check
                entirely, e.g. if you don't age anything yet.
            aging_room_used_kg: kg already occupying the room from earlier
                batches still aging (see cheese_data_access.get_aging_room_used_kg).
        """

        confirmed_demand = confirmed_demand or {}
        cheese_lines = []
        for cheese_name, (mean, std) in demand_forecast.items():
            recipe = self.recipes.get(cheese_name)
            current_stock = self.fefo.total_available_kg(cheese_name)
            cheese_lines.append(CheeseLine(
                name=cheese_name,
                yield_kg_per_liter=recipe.yield_kg_per_liter_milk(),
                selling_price=selling_prices[cheese_name],
                production_cost=recipe.unit_cost(),
                demand_mean=mean,
                demand_std=std,
                current_inventory=current_stock,
                aging=recipe.aging,
                shelf_life_days=recipe.shelf_life_days,
                confirmed_demand_kg=confirmed_demand.get(cheese_name, 0.0),
            ))

        # Aging-room constraint: every aged cheese is capped at whatever
        # room is left. This is a simplification (see module docstring) —
        # it stops any SINGLE aged cheese from overflowing the room, but if
        # several aged cheeses are planned on the same day you still need to
        # check the combined total below (we warn if that happens).
        capacity_by_cheese = None
        if aging_room_capacity_kg is not None:
            remaining_room = max(0.0, aging_room_capacity_kg - aging_room_used_kg)
            capacity_by_cheese = {
                line.name: remaining_room for line in cheese_lines if line.aging is not None
            }

        result = self.allocator.allocate(milk_available_l, cheese_lines,
                                          capacity_by_cheese=capacity_by_cheese)

        recommendations = []
        warnings = []

        for line in result.lines:
            recipe = self.recipes.get(line.cheese)
            current_stock = self.fefo.total_available_kg(line.cheese)
            forecast_mean = demand_forecast[line.cheese][0]
            reasons = []

            if current_stock < forecast_mean * 0.5:
                reasons.append(
                    f"Current stock ({current_stock:.0f}kg) covers less than half of "
                    f"forecast demand ({forecast_mean:.0f}kg)"
                )
            if recipe.aging:
                reasons.append(
                    f"Aged {recipe.aging.aging_years:.0f}yr — this is a bet on demand "
                    f"{recipe.aging.aging_years:.0f} years from now, not today's demand"
                )
            if line.capacity_applied:
                reasons.append(
                    f"Capped by aging-room capacity — newsvendor-optimal quantity was "
                    f"higher, but the room doesn't have space for more"
                )
            if line.floor_applied:
                reasons.append(
                    f"Raised to {line.confirmed_demand_kg:.0f}kg to cover confirmed LPO "
                    f"orders — newsvendor-optimal quantity alone was lower"
                )
            if line.shelf_life_multiplier > 1.05:
                reasons.append(
                    f"Short shelf life ({recipe.shelf_life_days}d) raises the effective "
                    f"cost of overproducing ({line.shelf_life_multiplier:.1f}x) — safety "
                    f"stock trimmed vs. what a longer-shelf-life SKU would carry"
                )
            if not line.fully_allocated:
                reasons.append("Milk-constrained — below newsvendor-optimal quantity")
                warnings.append(f"{line.cheese}: under-produced due to milk shortage today")
                if line.confirmed_demand_kg > 0 and line.cheese_produced_kg < line.confirmed_demand_kg:
                    warnings.append(
                        f"{line.cheese}: confirmed LPO demand ({line.confirmed_demand_kg:.0f}kg) "
                        f"not fully covered — {line.confirmed_demand_kg - line.cheese_produced_kg:.0f}kg short"
                    )
            if line.expected_leftover_kg > max(line.cheese_produced_kg * 0.15, 1e-6):
                warnings.append(
                    f"{line.cheese}: expected leftover {line.expected_leftover_kg:.1f}kg "
                    f"(>15% of today's production)"
                )
            if not reasons:
                reasons.append("Highest expected profit per liter of milk among today's options")

            recommendations.append(ProductionRecommendation(
                cheese_name=line.cheese,
                recommended_kg=line.cheese_produced_kg,
                milk_allocated_l=line.milk_allocated_l,
                reasons=reasons,
            ))

        if result.milk_wasted_l > 0:
            warnings.append(
                f"{result.milk_wasted_l:.0f}L milk wasted — no cheese line could "
                f"absorb it profitably (raw milk sale would have been the better option "
                f"only if price allows)"
            )

        # Joint aging-room check: if MULTIPLE aged cheeses are being produced
        # today, each was individually capped at "all remaining room", but
        # together they could still overflow it. Flag that plainly rather
        # than silently over-committing the room.
        if aging_room_capacity_kg is not None:
            aged_today_kg = sum(
                rec.recommended_kg for rec in recommendations
                if self.recipes.get(rec.cheese_name).aging is not None
            )
            remaining_room = max(0.0, aging_room_capacity_kg - aging_room_used_kg)
            if aged_today_kg > remaining_room + 1e-6:
                warnings.append(
                    f"Combined aged-cheese production today ({aged_today_kg:.1f}kg) exceeds "
                    f"remaining aging room ({remaining_room:.1f}kg) once multiple aged "
                    f"cheeses are counted together — manually reduce one of them before "
                    f"executing this plan."
                )

        return ProductionPlan(
            plan_date=datetime.now(),
            milk_available_l=milk_available_l,
            allocation=result,
            recommendations=recommendations,
            total_profit=result.total_profit,
            unmet_demand_kg=result.unmet_demand_kg,
            warnings=warnings,
        )

    def execute_plan(self, plan: ProductionPlan, operator: str,
                      milk_receipt_ids: List[str]) -> List[str]:
        """Turns a ProductionPlan's recommendations into real ProductionBatch
        records via BatchTracker. Returns the created batch IDs. This is the
        explicit "commit" step — building a plan never mutates tracker state
        on its own, so you can preview a plan before acting on it."""
        batch_ids = []
        for rec in plan.recommendations:
            if rec.recommended_kg <= 0:
                continue
            recipe = self.recipes.get(rec.cheese_name)
            batch = self.tracker.start_production(
                cheese_name=rec.cheese_name,
                recipe_version=recipe.recipe_version,
                quantity_kg=rec.recommended_kg,
                milk_receipt_ids=milk_receipt_ids,
                operator=operator,
                product_code=recipe.product_code,
            )
            batch_ids.append(batch.batch_id)
        return batch_ids


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    from newsvendor_engine import AgingConfig
    from production_tracking import CheeseRecipe, BOMLineItem, OperationStep, DEFAULT_PRODUCTION_CHECKPOINTS

    print("=" * 60)
    print("Setting up recipes, tracker, and existing stock")
    print("=" * 60)

    book = RecipeBook()
    mozzarella = CheeseRecipe(
        name="Mozzarella", product_code="MOZZ-5", category="Fresh Cheese",
        batch_size_kg=6.0, milk_liters_per_batch=50.0,
        shelf_life_days=30, lead_time_days=1,
        non_milk_ingredients=[
            BOMLineItem("Bacterial Culture", 0.3, "liters", 1200.0),
            BOMLineItem("Rennet", 0.06, "liters", 2500.0),
            BOMLineItem("Salt", 1.0, "kg", 80.0),
        ],
        packaging=[BOMLineItem("Plastic Wrap", 0.5, "roll", 150.0)],
        operations=[
            OperationStep("Pasteurization", 0.3, "Pasteurizer", 500.0),
            OperationStep("Curding", 0.5, "Cheese Vat", 400.0),
        ],
    )
    book.add(mozzarella)

    parmesan_aging = AgingConfig(aging_years=2.0, storage_cost_rate=0.035, aging_loss_rate=0.025,
                                  financing_rate=0.06, yield_rate=0.88, overage_penalty_multiplier=1.5)
    parmesan = CheeseRecipe(
        name="Reserve Parmesan", product_code="PARM-24", category="Aged Hard Cheese",
        batch_size_kg=35.0, milk_liters_per_batch=550.0,
        shelf_life_days=365 * 3, lead_time_days=365 * 2 + 30,
        non_milk_ingredients=[
            BOMLineItem("Bacterial Culture", 0.8, "liters", 1500.0),
            BOMLineItem("Rennet", 0.15, "liters", 3000.0),
            BOMLineItem("Salt", 2.5, "kg", 80.0),
        ],
        packaging=[BOMLineItem("Wooden Crate", 1, "unit", 350.0)],
        operations=[
            OperationStep("Pasteurization", 0.6, "Pasteurizer", 500.0),
            OperationStep("Pressing", 4.0, "Press", 300.0),
        ],
        aging=parmesan_aging,
    )
    book.add(parmesan)

    tracker = BatchTracker()

    # Simulate existing Mozzarella stock already on hand (low, to trigger a
    # "covers less than half of demand" reason) plus a Parmesan batch mid-aging
    low_stock_pb = tracker.start_production("Mozzarella", mozzarella.recipe_version,
                                             15.0, ["MILK-20260712-EXIST"], "J. Kamau")
    for stage in DEFAULT_PRODUCTION_CHECKPOINTS:
        tracker.record_production_checkpoint(low_stock_pb.batch_id, stage, passed=True)
    tracker.release_fresh_to_finished(low_stock_pb.batch_id, mozzarella.shelf_life_days)

    print(f"Existing Mozzarella stock: {FEFOInventory(tracker).total_available_kg('Mozzarella'):.1f} kg")

    print("\n" + "=" * 60)
    print("Building today's production plan (no aging-room constraint)")
    print("=" * 60)

    planner = ProductionPlanner(
        recipe_book=book,
        tracker=tracker,
        milk_cost_per_liter=45.0,
        raw_milk_price_per_liter=35.0,
    )

    plan = planner.build_plan(
        milk_available_l=700.0,
        demand_forecast={
            "Mozzarella": (60.0, 18.0),
            "Reserve Parmesan": (20.0, 6.0),
        },
        selling_prices={
            "Mozzarella": 650.0,
            "Reserve Parmesan": 1800.0,
        },
    )

    print(plan.summary())

    # ---- Assertions proving the explanation logic actually fired ----
    moz_rec = next(r for r in plan.recommendations if r.cheese_name == "Mozzarella")
    assert any("less than half" in r for r in moz_rec.reasons), \
        "Low existing stock should trigger the 'covers less than half' reason"

    parm_rec = next((r for r in plan.recommendations if r.cheese_name == "Reserve Parmesan"), None)
    if parm_rec:
        assert any("Aged" in r for r in parm_rec.reasons), \
            "Aged cheese should always carry the aging-horizon reason"

    print("\n" + "=" * 60)
    print("Building today's plan WITH a tight aging-room constraint (5kg left)")
    print("=" * 60)
    capped_plan = planner.build_plan(
        milk_available_l=700.0,
        demand_forecast={
            "Mozzarella": (60.0, 18.0),
            "Reserve Parmesan": (20.0, 6.0),
        },
        selling_prices={
            "Mozzarella": 650.0,
            "Reserve Parmesan": 1800.0,
        },
        aging_room_capacity_kg=5.0,
        aging_room_used_kg=0.0,
    )
    print(capped_plan.summary())
    capped_parm_rec = next(r for r in capped_plan.recommendations if r.cheese_name == "Reserve Parmesan")
    assert capped_parm_rec.recommended_kg <= 5.0 + 1e-6, "aging room cap should have capped production"
    assert any("aging-room capacity" in r for r in capped_parm_rec.reasons)

    print("\n" + "=" * 60)
    print("Executing the plan (creates real ProductionBatch records)")
    print("=" * 60)
    batch_ids = planner.execute_plan(plan, operator="M. Otieno", milk_receipt_ids=["MILK-20260713-TODAY"])
    print(f"Created batches: {batch_ids}")
    assert len(batch_ids) == len(plan.recommendations), "Every recommendation should produce a batch"
    for bid in batch_ids:
        assert bid in tracker.production_batches

    print("\nAll checks passed.")