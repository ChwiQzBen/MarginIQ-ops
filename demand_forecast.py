"""
demand_forecast.py
====================
Turns per-cheese historical daily sales into the (mean, std) demand
distribution that CheeseLine / NewsvendorModel / ProductionPlanner need.

Deliberately does NOT reimplement an ML ensemble. The Dry Ice app already
has a working, tuned forecaster (AdvancedForecaster in
app/core/advanced_forecasting_v2.py, using XGBoost/LightGBM/Prophet/ARIMA
with its own ~30-60 day reliability floor). This module is an ADAPTER
around that — pass it in as `ml_forecaster` once real per-SKU sales
history exists. Rebuilding the ensemble here would just be duplicate
maintenance of the same forecasting logic in two places.

Until there's enough history for ML to be reliable, this falls back to a
plain rolling mean/std — and says so explicitly in `confidence_note`,
rather than dressing up a guess as a forecast.
"""

from dataclasses import dataclass
from typing import Optional, Callable, List, Dict, Tuple
import numpy as np


MIN_DAYS_FOR_ML = 60             # matches the reliability floor already used for Dry Ice
MIN_DAYS_FOR_STABLE_STATS = 7    # below this, even a simple mean is noisy


@dataclass
class DemandForecast:
    cheese_name: str
    mean: float
    std: float
    method: str              # "ml_ensemble" | "rolling_stats" | "insufficient_data"
    data_points_used: int
    confidence_note: str


