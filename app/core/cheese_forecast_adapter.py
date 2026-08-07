"""
app/core/cheese_forecast_adapter.py
=====================================
Adapts the Dry Ice app's AdvancedForecaster (Prophet/XGBoost/LightGBM/
RandomForest/ARIMA/LSTM/Monte Carlo ensemble, app/core/advanced_forecasting_v2.py)
to the plain callable(cheese_name, daily_sales_kg) -> (mean, std) signature
that CheeseDemandForecaster(ml_forecaster=...) expects.

Kept as its own module — NOT inside demand_forecast.py — so importing
demand_forecast.py never pulls in xgboost/prophet/tensorflow/etc. unless
you actually opt into ML forecasting for cheese. demand_forecast.py stays
usable (rolling-stats fallback) even if this module's imports fail.

MODEL SUBSET: only prophet/xgboost/lightgbm/random_forest run by default,
NOT neural_prophet/arima/lstm. Two reasons, both already-hit problems on
the Dry Ice side of this app:
  1. LSTM requires tensorflow, which was previously missing from
     requirements.txt and caused it to fail silently — and Streamlit Cloud
     already hit a typing_extensions version conflict once. Pulling that
     same dependency into a second forecasting path doubles the surface
     area for that exact class of deployment failure.
  2. ARIMA's order search (9 candidate (p,d,q) combinations, each fit
     separately) and LSTM's 100-epoch training are the two slowest models
     in the ensemble — expensive to run synchronously inside a Streamlit
     tab for every cheese in the recipe book, on every cache miss.

Pass `models=[...]` to widen this back to the full 8-model ensemble if
you've confirmed tensorflow is in requirements.txt and are comfortable
with the added latency.
"""

from datetime import date, timedelta
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd

_FORECAST_DAYS = 30
_DEFAULT_MODELS = ["prophet", "xgboost", "lightgbm", "random_forest"]


def make_ensemble_demand_forecaster(models: Optional[List[str]] = None):
    """Returns a callable matching CheeseDemandForecaster's ml_forecaster
    signature:

        CheeseDemandForecaster(ml_forecaster=make_ensemble_demand_forecaster())

    A fresh AdvancedForecaster() is created on every call (not reused
    across cheeses/calls) so state from one cheese's fit — self.results,
    self.best_model, self._lstm_scaler, etc. — never leaks into another's.
    """
    active_models = models if models is not None else _DEFAULT_MODELS

    def _forecast(cheese_name: str, daily_sales_kg: List[float]) -> Tuple[float, float]:
        
        from app.core.advanced_forecasting_v2 import AdvancedForecaster
        af = AdvancedForecaster()

        end = date.today()
        n = len(daily_sales_kg)
        dates = [end - timedelta(days=n - 1 - i) for i in range(n)]
        df = pd.DataFrame({"Date": dates, "Order_Quantity_kg": daily_sales_kg})

        results = af.forecast(df, forecast_days=_FORECAST_DAYS, models=active_models)
        ensemble = results.get("ensemble")
        if not ensemble or not ensemble.get("forecast"):
            raise ValueError(
                f"Ensemble produced no forecast for {cheese_name} — "
                f"CheeseDemandForecaster will fall back to rolling stats."
            )

        forecast_vals = np.array(ensemble["forecast"])
        mean = float(np.mean(forecast_vals))

        # Derive std from the ensemble's own 95% CI band (upper/lower
        # already reflect the weighted-variance calc in
        # AdvancedForecaster._create_ensemble_forecast) rather than
        # recomputing a second, inconsistent uncertainty estimate.
        upper = np.array(ensemble.get("upper", forecast_vals))
        lower = np.array(ensemble.get("lower", forecast_vals))
        std = float(np.mean((upper - lower) / (2 * 1.96)))
        std = max(std, mean * 0.05)  # same floor CheeseDemandForecaster applies elsewhere

        return mean, std

    return _forecast