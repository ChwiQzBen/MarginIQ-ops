import pandas as pd
import numpy as np
from scipy.stats import norm
import sys
import os
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Path configuration for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

# Constants configuration with fallback
try:
    from config import constants as const
except ImportError:
    class DefaultConstants:
        INVENTORY_PARAMETERS = {
            'price_per_kg': 146.55,
            'container_size': 150,
            'transport_cost': 1741.94,
            'holding_rate': 0.03,
            'sub_loss_range': (1.51, 3.03),
            'lead_time_days': 1,
            'service_level': 0.95
        }
    const = DefaultConstants()

class DryIceAnalyzer:
    def __init__(self, data_loader):
        self.data_loader = data_loader
        self.constants = const.INVENTORY_PARAMETERS
        
    def calculate_kpis(self, period=None):
        """Calculate all key performance indicators with effective quantity"""
        # Get the dataframe from data loader or use directly
        df = self.data_loader.df if hasattr(self.data_loader, 'df') else self.data_loader
        
        # Filter by period if specified AND the column exists
        if period and not df.empty and 'analysis_period' in df.columns:
            original_count = len(df)
            df = df[df['analysis_period'] == period].copy()
            print(f"DEBUG: Filtered from {original_count} to {len(df)} records for period {period}")
        
        # Return empty KPIs if no data after filtering
        if df.empty:
            return self._get_empty_kpis()
        
        # Rest of your existing code...
        df = self._prepare_dataframe(df)
        
        sublimation_loss = np.mean(self.constants['sub_loss_range']) / 100
        df['Effective_Quantity'] = df['Order_Quantity_kg'] * (1 - sublimation_loss)
        
        metrics = self._calculate_base_metrics(df)
        metrics.update(self._calculate_container_metrics(df))
        metrics.update(self._calculate_cost_metrics(df))
        metrics.update(self._calculate_time_metrics(df))
        metrics.update({
            'effective_volume': df['Effective_Quantity'].sum(),
            'df': df
        })
        
        return metrics
    
    def _prepare_dataframe(self, df):
        """Ensure required columns exist and are properly formatted"""
        # Check for absolutely required columns
        if not all(col in df.columns for col in ['Order_Quantity_kg', 'Date']):
            raise ValueError("Data must contain 'Order_Quantity_kg' and 'Date' columns")
        
        # Ensure Date is datetime
        df['Date'] = pd.to_datetime(df['Date'])
        
        # Add estimated columns if missing
        if 'Containers_Used' not in df.columns:
            df['Containers_Used'] = np.ceil(
                df['Order_Quantity_kg'] / self.constants['container_size']
            )
        
        if 'Total_Cost' not in df.columns:
            df['Total_Cost'] = df['Order_Quantity_kg'] * self.constants['price_per_kg']
            
        return df
    
    def _calculate_base_metrics(self, df):
        """Calculate fundamental metrics"""
        return {
            'total_orders': len(df),
            'total_volume': df['Order_Quantity_kg'].sum(),
            'avg_order_size': df['Order_Quantity_kg'].mean(),
            'std_order_size': df['Order_Quantity_kg'].std()
        }
    
    def _calculate_container_metrics(self, df):
        """Calculate container-related metrics"""
        total_containers = df['Containers_Used'].sum()
        container_utilization = (
            df['Order_Quantity_kg'].sum() / 
            (total_containers * self.constants['container_size'])
            if total_containers > 0 else 0
        )
        return {'container_utilization': container_utilization}
    
    def _calculate_cost_metrics(self, df):
        """Calculate cost-related metrics"""
        return {
            'total_cost': df['Total_Cost'].sum(),
            'avg_cost_per_order': df['Total_Cost'].mean()
        }
    
    def _calculate_time_metrics(self, df):
        """Calculate time-based metrics"""
        time_span_days = max(1, (df['Date'].max() - df['Date'].min()).days)
        avg_order = df['Order_Quantity_kg'].mean()
        
        return {
            'avg_monthly_demand': avg_order * 30,
            'current_monthly_volume': df['Order_Quantity_kg'].sum() / time_span_days * 30,
            'order_frequency': len(df) / time_span_days * 30
        }
    
    def _get_empty_kpis(self):
        """Return default KPIs for empty dataframe"""
        return {
            'total_orders': 0,
            'total_volume': 0,
            'avg_order_size': 0,
            'std_order_size': 0,
            'avg_monthly_demand': 0,
            'current_monthly_volume': 0,
            'order_frequency': 0,
            'container_utilization': 0,
            'total_cost': 0,
            'avg_cost_per_order': 0,
            'effective_volume': 0
        }
    
    def calculate_eoq(self):
        """Economic Order Quantity calculation"""
        kpis = self.calculate_kpis()
        demand = kpis['avg_monthly_demand']
        
        # Add safety check for zero demand
        if demand <= 0:
            return 0
        
        # Check if required constants exist
        required_constants = ['transport_cost', 'holding_rate', 'price_per_kg']
        for constant in required_constants:
            if constant not in self.constants or self.constants[constant] <= 0:
                return 0
        
        return np.sqrt(
            (2 * demand * self.constants['transport_cost']) / 
            (self.constants['holding_rate'] * self.constants['price_per_kg'])
        )
    
    def calculate_safety_stock(self):
        """Calculate safety stock based on service level"""
        # Handle both DataFrame and data loader objects
        if hasattr(self.data_loader, 'df'):
            df = self.data_loader.df
        else:
            df = self.data_loader  # Assume it's a DataFrame directly
        
        # Check for empty dataframe
        if df.empty:
            return 0
        
        # Ensure Date column is datetime
        if not pd.api.types.is_datetime64_any_dtype(df['Date']):
            df['Date'] = pd.to_datetime(df['Date'])
        
        daily_demand = df.groupby(df['Date'].dt.date)['Order_Quantity_kg'].sum()
        
        if len(daily_demand) < 2:
            # Fallback for insufficient data
            return df['Order_Quantity_kg'].mean() * 0.5
        
        daily_std = daily_demand.std()
        
        # Handle case where std is 0 or NaN
        if pd.isna(daily_std) or daily_std <= 0:
            return df['Order_Quantity_kg'].mean() * 0.1
        
        # Check if service_level and lead_time_days exist in constants
        service_level = self.constants.get('service_level', 0.95)
        lead_time_days = self.constants.get('lead_time_days', 7)
        
        z_score = norm.ppf(service_level)
        lead_time_factor = np.sqrt(lead_time_days)
        
        return z_score * daily_std * lead_time_factor
    
    def forecast_demand(self, periods=30):
        """Generate demand forecast with Prophet"""
        from prophet import Prophet
        try:
            # Handle both DataFrame and data loader objects
            if hasattr(self.data_loader, 'df'):
                df = self.data_loader.df
            else:
                df = self.data_loader  # Assume it's a DataFrame directly
            
            # Check for empty dataframe
            if df.empty:
                return None
            
            # Ensure Date column is datetime
            if not pd.api.types.is_datetime64_any_dtype(df['Date']):
                df['Date'] = pd.to_datetime(df['Date'])
            
            # Prepare data for Prophet
            prophet_df = df.groupby('Date')['Order_Quantity_kg'].sum().reset_index()
            prophet_df = prophet_df.rename(columns={'Date': 'ds', 'Order_Quantity_kg': 'y'})
            
            # Check if we have enough data points
            if len(prophet_df) < 2:
                return None
            
            # Remove any rows with NaN values
            prophet_df = prophet_df.dropna()
            
            # Create and fit model
            model = Prophet(
                seasonality_mode='multiplicative',
                yearly_seasonality=True,
                weekly_seasonality=True,
                daily_seasonality=False
            )
            model.fit(prophet_df)
            
            # Generate forecast
            future = model.make_future_dataframe(periods=periods)
            forecast = model.predict(future)
            
            return forecast
        except Exception as e:
            print(f"Forecast error: {e}")
            return None
    
    def calculate_cost_savings(self, eoq):
        """Compare current costs vs EOQ optimized costs"""
        kpis = self.calculate_kpis()
        
        # Check for required constants
        transport_cost = self.constants.get('transport_cost', 0)
        holding_rate = self.constants.get('holding_rate', 0)
        price_per_kg = self.constants.get('price_per_kg', 0)
        
        # Safety checks
        if any(x <= 0 for x in [transport_cost, holding_rate, price_per_kg]):
            return {
                'current_cost': 0,
                'eoq_cost': 0,
                'savings': 0,
                'percent_savings': 0
            }
        
        # Avoid division by zero
        avg_order_size = kpis['avg_order_size']
        if avg_order_size <= 0:
            avg_order_size = 1
        
        if eoq <= 0:
            eoq = 1
        
        # Current ordering and holding costs
        current_order_cost = (
            (kpis['current_monthly_volume'] / avg_order_size) * transport_cost + 
            (holding_rate * price_per_kg * avg_order_size / 2)
        )
        
        # EOQ optimized costs
        eoq_order_cost = (
            (kpis['current_monthly_volume'] / eoq) * transport_cost + 
            (holding_rate * price_per_kg * eoq / 2)
        )
        
        savings = max(0, current_order_cost - eoq_order_cost)
        percent_savings = (savings / current_order_cost * 100) if current_order_cost > 0 else 0
        
        return {
            'current_cost': current_order_cost,
            'eoq_cost': eoq_order_cost,
            'savings': savings,
            'percent_savings': percent_savings
        }