class CheeseDemandForecaster:
    """
    forecaster = CheeseDemandForecaster()                      # stats-only, honest about it
    forecaster = CheeseDemandForecaster(ml_forecaster=my_fn)   # once real history exists

    `ml_forecaster` is a plain callable(cheese_name, daily_sales_kg) -> (mean, std),
    not a hard import — this module has zero dependency on the forecasting
    stack until you actually wire one in.
    """

    def __init__(self, ml_forecaster: Optional[Callable[[str, List[float]], Tuple[float, float]]] = None):
        self.ml_forecaster = ml_forecaster

    def forecast(self, cheese_name: str, daily_sales_kg: List[float],
                 fallback_mean: Optional[float] = None,
                 fallback_cv: float = 0.35) -> DemandForecast:
        n = len(daily_sales_kg)

        if n == 0:
            if fallback_mean is None:
                raise ValueError(
                    f"No sales history for {cheese_name} and no fallback_mean given — "
                    f"provide a rough manual estimate to get started."
                )
            return DemandForecast(
                cheese_name=cheese_name,
                mean=fallback_mean,
                std=fallback_mean * fallback_cv,
                method="insufficient_data",
                data_points_used=0,
                confidence_note=(
                    f"No sales history yet — using a manual estimate ({fallback_mean:.0f}kg), "
                    f"with extra margin built in since this is a guess, not based on real data."
                ),
            )

        if n >= MIN_DAYS_FOR_ML and self.ml_forecaster is not None:
            mean, std = self.ml_forecaster(cheese_name, daily_sales_kg)
            return DemandForecast(
                cheese_name=cheese_name,
                mean=float(mean),
                std=float(std),
                method="ml_ensemble",
                data_points_used=n,
                confidence_note=f"Advanced forecast based on {n} days of sales history.",
            )

        # Rolling-statistics fallback
        arr = np.array(daily_sales_kg[-90:])  # cap window so stale data doesn't dominate
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1)) if len(arr) > 1 else mean * fallback_cv
        std = max(std, mean * 0.05)  # floor so the newsvendor never treats demand as certain

        if n < MIN_DAYS_FOR_STABLE_STATS:
            note = (f"Only {n} days of sales so far — the estimate is still noisy. "
                    f"Treat this as a rough starting point, not a firm number.")
        elif n < MIN_DAYS_FOR_ML:
            note = (f"{n} days of sales history — using a simple average-based estimate. "
                    f"Needs {MIN_DAYS_FOR_ML}+ days before switching to the more advanced forecast.")
        else:
            note = f"{n} days of sales history — using a simple average-based estimate."

        return DemandForecast(
            cheese_name=cheese_name, mean=mean, std=std,
            method="rolling_stats", data_points_used=n, confidence_note=note,
        )

    def forecast_all(self, sales_by_cheese: Dict[str, List[float]],
                      fallback_means: Optional[Dict[str, float]] = None) -> Dict[str, DemandForecast]:
        fallback_means = fallback_means or {}
        return {
            name: self.forecast(name, sales, fallback_mean=fallback_means.get(name))
            for name, sales in sales_by_cheese.items()
        }

    @staticmethod
    def to_planner_input(forecasts: Dict[str, DemandForecast]) -> Dict[str, Tuple[float, float]]:
        """Reshapes forecaster output into what ProductionPlanner.build_plan() expects."""
        return {name: (f.mean, f.std) for name, f in forecasts.items()}


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Case 1: brand-new SKU, zero sales history")
    print("=" * 60)
    forecaster = CheeseDemandForecaster()
    result = forecaster.forecast("Gruyere", [], fallback_mean=15.0)
    print(f"Method: {result.method}")
    print(f"Mean: {result.mean:.1f}kg  Std: {result.std:.1f}kg")
    print(f"Note: {result.confidence_note}")
    assert result.method == "insufficient_data"
    assert result.std == 15.0 * 0.35

    print("\n" + "=" * 60)
    print("Case 2: thin history (10 days) — below ML floor")
    print("=" * 60)
    np.random.seed(42)
    thin_history = list(np.random.normal(50, 12, 10).clip(min=0))
    result = forecaster.forecast("Mozzarella", thin_history)
    print(f"Method: {result.method}  (n={result.data_points_used})")
    print(f"Mean: {result.mean:.1f}kg  Std: {result.std:.1f}kg")
    print(f"Note: {result.confidence_note}")
    assert result.method == "rolling_stats"
    assert "Needs" in result.confidence_note

    print("\n" + "=" * 60)
    print("Case 3: 90 days of history, no ML forecaster wired in")
    print("=" * 60)
    long_history = list(np.random.normal(60, 15, 90).clip(min=0))
    result = forecaster.forecast("Cheddar", long_history)
    print(f"Method: {result.method}  (n={result.data_points_used})")
    print(f"Mean: {result.mean:.1f}kg  Std: {result.std:.1f}kg")
    print(f"Note: {result.confidence_note}")
    assert result.method == "rolling_stats"
    assert "simple average-based estimate" in result.confidence_note

    print("\n" + "=" * 60)
    print("Case 4: 90 days + an ML forecaster IS wired in (dependency injection)")
    print("=" * 60)
    def fake_ensemble(cheese_name, sales):
        # Stand-in for AdvancedForecaster — proves the adapter pattern works
        # without this module knowing anything about XGBoost/Prophet/etc.
        return np.mean(sales) * 1.05, np.std(sales) * 0.9  # e.g. trend-adjusted, tighter CI

    forecaster_with_ml = CheeseDemandForecaster(ml_forecaster=fake_ensemble)
    result = forecaster_with_ml.forecast("Cheddar", long_history)
    print(f"Method: {result.method}  (n={result.data_points_used})")
    print(f"Mean: {result.mean:.1f}kg  Std: {result.std:.1f}kg")
    print(f"Note: {result.confidence_note}")
    assert result.method == "ml_ensemble"

    print("\n" + "=" * 60)
    print("End-to-end: sales history -> forecast -> ProductionPlan")
    print("=" * 60)
    from newsvendor_engine import AgingConfig
    from production_tracking import RecipeBook, CheeseRecipe, BOMLineItem, OperationStep, BatchTracker
    from production_plan import ProductionPlanner

    book = RecipeBook()
    book.add(CheeseRecipe(
        name="Mozzarella", product_code="MOZZ-5", category="Fresh Cheese",
        batch_size_kg=6.0, milk_liters_per_batch=50.0,
        shelf_life_days=30, lead_time_days=1,
        non_milk_ingredients=[
            BOMLineItem("Bacterial Culture", 0.3, "liters", 1200.0),
            BOMLineItem("Rennet", 0.06, "liters", 2500.0),
        ],
        packaging=[BOMLineItem("Plastic Wrap", 0.5, "roll", 150.0)],
        operations=[OperationStep("Pasteurization", 0.3, "Pasteurizer", 500.0)],
    ))
    book.add(CheeseRecipe(
        name="Cheddar", product_code="CHED-10", category="Semi-Hard Cheese",
        batch_size_kg=8.5, milk_liters_per_batch=100.0,
        shelf_life_days=180, lead_time_days=14,
        non_milk_ingredients=[BOMLineItem("Bacterial Culture", 0.5, "liters", 1200.0)],
        packaging=[BOMLineItem("Wax Coating", 1, "roll", 150.0)],
        operations=[OperationStep("Pressing", 2.0, "Press", 300.0)],
    ))

    tracker = BatchTracker()
    demand_forecasts = forecaster.forecast_all({
        "Mozzarella": thin_history,
        "Cheddar": long_history,
    })
    for name, f in demand_forecasts.items():
        print(f"  {name}: {f.mean:.1f}kg ± {f.std:.1f}kg [{f.method}]")

    planner = ProductionPlanner(book, tracker, milk_cost_per_liter=45.0, raw_milk_price_per_liter=35.0)
    plan = planner.build_plan(
        milk_available_l=900.0,
        demand_forecast=CheeseDemandForecaster.to_planner_input(demand_forecasts),
        selling_prices={"Mozzarella": 650.0, "Cheddar": 750.0},
    )
    print()
    print(plan.summary())
    assert len(plan.recommendations) > 0

    print("\nAll checks passed.")